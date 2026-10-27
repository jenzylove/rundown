"""How reliable is the agent crew? Runs the half-time crew on recorded matches and counts,
per agent, how often its answer passed the Verifier first time, after the one retry, or
was routed around to the rules crew; and, per rundown, what went on air.

    python -m rundown.evals.crew out/matches/3 out/matches/11 ... [--cuts neutral:en,away:es] [--kill telestrator]

Needs the AZURE_OPENAI_* settings. Runs four rundowns at a time.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..agents.halftime import run_half
from ..agents.llm import Models
from ..engine import load

AGENTS = ("analyst", "producer", "telestrator", "writer")


def score(results: list[dict]) -> dict:
    calls, rows = Counter(), []
    for res in results:
        segs = res["rundown"]["segments"]
        for t in res["trail"]:
            if t["agent"] in AGENTS and t["status"] in ("ok", "retry", "failed") and t["ms"] is not None:
                calls[(t["agent"], t["status"])] += 1
        rows.append(dict(match=res["match"], cut=res["cut"], lang=res["lang"], segments=len(segs),
                         agent_pictures=sum(s["provenance"]["crew"]["telestrator"] == "agent" for s in segs),
                         agent_words=sum(s["provenance"]["crew"]["writer"] == "agent" for s in segs),
                         verified=sum(bool(s["provenance"]["verified"]) for s in segs),
                         duration_s=res["rundown"]["duration_s"], lock_s=round(res["lock_s"], 2)))
    agents = {}
    for a in AGENTS:
        ok, retry, failed = calls[(a, "ok")], calls[(a, "retry")], calls[(a, "failed")]
        agents[a] = dict(jobs=ok + failed, first_try=ok - (retry - failed), after_retry=ok, routed_around=failed)
    locks = [r["lock_s"] for r in rows]
    return dict(rundowns=rows, agents=agents, lock_median_s=round(statistics.median(locks), 2),
                lock_max_s=max(locks))


def _brief(res: dict) -> dict:
    """A run without its tracking frames: what went on air, who made it, and the trail."""
    segs = [dict(title=x["title"], kind=x["kind"], script=[ln["text"] for ln in x["script"]],
                 provenance={k: v for k, v in x["provenance"].items() if k != "candidate"})
            for x in res["rundown"]["segments"]]
    return dict(match=res["match"], cut=res["cut"], lang=res["lang"], lock_s=round(res["lock_s"], 2),
                segments=segs, trail=res["trail"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("matches", nargs="+")
    ap.add_argument("--cuts", default="neutral:en,away:es")
    ap.add_argument("--kill", action="append", default=[])
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    models = Models()
    jobs = [(m, *cl.split(":")) for m in a.matches for cl in a.cuts.split(",")]

    def one(job):
        m, cut, lang = job
        r, trail, lock_s = run_half(load(m), models, cut, lang, tuple(a.kill))
        return dict(match=Path(m).name, cut=cut, lang=lang, rundown=r, trail=trail, lock_s=lock_s)
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(one, jobs))
    s = score(results)
    print(f"{'match':<6} {'cut':<8} {'lang':<4} pictures  words  verified  length  lock")
    for r in s["rundowns"]:
        n = r["segments"]
        print(f"{r['match']:<6} {r['cut']:<8} {r['lang']:<4} {r['agent_pictures']}/{n}       {r['agent_words']}/{n}    "
              f"{r['verified']}/{n}       {r['duration_s']:>5} s {r['lock_s']:>5} s")
    print(f"\n{'agent':<12} jobs  first try  after retry  routed around")
    for ag, v in s["agents"].items():
        print(f"{ag:<12} {v['jobs']:>4}  {v['first_try']:>9}  {v['after_retry']:>11}  {v['routed_around']:>13}")
    print(f"\nlock after the whistle: median {s['lock_median_s']} s, max {s['lock_max_s']} s")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(encoding="utf-8", data=json.dumps(dict(summary=s, runs=[_brief(r) for r in results]), indent=1,
                                    ensure_ascii=False, default=str))

if __name__ == "__main__":
    main()
