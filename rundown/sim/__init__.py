"""Synthetic football match simulator: 10 Hz tracking, an event stream, and
planted tactical stories kept apart as a hidden answer key."""

from .match import Match, simulate
from .teams import CLUBS, Club, Player

__all__ = ["Match", "simulate", "CLUBS", "Club", "Player"]
