"""Run the crew over a recorded first half, as a live run would, and lock the rundown.

    python -m rundown.agents.halftime out/matches/3 [--kill telestrator] [--cut away --lang es]

Scans every match minute, has the Analyst score new talking points and pre-drafts the
provisional picks, then locks at the whistle. Prints the rundown, the trail and the time
from whistle to lock. Needs the AZURE_OPENAI_* settings.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..engine import detect, load
from ..engine.data import MatchData
from .crew import Crew
from .llm import Models
from .state import Store


def run_half(md: MatchData, models: Models, cut: str = "neutral", lang: str = "en", kill: tuple = (),
             draft: bool = True) -> tuple[dict, list[dict], float]:
    """-> (rundown, trail, seconds from the whistle to the lock)"""
    store = Store()
    crew = Crew(models, store)
    for role in kill:
        crew.kill(role)
    t_end = float(md.frames[md.frame_index(1, 1e9), 1])
    cands: dict = {}
    t, pending = 60.0, []
    while t <= t_end:
        new = detect.track(cands, detect.scan(md, 1, t) + detect.moments(md, 1, t - 60, t), t)
        if new:
            pending.append(crew.analyse(md, new))
        if draft and int(t) % 300 == 0:  # every five match minutes, draft the provisional picks
            pending.append(crew.draft(md, list(cands.values()), t, lang, cut))
        t += 60
    for f in pending:
        f.result(600)
    whistle = time.time()
    r = crew.rundown(md, list(cands.values()), t_end, 90.0, cut, lang)
    return r, store.trail, time.time() - whistle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("match")
    ap.add_argument("--cut", default="neutral")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--kill", action="append", default=[])
    ap.add_argument("--no-draft", action="store_true", help="write everything at the whistle")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    r, trail, lock_s = run_half(load(a.match), Models(), a.cut, a.lang, tuple(a.kill), not a.no_draft)
    for s in r["segments"]:
        p = s["provenance"]
        print(f"\n[{s['segment_id']}] {s['title']}  ({s['kind']}, {s['duration_s']} s, crew={p['crew']}, verified={p['verified']})")
        print(f"   pick: {p.get('pick_reason')}")
        for ln in s["script"]:
            print(f"   > {ln['text']}")
    print(f"\nrundown {r['duration_s']} s for a {r['slot_s']} s slot; locked {lock_s:.1f} s after the whistle")
    by: dict = {}
    for it in trail:
        by[(it["agent"], it["status"])] = by.get((it["agent"], it["status"]), 0) + 1
    print("trail:", ", ".join(f"{ag}/{st}={n}" for (ag, st), n in sorted(by.items())))
    for it in trail:
        if it["status"] in ("retry", "failed", "fallback", "killed"):
            print(f"   {it['agent']:<11} {it['status']:<8} {it['action']}: {it['why'][:110]}")
    if a.out:
        a.out.write_text(json.dumps(dict(rundown=r, trail=trail, lock_s=round(lock_s, 2)), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
