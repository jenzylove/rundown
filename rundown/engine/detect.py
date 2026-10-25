"""Deterministic change detectors: candidate talking points with their facts.

Each detector compares a recent window against an earlier baseline for the
same team or player. The detectors know nothing about planted stories; they
look for the kinds of change a studio analyst looks for. The agents choose,
explain and package candidates. This module is also the rules-only baseline
the evals compare the agents against.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import stats as S
from .data import MatchData

RECENT_S = 600.0  # 10 minutes
MIN_BASELINE_S = 480.0


@dataclass
class Candidate:
    kind: str
    team: str
    period: int
    t: float  # clock when detected
    strength: float  # 0..1
    headline: str
    facts: list[S.Fact]
    player: str | None = None
    side: str | None = None
    evidence: list[str] = field(default_factory=list)
    seen: list[tuple[float, float]] = field(default_factory=list)  # (clock, strength) of every scan it showed in

    @property
    def minute(self) -> float:
        return self.t / 60.0

    @property
    def scans(self) -> int:
        return max(1, len(self.seen))

    @property
    def peak(self) -> float:
        return max([s for _, s in self.seen] + [self.strength])

    @property
    def last_t(self) -> float:
        return max([t for t, _ in self.seen] + [self.t])

    def to_dict(self) -> dict:
        return dict(kind=self.kind, team=self.team, period=self.period, t=self.t,
                    minute=round(self.minute, 1), strength=round(self.strength, 3), headline=self.headline,
                    player=self.player, side=self.side, evidence=self.evidence[:40],
                    facts=[f.to_dict() for f in self.facts],
                    scans=self.scans, peak=round(self.peak, 3), last_minute=round(self.last_t / 60.0, 1))


MOMENTS = ("goal", "big_chance")


def key(c: Candidate) -> str:
    """One talking point however many scans it shows up in (each goal or chance is its own)."""
    moment = c.evidence[0] if c.kind in MOMENTS and c.evidence else ""
    return f"{c.kind}:{c.team}:{c.side or ''}:{c.player or ''}:{c.period}:{moment}"


def track(known: dict[str, Candidate], found: list[Candidate], t: float) -> list[Candidate]:
    """Fold one scan's findings into the talking points so far: keep the first sighting of each,
    note every scan it shows up in (how long it lasts and how strong it gets), return the new ones."""
    new = []
    for c in found:
        k = key(c)
        if k not in known:
            known[k] = c
            new.append(c)
        known[k].seen.append((t, c.strength))
    return new


def _clip(v: float) -> float:
    return max(0.0, min(1.0, v))


def windows(md: MatchData, period: int, t: float) -> tuple[tuple[float, float], tuple[float, float]] | None:
    start = 0.0 if period == 1 else md.meta.get("period2_start", 2700.0)
    r0 = max(start, t - RECENT_S)
    if r0 - start < MIN_BASELINE_S:
        return None
    return (start, r0), (r0, t)


def detect_press(md, team, period, base, rec, t):
    pb, pr = S.ppda(md, team, period, *base), S.ppda(md, team, period, *rec)
    qb, qr = S.pressures(md, team, period, *base), S.pressures(md, team, period, *rec)
    if pb.value <= 0 or qb.value <= 0:
        return None
    ppda_drop = 1 - pr.value / pb.value
    press_rise = qr.value / qb.value - 1
    high_b = qb.detail["in_opp_half"] / max(qb.detail["total"], 1)
    high_r = qr.detail["in_opp_half"] / max(qr.detail["total"], 1)
    if ppda_drop < 0.25 or (press_rise < 0.2 and high_r - high_b < 0.15):
        return None
    s = _clip(0.45 * ppda_drop + 0.35 * press_rise + 0.8 * (high_r - high_b))
    if s < 0.25:
        return None
    return Candidate("press_up", team, period, t, s,
                     f"{S._short(md, team)} pressing higher: PPDA {pb.value} to {pr.value}",
                     [pb, pr, qb, qr], evidence=pr.evidence + qr.evidence)


def detect_flank(md, team, period, base, rec, t):
    out = []
    for side in ("left", "right"):
        fb, fr = S.flank_share(md, team, period, *base, side=side), S.flank_share(md, team, period, *rec, side=side)
        if fr.detail["final_third_actions"] < 8:
            continue
        ob, orr = S.overloads(md, team, period, *base, side=side), S.overloads(md, team, period, *rec, side=side)
        rise = (fr.value - fb.value) / 100.0
        over_rate_b = ob.value / max((base[1] - base[0]) / 600.0, 0.1)
        over_rise = orr.value - over_rate_b
        if rise < 0.12 or fr.value < 30 or orr.value < 2:
            continue
        s = _clip(1.6 * rise + 0.08 * over_rise + (0.15 if fr.value > 50 else 0))
        if s >= 0.25:
            out.append(Candidate("flank_overload", team, period, t, s,
                                 f"{S._short(md, team)} going down the {side}: {fb.value}% to {fr.value}% of attacks",
                                 [fb, fr, ob, orr], side=side, evidence=fr.evidence))
    return out


def detect_fatigue(md, team, period, base, rec, t):
    out = []
    on = md.slot_players(period, t)
    own = on[:11] if md.side(team) == 0 else on[11:]
    for pid in own[1:]:
        pb, pr = S.player_physical(md, pid, period, *base), S.player_physical(md, pid, period, *rec)
        mb, mr = (base[1] - base[0]) / 60.0, (rec[1] - rec[0]) / 60.0
        rate_b, rate_r = pb.value / mb, pr.value / mr
        top_b, top_r = pb.detail["top_speed_kmh"], pr.detail["top_speed_kmh"]
        if pb.value < 3 or top_b <= 0:
            continue
        sprint_drop = 1 - rate_r / rate_b if rate_b > 0 else 0
        top_drop = 1 - top_r / top_b
        if sprint_drop < 0.4 or top_drop < 0.08:
            continue
        s = _clip(0.6 * sprint_drop + 2.5 * top_drop)
        out.append(Candidate("fatigue", team, period, t, s,
                             f"{md.name(pid)} fading: top speed {top_b} to {top_r} km/h",
                             [pb, pr], player=pid))
    return out


def detect_deep(md, team, period, base, rec, t):
    lb, lr = S.defensive_line(md, team, period, *base), S.defensive_line(md, team, period, *rec)
    opp = md.opponent(team)
    tb, tr = S.field_tilt(md, opp, period, *base), S.field_tilt(md, opp, period, *rec)
    drop = lb.value - lr.value
    if drop < 4:
        return None
    s = _clip(drop / 12 + max(0.0, tr.value - tb.value) / 60)
    return Candidate("deep_block", team, period, t, s,
                     f"{S._short(md, team)} dropping deeper: line {lb.value} m to {lr.value} m",
                     [lb, lr, tb, tr])


def detect_tempo(md, team, period, base, rec, t):
    tb, tr = S.tempo(md, team, period, *base), S.tempo(md, team, period, *rec)
    if tb.value <= 0 or tr.detail["possession_minutes"] < 2:
        return None
    rise = tr.value / tb.value - 1
    if rise < 0.2:
        return None
    return Candidate("tempo_up", team, period, t, _clip(rise * 1.6),
                     f"{S._short(md, team)} moving it quicker: {tb.value} to {tr.value} passes a minute on the ball",
                     [tb, tr], evidence=tr.evidence)


def scan(md: MatchData, period: int, t: float) -> list[Candidate]:
    """All candidates at clock t (only using data up to t)."""
    w = windows(md, period, t)
    if w is None:
        return []
    base, rec = w
    out: list[Candidate] = []
    for team in (md.home, md.away):
        for fn in (detect_press, detect_deep, detect_tempo):
            c = fn(md, team, period, base, rec, t)
            if c:
                out.append(c)
        out += detect_flank(md, team, period, base, rec, t)
        out += detect_fatigue(md, team, period, base, rec, t)
    return out


def player_focus(md: MatchData, player: str, period: int, t0: float, t1: float) -> Candidate | None:
    """A followed player's window: his passing and his running, as one talking point.
    Made on request for a fan who follows him, not by the scans."""
    if player not in md.players:
        return None
    team = md.players[player]["team"]
    ps, ph = S.player_passing(md, player, period, t0, t1), S.player_physical(md, player, period, t0, t1)
    if ps.detail["attempted"] == 0 and ph.detail["distance_m"] == 0:
        return None  # not on the pitch in this window
    shots = [e for e in md.window(period, t0, t1, ("shot",)) if e.get("player") == player]
    facts = [ps, ph] + [S.shot_xg(md, e["id"]) for e in shots[:2]]
    return Candidate("player_focus", team, period, t1, 0.5,
                     f"{md.name(player)}: {ps.detail['completed']} of {ps.detail['attempted']} passes, "
                     f"{ph.detail['distance_m']} m covered",
                     facts, player=player, evidence=(ps.evidence + [e["id"] for e in shots])[:40])


def moments(md: MatchData, period: int, t0: float, t1: float) -> list[Candidate]:
    """Discrete key moments: goals, big chances, big saves."""
    out = []
    for e in md.window(period, t0, t1, ("shot",)):
        if e["outcome"] == "goal" or e["xg"] >= 0.25:
            f = S.shot_xg(md, e["id"])
            kind = "goal" if e["outcome"] == "goal" else "big_chance"
            out.append(Candidate(kind, e["team"], period, e["t"], 1.0 if kind == "goal" else _clip(e["xg"] * 2),
                                 f"{md.name(e['player'])} {'scores' if kind == 'goal' else 'big chance'} ({e['xg']} xG)",
                                 [f], player=e["player"], evidence=[e["id"]]))
    return out
