"""Match statistics as pure functions over MatchData.

Every public stat returns a Fact. A Fact names the function and arguments that
produced it, so the Verifier can recompute it exactly, and lists the events or
frame range behind it.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

from .data import HZ, PITCH_LEN, PITCH_WID, MatchData

SPRINT_KMH = 25.2
FINAL_THIRD_X = 70.0
CHANNELS = {"left": (0.0, 22.67), "centre": (22.67, 45.33), "right": (45.33, 68.0)}


@dataclass
class Fact:
    stat: str
    args: dict
    value: float | int | str
    unit: str
    label: str
    evidence: list[str] = field(default_factory=list)  # event ids
    frames: tuple[int, int] | None = None  # frame range used
    detail: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        a = ",".join(f"{k}={v}" for k, v in sorted(self.args.items()))
        return f"{self.stat}({a})"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d


REGISTRY = {}


def stat(fn):
    REGISTRY[fn.__name__] = fn
    return fn


def recompute(md: MatchData, name: str, args: dict) -> Fact:
    return REGISTRY[name](md, **args)


def _sig(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    L2 = float(ab @ ab)
    if L2 < 1e-9:
        return np.linalg.norm(p - a, axis=-1)
    u = np.clip(((p - a) @ ab) / L2, 0.0, 1.0)
    return np.linalg.norm(p - (a + u[..., None] * ab), axis=-1)


def _opp_slots(md: MatchData, team: str) -> slice:
    return slice(11, 22) if md.side(team) == 0 else slice(0, 11)


def _own_slots(md: MatchData, team: str) -> slice:
    return slice(0, 11) if md.side(team) == 0 else slice(11, 22)


# ---------------------------------------------------------------- passing

@stat
def passes(md: MatchData, team: str, period: int, t0: float, t1: float) -> Fact:
    ev = md.window(period, t0, t1, ("pass",), team)
    done = [e for e in ev if e["outcome"] == "complete"]
    pct = round(100.0 * len(done) / len(ev), 1) if ev else 0.0
    return Fact("passes", dict(team=team, period=period, t0=t0, t1=t1), pct, "%",
                f"{md.meta['home' if md.side(team) == 0 else 'away']['short']} pass completion",
                [e["id"] for e in ev], detail=dict(attempted=len(ev), completed=len(done)))


def pass_features(md: MatchData, ev: dict) -> dict:
    """Physical context of a pass from tracking: length, pressure on the passer,
    how blocked the lane was, and space at the receiving end."""
    i = md.frame_index(ev["period"], ev["t"])
    pos = md.positions(i)
    opp = pos[_opp_slots(md, ev["team"])]
    a = np.array([ev["x"], ev["y"]])
    b = np.array([ev["end_x"], ev["end_y"]])
    length = float(np.linalg.norm(b - a))
    pressure = float(np.min(np.linalg.norm(opp - a, axis=1)))
    if length > 2:
        u = 1.0 / length
        a2, b2 = a + (b - a) * u, b - (b - a) * u
        lane = float(np.min(_seg_dist(opp, a2, b2)))
    else:
        lane = 10.0
    arrive = md.frame_index(ev["period"], ev["t"] + length / max(ev.get("speed_kmh", 50) / 3.6, 1.0))
    space = float(np.min(np.linalg.norm(md.positions(arrive)[_opp_slots(md, ev["team"])] - b, axis=1)))
    return dict(length=length, pressure=pressure, lane=lane, space=space, lofted=ev.get("height") == "high")


def pass_difficulty_score(f: dict) -> float:
    """0..100, higher = harder. Deterministic, documented in docs/STATS.md."""
    z = (-3.1 + 0.055 * f["length"] + 1.3 * math.exp(-f["pressure"] / 2.0)
         + (0.4 if f["lofted"] else 1.6 * math.exp(-f["lane"] / 1.5)) + 0.9 * math.exp(-f["space"] / 2.5))
    return round(100.0 * _sig(z), 1)


@stat
def pass_difficulty(md: MatchData, event_id: str) -> Fact:
    ev = md.by_id[event_id]
    f = pass_features(md, ev)
    return Fact("pass_difficulty", dict(event_id=event_id), pass_difficulty_score(f), "/100",
                f"pass difficulty, {md.name(ev['player'])}", [event_id],
                detail={k: round(v, 2) if isinstance(v, float) else v for k, v in f.items()})


@stat
def hardest_pass(md: MatchData, team: str, period: int, t0: float, t1: float) -> Fact:
    ev = [e for e in md.window(period, t0, t1, ("pass",), team) if e["outcome"] == "complete" and not e.get("restart")]
    if not ev:
        return Fact("hardest_pass", dict(team=team, period=period, t0=t0, t1=t1), 0.0, "/100", "hardest completed pass")
    scored = [(pass_difficulty_score(pass_features(md, e)), e) for e in ev]
    s, e = max(scored, key=lambda x: x[0])
    return Fact("hardest_pass", dict(team=team, period=period, t0=t0, t1=t1), s, "/100",
                f"hardest completed pass, {md.name(e['player'])} to {md.name(e['recipient'])}",
                [e["id"]], detail=dict(length=e["length"], minute=e["minute"]))


# ---------------------------------------------------------------- speed

def _ball_speed_series(md: MatchData, a: int, b: int) -> np.ndarray:
    xy = md.frames[a:b, 2:4]
    return np.linalg.norm(np.diff(xy, axis=0), axis=1) * HZ * 3.6


@stat
def shot_speed(md: MatchData, event_id: str) -> Fact:
    """Shot speed measured from the tracking (first 0.4 s of ball flight)."""
    ev = md.by_id[event_id]
    i = md.frame_index(ev["period"], ev["t"])
    v = _ball_speed_series(md, i, i + 5)
    kmh = round(float(np.median(v[1:])) if len(v) > 1 else 0.0, 1)
    return Fact("shot_speed", dict(event_id=event_id), kmh, "km/h", f"shot speed, {md.name(ev['player'])}",
                [event_id], frames=(i, i + 5))


@stat
def player_physical(md: MatchData, player: str, period: int, t0: float, t1: float) -> Fact:
    """Distance covered, top speed and sprint count for one player in a window.
    A sprint is >= 25.2 km/h held for at least one second."""
    a, b = md.frame_range(period, t0, t1)
    v = md.speeds()
    dist, top, sprints, sprint_frames = 0.0, 0.0, 0, []
    # split the window at substitutions so each part reads the right slot
    cuts = [a] + [md.frame_index(period, tt) for tt, p, _ in md._slot_timeline[1:] if p == period
                  and md.frame_index(period, tt) in range(a + 1, b)] + [b]
    for s0, s1 in zip(cuts, cuts[1:]):
        slot = md.slot_of(player, period, float(md.frames[s0, 1]))
        if slot is None or s1 <= s0:
            continue
        seg = v[s0:s1, slot]
        dist += float(seg.sum()) / 3.6 / HZ
        top = max(top, float(_smooth_max(seg)))
        fast = seg >= SPRINT_KMH
        run = 0
        for k, f in enumerate(fast):
            run = run + 1 if f else 0
            if run == HZ:
                sprints += 1
                sprint_frames.append(s0 + k)
    return Fact("player_physical", dict(player=player, period=period, t0=t0, t1=t1), sprints, "sprints",
                f"{md.name(player)} sprints", frames=(a, b),
                detail=dict(distance_m=round(dist), top_speed_kmh=round(top, 1),
                            sprint_frames=sprint_frames[:50]))


def _smooth_max(seg: np.ndarray) -> float:
    """Top speed as the best 0.5 s average, so one noisy frame cannot set it."""
    if len(seg) < 5:
        return float(seg.max()) if len(seg) else 0.0
    return float(np.convolve(seg, np.ones(5) / 5, mode="valid").max())


@stat
def speed_triggers(md: MatchData, period: int, t0: float, t1: float, kmh: float = 30.0) -> Fact:
    """Moments a player first goes over `kmh` (debounced 10 s per player)."""
    a, b = md.frame_range(period, t0, t1)
    v = md.speeds()[a + 1:b]
    hits, last = [], {}
    for k in range(v.shape[0]):
        for s in np.nonzero(v[k] > kmh)[0]:
            t = float(md.frames[a + k + 1, 1])
            pid = md.slot_players(period, t)[s]
            if last.get(pid, -99) < t - 10:
                last[pid] = t
                hits.append(dict(player=pid, t=round(t, 1), kmh=round(float(v[k, s]), 1)))
    return Fact("speed_triggers", dict(period=period, t0=t0, t1=t1, kmh=kmh), len(hits), "triggers",
                f"players over {kmh:g} km/h", frames=(a, b), detail=dict(hits=hits))


# ---------------------------------------------------------------- team shape and pressure

@stat
def ppda(md: MatchData, team: str, period: int, t0: float, t1: float) -> Fact:
    """Passes allowed per defensive action: opponent passes in their own 60%
    divided by our tackles, interceptions and fouls in the same area. Lower = more intense press."""
    opp = md.opponent(team)
    opp_passes = []
    for e in md.window(period, t0, t1, ("pass",), opp):
        x, _ = md.to_team(opp, e["x"], e["y"])
        if x < 0.6 * PITCH_LEN and not e.get("restart"):
            opp_passes.append(e)
    actions = []
    for e in md.window(period, t0, t1, ("tackle", "interception", "foul"), team):
        x, _ = md.to_team(team, e["x"], e["y"])
        if x > 0.4 * PITCH_LEN:
            actions.append(e)
    val = round(len(opp_passes) / len(actions), 1) if actions else float(len(opp_passes))
    return Fact("ppda", dict(team=team, period=period, t0=t0, t1=t1), val, "passes per action",
                f"{_short(md, team)} PPDA", [e["id"] for e in actions],
                detail=dict(opp_passes=len(opp_passes), defensive_actions=len(actions)))


@stat
def pressures(md: MatchData, team: str, period: int, t0: float, t1: float) -> Fact:
    ev = md.window(period, t0, t1, ("pressure",), team)
    high = [e for e in ev if md.to_team(team, e["x"], e["y"])[0] > 0.5 * PITCH_LEN]
    mins = max((t1 - t0) / 60.0, 1e-6)
    return Fact("pressures", dict(team=team, period=period, t0=t0, t1=t1), round(len(ev) / mins, 2),
                "per min", f"{_short(md, team)} pressures per minute", [e["id"] for e in ev],
                detail=dict(total=len(ev), in_opp_half=len(high)))


@stat
def field_tilt(md: MatchData, team: str, period: int, t0: float, t1: float) -> Fact:
    """Share of all final-third passes that are ours."""
    mine, theirs = [], []
    for e in md.window(period, t0, t1, ("pass",)):
        x, _ = md.to_team(e["team"], e["x"], e["y"])
        if x > FINAL_THIRD_X:
            (mine if e["team"] == team else theirs).append(e)
    n = len(mine) + len(theirs)
    return Fact("field_tilt", dict(team=team, period=period, t0=t0, t1=t1),
                round(100.0 * len(mine) / n, 1) if n else 50.0, "%", f"{_short(md, team)} field tilt",
                [e["id"] for e in mine], detail=dict(ours=len(mine), theirs=len(theirs)))


@stat
def defensive_line(md: MatchData, team: str, period: int, t0: float, t1: float) -> Fact:
    """Mean height of the back four (metres from own goal) while out of possession."""
    a, b = md.frame_range(period, t0, t1)
    poss = possession_mask(md, team, period, a, b)
    sl = _own_slots(md, team)
    xs = md.frames[a:b, 5:].reshape(-1, 22, 2)[:, sl][:, 1:5, 0]
    if md.side(team) == 1:
        xs = PITCH_LEN - xs
    out = xs[~poss]
    val = round(float(out.mean()), 1) if len(out) else 0.0
    return Fact("defensive_line", dict(team=team, period=period, t0=t0, t1=t1), val, "m",
                f"{_short(md, team)} defensive line height", frames=(a, b))


def possession_mask(md: MatchData, team: str, period: int, a: int, b: int) -> np.ndarray:
    """Per frame: is `team` in possession (from possession_change events)."""
    changes = [(e["t"], e["team"]) for e in md.events if e["period"] == period
               and e["type"] in ("possession_change", "period_start")]
    clock = md.frames[a:b, 1]
    owner = np.full(len(clock), "", dtype=object)
    cur = changes[0][1] if changes else md.home
    j = 0
    for k, t in enumerate(clock):
        while j < len(changes) and changes[j][0] <= t:
            cur = changes[j][1]
            j += 1
        owner[k] = cur
    return owner == team


@stat
def possession(md: MatchData, team: str, period: int, t0: float, t1: float) -> Fact:
    a, b = md.frame_range(period, t0, t1)
    m = possession_mask(md, team, period, a, b)
    return Fact("possession", dict(team=team, period=period, t0=t0, t1=t1),
                round(100.0 * float(m.mean()), 1) if len(m) else 50.0, "%", f"{_short(md, team)} possession",
                frames=(a, b))


@stat
def tempo(md: MatchData, team: str, period: int, t0: float, t1: float) -> Fact:
    """Passes per minute of own possession."""
    a, b = md.frame_range(period, t0, t1)
    poss_min = float(possession_mask(md, team, period, a, b).sum()) / HZ / 60.0
    ev = md.window(period, t0, t1, ("pass",), team)
    val = round(len(ev) / poss_min, 1) if poss_min > 0.5 else 0.0
    return Fact("tempo", dict(team=team, period=period, t0=t0, t1=t1), val, "passes per possession minute",
                f"{_short(md, team)} tempo", [e["id"] for e in ev], frames=(a, b),
                detail=dict(passes=len(ev), possession_minutes=round(poss_min, 1)))


# ---------------------------------------------------------------- flanks

def channel_of(y_team: float) -> str:
    for name, (lo, hi) in CHANNELS.items():
        if lo <= y_team < hi:
            return name
    return "right"


@stat
def flank_share(md: MatchData, team: str, period: int, t0: float, t1: float, side: str) -> Fact:
    """Share of our final-third passes and crosses that start in a channel (our own left/right)."""
    pool, hits = [], []
    for e in md.window(period, t0, t1, ("pass", "cross"), team):
        x, y = md.to_team(team, e["x"], e["y"])
        if x > FINAL_THIRD_X - 10:
            pool.append(e)
            if channel_of(float(y)) == side:
                hits.append(e)
    return Fact("flank_share", dict(team=team, period=period, t0=t0, t1=t1, side=side),
                round(100.0 * len(hits) / len(pool), 1) if pool else 0.0, "%",
                f"{_short(md, team)} attacks down the {side}", [e["id"] for e in hits],
                detail=dict(in_channel=len(hits), final_third_actions=len(pool)))


@stat
def overloads(md: MatchData, team: str, period: int, t0: float, t1: float, side: str) -> Fact:
    """Episodes where we have more players than them in a flank channel near the
    ball, in their half, while we have the ball (sampled each second)."""
    a, b = md.frame_range(period, t0, t1)
    poss = possession_mask(md, team, period, a, b)
    lo, hi = CHANNELS[side]
    own, opp = _own_slots(md, team), _opp_slots(md, team)
    episodes, last_t, worst = [], -99.0, (0, 0)
    for k in range(0, b - a, HZ):
        if not poss[k]:
            continue
        i = a + k
        bx, by = md.to_team(team, *md.ball(i)[:2])
        if bx < 55 or not (lo <= by < hi):
            continue
        pos = md.positions(i)
        mx, my = md.to_team(team, pos[own][1:, 0], pos[own][1:, 1])
        ox, oy = md.to_team(team, pos[opp][1:, 0], pos[opp][1:, 1])
        zone = lambda x, y: (y >= lo - 3) & (y < hi + 3) & (x > bx - 15) & (x < bx + 20)
        att, dfn = int(zone(mx, my).sum()), int(zone(ox, oy).sum())
        t = float(md.frames[i, 1])
        if att >= 3 and att > dfn:
            if t - last_t > 6:
                episodes.append(dict(t=round(t, 1), frame=i, attackers=att, defenders=dfn))
                if att - dfn > worst[0] - worst[1]:
                    worst = (att, dfn)
            last_t = t
    return Fact("overloads", dict(team=team, period=period, t0=t0, t1=t1, side=side), len(episodes),
                "episodes", f"{_short(md, team)} overloads on the {side}", frames=(a, b),
                detail=dict(episodes=episodes, biggest=f"{worst[0]} v {worst[1]}" if worst[0] else None))


# ---------------------------------------------------------------- chances and momentum

@stat
def shots(md: MatchData, team: str, period: int, t0: float, t1: float) -> Fact:
    ev = md.window(period, t0, t1, ("shot",), team)
    xg = round(sum(e["xg"] for e in ev), 2)
    return Fact("shots", dict(team=team, period=period, t0=t0, t1=t1), len(ev), "shots",
                f"{_short(md, team)} shots", [e["id"] for e in ev],
                detail=dict(xg=xg, on_target=sum(e["outcome"] in ("goal", "saved") for e in ev),
                            goals=sum(e["outcome"] == "goal" for e in ev)))


@stat
def shot_xg(md: MatchData, event_id: str) -> Fact:
    ev = md.by_id[event_id]
    return Fact("shot_xg", dict(event_id=event_id), ev["xg"], "xG", f"chance quality, {md.name(ev['player'])}",
                [event_id], detail=dict(outcome=ev["outcome"], minute=ev["minute"]))


@stat
def momentum(md: MatchData, period: int, t0: float, t1: float, step_s: float = 60.0) -> Fact:
    """Per-minute threat for each side: xG plus a small value for final-third
    passes and crosses. Positive = home on top."""
    series = []
    t = t0
    while t < t1:
        v = 0.0
        for e in md.window(period, t, t + step_s, ("shot", "pass", "cross")):
            sign = 1 if e["team"] == md.home else -1
            if e["type"] == "shot":
                v += sign * e["xg"]
            else:
                x, _ = md.to_team(e["team"], e.get("end_x", e["x"]), e.get("end_y", e["y"]))
                if x > FINAL_THIRD_X and e.get("outcome") in ("complete", "header_shot", "cleared", "claimed"):
                    v += sign * (0.015 if e["type"] == "pass" else 0.03)
        series.append(round(v, 3))
        t += step_s
    return Fact("momentum", dict(period=period, t0=t0, t1=t1, step_s=step_s), round(sum(series), 3),
                "threat (home +)", "momentum", detail=dict(series=series))


def _short(md: MatchData, team: str) -> str:
    return md.meta["home"]["short"] if team == md.home else md.meta["away"]["short"]
