"""Rules Producer: choose talking points and fit them into a timed rundown.

The ranking is the rules-only baseline. The agent Producer makes the same
decision with reasons, and falls back to this when it fails.
"""

from __future__ import annotations

from ..engine.data import MatchData
from ..engine.detect import Candidate
from . import telestrate

INTRO_S = 4.0
MAX_SEGMENT_S = 60.0  # the Overlay Script schema's limit


def segment_length(slot_s: float, n: int) -> float:
    """Each segment's share of the slot. One talking point can't fill 90 s: it gets the schema's
    60 s and the rundown runs short, rather than breaking the schema."""
    return min(MAX_SEGMENT_S, (slot_s - INTRO_S) / n)
POSITIVE = {"press_up", "flank_overload", "tempo_up", "goal", "big_chance"}


def rank(md: MatchData, cands: list[Candidate], t_now: float, cut: str = "neutral") -> list[tuple[float, Candidate]]:
    """Score candidates: strength, recency, goals first; a club cut favours that club's good news."""
    favoured = md.home if cut == "home" else md.away if cut == "away" else None
    scored = []
    for c in cands:
        s = c.strength
        if c.kind == "goal":
            s += 0.6
        if t_now - c.t < 900:
            s += 0.1
        if favoured:
            good_for_fav = (c.team == favoured) == (c.kind in POSITIVE)
            s += 0.35 if good_for_fav else -0.2
        scored.append((round(s, 3), c))
    scored.sort(key=lambda x: -x[0])
    return scored


def pick(md: MatchData, cands: list[Candidate], t_now: float, n: int = 3, cut: str = "neutral") -> list[Candidate]:
    chosen, seen, moments = [], set(), 0
    for _, c in rank(md, cands, t_now, cut):
        key = c.kind  # one segment per kind keeps the rundown varied
        is_moment = c.kind in ("goal", "big_chance")
        if key in seen or (is_moment and moments >= 1):
            continue  # one goal or chance per rundown; the rest is analysis
        seen.add(key)
        moments += is_moment
        chosen.append(c)
        if len(chosen) == n:
            break
    return chosen


def rundown(md: MatchData, cands: list[Candidate], t_now: float, slot_s: float = 90.0,
            cut: str = "neutral", lang: str = "en") -> dict:
    n = 3 if slot_s >= 60 else 2
    chosen = pick(md, cands, t_now, n, cut)
    if not chosen:
        return dict(slot_s=slot_s, cut=cut, lang=lang, segments=[], duration_s=0.0)
    each = segment_length(slot_s, len(chosen))
    segs = []
    for i, c in enumerate(chosen):
        seg = telestrate.build(md, c, lang=lang, target_s=each, seg_id=f"s{i + 1}")
        segs.append(seg)
    total = INTRO_S + sum(s["duration_s"] for s in segs)
    score = f"{_goals(md, md.home, t_now)}-{_goals(md, md.away, t_now)}"
    return dict(slot_s=slot_s, cut=cut, lang=lang, made_by="rules", score=score,
                intro=dict(d=INTRO_S, home=md.meta["home"]["short"], away=md.meta["away"]["short"]),
                segments=segs, duration_s=round(total, 2))


def _goals(md: MatchData, team: str, t_now: float) -> int:
    return sum(1 for e in md.events if e["type"] == "goal" and e["team"] == team and e["t"] <= t_now)
