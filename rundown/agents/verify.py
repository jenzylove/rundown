"""The Verifier: deterministic checks on everything an agent hands over, before anything
is shown. Each check returns a list of plain-language errors; an empty list passes.
The errors go back to the agent for its one retry, and into the trace."""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

from ..engine import stats as S
from ..engine.data import MatchData

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "schema" / "overlay-script.v1.json").read_text())
_VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# Words that give away the wrong language. Two or more in one piece of text fails it.
FOREIGN = {
    "es": {"the", "and", "of", "per", "passes", "with", "their", "they", "is", "are", "from", "to", "now", "minute"},
    "en": {"el", "los", "las", "por", "que", "con", "una", "del", "pases", "ahora", "minuto", "están"},
}


# Numbers spelled out can't be checked against the engine, so they are rejected. "Two" and
# "dos" are left out: "the two sides" and "los dos equipos" are everyday phrases, not stats.
NUMBER_WORDS = {
    "en": set("three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
              "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred".split()),
    "es": set("tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciséis "
              "diecisiete dieciocho diecinueve veinte treinta cuarenta cincuenta sesenta setenta ochenta "
              "noventa cien ciento".split()),
}


def text_errors(text: str, lang: str, where: str, max_len: int | None = None, on_air: bool = True) -> list[str]:
    errs = []
    if CONTROL.search(text) or "�" in text:
        errs.append(f"{where}: broken characters in \"{text[:40]}\"")
    if max_len and len(text) > max_len:
        errs.append(f"{where}: {len(text)} characters, the limit is {max_len}")
    words = re.findall(r"[a-záéíóúñü]+", text.lower())
    hits = sorted({w for w in words if w in FOREIGN.get(lang, set())})
    if len(hits) >= 2:
        errs.append(f"{where}: not in the requested language (found {', '.join(hits)})")
    spelled = sorted({w for w in words if w in NUMBER_WORDS.get(lang, set())})
    if spelled and on_air:
        errs.append(f"{where}: number spelled out ({', '.join(spelled)}); write fact values in digits")
    return errs


def layout_errors(md: MatchData, plan: dict, cl: dict, opts: dict) -> list[str]:
    """A Telestrator plan names clip times, slots, events and option names; check each one."""
    errs = []
    clip_len = cl["t1"] - cl["t0"]
    freezes = plan.get("freezes", [])
    if not freezes:
        errs.append("needs at least one freeze on the key moment")
    for i, f in enumerate(freezes):
        if not 0 <= f["at"] <= clip_len:
            errs.append(f"freeze {i}: at={f['at']} is outside the clip (0 to {clip_len:.1f} s)")
    for kind in ("highlights", "runs", "zones", "lines"):
        for i, item in enumerate(plan.get(kind, [])):
            fi = item.get("freeze")
            if fi is not None and not 0 <= fi < len(freezes):
                errs.append(f"{kind} {i}: freeze {fi} does not exist")
            if fi is None and item.get("at") is None and kind != "runs":
                errs.append(f"{kind} {i}: give a freeze index or a clip time")
    for i, h in enumerate(plan.get("highlights", [])):
        if not 0 <= h["slot"] <= 21:
            errs.append(f"highlight {i}: slot {h['slot']} does not exist")
        if h.get("text") and h["text"] not in {md.name(p) for p in cl["slots"]}:
            errs.append(f"highlight {i}: text must be the player's name or empty, got \"{h['text']}\"")
    for i, r in enumerate(plan.get("runs", [])):
        if not 0 <= r["slot"] <= 21:
            errs.append(f"run {i}: slot {r['slot']} does not exist")
        if not 0 <= r["from_t"] < r["to_t"] <= clip_len + 0.05:
            errs.append(f"run {i}: from_t/to_t must be inside the clip and in order")
    in_clip = {e["id"] for e in md.window(cl["period"], cl["t0"], cl["t1"] + 0.01)}
    for eid in plan.get("arrows", []):
        ev = md.by_id.get(eid)
        if eid not in in_clip or ev is None or ev.get("end_x") is None:
            errs.append(f"arrow: {eid} is not a pass, cross, shot or carry in this clip")
    for kind, names in (("zones", opts["zones"]), ("lines", opts["lines"])):
        for item in plan.get(kind, []):
            if item["name"] not in names:
                errs.append(f"{kind}: \"{item['name']}\" is not one of {sorted(names)}")
    return errs


def segment_errors(md: MatchData, seg: dict) -> list[str]:
    """The finished segment: schema, every cited fact recomputed from the engine, every
    event id real, every cue and line inside the segment."""
    errs = [f"schema: {e.message} at /{'/'.join(map(str, e.path))}" for e in _VALIDATOR.iter_errors(seg)]
    facts = {f["key"]: f for f in seg["facts"]}
    for key, f in facts.items():
        again = S.recompute(md, f["stat"], f["args"])
        if again.value != f["value"]:
            errs.append(f"fact {key}: says {f['value']}, the engine says {again.value}")
    dur = seg["duration_s"]
    for i, c in enumerate(seg["cues"]):
        for k in c.get("fact_keys", []):
            if k not in facts:
                errs.append(f"cue {i}: cites {k}, which is not in the segment's facts")
        for e in c.get("event_ids", []):
            if e not in md.by_id:
                errs.append(f"cue {i}: event {e} does not exist")
        if c["t"] + c["d"] > dur + 0.05:
            errs.append(f"cue {i} ({c['kind']}): ends at {c['t'] + c['d']:.1f} s, the segment is {dur:.1f} s")
    for i, ln in enumerate(seg["script"]):
        for k in ln.get("fact_keys", []):
            if k not in facts:
                errs.append(f"line {i}: cites {k}, which is not in the segment's facts")
        if ln["t"] + ln["d"] > dur + 0.05:
            errs.append(f"line {i}: ends after the segment")
    return errs
