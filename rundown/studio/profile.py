"""A fan's profile: whose side they're on, the language they watch in, how deep they want
the analysis, and the player they follow. Every rundown is made for one profile; the
studio's master rundown is the neutral English analyst one."""

from __future__ import annotations

from dataclasses import dataclass

SIDES = ("neutral", "home", "away")
LANGS = ("en", "es", "fr", "pt")
DEPTHS = ("casual", "analyst")


@dataclass(frozen=True)
class Profile:
    side: str = "neutral"
    lang: str = "en"
    depth: str = "analyst"
    player: str | None = None

    def __post_init__(self):
        if self.side not in SIDES or self.lang not in LANGS or self.depth not in DEPTHS:
            raise ValueError(f"unsupported profile {self.side}/{self.lang}/{self.depth}")

    @property
    def key(self) -> str:
        return f"{self.side}|{self.lang}|{self.depth}|{self.player or '-'}"

    @classmethod
    def from_key(cls, key: str) -> "Profile":
        side, lang, depth, player = key.split("|")
        return cls(side, lang, depth, None if player == "-" else player)

    def to_dict(self) -> dict:
        return dict(side=self.side, lang=self.lang, depth=self.depth, player=self.player, key=self.key)


MASTER = Profile()
