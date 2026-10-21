"""Match data access: events, tracking frames and lineups, with team-frame helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

PITCH_LEN = 105.0
PITCH_WID = 68.0
HZ = 10


@dataclass
class MatchData:
    meta: dict
    events: list[dict]
    frames: np.ndarray  # [N, 49]
    _period_index: dict[int, tuple[int, int]] = field(default_factory=dict, repr=False)
    _speeds: np.ndarray | None = field(default=None, repr=False)

    def __post_init__(self):
        for p in (1, 2):
            idx = np.nonzero(self.frames[:, 0] == p)[0]
            if len(idx):
                self._period_index[p] = (int(idx[0]), int(idx[-1]) + 1)
        self.home = self.meta["home"]["code"]
        self.away = self.meta["away"]["code"]
        self.by_id = {e["id"]: e for e in self.events}
        self.players = {p["id"]: {**p, "team": c["code"]}
                        for c in (self.meta["home"], self.meta["away"]) for p in c["players"]}
        self._slot_timeline = self._build_slots()

    # ---- teams and frames ----
    def side(self, team: str) -> int:
        return 0 if team == self.home else 1

    def opponent(self, team: str) -> str:
        return self.away if team == self.home else self.home

    def to_team(self, team: str, x, y):
        """Absolute -> team frame (team attacks toward x=105)."""
        if self.side(team) == 0:
            return x, y
        return PITCH_LEN - np.asarray(x), PITCH_WID - np.asarray(y)

    def frame_index(self, period: int, t: float) -> int:
        a, b = self._period_index[period]
        start = float(self.frames[a, 1])
        i = a + int(round((t - start) * HZ))
        return max(a, min(b - 1, i))

    def frame_range(self, period: int, t0: float, t1: float) -> tuple[int, int]:
        return self.frame_index(period, t0), self.frame_index(period, t1) + 1

    def speeds(self) -> np.ndarray:
        """Per-frame speed of each slot in km/h ([N, 22]); frame-to-frame jumps that
        are not physically possible (period starts) are set to 0."""
        if self._speeds is None:
            xy = self.frames[:, 5:].reshape(-1, 22, 2)
            step = np.zeros((len(xy), 22))
            step[1:] = np.linalg.norm(np.diff(xy, axis=0), axis=2)
            step[1:][self.frames[1:, 0] != self.frames[:-1, 0]] = 0.0
            step[step > 1.2] = 0.0  # > 43 km/h: a reset, not running
            self._speeds = step * HZ * 3.6
        return self._speeds

    def positions(self, i: int) -> np.ndarray:
        return self.frames[i, 5:].reshape(22, 2)

    def ball(self, i: int) -> np.ndarray:
        return self.frames[i, 2:5]

    # ---- lineups over time ----
    def _build_slots(self) -> list[tuple[float, int, list[str]]]:
        slots = self.meta["home"]["starters"] + self.meta["away"]["starters"]
        timeline = [(0.0, 1, list(slots))]
        for e in self.events:
            if e["type"] == "substitution":
                slots = list(slots)
                slots[e["slot_index"]] = e["player_on"]
                timeline.append((e["t"], e["period"], slots))
        return timeline

    def slot_players(self, period: int, t: float) -> list[str]:
        cur = self._slot_timeline[0][2]
        for tt, p, s in self._slot_timeline:
            if (p, tt) <= (period, t):
                cur = s
        return cur

    def slot_of(self, player: str, period: int, t: float) -> int | None:
        s = self.slot_players(period, t)
        return s.index(player) if player in s else None

    def name(self, player: str) -> str:
        return self.players[player]["name"]

    # ---- event filters ----
    def window(self, period: int, t0: float, t1: float, types: tuple[str, ...] | None = None,
               team: str | None = None) -> list[dict]:
        return [e for e in self.events if e["period"] == period and t0 <= e["t"] < t1
                and (types is None or e["type"] in types) and (team is None or e["team"] == team)]


def load(match_dir: str | Path) -> MatchData:
    d = Path(match_dir)
    meta = json.loads((d / "meta.json").read_text())
    events = [json.loads(line) for line in (d / "events.jsonl").read_text().splitlines() if line]
    frames = np.load(d / "tracking.npy")
    return MatchData(meta=meta, events=events, frames=frames)


def from_match(m) -> MatchData:
    """Build directly from a simulator Match (no answer key involved)."""
    return MatchData(meta=m.meta(), events=list(m.events), frames=m.frames)
