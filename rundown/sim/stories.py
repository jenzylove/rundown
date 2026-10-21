"""Planted tactical stories: the hidden answer key.

Each seed plants a few stories that change how the simulator behaves from a
given minute. They are exported separately (answer_key.json) and must never be
shown to the agents. Evals score the rundown against them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

KINDS = ("press_switch", "flank_overload", "fatigue", "sit_deep", "tempo_up")


@dataclass
class Story:
    id: str
    kind: str
    team: str  # club code
    start_minute: float
    end_minute: float
    side: str | None = None  # "left"/"right" (team's own frame) for flank stories
    player: str | None = None  # player id for fatigue
    magnitude: float = 1.0
    description: str = ""

    def active(self, minute: float) -> bool:
        return self.start_minute <= minute < self.end_minute

    def to_dict(self) -> dict:
        return asdict(self)


def plant(rng: np.random.Generator, home, away) -> list[Story]:
    """Two or three stories in the first half, two in the second."""
    stories: list[Story] = []
    windows = [(8, 30, 45), (8, 30, 45), (55, 75, 90), (55, 75, 90)]
    if rng.random() < 0.6:
        windows.insert(2, (20, 35, 45))
    kinds = list(rng.permutation(KINDS))
    for n, (lo, hi, end) in enumerate(windows):
        kind = kinds[n % len(kinds)]
        club = home if rng.random() < 0.5 else away
        start = float(np.round(rng.uniform(lo, hi), 1))
        s = Story(id=f"s{n + 1}", kind=kind, team=club.code, start_minute=start, end_minute=float(end))
        if kind == "press_switch":
            s.magnitude = float(np.round(rng.uniform(0.82, 0.95), 2))
            s.description = f"{club.short} switch to a high press from {start:.0f}'"
        elif kind == "flank_overload":
            s.side = "right" if rng.random() < 0.5 else "left"
            s.magnitude = float(np.round(rng.uniform(0.7, 1.0), 2))
            s.description = f"{club.short} overload their {s.side} flank from {start:.0f}'"
        elif kind == "fatigue":
            outfield = [p for p in club.players[:11] if p.role in ("RB", "LB", "RW", "LW", "RCM", "LCM")]
            p = outfield[rng.integers(len(outfield))]
            s.player = p.id
            s.magnitude = float(np.round(rng.uniform(0.72, 0.82), 2))  # speed multiplier
            s.start_minute = float(np.round(rng.uniform(max(lo, 30), hi), 1)) if end == 45 else start
            s.description = f"{p.name} ({club.short} {p.role}) tires from {s.start_minute:.0f}'"
        elif kind == "sit_deep":
            s.magnitude = float(np.round(rng.uniform(0.1, 0.2), 2))
            s.description = f"{club.short} drop into a deep block from {start:.0f}'"
        elif kind == "tempo_up":
            s.magnitude = float(np.round(rng.uniform(0.85, 0.95), 2))
            s.description = f"{club.short} raise the tempo from {start:.0f}'"
        stories.append(s)
    # one story per (kind, team, half) at most
    seen, unique = set(), []
    for s in stories:
        key = (s.kind, s.team, s.end_minute)
        if key not in seen:
            seen.add(key)
            unique.append(s)
    for i, s in enumerate(unique):
        s.id = f"s{i + 1}"
    return unique


def tactic_at(base: dict[str, float], code: str, minute: float, stories: list[Story]) -> dict[str, float]:
    t = dict(base)
    for s in stories:
        if s.team != code or not s.active(minute):
            continue
        if s.kind == "press_switch":
            t["press"] = s.magnitude
            t["depth"] = max(t["depth"], 0.7)
        elif s.kind == "flank_overload":
            t["width"] = s.magnitude if s.side == "right" else -s.magnitude
        elif s.kind == "sit_deep":
            t["depth"] = s.magnitude
            t["press"] = min(t["press"], 0.15)
        elif s.kind == "tempo_up":
            t["tempo"] = s.magnitude
            t["directness"] = min(1.0, t["directness"] + 0.15)
    return t


def speed_mult(player_id: str, minute: float, stories: list[Story]) -> float:
    m = 1.0
    for s in stories:
        if s.kind == "fatigue" and s.player == player_id and s.active(minute):
            m = min(m, s.magnitude)
    return m
