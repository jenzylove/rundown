"""Export a simulated match.

    python -m rundown.sim.cli --seed 7 --out out/matches

writes <out>/<seed>/meta.json, events.jsonl, tracking.npy and, apart from the
rest, answer_key.json (never shown to agents).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from .match import simulate


def export(seed: int, out: Path, home: str = "HAR", away: str = "EMA") -> Path:
    t0 = time.time()
    m = simulate(seed, home, away)
    d = out / str(seed)
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(m.meta(), indent=2))
    with (d / "events.jsonl").open("w") as f:
        for ev in m.events:
            f.write(json.dumps(ev) + "\n")
    np.save(d / "tracking.npy", m.frames)
    (d / "answer_key.json").write_text(json.dumps(m.answer_key(), indent=2))
    print(f"seed {seed}: {m.home.short} {m.score[0]}-{m.score[1]} {m.away.short}, "
          f"{len(m.events)} events, {len(m.frames)} frames, {time.time() - t0:.1f}s -> {d}")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, nargs="+", default=[7])
    ap.add_argument("--home", default="HAR")
    ap.add_argument("--away", default="EMA")
    ap.add_argument("--out", type=Path, default=Path("out/matches"))
    a = ap.parse_args()
    for s in a.seed:
        export(s, a.out, a.home, a.away)


if __name__ == "__main__":
    main()
