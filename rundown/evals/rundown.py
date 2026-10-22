"""The half-time eval: what goes on air, scored against the planted stories.

    python -m rundown.evals.rundown --seeds 101-112 --out docs/evals/rundown.json [--rules-only]

For each seed it plays the first half as a live run does (talking points spotted every match
minute), then makes the neutral English rundown twice from the same talking points: once with
the agent crew, once with the rules-only baseline (the "biggest stat change" picker). Scores:

  spotted     planted first-half stories the detectors found by half-time, and how many minutes
              after the story started (shared by both systems: detection is code, not agents)
  recall      planted first-half stories that made it on air
  precision   aired talking points that are a planted story or a real moment (a goal or a big
              chance); anything else is a pattern the answer key doesn't contain
  grounding   aired segments whose every fact the Verifier recomputes to the same value, with every
              cited event real (both systems are checked the same way); plus, for the agents, how
              many answers the Verifier rejected before air
  slot fit    |rundown length - 90 s|
  lock        seconds from the whistle to the locked rundown (agents)

The answer key is read here and nowhere else: the agents never see it. Needs AZURE_OPENAI_*
unless --rules-only.
"""

from __future__ import annotations

import argparse
import json
import statistics
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from ..agents import verify
from ..agents.crew import cand_key
from ..engine import detect
from ..engine.data import MatchData
from ..sim.match import Sim
from ..studio import producer
from .stories import matches

MOMENTS = ("goal", "big_chance")


def first_half(seed: int) -> tuple[MatchData, list[dict]]:
    """The first half as the studio sees it live, and its planted stories."""
    sim = Sim(seed)
    frames = []
    for f in sim.run():
        if f[0] == 2:
            break
        frames.append(f)
    key = [s.to_dict() for s in sim.stories if s.end_minute <= 45]
    return MatchData(meta=sim.match.meta(), events=list(sim.match.events), frames=np.stack(frames)), key


def spot(md: MatchData) -> tuple[list, float]:
    """Talking points by half-time, scanned every match minute as a live run does."""
    t_end = float(md.frames[md.frame_index(1, 1e9), 1])
    cands, t = {}, 60.0
    while t <= t_end:
        for c in detect.scan(md, 1, t) + detect.moments(md, 1, t - 60, t):
            cands.setdefault(cand_key(c), c)
        t += 60
    return list(cands.values()), t_end


def _as_cand(d: dict):
    return SimpleNamespace(kind=d["kind"], team=d["team"], side=d.get("side"), player=d.get("player"),
                           minute=d["minute"])


def score_rundown(md: MatchData, key: list[dict], r: dict) -> dict:
    segs = r["segments"]
    aired = [s["provenance"]["candidate"] for s in segs]
    story_of = [next((st["id"] for st in key if matches(st, _as_cand(c))), None) for c in aired]
    errs = [verify.segment_errors(md, s) for s in segs]
    return dict(
        segments=len(segs),
        aired=[dict(kind=c["kind"], team=c["team"], minute=c["minute"], headline=c["headline"], story=sid,
                    moment=c["kind"] in MOMENTS, grounded=not e, errors=e[:3], title=s["title"],
                    script=[ln["text"] for ln in s["script"]],
                    facts=[f"{f['label']}: {f['value']} {f['unit']}".strip() for f in s["facts"]])
               for c, sid, e, s in zip(aired, story_of, errs, segs)],
        stories_aired=sorted({sid for sid in story_of if sid}),
        relevant=sum(1 for c, sid in zip(aired, story_of) if sid or c["kind"] in MOMENTS),
        grounded=sum(1 for e in errs if not e),
        facts_checked=sum(len(s["facts"]) for s in segs),
        duration_s=r["duration_s"],
        slot_error_s=round(abs(r["duration_s"] - r["slot_s"]), 2),
    )


def evaluate_seed(seed: int, models=None) -> dict:
    md, key = first_half(seed)
    cands, t_end = spot(md)
    spotted = {}
    for st in key:
        hit = [c for c in cands if matches(st, c)]
        if hit:
            spotted[st["id"]] = round(min(c.minute for c in hit) - st["start_minute"], 1)
    row = dict(seed=seed, stories=[dict(id=s["id"], description=s["description"], start=s["start_minute"]) for s in key],
               spotted=spotted, candidates=len(cands))
    row["rules"] = score_rundown(md, key, producer.rundown(md, cands, t_end, 90.0, "neutral", "en"))
    if models is not None:
        from ..agents.halftime import run_half

        r, trail, lock_s = run_half(md, models, "neutral", "en")
        row["agents"] = dict(score_rundown(md, key, r), lock_s=round(lock_s, 2),
                             rejected=sum(1 for t in trail if t["status"] in ("retry", "failed")),
                             routed_around=sum(1 for t in trail if t["status"] == "fallback"))
    return row


def summarise(rows: list[dict]) -> dict:
    n_stories = sum(len(r["stories"]) for r in rows)
    delays = [d for r in rows for d in r["spotted"].values()]
    out = dict(seeds=[r["seed"] for r in rows], stories=n_stories,
               spotted=sum(len(r["spotted"]) for r in rows),
               spotted_delay_median_min=round(statistics.median(delays), 1) if delays else None)
    for system in ("agents", "rules"):
        rs = [r[system] for r in rows if system in r]
        if not rs:
            continue
        segs = sum(x["segments"] for x in rs)
        s = dict(rundowns=len(rs), segments=segs,
                 stories_aired=sum(len(x["stories_aired"]) for x in rs),
                 relevant=sum(x["relevant"] for x in rs),
                 grounded=sum(x["grounded"] for x in rs),
                 facts_checked=sum(x["facts_checked"] for x in rs),
                 slot_error_median_s=round(statistics.median(x["slot_error_s"] for x in rs), 2),
                 slot_error_max_s=max(x["slot_error_s"] for x in rs))
        if system == "agents":
            locks = [x["lock_s"] for x in rs]
            s.update(lock_median_s=round(statistics.median(locks), 2), lock_max_s=max(locks),
                     rejected=sum(x["rejected"] for x in rs), routed_around=sum(x["routed_around"] for x in rs))
        out[system] = s
    return out


def misses(rows: list[dict]) -> list[str]:
    """Plain-language list of what went wrong, for publishing."""
    out = []
    for r in rows:
        for st in r["stories"]:
            if st["id"] not in r["spotted"]:
                out.append(f"seed {r['seed']}: not spotted by half-time: {st['description']}")
        for system in ("agents", "rules"):
            x = r.get(system)
            if not x:
                continue
            for st in r["stories"]:
                if st["id"] in r["spotted"] and st["id"] not in x["stories_aired"]:
                    out.append(f"seed {r['seed']} {system}: spotted but not aired: {st['description']}")
            for a in x["aired"]:
                if not a["story"] and not a["moment"]:
                    out.append(f"seed {r['seed']} {system}: aired a point not in the answer key: {a['headline']} ({a['minute']}')")
                if not a["grounded"]:
                    out.append(f"seed {r['seed']} {system}: not grounded: {a['title']}: {'; '.join(a['errors'])}")
    return out


def _seeds(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def _pct(a: int, b: int) -> str:
    return f"{a}/{b} ({a / b:.0%})" if b else "-"


def table(s: dict) -> str:
    """The results as a markdown table (README and docs/EVALS.md)."""
    lines = [f"{len(s['seeds'])} fresh seeds ({s['seeds'][0]} to {s['seeds'][-1]}), neutral English rundown, 90 s slot. "
             f"Planted first-half stories spotted by half-time: **{_pct(s['spotted'], s['stories'])}**, "
             f"a median {s['spotted_delay_median_min']} min after they start.", "",
             "| | Agent crew | Rules-only baseline |", "|---|---|---|"]
    a, b = s.get("agents"), s["rules"]

    def both(f):
        return f"| {f[0]} | {f[1](a) if a else '-'} | {f[1](b)} |"
    rows = [
        ("Story recall (planted stories on air)", lambda x: _pct(x["stories_aired"], s["stories"])),
        ("Precision (aired points that are a planted story or a real moment)", lambda x: _pct(x["relevant"], x["segments"])),
        ("Grounding (segments whose every fact the Verifier recomputes)", lambda x: _pct(x["grounded"], x["segments"])),
        ("Slot fit (median, worst)", lambda x: f"{x['slot_error_median_s']} s, {x['slot_error_max_s']} s"),
    ]
    lines += [both(r) for r in rows]
    if a:
        lines.append(f"| Locked after the whistle (median, worst) | {a['lock_median_s']} s, {a['lock_max_s']} s | at once |")
        lines.append(f"| Answers the Verifier rejected before air | {a['rejected']} ({a['routed_around']} routed to the rules crew) | - |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="101-112")
    ap.add_argument("--rules-only", action="store_true")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    seeds = _seeds(a.seeds)
    if a.rules_only:
        with ProcessPoolExecutor() as ex:
            rows = list(ex.map(evaluate_seed, seeds))
    else:
        from ..agents.llm import Models

        models = Models()
        with ThreadPoolExecutor(4) as ex:
            rows = list(ex.map(lambda sd: evaluate_seed(sd, models), seeds))
    s = summarise(rows)
    print(table(s))
    print("\nMisses:")
    for m in misses(rows):
        print(" -", m)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(dict(summary=s, table=table(s), misses=misses(rows), runs=rows), indent=1,
                                    ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
