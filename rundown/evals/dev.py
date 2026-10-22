"""Practice seeds for tuning the Producer, kept apart from the held-out eval seeds (101-112).

    python -m rundown.evals.dev cache --seeds 201-240     # play the first halves once, save them
    python -m rundown.evals.dev features                  # what separates planted stories from noise

Every number here is something the studio can know live at the whistle: how strong a talking point
was, and for how many of the minute-by-minute scans it kept showing up. The answer key is only used
to label the practice data.
"""

from __future__ import annotations

import argparse
import pickle
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .rundown import _seeds, first_half, spot
from .stories import matches

CACHE = Path("out/dev")
MOMENTS = ("goal", "big_chance")


def _cache_one(seed: int) -> int:
    """Play the first half once; keep the match data, the answer key and the frames' end."""
    md, key = first_half(seed)
    t_end = float(md.frames[md.frame_index(1, 1e9), 1])
    (CACHE / f"{seed}.pkl").write_bytes(pickle.dumps(dict(md=md, key=key, t_end=t_end)))
    return seed


def load(seed: int) -> dict:
    return pickle.loads((CACHE / f"{seed}.pkl").read_bytes())


def cached_seeds() -> list[int]:
    return sorted(int(p.stem) for p in CACHE.glob("*.pkl"))


def label(d: dict, c) -> str | None:
    return next((s["id"] for s in d["key"] if matches(s, c)), None)


def features(d: dict, c) -> dict:
    return dict(kind=c.kind, first_min=round(c.minute, 1), strength=round(c.strength, 3),
                max_strength=round(c.peak, 3), scans=c.scans,
                minutes_to_whistle=round((d["t_end"] - c.last_t) / 60, 1))


def persistence_pick(md, cands: list, t_end: float, n: int = 3) -> list:
    """A reference rule, not the baseline: the rules picker, but preferring points still showing near
    the whistle and seen in many scans."""
    def score(c):
        live = (t_end - c.last_t) <= 300
        return (c.kind == "goal", live, c.scans * c.peak)
    chosen, kinds, moments = [], set(), 0
    for c in sorted(cands, key=score, reverse=True):
        m = c.kind in MOMENTS
        if c.kind in kinds or (m and moments):
            continue
        chosen.append(c)
        kinds.add(c.kind)
        moments += m
        if len(chosen) == n:
            break
    return chosen


def agent_pick(d: dict, models) -> tuple[list, list]:
    """The Analyst scores talking points as they appear, minute by minute, and the Producer picks at the
    whistle: the agents' editorial choice, without drawing or writing the segments."""
    from ..agents.crew import Crew
    from ..agents.state import Store
    from ..engine import detect

    md, t_end = d["md"], d["t_end"]
    crew, cands, pending, t = Crew(models, Store()), {}, [], 60.0
    while t <= t_end:
        new = detect.track(cands, detect.scan(md, 1, t) + detect.moments(md, 1, t - 60, t), t)
        if new:
            pending.append(crew.analyse(md, new))
        t += 60
    for f in pending:
        f.result(600)
    picks = crew.submit(crew._pick(md, list(cands.values()), t_end, "neutral", 3)).result(600)
    return [c for c, _ in picks], list(cands.values())


def score_picks(d: dict, picks: list) -> tuple[int, int, int]:
    """-> (planted stories aired, aired points that are planted or a moment, aired points)"""
    stories = {label(d, c) for c in picks} - {None}
    relevant = sum(1 for c in picks if label(d, c) or c.kind in MOMENTS)
    return len(stories), relevant, len(picks)


def compare(seeds: list[int], with_agents: bool) -> dict:
    from ..studio import producer

    models = None
    if with_agents:
        from ..agents.llm import Models
        models = Models()
    totals: dict[str, list[int]] = {}
    stories = spotted = 0

    def one(seed):
        d = load(seed)
        out = {}
        if models is not None:
            picks, cands = agent_pick(d, models)
            out["agents"] = score_picks(d, picks)
        else:
            cands, _ = spot(d["md"])
        out["rules"] = score_picks(d, producer.pick(d["md"], cands, d["t_end"], 3))
        out["persistence rule"] = score_picks(d, persistence_pick(d["md"], cands, d["t_end"]))
        found = {label(d, c) for c in cands} - {None}
        return seed, out, len(d["key"]), len(found)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4 if with_agents else 1) as ex:
        for seed, out, n_key, n_found in ex.map(one, seeds):
            stories += n_key
            spotted += n_found
            for k, v in out.items():
                t = totals.setdefault(k, [0, 0, 0])
                for i in range(3):
                    t[i] += v[i]
    print(f"{len(seeds)} seeds: {stories} planted first-half stories, {spotted} spotted ({spotted / stories:.0%})")
    for k, (aired, rel, n) in totals.items():
        print(f"  {k:<17} recall {aired}/{stories} ({aired / stories:.0%})   precision {rel}/{n} ({rel / n:.0%})")
    return totals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["cache", "features", "picks"])
    ap.add_argument("--seeds", default="201-240")
    ap.add_argument("--agents", action="store_true")
    a = ap.parse_args()
    if a.cmd == "picks":
        compare([s for s in _seeds(a.seeds) if s in set(cached_seeds())], a.agents)
        return
    if a.cmd == "cache":
        CACHE.mkdir(parents=True, exist_ok=True)
        with ProcessPoolExecutor() as ex:
            print("cached", list(ex.map(_cache_one, _seeds(a.seeds))))
        return
    rows = []
    for seed in cached_seeds():
        d = load(seed)
        for c in spot(d["md"])[0]:
            if c.kind in MOMENTS:
                continue
            rows.append(dict(features(d, c), planted=label(d, c) is not None))
    for k in ("strength", "max_strength", "scans", "first_min", "minutes_to_whistle"):
        p = [r[k] for r in rows if r["planted"]]
        n = [r[k] for r in rows if not r["planted"]]
        print(f"{k:<20} planted median {statistics.median(p):>6}  (n={len(p)})   not planted median {statistics.median(n):>6}  (n={len(n)})")
    print("\nby kind: planted / all")
    for kind in sorted({r["kind"] for r in rows}):
        rs = [r for r in rows if r["kind"] == kind]
        print(f"  {kind:<15} {sum(r['planted'] for r in rs)}/{len(rs)}")


if __name__ == "__main__":
    main()
