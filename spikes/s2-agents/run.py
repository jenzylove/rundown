"""Spike S2: can an Agent Framework agent on a Foundry model emit valid Overlay Script?

    python spikes/s2-agents/run.py out/matches/3 out/matches/11 ... [--repeat 2]

For every first-half talking point, the rules Telestrator cuts the clip and
computes the facts; an agent then writes the segment's title, cues and script
as structured output. Each result is assembled into an Overlay Script v1
segment and checked: JSON schema, known fact keys and event ids, slots in
range, cues inside the segment, and every number in on-screen or spoken text
backed by a cited fact. A failed check goes back to the agent once with the
errors, the way the Verifier will.

Needs AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Literal

import jsonschema
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent_framework import Agent  # noqa: E402
from agent_framework.openai import OpenAIChatCompletionClient  # noqa: E402

from rundown.engine import load  # noqa: E402
from rundown.evals.stories import run_detectors  # noqa: E402
from rundown.studio import telestrate  # noqa: E402

SCHEMA = json.loads((ROOT / "schema/overlay-script.v1.json").read_text())
FRAMING_NUMBERS = {10.0, 45.0, 90.0}  # "last 10 minutes", "45 minutes", "90 minutes"


class Cue(BaseModel):
    kind: Literal["freeze", "label", "highlight", "run", "zone", "arrow", "line"]
    t: float = Field(description="segment time the cue starts (s); for freeze, ignored (computed from at)")
    d: float = Field(description="seconds on screen; for freeze, the hold length")
    at: float | None = Field(None, description="freeze: clip time to hold")
    slot: int | None = Field(None, description="highlight/run: player slot 0-21")
    text: str | None = None
    sub: str | None = None
    from_xy: list[float] | None = Field(None, description="arrow: [x, y] metres")
    to_xy: list[float] | None = Field(None, description="arrow: [x, y] metres")
    from_t: float | None = Field(None, description="run: clip time the run starts")
    to_t: float | None = Field(None, description="run: clip time the run ends")
    style: Literal["pass", "cross", "shot", "carry"] | None = None
    rect: list[float] | None = Field(None, description="zone: [x, y, width, height] metres")
    x: float | None = Field(None, description="line: x in metres")
    event_ids: list[str] | None = None
    fact_keys: list[str] | None = Field(None, description="fact aliases, e.g. F1")


class Line(BaseModel):
    t: float
    d: float
    text: str
    fact_keys: list[str] = Field(description="fact aliases backing every number in the line")


class Row(BaseModel):
    label: str
    value: str = Field(description="fact values only, e.g. '8.3 → 5.8'")


class StatCard(BaseModel):
    text: str = Field(description="card heading: a team or player name")
    rows: list[Row] = Field(description="2 to 4 rows")
    fact_keys: list[str]


class Segment(BaseModel):
    title: str = Field(description="headline, at most 48 characters, no numbers")
    cues: list[Cue]
    statcard: StatCard = Field(description="the end card; the studio places it in the final hold")
    script: list[Line]


INSTRUCTIONS = """You are the Telestrator and Writer in a TV football studio. You turn one talking point into a
timed analysis segment: overlays drawn on a tracking clip, and the presenter's lines.

Timeline. The clip plays from clip time 0 to clip_len. A freeze cue holds the picture at clip time `at`
for `d` seconds. Segment time = clip time + the holds of all freezes before it. Segment length =
clip_len + all holds. Every cue and line must fit inside the segment.

Cue kinds: freeze (at, d); label (text, sub: a lower third); highlight (slot, optional text: ring a
player); run (slot, from_t, to_t: dashed path of that player's movement between two clip times); zone
(rect [x, y, w, h], text); arrow (from_xy, to_xy, style, event_ids: draw a pass, cross, shot or carry
from a listed event); line (x, text: a vertical line across the pitch).

The studio adds the end: after the clip it holds the last frame and shows your statcard until the
segment reaches the target length. Your cues belong to the clip and your freezes; lines may run into
the end hold.

Pitch: 105 x 68 m, absolute frame, home attacks towards x = 105.

Rules:
- Use only the facts given. Every number you write, on screen or spoken, must be a fact value (or in its
  detail) and that fact's alias must be in the cue's or line's fact_keys. Never invent numbers and never
  compute new ones (no differences, ratios or rounding). The talking point's minute may be quoted.
- Arrows only for events that have to_xy (passes, crosses, shots, carries): copy from_xy and to_xy and
  put the event id in event_ids. Highlights use listed slots. fact_keys hold fact aliases only.
- Never say or show clip times, seconds into the clip, or shirt numbers.
- Open with a label and put at least one freeze on the key moment.
- Title: at most 48 characters, no numbers, in the requested language.
- Script lines: plain spoken sentences, about 2.6 words per second, lines must not overlap.
- Write in the requested language. Aim for the target length."""


def number_set(fact: dict) -> set[float]:
    out = set()

    def add(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            out.add(round(float(v), 2))
        elif isinstance(v, dict):
            for w in v.values():
                add(w)
        elif isinstance(v, list):
            for w in v:
                add(w)
    add(fact["value"])
    add(fact.get("detail", {}))
    return out


def numbers_in(text: str) -> list[float]:
    text = re.sub(r"(\d+) (?:point|punto|coma) (\d+)", r"\1.\2", text)  # spoken decimals
    return [round(float(n.replace(",", ".")), 2) for n in re.findall(r"(?<![\w.])\d+(?:[.,]\d+)?", text)]


def context(md, cand, seg, lang: str, target_s: float) -> tuple[str, dict]:
    clip = seg["clip"]
    clip_len = clip["t1"] - clip["t0"]
    aliases = {f"F{i + 1}": f for i, f in enumerate(seg["facts"])}
    facts = [dict(alias=a, label=f["label"], value=f["value"], unit=f["unit"],
                  detail={k: v for k, v in f.get("detail", {}).items() if not isinstance(v, (list, dict))})
             for a, f in aliases.items()]
    slots = []
    for i, pid in enumerate(clip["slots"]):
        p = md.players[pid]
        slots.append(f"{i}: {p['name']} ({p['team']}, {p.get('pos', p.get('role', ''))})")
    evs = []
    for e in md.window(clip["period"], clip["t0"], clip["t1"] + 0.01):
        if e["type"] in ("pass", "cross", "shot", "carry", "pressure", "tackle", "interception", "goal"):
            s = md.slot_of(e["player"], clip["period"], e["t"]) if e.get("player") else None
            evs.append(dict(id=e["id"], clip_t=round(e["t"] - clip["t0"], 1), type=e["type"], team=e["team"],
                            slot=s, player=md.name(e["player"]) if e.get("player") else None,
                            from_xy=[e.get("x"), e.get("y")], outcome=e.get("outcome")))
            if e.get("end_x") is not None:
                evs[-1]["to_xy"] = [e["end_x"], e["end_y"]]
    snaps = {}
    for k in (0.0, clip_len / 2, clip_len):
        fr = clip["frames"][min(int(round(k * 10)), len(clip["frames"]) - 1)]
        snaps[f"clip_t={k:.1f}"] = dict(ball=[round(v, 1) for v in fr[:2]],
                                         players={i: [round(fr[3 + 2 * i], 1), round(fr[4 + 2 * i], 1)] for i in range(22)})
    msg = dict(language={"en": "English", "es": "Spanish"}[lang], target_segment_s=round(target_s, 1),
               match=f"{md.meta['home']['name']} v {md.meta['away']['name']}",
               talking_point=dict(kind=cand.kind, team=cand.team, headline=cand.headline,
                                  minute=round(cand.minute), player=md.name(cand.player) if cand.player else None,
                                  side=cand.side),
               facts=facts, clip=dict(clip_len=round(clip_len, 1), slots=slots, events=evs, positions=snaps))
    return json.dumps(msg, ensure_ascii=False), aliases


def assemble(out: Segment, seg: dict, aliases: dict, lang: str, target_s: float) -> dict:
    clip_len = seg["clip"]["t1"] - seg["clip"]["t0"]
    freezes = sorted((c.at, c.d) for c in out.cues if c.kind == "freeze" and c.at is not None)
    cues = []
    holds = sum(h for _, h in freezes)
    end_hold = max(telestrate.END_HOLD_S, target_s - clip_len - holds)
    freezes.append((clip_len, end_hold))
    end_t = clip_len + holds
    sc = out.statcard
    cues.append(dict(t=round(end_t, 2), d=round(end_hold, 2), kind="statcard", text=sc.text, rows=[[r.label, r.value] for r in sc.rows],
                     fact_keys=[aliases[k]["key"] if k in aliases else k for k in sc.fact_keys]))
    cues.append(dict(t=round(end_t, 2), d=round(end_hold, 2), kind="freeze", at=round(clip_len, 2)))
    for c in out.cues:
        q = c.model_dump(exclude_none=True)
        if "from_xy" in q:
            q["from"] = q.pop("from_xy")
        if "to_xy" in q:
            q["to"] = q.pop("to_xy")
        if c.kind == "freeze" and c.at is not None:  # freeze start is arithmetic; don't trust the model with it
            q["t"] = round(c.at + sum(h for a, h in freezes if a < c.at), 2)
        if "fact_keys" in q:
            q["fact_keys"] = [aliases[k]["key"] if k in aliases else k for k in q["fact_keys"]]
        cues.append(q)
    script = [dict(t=s.t, d=s.d, text=s.text, fact_keys=[aliases[k]["key"] if k in aliases else k for k in s.fact_keys])
              for s in out.script]
    dur = clip_len + sum(h for _, h in freezes)
    return dict(seg, title=out.title, lang=lang, cues=sorted(cues, key=lambda q: q["t"]), script=script,
                duration_s=round(max(dur, max((s["t"] + s["d"] for s in script), default=0)), 2),
                provenance=dict(seg["provenance"], made_by="agent"))


def recite(s: dict, minute: int) -> int:
    """Verifier repair: a number that is a value of a fact the item didn't cite gets the citation.
    Numbers that match no fact are left for check() to reject. Returns the citations added."""
    added = 0
    for item in s["cues"] + s["script"]:
        texts = [item.get("text", ""), item.get("sub", "")] + [v for r in item.get("rows", []) for v in r]
        keys = item.setdefault("fact_keys", [])
        cited = set(FRAMING_NUMBERS) | {float(minute)}
        for k in keys:
            cited |= next((number_set(f) for f in s["facts"] if f["key"] == k), set())
        for n in numbers_in(" ".join(t for t in texts if t)):
            if any(abs(n - a) < 0.051 for a in cited):
                continue
            f = next((f for f in s["facts"] if any(abs(n - a) < 0.051 for a in number_set(f))), None)
            if f:
                keys.append(f["key"])
                cited |= number_set(f)
                added += 1
    return added


def check(s: dict, md, aliases: dict, minute: int) -> list[str]:
    errs = [f"schema: {e.message} at {'/'.join(map(str, e.path))}"
            for e in jsonschema.Draft202012Validator(SCHEMA).iter_errors(s)]
    by_key = {f["key"]: f for f in s["facts"]}
    alias_of = {f["key"]: a for a, f in aliases.items()}
    clip = s["clip"]
    clip_len = clip["t1"] - clip["t0"]
    ev_ids = {e["id"] for e in md.window(clip["period"], clip["t0"], clip["t1"] + 0.01)}
    dur = s["duration_s"]
    kinds = [c["kind"] for c in s["cues"]]
    if "label" not in kinds or "freeze" not in kinds or "statcard" not in kinds:
        errs.append(f"structure: needs a label, a freeze and a statcard (got {sorted(set(kinds))})")
    for i, c in enumerate(s["cues"]):
        where = f"cue {i} ({c['kind']})"
        for k in c.get("fact_keys", []):
            if k not in by_key:
                errs.append(f"{where}: unknown fact {k}")
        for e in c.get("event_ids", []):
            if e not in ev_ids:
                errs.append(f"{where}: event {e} is not in the clip")
        if c["t"] + c["d"] > dur + 0.5:
            errs.append(f"{where}: ends at {c['t'] + c['d']:.1f}s, segment is {dur:.1f}s")
        if c["kind"] == "freeze" and not (0 <= c.get("at", -1) <= clip_len + 0.05):
            errs.append(f"{where}: at={c.get('at')} outside clip 0..{clip_len:.1f}")
        if c["kind"] in ("highlight", "run") and "slot" not in c:
            errs.append(f"{where}: needs a slot")
        if c["kind"] == "arrow":
            ev = next((md.by_id[e] for e in c.get("event_ids", []) if e in md.by_id), None)
            if not ev or "from" not in c or "to" not in c or ev.get("end_x") is None:
                errs.append(f"{where}: arrow needs from, to and a listed pass, cross, shot or carry")
            elif abs(c["from"][0] - ev["x"]) > 1 or abs(c["to"][0] - ev["end_x"]) > 1:
                errs.append(f"{where}: arrow coordinates don't match event {ev['id']}")
        texts = [c.get("text", ""), c.get("sub", "")] + [v for r in c.get("rows", []) for v in r]
        errs += ungrounded(" ".join(t for t in texts if t), c.get("fact_keys", []), by_key, alias_of, where,
                           extra={float(minute), float(len(c.get("event_ids", [])))})
    end = 0.0
    for i, ln in enumerate(s["script"]):
        if ln["t"] < end - 0.05:
            errs.append(f"line {i}: starts at {ln['t']} before the previous line ends ({end:.1f})")
        end = ln["t"] + ln["d"]
        errs += ungrounded(ln["text"], ln["fact_keys"], by_key, alias_of, f"line {i}", extra={float(minute)})
    return errs


def ungrounded(text, keys, by_key, alias_of, where, extra=frozenset()) -> list[str]:
    allowed = set(FRAMING_NUMBERS) | set(extra)
    for k in keys:
        if k in by_key:
            allowed |= number_set(by_key[k])
    bad = [n for n in numbers_in(text) if n not in allowed and not any(abs(n - a) < 0.051 for a in allowed)]
    if not bad:
        return []
    hint = ", ".join(alias_of.get(k, k) for k in keys) or "none"
    return [f"{where}: number(s) {bad} in \"{text[:70]}\" not backed by its cited facts ({hint})"]


async def one(agent, md, cand, lang, target_s, sem) -> dict:
    seg = telestrate.build(md, cand, lang=lang, target_s=target_s)
    prompt, aliases = context(md, cand, seg, lang, target_s)
    rec = dict(seed=md.meta["seed"], kind=cand.kind, team=cand.team, lang=lang, attempts=[])
    async with sem:
        session = agent.create_session()
        msg = prompt
        for attempt in range(2):
            t = time.time()
            try:
                r = await agent.run(msg, session=session, options={"response_format": Segment})
                out = r.value
                usage = r.usage_details or {}
                s = assemble(out, seg, aliases, lang, target_s)
                rec["recited"] = rec.get("recited", 0) + recite(s, round(cand.minute))
                errs = check(s, md, aliases, round(cand.minute))
            except Exception as e:  # malformed output counts as a failure
                errs, s, usage = [f"exception: {type(e).__name__}: {str(e)[:200]}"], None, {}
            rec["attempts"].append(dict(latency_s=round(time.time() - t, 2), errors=errs,
                                        in_tokens=usage.get("input_token_count"),
                                        out_tokens=usage.get("output_token_count")))
            if not errs:
                rec["segment"] = s
                break
            msg = "The Verifier rejected the segment. Fix these and return the whole segment again:\n- " + "\n- ".join(errs)
    rec["ok"] = not rec["attempts"][-1]["errors"]
    rec["first_try"] = not rec["attempts"][0]["errors"]
    print(f"{rec['seed']} {rec['kind']:<15} {lang} first={'ok' if rec['first_try'] else 'FAIL'} "
          f"final={'ok' if rec['ok'] else 'FAIL'} " + " | ".join(e for a in rec["attempts"] for e in a["errors"][:2]),
          flush=True)
    return rec


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("matches", nargs="+")
    ap.add_argument("--langs", default="en,es")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--out", default=str(ROOT / "spikes/s2-agents/results.json"))
    a = ap.parse_args()
    client = OpenAIChatCompletionClient(model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
                                        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                                        api_key=os.environ["AZURE_OPENAI_API_KEY"])
    agent = Agent(client=client, name="Telestrator", instructions=INSTRUCTIONS)
    sem = asyncio.Semaphore(4)
    jobs = []
    for m in a.matches:
        md = load(m)
        cands = [c for c in run_detectors(md) if c.period == 1]
        for c in cands:
            for lang in a.langs.split(","):
                for _ in range(a.repeat):
                    jobs.append(one(agent, md, c, lang, 28.0, sem))
    t0 = time.time()
    recs = await asyncio.gather(*jobs)
    lat = [x["latency_s"] for r in recs for x in r["attempts"]]
    n = len(recs)
    summary = dict(model=os.environ["AZURE_OPENAI_DEPLOYMENT"], segments=n,
                   first_try_valid=sum(r["first_try"] for r in recs), valid_after_retry=sum(r["ok"] for r in recs),
                   calls=len(lat), citations_repaired=sum(r.get("recited", 0) for r in recs), latency_p50=round(statistics.median(lat), 2),
                   latency_p95=round(sorted(lat)[int(0.95 * (len(lat) - 1))], 2),
                   wall_s=round(time.time() - t0, 1))
    print(json.dumps(summary))
    for r in recs:  # the tracking frames are the rules clip, unchanged; keep the file small
        if "segment" in r:
            r["segment"]["clip"] = {k: v for k, v in r["segment"]["clip"].items() if k != "frames"}
    Path(a.out).write_text(json.dumps(dict(summary=summary, runs=recs), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
