"""Fictional clubs and squads. No real club, player or league marks.

Coordinates in this module are in the *team frame*: x is metres from the
team's own goal line (0..105), y is metres across with y=68 on the team's
right-hand side as it attacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

PITCH_LEN = 105.0
PITCH_WID = 68.0

# 4-3-3 slots: role, team-frame base position
FORMATION_433: list[tuple[str, float, float]] = [
    ("GK", 5.0, 34.0),
    ("RB", 30.0, 58.0),
    ("RCB", 24.0, 43.0),
    ("LCB", 24.0, 25.0),
    ("LB", 30.0, 10.0),
    ("DM", 40.0, 34.0),
    ("RCM", 50.0, 46.0),
    ("LCM", 50.0, 22.0),
    ("RW", 68.0, 60.0),
    ("ST", 75.0, 34.0),
    ("LW", 68.0, 8.0),
]
ROLES = [r for r, _, _ in FORMATION_433]


@dataclass
class Player:
    id: str
    name: str
    number: int
    role: str
    pace: float  # top speed, m/s
    passing: float  # 0..1
    finishing: float  # 0..1
    stamina: float  # 0..1


@dataclass
class Club:
    code: str
    name: str
    short: str
    colour: str
    style: dict[str, float]  # baseline tactic
    players: list[Player] = field(default_factory=list)  # 11 starters then bench


FIRST = [
    "Tomas", "Kwame", "Luca", "Mateo", "Idris", "Jonah", "Rafael", "Emeka", "Soren", "Yusuf",
    "Dario", "Felix", "Kenji", "Amadou", "Elias", "Nico", "Bruno", "Oskar", "Teo", "Malik",
    "Andrei", "Caio", "Ilya", "Joel", "Samir", "Tiago", "Viktor", "Wren", "Ezra", "Hugo",
    "Kofi", "Lars", "Milo", "Omar", "Pablo", "Ruben", "Stellan", "Ugo", "Zane", "Arlo",
]
LAST = [
    "Okafor", "Reyes", "Lindqvist", "Mensah", "Duarte", "Haddad", "Kowal", "Sato", "Adeyemi",
    "Brandt", "Ilunga", "Varga", "Nunes", "Petit", "Moreau", "Saari", "Abara", "Lund",
    "Castillo", "Ivanov", "Tanaka", "Oduya", "Ferreira", "Halvorsen", "Quist", "Baptiste",
    "Ekwueme", "Marchetti", "Novak", "Osei", "Pereira", "Rasku", "Strand", "Toure", "Vidal",
    "Weller", "Yilmaz", "Zielinski", "Achterberg", "Bellamy", "Cerny", "Dlamini", "Esposito",
]

CLUBS: dict[str, dict] = {
    "HAR": dict(name="Harbour City", short="Harbour", colour="#f4f1ea",
                style=dict(press=0.55, depth=0.5, width=0.0, tempo=0.6, directness=0.35)),
    "EMA": dict(name="Eastmoor Athletic", short="Eastmoor", colour="#e5484d",
                style=dict(press=0.35, depth=0.4, width=0.0, tempo=0.45, directness=0.55)),
    "RVN": dict(name="Ravensholt", short="Ravensholt", colour="#3e63dd",
                style=dict(press=0.65, depth=0.6, width=0.0, tempo=0.7, directness=0.3)),
    "KGT": dict(name="Kingsgate Rovers", short="Kingsgate", colour="#f5a524",
                style=dict(press=0.45, depth=0.45, width=0.0, tempo=0.5, directness=0.5)),
}

BENCH_ROLES = ["GK", "RCB", "DM", "RW", "ST"]


def build_club(code: str, rng: np.random.Generator, used_names: set[str]) -> Club:
    spec = CLUBS[code]
    club = Club(code=code, name=spec["name"], short=spec["short"], colour=spec["colour"],
                style=dict(spec["style"]))
    numbers = {"GK": 1, "RB": 2, "LB": 3, "RCB": 4, "LCB": 5, "DM": 6, "RW": 7, "RCM": 8,
               "ST": 9, "LCM": 10, "LW": 11}
    bench_numbers = iter([13, 15, 16, 17, 19])
    for k, role in enumerate(ROLES + BENCH_ROLES):
        while True:
            name = f"{FIRST[rng.integers(len(FIRST))]} {LAST[rng.integers(len(LAST))]}"
            if name not in used_names:
                used_names.add(name)
                break
        attacker = role in ("RW", "LW", "ST")
        club.players.append(Player(
            id=f"{code}{k:02d}",
            name=name,
            number=numbers[role] if k < 11 else next(bench_numbers),
            role=role,
            pace=float(rng.uniform(8.6, 9.6) if attacker or role in ("RB", "LB") else
                       (rng.uniform(6.8, 7.6) if role == "GK" else rng.uniform(8.0, 9.0))),
            passing=float(rng.uniform(0.7, 0.9) if role in ("DM", "RCM", "LCM") else rng.uniform(0.6, 0.8)),
            finishing=float(rng.uniform(0.6, 0.9) if attacker else rng.uniform(0.3, 0.55)),
            stamina=float(rng.uniform(0.75, 0.95)),
        ))
    return club
