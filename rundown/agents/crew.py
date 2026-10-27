"""The agent crew: Spotter, Analyst, Producer, Telestrator, Writer and Verifier.

    Spotter      the deterministic detectors (engine.detect); raises talking points
    Analyst      agent: does each talking point matter, and why
    Producer     agent: which points make the rundown for this slot and cut, in what order
    Telestrator  agent: the picture for a point (freezes, players, passes, zones), language-free
    Writer       agent: title, stat card and presenter lines per language and cut
    Verifier     code: checks everything an agent hands over (agents.verify)

Agents choose and explain; they never write numbers or coordinates. Each agent call
gets one retry with the Verifier's errors, then the crew routes around it to the rules
version of that piece and says so in the trail. The Telestrator's plan is made once per
talking point and reused by every language and cut, so a re-cut only needs the Writer.
During the half the crew analyses new points and drafts segments for the provisional
picks, so at the whistle most of the rundown is already written.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import Future
from typing import Callable, Literal

from pydantic import BaseModel, Field, create_model

from ..engine.data import MatchData
from ..engine import detect
from ..engine.detect import Candidate
from ..studio import producer, telestrate, writer
from . import verify
from .llm import AgentDown, Models
from .state import Store
from .words import Table

ROLES = ("analyst", "producer", "telestrator", "writer")
LOCK_DEADLINE_S = 20.0  # a segment not ready this long after the whistle goes out as the rules version
SEGMENT_S = (90.0 - producer.INTRO_S) / 3  # a segment in a three-segment, 90 s rundown
WORDS_PER_S = 2.6
MOMENTS = detect.MOMENTS
LANGS = {"en": "English", "es": "Spanish", "fr": "French", "pt": "Portuguese"}
ZONE_TEXT = {
    "en": {"left channel": "left side", "centre channel": "through the middle", "right channel": "right side"},
    "es": {"left channel": "banda izquierda", "centre channel": "por el centro", "right channel": "banda derecha"},
    "fr": {"left channel": "côté gauche", "centre channel": "dans l'axe", "right channel": "côté droit"},
    "pt": {"left channel": "lado esquerdo", "centre channel": "pelo meio", "right channel": "lado direito"},
}
LINE_TEXT = {"en": {"halfway": "halfway", "back line": "back line"},
             "es": {"halfway": "medio campo", "back line": "línea defensiva"},
             "fr": {"halfway": "milieu de terrain", "back line": "ligne défensive"},
             "pt": {"halfway": "meio de campo", "back line": "linha defensiva"}}

# ---- what each agent returns ----


class Verdict(BaseModel):
    id: str
    matters: float = Field(description="0 to 1: how much a TV audience needs to hear this at half-time")
    why: str = Field(description="one sentence for the producer; numbers only as placeholders like {F1}")


class Analysis(BaseModel):
    verdicts: list[Verdict]


class Pick(BaseModel):
    id: str
    reason: str = Field(description="why it is in, and why in this position")


class Picks(BaseModel):
    picks: list[Pick] = Field(description="in running order, lead story first")


class Freeze(BaseModel):
    at: float = Field(description="clip time (s) to hold the picture")
    hold: float = Field(description="1.5 to 4 seconds")


class Highlight(BaseModel):
    slot: int
    freeze: int | None = Field(None, description="index into freezes to show it during, or null")
    at: float | None = Field(None, description="clip time, when not tied to a freeze")
    show_name: bool = False


class Run(BaseModel):
    slot: int
    from_t: float = Field(description="clip time the run starts")
    to_t: float = Field(description="clip time the run ends")
    freeze: int | None = None


class Named(BaseModel):
    name: str = Field(description="one of the offered names")
    freeze: int | None = None
    at: float | None = None


class VisualPlan(BaseModel):
    focus: str = Field(description="one sentence: what the picture proves")
    freezes: list[Freeze]
    highlights: list[Highlight] = []
    arrows: list[str] = Field([], description="event ids to draw (passes, crosses, shots, carries)")
    runs: list[Run] = []
    zones: list[Named] = []
    lines: list[Named] = []


class Row(BaseModel):
    label: str = Field(description="at most 44 characters")
    facts: list[str] = Field(description="fact aliases: ['F1', 'F2'] shows 'value1 → value2' with units; "
                                         "put unit words in the label")


class Script(BaseModel):
    title: str = Field(description="at most 48 characters, no numbers")
    sub: str = Field(description="lower-third subtitle, at most 80 characters; placeholders allowed")
    card_title: str = Field(description="stat card heading, at most 44 characters: the team or player")
    rows: list[Row] = Field(description="1 to 4 rows")
    lines: list[str] = Field(description="2 to 5 presenter lines, placeholders for every number")


# ---- instructions ----

NUMBERS_RULE = """Numbers: every number must be a value from the facts table, written in digits exactly as
given, or as a placeholder: {F1} for a fact's value, {F2.top_speed_kmh} for a detail value, {MIN} for
the minute the point was spotted, {WIN} for the length in minutes of the recent window the facts
compare ("the last {WIN} minutes"). The Verifier checks every number against the engine and rejects
any other: no arithmetic (no differences, ratios, rounding), no numbers spelled out in words."""

INSTRUCTIONS = {
    "analyst": f"""You are the Analyst in a live football TV studio. The Spotter raises talking points from
the stats. For each, decide how much it matters to a half-time audience (0 to 1) and say why in one
sentence: is it a change in control, a pressure shift, a rhythm change, a player fading, a moment that
decided the half? Strong, recent, and causal beats weak or cosmetic. Be decisive; spread your scores.
{NUMBERS_RULE}""",
    "producer": """You are the Producer of a half-time show. Choose the talking points that make the rundown
and their running order, lead story first. Rules: no two points of the same kind; at most one goal or
big chance; prefer points the Analyst says matter; for a club channel (cut home or away), lead with
what matters most to that club's fans, good or bad: a point where about_your_club is true. Sustained beats fleeting: the Analyst scored each
point when it first appeared, and `held_up` shows what happened next. A pattern the detectors saw in
most scans since it appeared and still see at the whistle is a real change in the match; one seen in
a few scans that faded minutes ago is probably noise, however well it was rated at first. Peak
strength near 1 is a clear change; below about 0.5 it is marginal. A point first seen in the last two
or three minutes hasn't had time to prove itself: it needs a strong peak to beat a sustained one.
A fan profile shapes the choice: a casual fan wants clear, visible stories (a goal or chance, a player
fading, a side being pinned back) over subtle metric shifts; an analyst wants the tactical changes. When
the fan follows a player, a segment on that player is added after yours, so don't spend a pick on him
unless he is the story of a point. The talking points have been screened for clear evidence and come strongest first. Goals and big chances are marked `moment`: pick at most one of them in total. Return exactly the
number asked for when there are enough points. Give a short reason for each, written for the human producer: what the story is
and why it earns its place. No numbers or scores in the reasons.""",
    "telestrator": """You are the Telestrator. You plan the picture for one talking point: a short tracking
clip that gets frozen and drawn on, like TV analysis. You choose; the studio works out timing and
geometry.
- freezes: hold the picture at the key moment (clip time `at`, `hold` 1.5 to 4 s). One or two.
- highlights: ring players by slot during a freeze; show_name for the main one only.
- arrows: draw passes, crosses, shots or carries by event id (only events that have to_xy).
- runs: a player's movement between two clip times, shown during a freeze.
- zones and lines: pick by name from the offered options, shown during a freeze.
Show what proves the point and nothing else: two to five drawn elements in total. The detector's key
moment is a good first freeze. Write `focus` in English.""",
    "writer": f"""You are the Writer for a TV football presenter. Write the segment's words in the requested
language: a title, a lower-third subtitle, the stat card (heading and 1 to 4 rows, each row a label
plus the facts that fill it), and 2 to 5 short spoken lines that walk the viewer through the picture.
Speak like TV: plain, vivid, present tense, no jargon without a gloss. For a club channel (cut home or
away), speak to that club's fans. Depth: for a casual fan, plain words, at most three numbers in the whole script and two
in a line, never PPDA, xG or field tilt (say what they mean), and a stat card of 1 or 2 rows; for an analyst, name
the metric, give the before and the now values, explain the mechanism, and a stat card of 2 to 4 rows.
When the fan follows a player, mention him if he is part of the picture. Before and now: when you quote
both values of a change, say "from" the earlier value "to" the recent one, and a rise or fall word must
match what the values did. Keep to the word budget and the character limits. Use the language's own words
for units. Everything here happened in the first half; minutes past 45 are first-half stoppage time.
{NUMBERS_RULE}""",
}


class Rejected(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors[:3]))
        self.errors = errors


_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def crew_loop() -> asyncio.AbstractEventLoop:
    """One event loop for every crew in the process. The model client's connections belong to
    the loop that opened them, so crews that share a client must share its loop too."""
    global _loop
    with _loop_lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True, name="crew").start()
    return _loop


cand_key = detect.key


def _evidence(c: Candidate, t_now: float) -> tuple:
    return (c.kind in MOMENTS, t_now - c.last_t <= 300, c.scans * c.peak)


def _clear(c: Candidate, t_now: float) -> bool:
    """Clear evidence: seen in at least half the scans since it was spotted, seen in the last five
    minutes, and a peak strength of at least 0.6. (Set on practice seeds 201-240, never on the
    held-out eval seeds.)"""
    since = max(1, int((t_now - c.t) // 60) + 1)
    return c.scans / since >= 0.5 and t_now - c.last_t <= 300 and c.peak >= 0.6


def _held_up(c: Candidate, t_now: float) -> dict:
    """How a talking point held up after it was first spotted, as of t_now (scans run every minute)."""
    since = max(1, int((t_now - c.t) // 60) + 1)
    return dict(seen_in_scans=f"{c.scans} of {since}", peak_strength=round(c.peak, 2),
                still_showing=t_now - c.last_t <= 120, minutes_since_last_seen=round((t_now - c.last_t) / 60))


class Crew:
    def __init__(self, models: Models, store: Store, timeout_s: float = 90.0, concurrency: int = 4):
        self.models = models
        self.store = store
        self.timeout_s = timeout_s
        self.killed: set[str] = set()
        self.analysis: dict[str, dict] = {}
        self._plans: dict[str, asyncio.Future] = {}
        self._scripts: dict[tuple, asyncio.Future] = {}
        self.loop = crew_loop()
        self._sem: asyncio.Semaphore | None = None
        self._concurrency = concurrency

    # ---- thread-safe entry points ----
    def submit(self, coro) -> Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def kill(self, role: str):
        self.killed.add(role)
        self.store.record(role, "switched off", "killed", why="kill switch")

    def revive(self, role: str):
        self.killed.discard(role)
        self.store.record(role, "switched back on", "ok")

    def analyse(self, md: MatchData, cands: list[Candidate]) -> Future:
        return self.submit(self._analyse(md, cands))

    def draft(self, md: MatchData, cands: list[Candidate], t_now: float, lang: str = "en", cut: str = "neutral",
              depth: str = "analyst", player: str | None = None) -> Future:
        """Start plans and scripts in the background for the points likely to make the rundown:
        the rules picks, the Analyst's top three and the three with the clearest evidence."""
        likely = producer.pick(md, cands, t_now, 3, cut, player)
        rated = sorted((c for c in cands if cand_key(c) in self.analysis),
                       key=lambda c: -self.analysis[cand_key(c)]["matters"])
        clear = sorted((c for c in cands if c.kind not in MOMENTS and _clear(c, t_now)),
                       key=lambda c: _evidence(c, t_now), reverse=True)
        for c in rated[:3] + clear[:3]:
            if c not in likely:
                likely.append(c)

        async def go():
            await asyncio.gather(*(self._script(md, c, lang, cut, depth, player) for c in likely), return_exceptions=True)
        return self.submit(go())

    def rundown(self, md: MatchData, cands: list[Candidate], t_now: float, slot_s: float = 90.0,
                cut: str = "neutral", lang: str = "en", timeout: float = 120.0, depth: str = "analyst",
                player: str | None = None, t_from: float | None = None) -> dict:
        return self.submit(self._rundown(md, cands, t_now, slot_s, cut, lang, depth, player, t_from)).result(timeout)

    def segment(self, md: MatchData, c: Candidate, lang: str, cut: str, target_s: float, seg_id: str,
                timeout: float = 90.0, depth: str = "analyst", player: str | None = None) -> dict:
        return self.submit(self._segment(md, c, lang, cut, target_s, seg_id, depth, player)).result(timeout)

    # ---- one agent call, gated by the Verifier ----
    async def _call(self, role: str, action: str, prompt: str, schema: type[BaseModel],
                    check: Callable[[BaseModel], tuple[list[str], object]], event_ids=None):
        if role in self.killed:
            self.store.record(role, action, "killed", why="switched off; routing around it")
            raise AgentDown(role)
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._concurrency)
        session = self.models.session(role, INSTRUCTIONS[role])
        text, errs = prompt, []
        for attempt in range(2):
            t0 = time.time()
            usage = {}
            async with self._sem:
                try:
                    value, usage = await asyncio.wait_for(
                        self.models.ask(role, INSTRUCTIONS[role], session, text, schema), self.timeout_s)
                    errs, result = check(value)
                except asyncio.TimeoutError:
                    errs = [f"no answer within {self.timeout_s:.0f} s"]
                except Exception as e:  # malformed output, network, content filter
                    errs = [f"{type(e).__name__}: {str(e)[:160]}"]
            ms = (time.time() - t0) * 1000
            if role in self.killed:
                self.store.record(role, action, "killed", ms=ms, why="switched off mid-call; routing around it")
                raise AgentDown(role)
            if not errs:
                self.store.record(role, action, "ok", ms=ms, event_ids=event_ids, detail=usage)
                return result
            self.store.record(role, action, "retry" if attempt == 0 else "failed", ms=ms,
                              why="; ".join(errs[:4]), event_ids=event_ids, detail=dict(usage, errors=errs[:8]))
            text = "The Verifier rejected that. Fix these and return the whole answer again:\n- " + "\n- ".join(errs[:8])
        raise Rejected(errs)

    # ---- Analyst ----
    async def _analyse(self, md: MatchData, cands: list[Candidate]) -> None:
        todo = [c for c in cands if cand_key(c) not in self.analysis]
        if not todo:
            return
        tables = {f"c{i + 1}": (c, Table(c)) for i, c in enumerate(todo)}
        prompt = json.dumps(dict(
            match=_match_line(md),
            talking_points=[dict(id=i, kind=c.kind, team=_team(md, c.team), player=_player(md, c), side=c.side,
                                 minute=_minute(c), detector_strength=round(c.strength, 2), headline=c.headline,
                                 facts=t.for_prompt()) for i, (c, t) in tables.items()]), ensure_ascii=False)

        verdict = create_model("Verdict", __base__=Verdict, id=(Literal[tuple(tables)], ...))
        schema = create_model("Analysis", __base__=Analysis, verdicts=(list[verdict], ...))

        def check(v: Analysis):
            errs, out = [], {}
            for vd in v.verdicts:
                if vd.id not in tables:
                    errs.append(f"unknown id {vd.id}")
                    continue
                c, t = tables[vd.id]
                why, _, e = t.fill(vd.why, "en")
                errs += [f"{vd.id}: {x}" for x in e + verify.text_errors(why, "en", vd.id)]
                if not 0 <= vd.matters <= 1:
                    errs.append(f"{vd.id}: matters must be 0 to 1")
                out[vd.id] = dict(matters=round(vd.matters, 2), why=why, made_by="analyst")
            missing = sorted(set(tables) - set(out))
            if missing:
                errs.append(f"missing verdicts for {', '.join(missing)}")
            return errs, out
        try:
            out = await self._call("analyst", f"scored {len(todo)} talking points", prompt, schema, check,
                                   event_ids=[e for c in todo for e in c.evidence[:3]])
        except (AgentDown, Rejected):
            out = {i: dict(matters=round(c.strength, 2), why=c.headline, made_by="rules") for i, (c, _) in tables.items()}
            self.store.record("analyst", f"scored {len(todo)} talking points", "fallback",
                              why="used detector strength and headline")
        for i, (c, _) in tables.items():
            self.analysis[cand_key(c)] = out[i]

    # ---- Producer ----
    async def _pick(self, md: MatchData, cands: list[Candidate], t_now: float, cut: str, n: int,
                    depth: str = "analyst", player: str | None = None) -> list[tuple[Candidate, str]]:
        # Code screens the evidence, the Producer decides: only points the detectors saw steadily, recently
        # and clearly go forward (plus every goal and chance), strongest evidence first. If that leaves
        # too few kinds to fill the rundown, the next strongest come back in.
        by_evidence = sorted(cands, key=lambda c: _evidence(c, t_now), reverse=True)
        ranked = [c for c in by_evidence if c.kind in MOMENTS or _clear(c, t_now)]
        for c in by_evidence:
            if len({x.kind for x in ranked}) >= n or len(ranked) >= 12:
                break
            if c not in ranked:
                ranked.append(c)
        ranked = ranked[:12]
        ids = {f"c{i + 1}": c for i, c in enumerate(ranked)}
        fav = {"home": md.home, "away": md.away}.get(cut)
        prompt = json.dumps(dict(
            match=_match_line(md, t_now), slot_s=90, cut=cut, club_channel=_team(md, fav) if fav else None,
            fan=dict(depth=depth, follows=md.name(player) if player else None), segments_wanted=n,
            talking_points=[dict(id=i, kind=c.kind, team=_team(md, c.team), player=_player(md, c), minute=_minute(c),
                                 headline=c.headline, analyst=self.analysis.get(cand_key(c)),
                                 moment=c.kind in MOMENTS,
                                 held_up=_held_up(c, t_now) if c.kind not in MOMENTS else None,
                                 about_your_club=(c.team == fav or c.kind in MOMENTS) if fav else None)
                            for i, c in ids.items()]), ensure_ascii=False)
        want = min(n, len({c.kind for c in ranked}))
        ours = fav is not None and any(c.team == fav or c.kind in MOMENTS for c in ranked)

        pick = create_model("Pick", __base__=Pick, id=(Literal[tuple(ids)], ...))
        schema = create_model("Picks", __base__=Picks, picks=(list[pick], Picks.model_fields["picks"]))

        def check(v: Picks):
            errs, seen, kinds, moments = [], set(), set(), 0
            for p in v.picks:
                c = ids.get(p.id)
                if c is None:
                    errs.append(f"unknown id {p.id}")
                    continue
                if p.id in seen:
                    errs.append(f"{p.id} picked twice")
                if c.kind in kinds:
                    errs.append(f"two {c.kind} segments")
                seen.add(p.id)
                kinds.add(c.kind)
                moments += c.kind in MOMENTS
            if moments > 1:
                errs.append("more than one goal or big chance")
            if len(v.picks) != want:
                errs.append(f"pick exactly {want}")
            lead = ids.get(v.picks[0].id) if v.picks else None
            if fav and ours and lead is not None and lead.team != fav and lead.kind not in MOMENTS:
                errs.append(f"this is {_team(md, fav)}'s fans' show: lead with a point about {_team(md, fav)} "
                            f"(about_your_club is true), good news or bad")
            for p in v.picks:
                if any(ch.isdigit() for ch in p.reason):
                    errs.append(f"{p.id}: the reason quotes a number; explain in words")
                errs += verify.text_errors(p.reason, "en", f"{p.id} reason", on_air=False)
            return errs, [(ids[p.id], p.reason) for p in v.picks]
        try:
            return await self._call("producer", f"planned the {cut} {depth} rundown", prompt, schema, check)
        except (AgentDown, Rejected):
            self.store.record("producer", f"planned the {cut} {depth} rundown", "fallback", why="used the rules ranking")
            return [(c, "rules ranking") for c in producer.pick(md, cands, t_now, n, cut, player)]

    # ---- Telestrator (one plan per talking point, cached) ----
    async def _plan(self, md: MatchData, c: Candidate) -> dict | None:
        k = cand_key(c)
        if k not in self._plans:
            self._plans[k] = asyncio.ensure_future(self._make_plan(md, c))
        return await self._plans[k]

    async def _make_plan(self, md: MatchData, c: Candidate) -> dict | None:
        anchor, t0, t1 = telestrate.moment(md, c)
        cl = telestrate.clip(md, c.period, t0, t1)
        opts = telestrate.options(md, c, cl, anchor)
        prompt = _telestrator_context(md, c, cl, anchor, opts)

        def check(v: VisualPlan):
            plan = v.model_dump()
            for h in plan["highlights"]:
                h["text"] = md.name(cl["slots"][h["slot"]]) if h.pop("show_name") and 0 <= h["slot"] <= 21 else None
            errs = verify.layout_errors(md, plan, cl, opts)
            n = len(plan["highlights"]) + len(plan["arrows"]) + len(plan["runs"]) + len(plan["zones"]) + len(plan["lines"])
            if not 1 <= n <= 8:
                errs.append(f"{n} drawn elements; use two to five")
            return errs, plan
        try:
            plan = await self._call("telestrator", f"planned the picture for {c.kind} ({c.team})", prompt, VisualPlan,
                                    check, event_ids=c.evidence[:10])
            self.store.put("plans", cand_key(c), plan, agent="telestrator", why=plan["focus"], event_ids=plan["arrows"])
            return plan
        except (AgentDown, Rejected):
            self.store.record("telestrator", f"planned the picture for {c.kind} ({c.team})", "fallback",
                              why="routed around: rules Telestrator draws this one")
            return None

    # ---- Writer (per talking point, language and cut, cached) ----
    async def _script(self, md: MatchData, c: Candidate, lang: str, cut: str, depth: str = "analyst",
                      player: str | None = None) -> tuple[dict | None, dict | None]:
        k = (cand_key(c), lang, cut, depth, player)
        if k not in self._scripts:
            self._scripts[k] = asyncio.ensure_future(self._make_script(md, c, lang, cut, depth, player))
        return await self._scripts[k]

    async def _make_script(self, md: MatchData, c: Candidate, lang: str, cut: str, depth: str = "analyst",
                           player: str | None = None) -> tuple[dict | None, dict | None]:
        plan = await self._plan(md, c)
        table = Table(c)
        fav = {"home": md.home, "away": md.away}.get(cut)
        budget = int(SEGMENT_S * WORDS_PER_S * 0.7)  # leave room for the picture to breathe
        prompt = json.dumps(dict(
            language=LANGS[lang], cut=cut, club_channel=_team(md, fav) if fav else None, word_budget=budget,
            fan=dict(depth=depth, follows=md.name(player) if player else None,
                     rules=("casual: at most THREE numbers in all the spoken lines together, at most two in one line; "
                            "no PPDA, xG or field tilt anywhere, not even in the stat card; stat card 1 or 2 rows"
                            if depth == "casual" else "analyst: stat card of 2 to 4 rows")),
            match=_match_line(md), talking_point=dict(kind=c.kind, team=_team(md, c.team), player=_player(md, c),
                                                      side=c.side, minute=_minute(c), headline=c.headline),
            analyst=self.analysis.get(cand_key(c), {}).get("why"),
            picture=plan["focus"] if plan else "standard picture for this kind of point",
            facts=table.for_prompt()), ensure_ascii=False)

        row = create_model("Row", __base__=Row, facts=(list[Literal[tuple(table.row_aliases())]], Row.model_fields["facts"]))
        schema = create_model("Script", __base__=Script, rows=(list[row], Script.model_fields["rows"]))

        pairs = verify.pairs_for(c.kind, [f.to_dict() for f in c.facts])

        def check(v: Script):
            errs = []
            title, _, e = table.fill(v.title, lang)
            errs += [f"title: {x}" for x in e] + verify.text_errors(title, lang, "title", 48)
            sub, sub_keys, e = table.fill(v.sub, lang)
            errs += [f"subtitle: {x}" for x in e] + verify.text_errors(sub, lang, "subtitle", 80)
            errs += verify.claim_errors(sub, lang, pairs, "subtitle")
            errs += verify.text_errors(v.card_title, lang, "card title", 44)
            if not 1 <= len(v.rows) <= 4:
                errs.append("the stat card needs 1 to 4 rows")
            rows, card_keys = [], []
            for i, r in enumerate(v.rows):
                val, keys, e = table.value(r.facts, lang)
                label, lkeys, le = table.fill(r.label, lang)
                errs += [f"row {i}: {x}" for x in e + le] + verify.text_errors(label, lang, f"row {i}", 44)
                errs += verify.claim_errors(f"{label}: {val}", lang, pairs, f"row {i}")
                rows.append([label, val])
                keys += lkeys
                card_keys += [k for k in keys if k not in card_keys]
            if not 2 <= len(v.lines) <= 5:
                errs.append("write 2 to 5 lines")
            lines = []
            for i, ln in enumerate(v.lines):
                text, keys, e = table.fill(ln, lang)
                errs += [f"line {i}: {x}" for x in e] + verify.text_errors(text, lang, f"line {i}")
                errs += verify.claim_errors(text, lang, pairs, f"line {i}")
                lines.append((text, keys))
            errs += verify.depth_errors([t for t, _ in lines], rows, depth, len(c.facts))
            words = sum(len(t.split()) for t, _ in lines)
            if words > budget * 1.25:
                errs.append(f"{words} words; the budget is {budget}")
            return errs, dict(title=title, sub=sub, sub_keys=sub_keys, card_title=v.card_title, rows=rows,
                              card_keys=card_keys, lines=lines)
        try:
            words = await self._call("writer", f"wrote {c.kind} ({c.team}) in {lang}, {cut} {depth} cut", prompt, schema, check)
            self.store.put("scripts", f"{cand_key(c)}|{lang}|{cut}|{depth}|{player or '-'}", words, agent="writer")
        except (AgentDown, Rejected):
            words = None
            self.store.record("writer", f"wrote {c.kind} ({c.team}) in {lang}, {cut} {depth} cut", "fallback",
                              why="routed around: rules Writer")
        return plan, words

    # ---- assembly, gated by the Verifier ----
    async def _segment(self, md: MatchData, c: Candidate, lang: str, cut: str, target_s: float, seg_id: str,
                       depth: str = "analyst", player: str | None = None) -> dict:
        plan, words = await self._script(md, c, lang, cut, depth, player)
        if words is None and lang not in writer.T:
            raise Rejected([f"no rules Writer for {lang}"])
        if plan is not None:
            plan = _localise(plan, lang)
        made_by = dict(telestrator="agent" if plan else "rules", writer="agent" if words else "rules")
        seg = telestrate.build(md, c, lang=lang, target_s=target_s, seg_id=seg_id, plan=plan, words=words, depth=depth,
                               made_by="agents" if plan and words else "agents+rules" if plan or words else "rules")
        errs = verify.segment_errors(md, seg)
        if errs and (plan or words):
            self.store.record("verifier", f"checked {seg_id}", "fallback", why="; ".join(errs[:3]),
                              detail=dict(errors=errs[:8]))
            seg = telestrate.build(md, c, lang=lang, target_s=target_s, seg_id=seg_id, depth=depth)
            made_by = dict(telestrator="rules", writer="rules")
            errs = verify.segment_errors(md, seg)
        seg["provenance"].update(verified=not errs, crew=made_by, why=self.analysis.get(cand_key(c), {}).get("why"))
        self.store.record("verifier", f"checked {seg_id} ({c.kind})", "ok" if not errs else "failed",
                          why="; ".join(errs[:3]) or f"{len(seg['facts'])} facts recomputed, schema valid",
                          event_ids=[e for q in seg["cues"] for e in q.get("event_ids", [])])
        return seg

    async def _rundown(self, md: MatchData, cands: list[Candidate], t_now: float, slot_s: float, cut: str, lang: str,
                       depth: str = "analyst", player: str | None = None, t_from: float | None = None) -> dict:
        t0 = time.time()
        n = producer.n_segments(slot_s)
        base = dict(slot_s=slot_s, cut=cut, lang=lang, depth=depth, player=player)
        period = cands[0].period if cands else int(md.frames[-1, 0])
        mine = producer.focus(md, player, period, t_from, t_now)
        if not cands and not mine:
            return dict(base, segments=[], duration_s=0.0, made_by="crew")
        chosen = []
        if cands:
            await self._analyse(md, cands)
            chosen = await self._pick(md, cands, t_now, cut, n - (1 if mine and n >= 2 else 0), depth, player)
        if mine and (n >= 2 or not chosen):
            chosen.append((mine, f"you follow {md.name(player)}"))
        each = producer.segment_length(slot_s, len(chosen))
        # shield: a segment that misses the deadline keeps being written, for swaps and other cuts
        segs = await asyncio.gather(*(asyncio.wait_for(asyncio.shield(self._segment(md, c, lang, cut, each, f"s{i + 1}",
                                                                                     depth, player)),
                                                       max(LOCK_DEADLINE_S - (time.time() - t0), 1.0))
                                      for i, (c, _) in enumerate(chosen)), return_exceptions=True)
        out = []
        for i, ((c, reason), s) in enumerate(zip(chosen, segs)):
            if isinstance(s, Exception):
                why = (f"not ready {LOCK_DEADLINE_S:.0f} s after the whistle; the rules version goes out"
                       if isinstance(s, asyncio.TimeoutError) else f"{type(s).__name__}: {s}")
                self.store.record("producer", f"segment s{i + 1} ({c.kind})", "fallback", why=why)
                s = telestrate.build(md, c, lang=lang, target_s=each, seg_id=f"s{i + 1}", depth=depth)
                s["provenance"].update(crew=dict(telestrator="rules", writer="rules"),
                                       verified=not verify.segment_errors(md, s))
            if not s["provenance"]["verified"]:  # never on air: the next point that verifies takes the slot
                s = self._replacement(md, cands, t_now, cut, lang, each, f"s{i + 1}", [c for c, _ in chosen], depth)
                if s is None:
                    continue
                reason = "replaced a segment that failed the Verifier"
            s["provenance"]["pick_reason"] = reason
            out.append(s)
        total = producer.INTRO_S + sum(s["duration_s"] for s in out)
        score = f"{producer._goals(md, md.home, t_now)}-{producer._goals(md, md.away, t_now)}"
        r = dict(base, made_by="crew", score=score,
                 intro=dict(d=producer.INTRO_S, home=md.meta["home"]["short"], away=md.meta["away"]["short"]),
                 segments=out, duration_s=round(total, 2), built_ms=round((time.time() - t0) * 1000))
        self.store.put("rundowns", f"{cut}|{lang}|{depth}|{player or '-'}|{slot_s:g}",
                       dict(segments=[s["segment_id"] for s in out], duration_s=r["duration_s"]),
                       agent="producer", why=" / ".join(rs for _, rs in chosen))
        return r


    def _replacement(self, md, cands, t_now, cut, lang, target_s, seg_id, taken, depth="analyst") -> dict | None:
        kinds = {c.kind for c in taken}
        for _, c in producer.rank(md, cands, t_now, cut):
            if c in taken or c.kind in kinds or c.kind in MOMENTS:
                continue
            s = telestrate.build(md, c, lang=lang, target_s=target_s, seg_id=seg_id, depth=depth)
            if not verify.segment_errors(md, s):
                s["provenance"].update(verified=True, crew=dict(telestrator="rules", writer="rules"))
                self.store.record("producer", f"{seg_id}: swapped in {c.kind} ({c.team})", "fallback",
                                  why="the planned segment failed the Verifier")
                return s
        self.store.record("producer", f"{seg_id}: dropped", "fallback", why="failed the Verifier and nothing could replace it")
        return None


# ---- prompt helpers ----

def _minute(c: Candidate) -> str:
    m = round(c.minute)
    half = "first" if c.period == 1 else "second"
    base = 45 if c.period == 1 else 90
    return f"{m} ({half} half{', stoppage time' if m > base else ''})"


def _team(md: MatchData, code: str | None) -> str | None:
    if code is None:
        return None
    return md.meta["home"]["name"] if code == md.home else md.meta["away"]["name"]


def _player(md: MatchData, c: Candidate) -> str | None:
    return md.name(c.player) if c.player else None


def _match_line(md: MatchData, t_now: float | None = None) -> str:
    h, a = md.meta["home"]["name"], md.meta["away"]["name"]
    if t_now is None:
        return f"{h} v {a}"
    return f"{h} {producer._goals(md, md.home, t_now)}-{producer._goals(md, md.away, t_now)} {a}"


def _localise(plan: dict, lang: str) -> dict:
    p = json.loads(json.dumps(plan))
    zt, lt = ZONE_TEXT.get(lang, ZONE_TEXT["en"]), LINE_TEXT.get(lang, LINE_TEXT["en"])
    for z in p.get("zones", []):
        z["text"] = zt.get(z["name"])
    for ln in p.get("lines", []):
        ln["text"] = lt.get(ln["name"])
    return p


def _telestrator_context(md: MatchData, c: Candidate, cl: dict, anchor: float, opts: dict) -> str:
    clip_len = cl["t1"] - cl["t0"]
    slots = []
    for i, pid in enumerate(cl["slots"]):
        p = md.players[pid]
        slots.append(f"{i}: {p['name']} ({_team(md, p['team'])}, {p.get('role', '')})")
    evs = []
    for e in md.window(cl["period"], cl["t0"], cl["t1"] + 0.01):
        if e["type"] not in ("pass", "cross", "shot", "carry", "pressure", "tackle", "interception", "clearance"):
            continue
        s = md.slot_of(e["player"], cl["period"], e["t"]) if e.get("player") else None
        item = dict(id=e["id"], clip_t=round(e["t"] - cl["t0"], 1), type=e["type"], team=_team(md, e["team"]),
                    slot=s, outcome=e.get("outcome"), from_xy=[e.get("x"), e.get("y")])
        if e.get("end_x") is not None:
            item["to_xy"] = [e["end_x"], e["end_y"]]
        evs.append(item)
    snaps = {}
    for k in (0.0, anchor - cl["t0"], clip_len):
        fr = cl["frames"][min(max(int(round(k * 10)), 0), len(cl["frames"]) - 1)]
        snaps[f"clip_t={k:.1f}"] = dict(ball=[round(v, 1) for v in fr[:2]],
                                         players={i: [round(fr[3 + 2 * i], 1), round(fr[4 + 2 * i], 1)] for i in range(22)})
    return json.dumps(dict(
        talking_point=dict(kind=c.kind, team=_team(md, c.team), player=_player(md, c), side=c.side, headline=c.headline),
        pitch="105 x 68 m, home attacks towards x = 105",
        clip=dict(clip_len=round(clip_len, 1), key_moment_clip_t=round(anchor - cl["t0"], 1), slots=slots, events=evs,
                  positions=snaps),
        options=dict(zones=sorted(opts["zones"]), lines=sorted(opts["lines"]))), ensure_ascii=False)
