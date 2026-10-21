"""The match simulator.

Absolute pitch coordinates: x 0..105 (home attacks +x for the whole match),
y 0..68. Each team also has a *team frame* (see teams.py); for the away side it
is the absolute frame rotated 180 degrees.

The model works at two levels:
- Off the ball, every player moves toward a target derived from their slot,
  the ball, their team's phase and the live tactic, with speed and accel limits.
- On the ball, the carrier decides (pass, carry, cross, shot), the outcome is
  drawn from position, pressure and skill, and the ball travels in real time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from .stories import Story, plant, speed_mult, tactic_at
from .teams import FORMATION_433, PITCH_LEN, PITCH_WID, ROLES, Club, Player, build_club

HZ = 10
DT = 1.0 / HZ
GOAL_Y = (30.34, 37.66)

ROLE_IDX = {r: i for i, r in enumerate(ROLES)}
# how far each role drifts toward an overloaded flank, in metres
WIDTH_DRIFT = np.array([0, 4, 3, 3, 4, 6, 9, 8, 6, 9, 12], dtype=float)
ATTACKERS = {"RW", "LW", "ST"}
WIDE_ROLES = np.array([r in ("RB", "LB", "RW", "LW") for r in ROLES])
BACK_FOUR = [1, 2, 3, 4]


def xg_model(x: float, y: float, header: bool = False) -> float:
    """Expected goals from team-frame shot location (attacking toward x=105)."""
    dx, dy = PITCH_LEN - x, y - 34.0
    d = math.hypot(dx, dy)
    a1 = math.atan2(GOAL_Y[1] - y, max(dx, 0.1))
    a0 = math.atan2(GOAL_Y[0] - y, max(dx, 0.1))
    angle = abs(a1 - a0)
    xg = 1.0 / (1.0 + math.exp(-(-0.7 + 1.8 * angle - 0.11 * d)))
    return float(xg * (0.5 if header else 1.0))


def flip(side: int, p):
    """Team frame <-> absolute frame (an involution)."""
    if side == 0:
        return np.asarray(p, dtype=float)
    p = np.asarray(p, dtype=float)
    return np.array([PITCH_LEN - p[..., 0], PITCH_WID - p[..., 1]]).T if p.ndim > 1 else \
        np.array([PITCH_LEN - p[0], PITCH_WID - p[1]])


def seg_dist(p, a, b) -> float:
    ab = b - a
    L2 = float(ab @ ab)
    if L2 < 1e-9:
        return float(np.linalg.norm(p - a))
    u = max(0.0, min(1.0, float((p - a) @ ab) / L2))
    return float(np.linalg.norm(p - (a + u * ab)))


@dataclass
class Flight:
    start: np.ndarray
    end: np.ndarray
    t0: float
    dur: float
    peak: float
    on_arrive: str  # "receive" | "intercept" | "out" | "loose" | "shot" | "cross"
    actor: int  # receiving / intercepting slot (or -1)
    data: dict = field(default_factory=dict)


@dataclass
class Match:
    seed: int
    home: Club
    away: Club
    stories: list[Story]
    events: list[dict] = field(default_factory=list)
    frames: np.ndarray | None = None  # [N, 5 + 44]: period, clock, bx, by, bz, 22 x (x, y)
    lineup_changes: list[dict] = field(default_factory=list)
    score: list[int] = field(default_factory=lambda: [0, 0])

    def meta(self) -> dict:
        def club(c: Club):
            return dict(code=c.code, name=c.name, short=c.short, colour=c.colour,
                        players=[dict(id=p.id, name=p.name, number=p.number, role=p.role) for p in c.players],
                        starters=[p.id for p in c.players[:11]])
        return dict(seed=self.seed, hz=HZ, pitch=[PITCH_LEN, PITCH_WID], home_attacks="+x",
                    home=club(self.home), away=club(self.away), score=self.score,
                    frame_layout="period, clock_s, ball_x, ball_y, ball_z, then slot0_x, slot0_y ... slot21_x, slot21_y; "
                                 "slots 0-10 home, 11-21 away in formation order; see substitution events for slot changes")

    def answer_key(self) -> list[dict]:
        return [s.to_dict() for s in self.stories]


class Sim:
    def __init__(self, seed: int, home: str = "HAR", away: str = "EMA", period_minutes: float = 45.0):
        self.rng = np.random.default_rng(seed)
        used: set[str] = set()
        self.home = build_club(home, self.rng, used)
        self.away = build_club(away, self.rng, used)
        self.clubs = (self.home, self.away)
        self.stories = plant(self.rng, self.home, self.away)
        self.period_minutes = period_minutes
        self.match = Match(seed=seed, home=self.home, away=self.away, stories=self.stories)

        self.slot_player: list[Player] = self.home.players[:11] + self.away.players[:11]
        self.bench = [list(self.home.players[11:]), list(self.away.players[11:])]
        self.subs_left = [3, 3]
        self.base_tf = np.array([[x, y] for _, x, y in FORMATION_433] * 2, dtype=float)
        self.pos = np.vstack([flip(0, self.base_tf[:11]), flip(1, self.base_tf[11:])])
        self.vel = np.zeros((22, 2))
        self.ball = np.array([52.5, 34.0, 0.0])
        self.mode = "dead"
        self.carrier = -1
        self.flight: Flight | None = None
        self.poss = 0
        self.next_decision = 0.0
        self.carry_to: np.ndarray | None = None
        self.carry_start: np.ndarray | None = None
        self.carry_t0 = 0.0
        self.restart: tuple[float, int, str] | None = None
        self.presser: list[int] = []
        self.period = 1
        self.clock = 0.0
        self.elapsed = 0.0
        self.eid = 0
        self.last_press: dict[tuple[int, int], float] = {}
        self.tactics = [dict(c.style) for c in self.clubs]
        self.sub_plan = self._plan_subs()

    # ---------- helpers ----------
    def side(self, slot: int) -> int:
        return 0 if slot < 11 else 1

    def team_slots(self, side: int) -> range:
        return range(0, 11) if side == 0 else range(11, 22)

    def tf(self, side: int, p):
        return flip(side, p)

    @property
    def minute(self) -> float:
        return self.clock / 60.0

    def emit(self, type_: str, slot: int | None = None, **kw) -> dict:
        self.eid += 1
        if slot is not None and slot >= 0:
            team = self.clubs[self.side(slot)].code
            player = self.slot_player[slot].id
            x, y = self.pos[slot]
        else:
            team, player = kw.pop("team", None), None
            x, y = self.ball[0], self.ball[1]
        ev = dict(id=f"e{self.eid:05d}", t=round(self.clock, 1), period=self.period,
                  minute=round(self.minute, 2), type=type_, team=team, player=player,
                  x=round(float(kw.pop("x", x)), 2), y=round(float(kw.pop("y", y)), 2))
        for k, v in kw.items():
            ev[k] = round(v, 3) if isinstance(v, float) else v
        self.match.events.append(ev)
        return ev

    def nearest(self, side: int, p, exclude_gk: bool = True) -> tuple[int, float]:
        best, bd = -1, 1e9
        for i in self.team_slots(side):
            if exclude_gk and i % 11 == 0:
                continue
            d = float(np.linalg.norm(self.pos[i] - p))
            if d < bd:
                best, bd = i, d
        return best, bd

    def speed_of(self, slot: int) -> float:
        p = self.slot_player[slot]
        global_fatigue = 1.0 - (1.0 - p.stamina) * 0.25 * min(self.minute, 95) / 90.0
        return p.pace * global_fatigue * speed_mult(p.id, self.minute, self.stories)

    def set_poss(self, side: int, cause: dict | None = None):
        if side != self.poss:
            self.poss = side
            self.emit("possession_change", team=self.clubs[side].code,
                      cause=cause["id"] if cause else None)

    # ---------- substitutions ----------
    def _plan_subs(self) -> list[tuple[float, int]]:
        plan = []
        for side in (0, 1):
            for _ in range(3):
                plan.append((float(self.rng.uniform(56, 82)), side))
        return sorted(plan)

    def do_subs(self):
        while self.sub_plan and self.period == 2 and self.minute >= self.sub_plan[0][0] and self.mode == "dead":
            _, side = self.sub_plan.pop(0)
            if not self.subs_left[side] or not self.bench[side]:
                continue
            slots = list(self.team_slots(side))[1:]
            # most tired outfield player goes off; fatigue stories make that visible late
            off = min(slots, key=lambda s: self.speed_of(s) / self.slot_player[s].pace
                      + (0.05 if self.slot_player[s].role in ("RCB", "LCB") else 0))
            role = self.slot_player[off].role
            on = next((b for b in self.bench[side] if b.role == role), None) or \
                next((b for b in self.bench[side] if b.role != "GK"), None)
            if on is None:
                continue
            self.bench[side].remove(on)
            old = self.slot_player[off]
            self.emit("substitution", off, player_on=on.id, slot_index=off)
            self.slot_player[off] = Player(**{**on.__dict__, "role": role})
            self.subs_left[side] -= 1
            self.match.lineup_changes.append(dict(t=self.clock, slot=off, off=old.id, on=on.id))

    # ---------- off-ball movement ----------
    def targets(self) -> tuple[np.ndarray, np.ndarray]:
        tgt = np.zeros((22, 2))
        urgency = np.full(22, 0.55)
        for side in (0, 1):
            tac = self.tactics[side]
            sl = slice(0, 11) if side == 0 else slice(11, 22)
            base = self.base_tf[sl]
            b = self.tf(side, self.ball[:2])
            in_poss = self.poss == side and self.mode != "dead"
            opp_tf = self.tf(side, self.pos[slice(11, 22) if side == 0 else slice(0, 11)])
            opp_line = float(np.sort(opp_tf[1:, 0])[-2]) if len(opp_tf) > 2 else 70.0  # 2nd last defender
            if in_poss:
                tx = base[:, 0] * 0.6 + b[0] * 0.42 + 4.0
                tx[[8, 9, 10]] += 4.0
                pull = np.where(WIDE_ROLES, 0.1, 0.26)  # wide players hold the touchline
                ty = base[:, 1] * (1 - pull) + b[1] * pull + WIDTH_DRIFT * tac["width"]
                if tac["width"] > 0.2:
                    tx[1] += 10 * tac["width"]
                elif tac["width"] < -0.2:
                    tx[4] += 10 * -tac["width"]
                tx = np.minimum(tx, max(opp_line + 0.5, b[0]))
            else:
                shift = -10.0 + 16.0 * tac["depth"] + 6.0 * tac["press"]
                tx = base[:, 0] * 0.55 + b[0] * 0.38 + shift
                ty = base[:, 1] * 0.6 + b[1] * 0.4
                tx[BACK_FOUR] = np.maximum(tx[BACK_FOUR], 10.0)
            # goalkeeper
            tx[0] = np.clip(4.0 + 0.1 * (b[0] - 4.0), 2.0, 16.0)
            ty[0] = 34.0 + 0.18 * (b[1] - 34.0)
            tx = np.clip(tx, 1.0, PITCH_LEN - 1.0)
            ty = np.clip(ty, 1.0, PITCH_WID - 1.0)
            tgt[sl] = self.tf(side, np.stack([tx, ty], axis=1))
            urgency[sl] = 0.8 if not in_poss else 0.6

        # pressing: out-of-possession team engages the ball
        self.presser = []
        if self.mode in ("held", "flight"):
            dside = 1 - self.poss
            tac = self.tactics[dside]
            b_def = self.tf(dside, self.ball[:2])  # distance from the defending team's own goal
            reach = PITCH_LEN * (0.3 + 0.65 * tac["press"])
            order = sorted([i for i in self.team_slots(dside) if i % 11 != 0],
                           key=lambda i: float(np.linalg.norm(self.pos[i] - self.ball[:2])))
            if b_def[0] < reach or float(np.linalg.norm(self.pos[order[0]] - self.ball[:2])) < 8:
                n_press = 2 if tac["press"] > 0.7 else 1
                for k, i in enumerate(order[:n_press]):
                    w = 1.0 if k == 0 else 0.55 + 0.3 * tac["press"]
                    # stand off goal-side unless the press is on
                    own_goal = self.tf(dside, np.array([0.0, 34.0]))
                    to_goal = own_goal - self.ball[:2]
                    screen = self.ball[:2] + to_goal / max(float(np.linalg.norm(to_goal)), 1e-6) * 5.0 * (1 - tac["press"])
                    tgt[i] = tgt[i] * (1 - w) + screen * w
                    urgency[i] = 0.5 + 0.4 * tac["press"]
                    self.presser.append(i)

        if self.mode == "held" and self.carrier >= 0:
            tgt[self.carrier] = self.carry_to if self.carry_to is not None else self.pos[self.carrier]
            urgency[self.carrier] = 0.75
        if self.mode == "flight" and self.flight and self.flight.actor >= 0:
            tgt[self.flight.actor] = self.flight.end
            urgency[self.flight.actor] = 1.0
        if self.mode == "dead" and self.restart:
            _, taker, _ = self.restart
            tgt[taker] = self.ball[:2]
            urgency[taker] = 0.7
        return tgt, urgency

    def move(self):
        tgt, urg = self.targets()
        for i in range(22):
            d = tgt[i] - self.pos[i]
            dist = float(np.linalg.norm(d))
            vmax = self.speed_of(i) * urg[i]
            want = d / dist * min(vmax, dist * 1.4) if dist > 1e-6 else np.zeros(2)
            dv = want - self.vel[i]
            dvn = float(np.linalg.norm(dv))
            if dvn > 5.0 * DT:
                dv *= 5.0 * DT / dvn
            self.vel[i] += dv
            self.vel[i] += self.rng.normal(0, 0.05, 2)
            self.pos[i] = np.clip(self.pos[i] + self.vel[i] * DT, [0.5, 0.5], [PITCH_LEN - 0.5, PITCH_WID - 0.5])

    # ---------- on-ball decisions ----------
    def hold_time(self, side: int, pressured: bool) -> float:
        tempo = self.tactics[side]["tempo"]
        t = (1.7 + 2.4 * (1 - tempo)) * float(self.rng.uniform(0.5, 1.5))
        return t * (0.6 if pressured else 1.0)

    def give(self, slot: int, delay: float):
        self.mode = "held"
        self.carrier = slot
        self.carry_to = None
        self.flight = None
        self.next_decision = self.elapsed + delay

    def decide(self):
        c = self.carrier
        side = self.side(c)
        opp = 1 - side
        tac = self.tactics[side]
        b = self.tf(side, self.ball[:2])
        j, d = self.nearest(opp, self.ball[:2])
        pressured = d < 2.6 and j in self.presser
        if pressured and self.last_press.get((j, c), -99.0) < self.elapsed - 5.0:
            self.last_press[(j, c)] = self.elapsed
            self.emit("pressure", j, on=self.slot_player[c].id)

        # duel
        if d < 2.0 and j in self.presser:
            otac = self.tactics[opp]
            if self.rng.random() < 0.04 + 0.12 * otac["press"]:
                tired = speed_mult(self.slot_player[j].id, self.minute, self.stories) < 0.9
                carrier_tired = speed_mult(self.slot_player[c].id, self.minute, self.stories) < 0.9
                p_win = 0.5 * (0.6 if tired else 1.0) * (1.4 if carrier_tired else 1.0)
                if self.rng.random() < 0.3:
                    ev = self.emit("foul", j, on=self.slot_player[c].id)
                    if self.rng.random() < 0.16:
                        self.emit("card", j, card="yellow", cause=ev["id"])
                    self.dead_ball(side, "free_kick", self.ball[:2].copy(), float(self.rng.uniform(12, 25)))
                    return
                won = self.rng.random() < p_win
                ev = self.emit("tackle", j, outcome="won" if won else "lost", on=self.slot_player[c].id)
                if won:
                    self.set_poss(opp, ev)
                    self.give(j, self.hold_time(opp, False) + 0.3)
                    return

        # shot
        if b[0] > 78:
            xg = xg_model(b[0], b[1])
            if xg > 0.025 and self.rng.random() < min(0.92, 0.15 + 5.0 * xg) * (0.75 if pressured else 1.0):
                self.shoot(c, xg)
                return
        # cross
        if b[0] > 76 and (b[1] < 16 or b[1] > 52) and self.rng.random() < 0.4:
            self.cross(c)
            return
        # carry
        space = d
        p_carry = 0.12 + (0.25 if space > 7 else 0.0) + (0.1 if b[0] < 70 else 0.0)
        if self.carry_to is None and self.rng.random() < p_carry:
            fwd = float(self.rng.uniform(6, 15))
            lat = float(self.rng.normal(0, 4)) + 6 * tac["width"] * (1 if b[1] < 52 else 0)
            to_tf = np.clip(b + np.array([fwd, lat]), [2, 2], [PITCH_LEN - 6, PITCH_WID - 2])
            self.carry_to = self.tf(side, to_tf)
            self.carry_start = self.ball[:2].copy()
            self.carry_t0 = self.elapsed
            self.next_decision = self.elapsed + fwd / 6.0
            return
        self.pass_ball(c, pressured)

    def finish_carry(self):
        c = self.carrier
        if self.carry_start is not None:
            dist = float(np.linalg.norm(self.ball[:2] - self.carry_start))
            if dist > 3:
                self.emit("carry", c, x=self.carry_start[0], y=self.carry_start[1],
                          end_x=round(float(self.ball[0]), 2), end_y=round(float(self.ball[1]), 2),
                          length=round(dist, 2), duration=round(self.elapsed - self.carry_t0, 2))
        self.carry_to = None
        self.carry_start = None

    def pass_ball(self, c: int, pressured: bool, restart: str | None = None, long_bias: float = 0.0):
        side = self.side(c)
        opp = 1 - side
        tac = self.tactics[side]
        b = self.tf(side, self.ball[:2])
        cands, weights = [], []
        opp_pos = self.pos[list(self.team_slots(opp))]
        for k in self.team_slots(side):
            if k == c:
                continue
            p = self.tf(side, self.pos[k])
            dv = p - b
            dist = float(np.linalg.norm(dv))
            if dist < 4 or dist > 62:
                continue
            open_d = float(np.min(np.linalg.norm(opp_pos - self.pos[k], axis=1)))
            lane = min(seg_dist(o, self.ball[:2], self.pos[k]) for o in opp_pos)
            fwd = max(float(dv[0]), 0.0)
            w = math.exp(-dist / (16 + 14 * tac["directness"] + 20 * long_bias))
            w *= 1 + (0.5 + tac["directness"]) * fwd / 18
            w *= 1 / (1 + math.exp(-(open_d - 2.5)))
            w *= 1 / (1 + math.exp(-(lane - 1.5) * 1.5))
            if dv[0] < -5:
                w *= 0.35 + 0.5 * (1 - tac["tempo"])
            if k % 11 == 0:
                w *= 0.15
            w *= max(0.05, 1 + 0.9 * tac["width"] * (p[1] - 34) / 34)
            cands.append((k, dist, open_d, lane))
            weights.append(w)
        if not cands:
            self.carry_to = self.tf(side, np.clip(b + [5, 0], 2, [PITCH_LEN - 3, PITCH_WID - 2]))
            self.next_decision = self.elapsed + 1.0
            return
        wts = np.array(weights)
        k, dist, open_d, lane = cands[int(self.rng.choice(len(cands), p=wts / wts.sum()))]
        passer = self.slot_player[c]
        lofted = dist > 32 or (lane < 1.5 and dist > 18)
        speed = (17 + 0.1 * dist if lofted else 12 + 0.16 * dist) * float(self.rng.uniform(0.92, 1.08))
        lead = self.pos[k] + self.vel[k] * (dist / speed) * 0.8
        lead = np.clip(lead, [0.5, 0.5], [PITCH_LEN - 0.5, PITCH_WID - 0.5])
        p_ok = 1.0 - 0.0045 * dist - (0.07 if pressured else 0) \
            - (0.0 if lofted else 0.22 * math.exp(-lane / 1.1)) - (0.06 if lofted else 0) \
            + 0.3 * (passer.passing - 0.75)
        p_ok = min(0.985, max(0.12, p_ok))
        ok = self.rng.random() < p_ok
        speed_kmh = round(speed * 3.6, 1)
        height = "high" if lofted else "ground"
        if ok:
            ev = self.emit("pass", c, end_x=round(float(lead[0]), 2), end_y=round(float(lead[1]), 2),
                           length=round(float(np.linalg.norm(lead - self.ball[:2])), 2), outcome="complete",
                           recipient=self.slot_player[k].id, height=height, speed_kmh=speed_kmh,
                           under_pressure=pressured, restart=restart)
            self.launch(lead, speed, 6.0 if lofted else 0.0, "receive", k, dict(ev=ev["id"]))
            return
        # failure: interception in lane, or a loose / out of play ball
        best_o, best_lane = -1, 1e9
        for o in self.team_slots(opp):
            ld = seg_dist(self.pos[o], self.ball[:2], lead)
            if ld < best_lane:
                best_o, best_lane = o, ld
        if best_lane < 3.5 and not lofted or self.rng.random() < 0.55:
            a, bb = self.ball[:2], lead
            ab = bb - a
            u = float(np.clip((self.pos[best_o] - a) @ ab / max(ab @ ab, 1e-6), 0.15, 0.95))
            pt = a + u * ab
            ev = self.emit("pass", c, end_x=round(float(pt[0]), 2), end_y=round(float(pt[1]), 2),
                           length=round(float(np.linalg.norm(pt - a)), 2), outcome="intercepted",
                           recipient=self.slot_player[k].id, height=height, speed_kmh=speed_kmh,
                           under_pressure=pressured, restart=restart)
            self.launch(pt, speed, 4.0 if lofted else 0.0, "intercept", best_o, dict(ev=ev["id"]))
        else:
            miss = lead + self.rng.normal(0, 6, 2)
            if self.rng.random() < 0.6:  # overhit or mis-hit out of play
                if min(miss[1], PITCH_WID - miss[1]) < 20 or self.rng.random() < 0.5:
                    miss[1] = -1.0 if miss[1] < 34 else PITCH_WID + 1.0
                else:
                    miss[0] = -1.0 if miss[0] < 52.5 else PITCH_LEN + 1.0
            ev = self.emit("pass", c, end_x=round(float(miss[0]), 2), end_y=round(float(miss[1]), 2),
                           length=round(float(np.linalg.norm(miss - self.ball[:2])), 2), outcome="incomplete",
                           recipient=self.slot_player[k].id, height=height, speed_kmh=speed_kmh,
                           under_pressure=pressured, restart=restart)
            self.launch(miss, speed, 6.0 if lofted else 0.0, "loose", -1, dict(ev=ev["id"]))

    def shoot(self, c: int, xg: float, header: bool = False):
        side = self.side(c)
        opp = 1 - side
        shooter = self.slot_player[c]
        target_tf = np.array([PITCH_LEN, float(self.rng.uniform(GOAL_Y[0] - 2.5, GOAL_Y[1] + 2.5))])
        target = self.tf(side, target_tf)
        blockers = [o for o in self.team_slots(opp) if o % 11 and seg_dist(self.pos[o], self.ball[:2], target) < 1.2]
        p_goal = min(0.95, xg * (0.55 + 0.9 * shooter.finishing))
        r = self.rng.random()
        if blockers and r < 0.35:
            outcome = "blocked"
        elif self.rng.random() < p_goal:
            outcome = "goal"
            target = self.tf(side, np.array([PITCH_LEN + 0.5, float(self.rng.uniform(GOAL_Y[0] + 0.4, GOAL_Y[1] - 0.4))]))
        elif self.rng.random() < 0.45:
            outcome = "saved"
        else:
            outcome = "off_target"
            if GOAL_Y[0] <= target_tf[1] <= GOAL_Y[1]:
                target = self.tf(side, np.array([PITCH_LEN, GOAL_Y[1] + float(self.rng.uniform(0.5, 3))]))
        speed = (14 + 6 * self.rng.random() if header else 22 + 10 * self.rng.random())
        ev = self.emit("shot", c, end_x=round(float(target[0]), 2), end_y=round(float(target[1]), 2),
                       outcome=outcome, xg=round(xg, 3), speed_kmh=round(speed * 3.6, 1),
                       body="head" if header else "foot")
        if outcome == "blocked":
            o = blockers[0]
            pt = self.ball[:2] + (target - self.ball[:2]) * 0.25
            self.launch(pt, speed, 0.5, "loose", -1, dict(ev=ev["id"], blocker=o))
        else:
            self.launch(target, speed, 1.2 if outcome != "off_target" else 2.5, "shot", -1,
                        dict(ev=ev["id"], outcome=outcome, side=side))

    def cross(self, c: int):
        side = self.side(c)
        tgt_tf = np.array([float(self.rng.uniform(92, 100)), float(self.rng.uniform(25, 43))])
        target = self.tf(side, tgt_tf)
        r = self.rng.random()
        outcome = "header_shot" if r < 0.2 else ("cleared" if r < 0.72 else ("claimed" if r < 0.9 else "out"))
        speed = float(self.rng.uniform(18, 23))
        ev = self.emit("cross", c, end_x=round(float(target[0]), 2), end_y=round(float(target[1]), 2),
                       outcome=outcome, speed_kmh=round(speed * 3.6, 1), height="high")
        self.launch(target, speed, 7.0, "cross", -1, dict(ev=ev["id"], outcome=outcome, side=side))

    def launch(self, end, speed, peak, on_arrive, actor, data=None):
        start = self.ball[:2].copy()
        dist = float(np.linalg.norm(np.asarray(end) - start))
        self.mode = "flight"
        self.carrier = -1
        self.carry_to = None
        self.flight = Flight(start, np.asarray(end, dtype=float), self.elapsed, max(dist / speed, 0.15),
                             peak, on_arrive, actor, data or {})

    def arrive(self):
        f = self.flight
        assert f is not None
        self.ball[:2] = f.end
        self.ball[2] = 0.0
        if f.on_arrive == "receive":
            side = self.side(f.actor)
            self.give(f.actor, self.hold_time(side, self.nearest(1 - side, f.end)[1] < 3.5))
        elif f.on_arrive == "intercept":
            ev = self.emit("interception", f.actor, cause=f.data.get("ev"))
            self.set_poss(self.side(f.actor), ev)
            self.give(f.actor, self.hold_time(self.side(f.actor), False) + 0.2)
        elif f.on_arrive == "loose":
            self.loose_ball(f.end, f.data.get("ev"))
        elif f.on_arrive == "shot":
            side, outcome = f.data["side"], f.data["outcome"]
            opp = 1 - side
            if outcome == "goal":
                self.match.score[side] += 1
                self.emit("goal", team=self.clubs[side].code, cause=f.data["ev"],
                          score=f"{self.match.score[0]}-{self.match.score[1]}")
                self.set_poss(opp)
                self.dead_ball(opp, "kickoff", np.array([52.5, 34.0]), float(self.rng.uniform(35, 50)))
            elif outcome == "saved":
                gk = 11 * opp
                self.emit("save", gk, cause=f.data["ev"])
                self.set_poss(opp)
                self.dead_ball(opp, "gk_hold", self.pos[gk].copy(), float(self.rng.uniform(4, 9)))
            else:
                self.set_poss(opp)
                spot = self.tf(opp, np.array([5.5, 34.0 + float(self.rng.uniform(-9, 9))]))
                self.dead_ball(opp, "goal_kick", spot, float(self.rng.uniform(10, 18)))
        elif f.on_arrive == "cross":
            side, outcome = f.data["side"], f.data["outcome"]
            opp = 1 - side
            if outcome == "header_shot":
                k, _ = self.nearest(side, f.end)
                self.ball[:2] = self.pos[k]  # the header is taken where the attacker meets it
                self.carrier = k
                b = self.tf(side, f.end)
                self.shoot(k, xg_model(b[0], b[1], header=True), header=True)
            elif outcome == "cleared":
                k, _ = self.nearest(opp, f.end)
                ev = self.emit("clearance", k, cause=f.data["ev"])
                land = self.tf(opp, np.array([float(self.rng.uniform(25, 45)), float(self.rng.uniform(8, 60))]))
                self.launch(land, 20.0, 9.0, "loose", -1, dict(ev=ev["id"]))
            elif outcome == "claimed":
                gk = 11 * opp
                self.ball[:2] = self.pos[gk]
                ev = self.emit("claim", gk, cause=f.data["ev"])
                self.set_poss(opp, ev)
                self.dead_ball(opp, "gk_hold", f.end.copy(), float(self.rng.uniform(4, 8)))
            else:
                k, _ = self.nearest(side, self.tf(side, np.array([104.0, 1.0])))
                corner = self.tf(side, np.array([104.5, 0.5 if f.end[1] < 34 else 67.5]))
                self.set_poss(side)
                self.dead_ball(side, "corner", corner, float(self.rng.uniform(18, 30)))

    def loose_ball(self, at, cause):
        if not (0 < at[0] < PITCH_LEN and 0 < at[1] < PITCH_WID):
            # out of play: last touch was the attacking side in possession
            side = 1 - self.poss
            if at[1] <= 0 or at[1] >= PITCH_WID:
                spot = np.array([np.clip(at[0], 1, PITCH_LEN - 1), 0.2 if at[1] <= 0 else PITCH_WID - 0.2])
                self.set_poss(side)
                self.dead_ball(side, "throw_in", spot, float(self.rng.uniform(7, 14)))
            else:
                self.set_poss(side)
                spot = self.tf(side, np.array([5.5, 34.0]))
                self.dead_ball(side, "goal_kick", spot, float(self.rng.uniform(10, 18)))
            return
        h, dh = self.nearest(0, at, exclude_gk=False)
        a, da = self.nearest(1, at, exclude_gk=False)
        winner = h if dh + self.rng.normal(0, 1.5) < da else a
        self.ball[:2] = at
        ev = self.emit("recovery", winner, cause=cause)
        self.set_poss(self.side(winner), ev)
        self.give(winner, self.hold_time(self.side(winner), False) + 0.4)

    def dead_ball(self, side: int, kind: str, spot, wait: float):
        self.mode = "dead"
        self.carrier = -1
        self.flight = None
        self.carry_to = None
        self.ball[:2] = np.clip(spot, [0, 0], [PITCH_LEN, PITCH_WID])
        self.ball[2] = 0.0
        if kind in ("gk_hold", "goal_kick"):
            taker = 11 * side
        elif kind == "kickoff":
            taker = 11 * side + ROLE_IDX["ST"]
        elif kind == "corner":
            taker = 11 * side + (ROLE_IDX["RW"] if self.tf(side, spot)[1] > 34 else ROLE_IDX["LW"])
        elif kind == "throw_in":
            fb = [11 * side + 1, 11 * side + 4]
            taker = min(fb, key=lambda s: float(np.linalg.norm(self.pos[s] - spot)))
        else:
            taker = 11 * side + ROLE_IDX["DM"]
        self.restart = (self.elapsed + wait, taker, kind)

    def take_restart(self):
        _, taker, kind = self.restart
        self.restart = None
        side = self.side(taker)
        self.ball[:2] = self.pos[taker]  # restart taken where the taker stands (within a few metres)
        self.carrier = taker
        if kind == "corner":
            self.cross(taker)
        elif kind == "kickoff":
            mates = [11 * side + ROLE_IDX["LCM"], 11 * side + ROLE_IDX["RCM"]]
            k = mates[int(self.rng.integers(2))]
            dist = float(np.linalg.norm(self.pos[k] - self.ball[:2]))
            ev = self.emit("pass", taker, end_x=round(float(self.pos[k][0]), 2), end_y=round(float(self.pos[k][1]), 2),
                           length=round(dist, 2), outcome="complete", recipient=self.slot_player[k].id,
                           height="ground", speed_kmh=round(12 * 3.6, 1), under_pressure=False, restart="kickoff")
            self.launch(self.pos[k].copy(), 12.0, 0.0, "receive", k, dict(ev=ev["id"]))
        else:
            long_bias = 0.6 * self.tactics[side]["directness"] if kind in ("goal_kick", "gk_hold") else 0.0
            self.pass_ball(taker, False, restart=kind, long_bias=long_bias)

    # ---------- main loop ----------
    def ball_update(self):
        if self.mode == "flight":
            f = self.flight
            u = (self.elapsed - f.t0) / f.dur
            if u >= 1.0:
                self.arrive()
            else:
                self.ball[:2] = f.start + (f.end - f.start) * u
                self.ball[2] = 4 * f.peak * u * (1 - u)
        elif self.mode == "held":
            c = self.carrier
            side = self.side(c)
            fwd = np.array([0.5, 0.0]) if side == 0 else np.array([-0.5, 0.0])
            self.ball[:2] = self.pos[c] + fwd
            self.ball[2] = 0.0
            if self.carry_to is not None and (np.linalg.norm(self.pos[c] - self.carry_to) < 1.0
                                              or self.elapsed >= self.next_decision):
                self.finish_carry()
                self.next_decision = self.elapsed
            if self.elapsed >= self.next_decision and self.carry_to is None:
                self.decide()
        elif self.mode == "dead":
            if self.restart and self.elapsed >= self.restart[0]:
                taker = self.restart[1]
                if np.linalg.norm(self.pos[taker] - self.ball[:2]) < 2.5 or self.elapsed >= self.restart[0] + 15:
                    self.take_restart()

    def run_period(self, period: int) -> Iterator[np.ndarray]:
        self.period = period
        stoppage = float(self.rng.uniform(1, 4)) * 60
        length = self.period_minutes * 60 + stoppage
        start_clock = 0.0 if period == 1 else self.period_minutes * 60
        self.elapsed = 0.0
        self.clock = start_clock
        self.pos = np.vstack([flip(0, self.base_tf[:11]), flip(1, self.base_tf[11:])])
        self.vel[:] = 0
        kick = 0 if period == 1 else 1
        self.poss = kick
        self.emit("period_start", team=self.clubs[kick].code, added_time_s=round(stoppage))
        self.dead_ball(kick, "kickoff", np.array([52.5, 34.0]), 1.0)
        tick_frame = np.zeros(5 + 44, dtype=np.float32)
        while self.elapsed < length or self.mode == "flight":
            if self.elapsed % 10 < DT:
                for side in (0, 1):
                    self.tactics[side] = tactic_at(self.clubs[side].style, self.clubs[side].code,
                                                   self.minute, self.stories)
                self.do_subs()
            self.move()
            self.ball_update()
            tick_frame[0] = period
            tick_frame[1] = self.clock
            tick_frame[2:5] = self.ball
            tick_frame[5:] = self.pos.reshape(-1)
            yield tick_frame.copy()
            self.elapsed += DT
            self.clock = start_clock + self.elapsed
            if self.elapsed > length + 20:
                break
        self.emit("period_end", team=None)

    def run(self) -> Iterator[np.ndarray]:
        yield from self.run_period(1)
        yield from self.run_period(2)
        self.emit("match_end", team=None, score=f"{self.match.score[0]}-{self.match.score[1]}")


def simulate(seed: int, home: str = "HAR", away: str = "EMA", period_minutes: float = 45.0) -> Match:
    sim = Sim(seed, home, away, period_minutes)
    frames = list(sim.run())
    sim.match.frames = np.stack(frames)
    return sim.match
