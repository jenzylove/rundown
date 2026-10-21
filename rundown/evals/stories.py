"""Score detected talking points against a match's planted stories (answer key).

    python -m rundown.evals.stories out/matches/1 out/matches/2 ...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..engine import detect, load

KIND_MAP = {"press_switch": "press_up", "flank_overload": "flank_overload", "fatigue": "fatigue",
            "sit_deep": "deep_block", "tempo_up": "tempo_up"}
MAX_DELAY_MIN = 15.0


def matches(story: dict, c) -> bool:
    if KIND_MAP[story["kind"]] != c.kind or story["team"] != c.team:
        return False
    if story["kind"] == "flank_overload" and story["side"] != c.side:
        return False
    if story["kind"] == "fatigue" and story["player"] != c.player:
        return False
    return story["start_minute"] - 1 <= c.minute <= story["start_minute"] + MAX_DELAY_MIN


def run_detectors(md, step_s: float = 60.0):
    """Scan the whole match minute by minute; keep each distinct finding once (first detection)."""
    first = {}
    for period, (t0, t1) in ((1, (0.0, md.frames[md.frame_index(1, 1e9), 1])),
                             (2, (2700.0, md.frames[md.frame_index(2, 1e9), 1]))):
        t = t0 + step_s
        while t <= t1:
            for c in detect.scan(md, period, t):
                key = (c.kind, c.team, c.side, c.player, period)
                if key not in first:
                    first[key] = c
            t += step_s
    return list(first.values())


def score(match_dir: Path) -> dict:
    md = load(match_dir)
    key = json.loads((match_dir / "answer_key.json").read_text())
    cands = run_detectors(md)
    found, delays = [], []
    for s in key:
        hit = [c for c in cands if matches(s, c)]
        if hit:
            c = min(hit, key=lambda c: c.t)
            found.append(s["id"])
            delays.append(round(c.minute - s["start_minute"], 1))
    true_c = [c for c in cands if any(matches(s, c) for s in key)]
    return dict(seed=md.meta["seed"], stories=len(key), found=len(found), delays=delays,
                candidates=len(cands), matched_candidates=len(true_c),
                missed=[s["description"] for s in key if s["id"] not in found],
                false=[c.headline + f" @{c.minute:.0f}'" for c in cands if c not in true_c])


def main():
    rows = [score(Path(p)) for p in sys.argv[1:]]
    for r in rows:
        print(json.dumps(r))
    S = sum(r["stories"] for r in rows)
    F = sum(r["found"] for r in rows)
    C = sum(r["candidates"] for r in rows)
    M = sum(r["matched_candidates"] for r in rows)
    d = [x for r in rows for x in r["delays"]]
    print(f"recall {F}/{S} = {F / max(S, 1):.0%}, precision {M}/{C} = {M / max(C, 1):.0%}, "
          f"median delay {sorted(d)[len(d) // 2] if d else '-'} min")


if __name__ == "__main__":
    main()
