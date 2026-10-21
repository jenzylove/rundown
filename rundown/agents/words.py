"""Every number an agent writes must be one the engine computed. Agents either write a
{F1}-style placeholder, which is filled with the fact's value, or type the value itself,
which must match one of the talking point's facts exactly. Either way this module
formats the number for the language and records which fact each piece of text used;
a number that matches no fact is an error."""

from __future__ import annotations

import re

from ..engine.detect import RECENT_S, Candidate

COMMA_DECIMAL = {"es", "fr", "pt"}
SHORT_UNITS = {"%": "%", "m": " m", "km/h": " km/h", "/100": "/100"}
DETAIL_UNITS = {"top_speed_kmh": " km/h", "distance_m": " m"}
PLACEHOLDER = re.compile(r"\{([A-Z]+\d*(?:\.[a-z_]+)?)\}")
NUMBER = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?(?![.,]?\d)")


def fmt(v: float | int, lang: str) -> str:
    s = str(v) if isinstance(v, int) else f"{v:g}"
    return s.replace(".", ",") if lang in COMMA_DECIMAL else s


class Table:
    """Placeholder -> (value, unit suffix, fact key) for one talking point."""

    def __init__(self, c: Candidate):
        self.entries: dict[str, tuple[float | int, str, str | None]] = {
            "MIN": (round(c.minute), "", None), "WIN": (int(RECENT_S // 60), "", None)}
        self.facts: dict[str, dict] = {}
        for i, f in enumerate(c.facts):
            a = f"F{i + 1}"
            self.facts[a] = dict(alias=a, label=f.label, value=f.value, unit=f.unit, key=f.key)
            if isinstance(f.value, (int, float)) and not isinstance(f.value, bool):
                self.entries[a] = (f.value, SHORT_UNITS.get(f.unit, ""), f.key)
            for k, v in f.detail.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    self.entries[f"{a}.{k}"] = (v, DETAIL_UNITS.get(k, ""), f.key)
                    self.facts[a].setdefault("detail", {})[k] = v

    def row_aliases(self) -> list[str]:
        return [a for a in self.entries if a not in ("MIN", "WIN")]

    def key_of(self, alias: str) -> str | None:
        return self.entries[alias][2] if alias in self.entries else None

    def for_prompt(self) -> list[dict]:
        return list(self.facts.values())

    def match(self, n: float) -> tuple[float | int, str | None] | None:
        """The fact value a typed number stands for, or None if it is no fact's value."""
        for v, _, key in self.entries.values():
            if abs(float(v) - n) < 0.0051:
                return v, key
        return None

    def fill(self, text: str, lang: str) -> tuple[str, list[str], list[str]]:
        """-> (text with every number checked and formatted, fact keys used, errors).
        Unknown placeholders and typed numbers that match no fact are errors."""
        errs, keys, out = [], [], []

        def use(v, key) -> str:
            if key and key not in keys:
                keys.append(key)
            return fmt(v, lang)

        def typed(m) -> str:
            hit = self.match(float(m.group(0).replace(",", ".")))
            if hit is None:
                errs.append(f"{m.group(0)} in \"{text[:60]}\" is not one of the facts' values")
                return m.group(0)
            return use(*hit)

        # re.split with a group alternates literal text and placeholder names
        for i, part in enumerate(PLACEHOLDER.split(text)):
            if i % 2 == 0:
                out.append(NUMBER.sub(typed, part))
            elif part in self.entries:
                v, _, key = self.entries[part]
                out.append(use(v, key))
            else:
                errs.append(f"unknown placeholder {{{part}}}")
        if errs:
            return text, [], errs
        return "".join(out), keys, []

    def value(self, aliases: list[str], lang: str) -> tuple[str, list[str], list[str]]:
        """Stat card value: the facts' values with their units, joined with arrows."""
        aliases = [a.strip("{} ") for a in aliases]
        bad = [a for a in aliases if a not in self.entries or a in ("MIN", "WIN")]
        if bad or not aliases:
            return "", [], [f"stat card row needs known facts, got {aliases}"]
        parts, keys = [], []
        for a in aliases:
            v, unit, key = self.entries[a]
            parts.append(fmt(v, lang) + unit)
            if key not in keys:
                keys.append(key)
        return " → ".join(parts), keys, []
