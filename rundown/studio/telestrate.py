"""Rules Telestrator: a talking point -> an Overlay Script v1 segment.

Picks the moment in the match that best shows the point, cuts a tracking clip
around it, and lays out freezes, zones, highlights, arrows and a stat card.
The agent Telestrator produces the same format; this is its fallback.
"""

from __future__ import annotations

import numpy as np

from ..engine import stats as S
from ..engine.data import PITCH_LEN, PITCH_WID, MatchData
from ..engine.detect import Candidate
from ..agents.words import fmt
from . import writer

LABEL_S = 3.5
MOVE_WORDS = {"en": ("passes", "seconds"), "es": ("pases", "segundos"), "fr": ("passes", "secondes"),
              "pt": ("passes", "segundos")}
END_HOLD_S = 5.0


class Timeline:
    """Maps clip time to segment time as freezes are inserted."""

    def __init__(self, clip_len: float):
        self.clip_len = clip_len
        self.freezes: list[tuple[float, float]] = []  # (clip time, hold seconds)

    def freeze(self, at: float, hold: float):
        self.freezes.append((at, hold))
        self.freezes.sort()

    def seg(self, clip_t: float) -> float:
        return clip_t + sum(h for at, h in self.freezes if at < clip_t)

    def seg_at_freeze(self, at: float) -> float:
        """Segment time the freeze at clip time `at` begins."""
        return at + sum(h for a, h in self.freezes if a < at)

    @property
    def duration(self) -> float:
        return self.clip_len + sum(h for _, h in self.freezes)


def _seg_at(freezes, at):
    return at + sum(h for a, h in freezes if a < at)


def _remap(seg_t: float, old: list, tl: "Timeline") -> float:
    """Segment time under the old freezes -> segment time under the new ones."""
    clip_t, frozen_at, into = seg_t, None, 0.0
    for at, h in old:
        start = _seg_at(old, at)
        if seg_t >= start + h:
            clip_t -= h
        elif seg_t >= start:
            frozen_at, into = at, seg_t - start
            break
    if frozen_at is not None:
        return tl.seg_at_freeze(frozen_at) + into
    return tl.seg(clip_t)


def _grow_inside(q: dict, old: list, new: list) -> float:
    """Extra hold time added inside a cue's span (so it stays on screen through its freeze)."""
    g = 0.0
    for (at, h1), (_, h2) in zip(old, new):
        start = _seg_at(old, at)
        if q["t"] <= start + 0.05 and start < q["t"] + q["d"]:
            g += h2 - h1
        elif start <= q["t"] < start + h1:
            g += h2 - h1
    return g


def clip(md: MatchData, period: int, t0: float, t1: float) -> dict:
    a, b = md.frame_range(period, t0, t1)
    fr = md.frames[a:b]
    frames = np.concatenate([fr[:, 2:5], fr[:, 5:]], axis=1)
    return dict(period=period, t0=round(float(fr[0, 1]), 1), t1=round(float(fr[-1, 1]), 1), hz=10,
                slots=md.slot_players(period, float(fr[0, 1])),
                frames=np.round(frames.astype(float), 2).tolist())


def _team_abs(md: MatchData, team: str, x: float, y: float) -> tuple[float, float]:
    tx, ty = md.to_team(team, x, y)  # the transform is its own inverse
    return float(tx), float(ty)


def _channel_rect(md: MatchData, team: str, side: str, ball_x_team: float) -> list[float]:
    lo, hi = S.CHANNELS[side]
    x0, x1 = max(ball_x_team - 15, 0), min(ball_x_team + 20, PITCH_LEN)
    ax0, ay0 = _team_abs(md, team, x0, lo - 3)
    ax1, ay1 = _team_abs(md, team, x1, hi + 3)
    x, y = min(ax0, ax1), max(min(ay0, ay1), 0)
    return [round(x, 1), round(y, 1), round(abs(ax1 - ax0), 1), round(min(abs(ay1 - ay0), PITCH_WID - y), 1)]


def _slot_cue(md, period, at_t, pid, **kw):
    s = md.slot_of(pid, period, at_t)
    return None if s is None else dict(kind="highlight", slot=s, **kw)


def _events_between(md, period, t0, t1, types, team=None):
    return md.window(period, t0, t1, types, team)


def _arrow(ev, tl: Timeline, clip_t0: float, d: float = 2.5) -> dict:
    style = {"pass": "pass", "cross": "cross", "shot": "shot", "carry": "carry"}[ev["type"]]
    return dict(t=round(tl.seg(ev["t"] - clip_t0), 2), d=d, kind="arrow", style=style,
                **{"from": [ev["x"], ev["y"]], "to": [ev["end_x"], ev["end_y"]]}, event_ids=[ev["id"]])


def moment(md: MatchData, c: Candidate) -> tuple[float, float, float]:
    """The moment that best shows the talking point: (anchor, clip start, clip end), match clock."""
    period = c.period
    rec_t0 = max(c.t - 600.0, 0.0)
    if c.kind == "flank_overload":
        over = next(f for f in reversed(c.facts) if f.stat == "overloads")
        eps = over.detail["episodes"] or [dict(t=c.t - 30)]
        ep = max(eps, key=lambda e: e.get("attackers", 0) - e.get("defenders", 0))
        anchor = ep["t"]
        return anchor, anchor - 6, anchor + 6
    if c.kind == "press_up":
        acts = [md.by_id[i] for i in c.facts[1].evidence if i in md.by_id]
        acts = [e for e in acts if md.to_team(c.team, e["x"], e["y"])[0] > 52.5] or acts
        anchor = acts[-1]["t"] if acts else c.t - 30
        return anchor, anchor - 7, anchor + 3
    if c.kind == "fatigue":
        opp = md.opponent(c.team)
        near = []
        for e in md.window(period, rec_t0, c.t, ("pass", "carry", "cross"), opp):
            s = md.slot_of(c.player, period, e["t"])
            if s is None:
                continue
            p = md.positions(md.frame_index(period, e["t"]))[s]
            if np.hypot(p[0] - e["x"], p[1] - e["y"]) < 15:
                near.append(e)
        anchor = near[-1]["t"] if near else c.t - 30
        return anchor, anchor - 6, anchor + 4
    if c.kind == "deep_block":
        opp = md.opponent(c.team)
        ft = [e for e in md.window(period, rec_t0, c.t, ("pass",), opp) if md.to_team(opp, e["x"], e["y"])[0] > 70]
        anchor = ft[-1]["t"] if ft else c.t - 30
        return anchor, anchor - 5, anchor + 5
    if c.kind == "tempo_up":
        passes = md.window(period, rec_t0, c.t, ("pass",), c.team)
        best, run = [], []
        for e in passes:
            if run and (e["t"] - run[-1]["t"] > 6 or run[-1]["outcome"] != "complete"):
                run = []
            run.append(e)
            if len(run) > len(best) and run[-1]["t"] - run[0]["t"] < 14:
                best = list(run)
        anchor = best[0]["t"] if best else c.t - 30
        return anchor, anchor - 1.5, (best[-1]["t"] + 2.5) if best else anchor + 10
    if c.kind == "player_focus":
        # his best moment of the window: a shot if he had one, else his hardest completed pass
        shots = [f for f in c.facts if f.stat == "shot_xg"]
        eid = (max(shots, key=lambda f: f.value).args["event_id"] if shots
               else c.facts[0].detail.get("hardest_id"))
        ev = md.by_id.get(eid) if eid else None
        if ev is None:
            mine = [md.by_id[i] for i in c.evidence if i in md.by_id]
            ev = mine[-1] if mine else None
        anchor = ev["t"] if ev else c.t - 30
        return anchor, anchor - 6, anchor + 3
    # goal / big chance
    shot = md.by_id[c.evidence[0]]
    anchor = shot["t"]
    flight = float(np.hypot(shot["end_x"] - shot["x"], shot["end_y"] - shot["y"])) / max(shot["speed_kmh"] / 3.6, 1.0)
    return anchor, anchor - 10, anchor + flight - 0.1  # end as the ball arrives, before the restart


def options(md: MatchData, c: Candidate, cl: dict, anchor: float) -> dict:
    """Named zones and lines a Telestrator may place, with the geometry worked out here."""
    period = cl["period"]
    bx_team = float(md.to_team(c.team, *md.ball(md.frame_index(period, anchor))[:2])[0])
    zones = {f"{side} channel": _channel_rect(md, c.team, side, bx_team) for side in S.CHANNELS}
    lines = {"halfway": 52.5}
    if c.kind == "deep_block":
        lx, _ = _team_abs(md, c.team, c.facts[1].value, 34.0)
        lines["back line"] = round(lx, 1)
    return dict(zones=zones, lines=lines)


def _rules_layout(md: MatchData, c: Candidate, cl: dict, tl: Timeline, anchor: float, lang: str = "en") -> list[dict]:
    period = c.period
    clip_t0 = cl["t0"]
    t0, t1 = cl["t0"], cl["t1"]
    a = anchor - clip_t0
    cues: list[dict] = []
    if c.kind == "flank_overload":
        over = next(f for f in reversed(c.facts) if f.stat == "overloads")
        tl.freeze(a, 3.2)
        ft = tl.seg_at_freeze(a)
        bx_team = md.to_team(c.team, *md.ball(md.frame_index(period, anchor))[:2])[0]
        cues.append(dict(t=round(ft, 2), d=5.5, kind="zone", rect=_channel_rect(md, c.team, c.side, float(bx_team)),
                         text=over.detail.get("biggest") or "overload", fact_keys=[over.key]))
        own = slice(0, 11) if md.side(c.team) == 0 else slice(11, 22)
        pos = md.positions(md.frame_index(period, anchor))
        ball = md.ball(md.frame_index(period, anchor))[:2]
        mates = sorted(range(own.start, own.stop), key=lambda s: float(np.linalg.norm(pos[s] - ball)))[:3]
        for k, s in enumerate(mates):
            cues.append(dict(t=round(ft + 0.3 * k, 2), d=2.9 - 0.3 * k, kind="highlight", slot=s,
                             text=md.name(cl["slots"][s]) if k == 0 else None))
        nxt = _events_between(md, period, anchor, cl["t1"], ("pass", "cross", "shot"), c.team)[:2]
        cues += [_arrow(e, tl, clip_t0) for e in nxt]
    elif c.kind == "press_up":
        tl.freeze(max(a - 1.5, 0.5), 2.8)
        ft = tl.seg_at_freeze(max(a - 1.5, 0.5))
        pressers = {e["player"] for e in _events_between(md, period, t0, anchor + 0.5, ("pressure",), c.team)}
        for k, pid in enumerate(list(pressers)[:3]):
            cue = _slot_cue(md, period, anchor, pid, t=round(ft + 0.25 * k, 2), d=2.6,
                            text=md.name(pid) if k == 0 else None)
            if cue:
                cues.append(cue)
        x_line = 52.5
        cues.append(dict(t=round(ft, 2), d=2.8, kind="line", x=x_line, text="halfway"))
        turn = md.by_id[c.facts[1].evidence[-1]] if c.facts[1].evidence else None
        if turn and t0 <= turn["t"] <= t1:  # the latest regain can be outside this clip
            cues.append(dict(t=round(tl.seg(turn["t"] - clip_t0), 2), d=2.5, kind="highlight",
                             slot=md.slot_of(turn["player"], period, turn["t"]) or 0, text="Won back",
                             event_ids=[turn["id"]]))
    elif c.kind == "fatigue":
        tl.freeze(max(a, 0.5), 3.0)
        ft = tl.seg_at_freeze(max(a, 0.5))
        s = md.slot_of(c.player, period, anchor)
        cues.append(dict(t=round(ft, 2), d=3.0, kind="highlight", slot=s, text=md.name(c.player)))
        cues.append(dict(t=round(ft + 3.0, 2), d=4.0, kind="run", slot=s, from_t=round(a, 2),
                         to_t=round(min(a + 4, tl.clip_len), 2)))
    elif c.kind == "deep_block":
        tl.freeze(max(a, 0.5), 3.2)
        ft = tl.seg_at_freeze(max(a, 0.5))
        line_fact = c.facts[1]
        lx, _ = _team_abs(md, c.team, line_fact.value, 34.0)
        cues.append(dict(t=round(ft, 2), d=3.2, kind="line", x=round(lx, 1),
                         text=f"line {line_fact.value} m", fact_keys=[line_fact.key]))
    elif c.kind == "tempo_up":
        chain = [e for e in md.window(period, t0, t1, ("pass",), c.team)]
        cues += [_arrow(e, tl, clip_t0, d=2.0) for e in chain]
        if chain:
            cues.append(dict(t=round(tl.seg(chain[0]["t"] - clip_t0), 2), d=1.5 + chain[-1]["t"] - chain[0]["t"],
                             kind="label", text=f"{len(chain)} {MOVE_WORDS[lang][0]}",
                             sub=f"{fmt(round(chain[-1]['t'] - chain[0]['t'], 1), lang)} {MOVE_WORDS[lang][1]}",
                             event_ids=[e["id"] for e in chain]))
    elif c.kind == "player_focus":
        tl.freeze(max(a, 0.5), 3.2)
        ft = tl.seg_at_freeze(max(a, 0.5))
        s = md.slot_of(c.player, period, anchor)
        if s is not None:
            cues.append(dict(t=round(ft, 2), d=3.2, kind="highlight", slot=s, text=md.name(c.player)))
            cues.append(dict(t=round(ft + 0.4, 2), d=2.8, kind="run", slot=s, from_t=0.0, to_t=round(max(a, 0.5), 2)))
        own = [e for e in md.window(period, t0, t1, ("pass", "cross", "shot", "carry"))
               if e.get("player") == c.player and e.get("end_x") is not None]
        cues += [_arrow(e, tl, clip_t0) for e in own[-2:]]
    else:
        build_up = [e for e in md.window(period, t0, anchor, ("pass", "cross", "carry"), c.team)][-3:]
        tl.freeze(max(a - 0.1, 0.5), 2.0)
        cues += [_arrow(e, tl, clip_t0) for e in build_up]
        shot = md.by_id[c.evidence[0]]
        cues.append(_arrow(shot, tl, clip_t0, d=2.5))
        sh = _slot_cue(md, period, anchor, shot["player"], t=round(tl.seg_at_freeze(max(a - 0.1, 0.5)), 2), d=2.0,
                       text=md.name(shot["player"]))
        if sh:
            cues.append(sh)
    return cues


def plan_layout(md: MatchData, plan: dict, cl: dict, tl: Timeline, opts: dict) -> list[dict]:
    """An agent's visual plan -> cues. The plan names moments (clip times), slots, events and
    option names; every time and coordinate is worked out here. Call verify.layout_errors first."""
    clip_t0 = cl["t0"]
    freezes = [(min(max(f["at"], 0.3), tl.clip_len - 0.1), min(max(f["hold"], 1.5), 4.0)) for f in plan.get("freezes", [])[:3]]
    for at, hold in freezes:
        tl.freeze(at, hold)
    hold_of = dict(freezes)

    def when(item, d):
        """Cue time and length: pinned to one of the plan's freezes, else at a clip time."""
        i = item.get("freeze")
        if i is not None and 0 <= i < len(freezes):
            at = freezes[i][0]
            return tl.seg_at_freeze(at), hold_of[at]
        return tl.seg(min(max(item.get("at") or 0.0, 0.0), tl.clip_len)), d

    cues: list[dict] = []
    for k, h in enumerate(plan.get("highlights", [])[:4]):
        t, d = when(h, 2.5)
        cues.append(dict(t=round(t + 0.25 * k, 2), d=round(max(d - 0.25 * k, 1.0), 2), kind="highlight",
                         slot=h["slot"], text=h.get("text") or None))
    for eid in plan.get("arrows", [])[:4]:
        cues.append(_arrow(md.by_id[eid], tl, clip_t0))
    for r in plan.get("runs", [])[:2]:
        t, d = when(r, 3.0)
        cues.append(dict(t=round(t, 2), d=round(max(d, 3.0), 2), kind="run", slot=r["slot"],
                         from_t=round(r["from_t"], 2), to_t=round(min(r["to_t"], tl.clip_len), 2)))
    for z in plan.get("zones", [])[:1]:
        t, d = when(z, 3.0)
        cues.append(dict(t=round(t, 2), d=round(d, 2), kind="zone", rect=opts["zones"][z["name"]], text=z.get("text") or None))
    for ln in plan.get("lines", [])[:1]:
        t, d = when(ln, 3.0)
        cues.append(dict(t=round(t, 2), d=round(d, 2), kind="line", x=opts["lines"][ln["name"]], text=ln.get("text") or None))
    return cues


def build(md: MatchData, c: Candidate, lang: str = "en", target_s: float | None = None,
          seg_id: str = "seg", plan: dict | None = None, words: dict | None = None,
          made_by: str = "rules", depth: str = "analyst") -> dict:
    """Talking point -> segment. `plan` (an agent's visual plan) replaces the rules layout and
    `words` (an agent Writer's text, numbers already filled in) the rules writer; timing,
    geometry and the end card are always worked out here."""
    period = c.period
    facts = list(c.facts)
    anchor, t0, t1 = moment(md, c)
    cl = clip(md, period, t0, t1)
    clip_t0 = cl["t0"]
    tl = Timeline(cl["t1"] - clip_t0)
    if plan is None:
        cues = _rules_layout(md, c, cl, tl, anchor, lang)
    else:
        cues = plan_layout(md, plan, cl, tl, options(md, c, cl, anchor))

    # end hold with the stat card; extra time to fill the slot is shared across
    # all holds (each at most 2.5x its length), the rest goes to the end hold
    tl.freeze(tl.clip_len, END_HOLD_S)
    if target_s and target_s > tl.duration:
        extra = target_s - tl.duration
        held = sum(h for _, h in tl.freezes)
        grown = [(at, min(h * 2.5, h + extra * h / held)) for at, h in tl.freezes]
        rest = target_s - (tl.clip_len + sum(h for _, h in grown))
        grown[-1] = (grown[-1][0], grown[-1][1] + max(0.0, rest))
        # cues pinned to a freeze move with it
        old = list(tl.freezes)
        tl.freezes = grown
        for q in cues:
            if not q:
                continue
            q["t"] = round(_remap(q["t"], old, tl), 2)
            if q.get("kind") in ("highlight", "zone", "line", "run"):
                q["d"] = round(q["d"] + _grow_inside(q, old, grown), 2)
    end_t = tl.seg_at_freeze(tl.clip_len)
    if words is None:
        rows, card_facts = writer.stat_rows(md, c, lang, depth)
        card_keys = [f.key for f in card_facts]
        words = dict(title=writer.title(md, c, lang), sub=writer.subtitle(md, c, lang), sub_keys=[f.key for f in c.facts[:2]],
                     card_title=writer.card_title(md, c, lang), rows=rows, card_keys=card_keys,
                     lines=writer.script(md, c, lang, depth))
    else:
        card_facts = [f for f in c.facts if f.key in set(words["card_keys"])]
    cues.append(dict(t=round(end_t, 2), d=round(tl.freezes[-1][1], 2), kind="statcard",
                     text=words["card_title"], rows=words["rows"], fact_keys=list(words["card_keys"])))
    title = words["title"]
    cues.insert(0, dict(t=0.2, d=LABEL_S, kind="label", text=title.upper(), sub=words["sub"],
                        fact_keys=list(words["sub_keys"])))
    cues += [dict(t=round(tl.seg_at_freeze(at), 2), d=round(h, 2), kind="freeze", at=round(at, 2))
             for at, h in tl.freezes]
    cues = [{k: v for k, v in q.items() if v is not None} for q in cues if q]
    cues.sort(key=lambda q: q["t"])

    lines = words["lines"]
    script, t = [], 0.4
    for text, keys in lines:
        d = max(2.0, len(text.split()) / 2.6)
        script.append(dict(t=round(t, 2), d=round(d, 2), text=text, fact_keys=keys))
        t += d + 0.3

    return dict(version="1", segment_id=seg_id, title=title[:48], kind=c.kind, team=c.team, lang=lang, depth=depth,
                duration_s=round(max(tl.duration, t), 2), clip=cl, cues=cues, script=script,
                facts=list({f.key: f.to_dict() for f in facts + card_facts}.values()),
                provenance=dict(made_by=made_by, candidate=c.to_dict(), verified=False))
