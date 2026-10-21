"""Deterministic match statistics. No AI anywhere in this package: every
number the product shows is computed here and carries the ids of the events
(or the frame range) it came from."""

from .data import MatchData, load
from . import stats, detect

__all__ = ["MatchData", "load", "stats", "detect"]
