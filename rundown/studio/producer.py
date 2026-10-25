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


def n_segments(slot_s: float) -> int:
    """Three segments fill a half-time slot; a catch-up of 30 to 40 s has room for one."""
    return 3 if slot_s >= 60 else 2 if slot_s >= 45 else 1


def segment_length(slot_s: float, n: int) -> float:
    """Each segment's share of the slot. One talking point can't fill 90 s: it gets the schema's
    60 s and the rundown runs short, rather than breaking the schema."""
    return min(MAX_SEGMENT_S, (slot_s - INTRO_S) / n)
POSITIVE = {"press_up", "flank_overload", "tempo_up", "goal", "big_chance"}


def rank(md: MatchData, cands: list[Candidate], t_now: float, cut: str = "neutral",
         player: str | None = None) -> list[tuple[float, Candidate]]:
    """Score candidates: strength, recency, goals first; a club cut favours that club's good news,
    and a fan who follows a player hears first about the points he is in."""
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
        if player and c.player == player:
            s += 0.4
        scored.append((round(s, 3), c))
    scored.sort(key=lambda x: -x[0])
    return scored


def pick(md: MatchData, cands: list[Candidate], t_now: float, n: int = 3, cut: str = "neutral",
         player: str | None = None) -> list[Candidate]:
    chosen, seen, moments = [], set(), 0
    for _, c in rank(md, cands, t_now, cut, player):
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


def focus(md: MatchData, player: str | None, period: int, t_from: float | None, t_now: float) -> Candidate | None:
    """The followed player's own segment, over the same window as the rundown."""
    from ..engine import detect
    if not player:
        return None
    start = t_from if t_from is not None else (0.0 if period == 1 else md.meta.get("period2_start", 2700.0))
    return detect.player_focus(md, player, period, start, t_now)


def choose(md: MatchData, cands: list[Candidate], t_now: float, n: int, cut: str = "neutral",
           player: str | None = None, t_from: float | None = None) -> list[Candidate]:
    """The rules choice for a profile: the best stories, and the followed player's segment last."""
    period = cands[0].period if cands else int(md.frames[-1, 0])  # nothing spotted: the half being played
    mine = focus(md, player, period, t_from, t_now) if n >= 2 else None
    chosen = pick(md, cands, t_now, n - (1 if mine else 0), cut, player)
    if mine:
        chosen.append(mine)
    elif not chosen and player:  # a catch-up with nothing new: the player still has a story
        chosen = [c for c in [focus(md, player, period, t_from, t_now)] if c]
    return chosen


def rundown(md: MatchData, cands: list[Candidate], t_now: float, slot_s: float = 90.0,
            cut: str = "neutral", lang: str = "en", depth: str = "analyst", player: str | None = None,
            t_from: float | None = None) -> dict:
    n = n_segments(slot_s)
    chosen = choose(md, cands, t_now, n, cut, player, t_from)
    base = dict(slot_s=slot_s, cut=cut, lang=lang, depth=depth, player=player)
    if not chosen:
        return dict(base, segments=[], duration_s=0.0)
    each = segment_length(slot_s, len(chosen))
    segs = []
    for i, c in enumerate(chosen):
        seg = telestrate.build(md, c, lang=lang, target_s=each, seg_id=f"s{i + 1}", depth=depth)
        segs.append(seg)
    total = INTRO_S + sum(s["duration_s"] for s in segs)
    score = f"{_goals(md, md.home, t_now)}-{_goals(md, md.away, t_now)}"
    return dict(base, made_by="rules", score=score,
                intro=dict(d=INTRO_S, home=md.meta["home"]["short"], away=md.meta["away"]["short"]),
                segments=segs, duration_s=round(total, 2))


def _goals(md: MatchData, team: str, t_now: float) -> int:
    return sum(1 for e in md.events if e["type"] == "goal" and e["team"] == team and e["t"] <= t_now)
