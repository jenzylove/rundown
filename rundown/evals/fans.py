"""The fans eval: does every fan get a show that is true, and genuinely theirs?

    python -m rundown.evals.fans --seeds 101-112 --out docs/evals/fans.json [--rules-only]

For each held-out seed it plays the first half as a live run does, then makes the half-time
rundown for eight fan profiles (every side at both depths in English, plus two fans who follow
a player: a home fan in Spanish, an away analyst in French), with the agent crew and with the
rules crew. Scores:

  verified       segments that pass today's Verifier (both crews are checked the same way)
  agent words    segments whose words the Writer agent wrote (the rest fell back to the rules)
  different      home and away cuts of the same depth that lead with a different story;
                 casual and analyst cuts of the same side whose scripts differ
  casual         casual segments with more than three numbers, two in a line, or PPDA/xG (target 0)
  player         followed players who got their own segment
  time           seconds to make each cut after the whistle (agents)

Then the planted-error test, with no model calls: every aired segment is copied with one error
planted in it (a before/now pair quoted the wrong way round, a rise said as a fall, a number
changed in a spoken line, a number changed on the stat card), and each copy goes through the
Verifier as it was on 22 Oct and as it is now. A copy that passes would have gone on air.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..agents import verify
from ..studio import producer
from ..studio.profile import Profile
from .rundown import _pct, _seeds, first_half, spot


def profiles(md) -> list[Profile]:
    home = [p for p in md.meta["home"]["starters"] if md.players[p]["role"] not in ("GK",)]
    away = [p for p in md.meta["away"]["starters"] if md.players[p]["role"] not in ("GK",)]
    return ([Profile(side, "en", depth) for depth in ("casual", "analyst") for side in ("neutral", "home", "away")]
            + [Profile("home", "es", "casual", home[-2]), Profile("away", "fr", "analyst", away[-3])])


def checked(md, r: dict) -> dict:
    """What would air: segments that pass the Verifier (the run manager drops the rest)."""
    for s in r["segments"]:
        s["provenance"]["errors"] = verify.segment_errors(md, s)
        s["provenance"]["verified"] = not s["provenance"]["errors"]
    return r


def cut_row(prof: Profile, r: dict, took_s: float | None) -> dict:
    segs = r["segments"]
    lines = [ln["text"] for s in segs for ln in s["script"]]
    return dict(profile=prof.key, segments=len(segs),
                verified=sum(1 for s in segs if s["provenance"]["verified"]),
                agent_words=sum(1 for s in segs if (s["provenance"].get("crew") or {}).get("writer") == "agent"),
                lead=(segs[0]["kind"], segs[0]["team"]) if segs else None, kinds=[s["kind"] for s in segs],
                titles=[s["title"] for s in segs], script=lines,
                casual_breaks=sum(1 for s in segs if verify.depth_errors([ln["text"] for ln in s["script"]], [], "casual", 0))
                if prof.depth == "casual" else 0,
                player_segment=any(s["kind"] == "player_focus" for s in segs) if prof.player else None,
                errors=[e for s in segs for e in s["provenance"]["errors"]][:4],
                took_s=None if took_s is None else round(took_s, 2))


def plant(seg: dict) -> list[tuple[str, dict]]:
    """Copies of a segment, each with one error planted in what it says."""
    out = []
    lang, pairs = seg.get("lang", "en"), verify._pairs(seg)
    for i, ln in enumerate(seg["script"]):
        nums = list(verify.NUM.finditer(ln["text"]))
        roles = [verify._role(verify._num(m.group(0)), pairs) for m in nums]
        for (m1, r1), (m2, r2) in zip(zip(nums, roles), list(zip(nums, roles))[1:]):
            if r1 and r2 and r1[0] == r2[0] and r1[1] != r2[1]:  # a before/now pair: swap it
                t = ln["text"]
                bad = t[:m1.start()] + m2.group(0) + t[m1.end():m2.start()] + m1.group(0) + t[m2.end():]
                c = copy.deepcopy(seg); c["script"][i]["text"] = bad
                out.append(("pair the wrong way round", c))
                break
        words = re.findall(r"[^\W\d_]+", ln["text"].lower())
        up = [w for w in words if w in verify.UP.get(lang, set())]
        down = [w for w in words if w in verify.DOWN.get(lang, set())]
        if bool(up) != bool(down) and len({r for r in roles if r}) >= 2:
            w, to = (up[0], next(iter(sorted(verify.DOWN[lang])))) if up else (down[0], next(iter(sorted(verify.UP[lang]))))
            c = copy.deepcopy(seg)
            c["script"][i]["text"] = re.sub(rf"\b{re.escape(w)}\b", to, ln["text"], count=1, flags=re.I)
            out.append(("rise said as a fall, or a fall as a rise", c))
        if nums:
            m = nums[-1]
            n = verify._num(m.group(0))
            new = f"{n + 3:g}".replace(".", "," if "," in m.group(0) else ".")
            c = copy.deepcopy(seg); c["script"][i]["text"] = ln["text"][:m.start()] + new + ln["text"][m.end():]
            out.append(("number changed in a spoken line", c))
    for q_i, q in enumerate(seg["cues"]):
        if q["kind"] == "statcard" and q.get("rows"):
            label, val = q["rows"][0]
            m = list(verify.NUM.finditer(val))
            if m:
                n = verify._num(m[-1].group(0))
                c = copy.deepcopy(seg)
                c["cues"][q_i]["rows"][0] = [label, val[:m[-1].start()] + f"{n + 3:g}" + val[m[-1].end():]]
                out.append(("number changed on the stat card", c))
    return out


def planted(md, segs: list[dict]) -> dict:
    by: dict = {}
    for s in segs:
        if verify.segment_errors(md, s):
            continue  # only plant into segments that were clean
        for kind, c in plant(s):
            b = by.setdefault(kind, dict(planted=0, caught_22oct=0, caught_now=0))
            b["planted"] += 1
            b["caught_22oct"] += bool(verify.base_errors(md, c))
            b["caught_now"] += bool(verify.segment_errors(md, c))
    return by


def evaluate_seed(seed: int, models=None) -> dict:
    md, _ = first_half(seed)
    cands, t_end = spot(md)
    row = dict(seed=seed, candidates=len(cands), rules=[], agents=[])
    profs = profiles(md)
    for prof in profs:
        r = checked(md, producer.rundown(md, cands, t_end, 90.0, prof.side, prof.lang, prof.depth, prof.player))
        row["rules"].append(cut_row(prof, r, None))
    aired = []
    if models is not None:
        from ..agents.crew import Crew
        from ..agents.state import Store
        crew = Crew(models, Store())
        crew.analyse(md, cands).result(600)
        for prof in profs:
            t0 = time.time()
            r = checked(md, crew.rundown(md, cands, t_end, 90.0, prof.side, prof.lang, timeout=300,
                                         depth=prof.depth, player=prof.player))
            row["agents"].append(cut_row(prof, r, time.time() - t0))
            aired += r["segments"]
    else:
        for prof in profs:
            aired += producer.rundown(md, cands, t_end, 90.0, prof.side, prof.lang, prof.depth, prof.player)["segments"]
    row["planted"] = planted(md, aired)
    return row


def summarise(rows: list[dict]) -> dict:
    out = dict(seeds=[r["seed"] for r in rows])
    for system in ("agents", "rules"):
        cuts = [c for r in rows for c in r[system]]
        if not cuts:
            continue
        diff_side = same_side = diff_depth = same_depth = 0
        for r in rows:
            by = {c["profile"]: c for c in r[system]}
            for depth in ("casual", "analyst"):
                h, a = by.get(f"home|en|{depth}|-"), by.get(f"away|en|{depth}|-")
                if h and a and h["lead"] and a["lead"]:
                    same_side += 1
                    diff_side += h["lead"] != a["lead"] or h["kinds"] != a["kinds"]
            for side in ("neutral", "home", "away"):
                c, a = by.get(f"{side}|en|casual|-"), by.get(f"{side}|en|analyst|-")
                if c and a:
                    same_depth += 1
                    diff_depth += c["script"] != a["script"]
        segs = sum(c["segments"] for c in cuts)
        took = [c["took_s"] for c in cuts if c["took_s"] is not None]
        followed = [c for c in cuts if c["player_segment"] is not None]
        out[system] = dict(
            cuts=len(cuts), segments=segs, verified=sum(c["verified"] for c in cuts),
            agent_words=sum(c["agent_words"] for c in cuts),
            home_away_different=[diff_side, same_side], casual_analyst_different=[diff_depth, same_depth],
            casual_breaks=sum(c["casual_breaks"] for c in cuts),
            player_segments=[sum(1 for c in followed if c["player_segment"]), len(followed)],
            took_median_s=round(statistics.median(took), 2) if took else None, took_max_s=max(took) if took else None)
    pl: dict = {}
    for r in rows:
        for k, v in r["planted"].items():
            b = pl.setdefault(k, dict(planted=0, caught_22oct=0, caught_now=0))
            for f in b:
                b[f] += v[f]
    out["planted"] = pl
    return out


def table(s: dict) -> str:
    a, b = s.get("agents"), s["rules"]
    lines = [f"{len(s['seeds'])} held-out seeds ({s['seeds'][0]} to {s['seeds'][-1]}), 8 fan profiles each.", "",
             "| | Agent crew | Rules crew |", "|---|---|---|"]

    def both(name, f):
        lines.append(f"| {name} | {f(a) if a else '-'} | {f(b)} |")
    both("Segments that pass the Verifier", lambda x: _pct(x["verified"], x["segments"]))
    both("Home and away cuts that differ", lambda x: _pct(*x["home_away_different"]))
    both("Casual and analyst scripts that differ", lambda x: _pct(*x["casual_analyst_different"]))
    both("Casual segments that break the casual rules", lambda x: str(x["casual_breaks"]))
    both("Followed players with their own segment", lambda x: _pct(*x["player_segments"]))
    if a:
        lines.append(f"| Words written by the Writer agent | {_pct(a['agent_words'], a['segments'])} | - |")
        lines.append(f"| Seconds to make a cut (median, worst) | {a['took_median_s']} s, {a['took_max_s']} s | at once |")
    lines += ["", "Planted errors (one per copy of a clean aired segment), caught before air:", "",
              "| Error planted | Copies | Verifier of 22 Oct | Verifier now |", "|---|---|---|---|"]
    for k, v in s["planted"].items():
        lines.append(f"| {k} | {v['planted']} | {_pct(v['caught_22oct'], v['planted'])} | {_pct(v['caught_now'], v['planted'])} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="101-112")
    ap.add_argument("--rules-only", action="store_true")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    seeds = _seeds(a.seeds)
    models = None
    if not a.rules_only:
        from ..agents.llm import Models
        models = Models()
    with ThreadPoolExecutor(3 if models else 6) as ex:
        rows = list(ex.map(lambda sd: evaluate_seed(sd, models), seeds))
    s = summarise(rows)
    print(table(s))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(encoding="utf-8", data=json.dumps(dict(summary=s, table=table(s), rows=rows), ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
