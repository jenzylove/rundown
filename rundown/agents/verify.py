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


# ---- claims: a number can be the engine's and the sentence still be false ----
# Most talking points compare a baseline window with the recent one, so their facts come in
# (before, now) pairs. "Control drops from 76.9% to 55.2%" uses two real values and is still
# wrong when control went up. These checks catch a pair quoted in the wrong order and a verb
# that says the opposite of what the numbers did.

PAIRS = {  # kind -> (before index, now index, detail field or None for the fact's value)
    "press_up": [(0, 1, None), (2, 3, None)],
    "flank_overload": [(0, 1, None), (2, 3, None)],
    "deep_block": [(0, 1, None), (2, 3, None)],
    "tempo_up": [(0, 1, None)],
    "fatigue": [(0, 1, None), (0, 1, "top_speed_kmh"), (0, 1, "distance_m")],
}
UP = {
    "en": "rise rises rising rose risen climb climbs climbed climbing increase increases increased increasing jump jumps "
          "jumped grow grows grew growing up surge surges surged soar soars soared",
    "es": "sube suben subió subido subiendo aumenta aumentan aumentó aumentado crece crecen creció crecido dispara disparan disparó",
    "fr": "augmente augmentent augmenté monte montent monté grimpe grimpent grimpé bondit progresse progressé",
    "pt": "sobe sobem subiu subido aumenta aumentam aumentou cresce crescem cresceu dispara disparou",
}
DOWN = {
    "en": "drop drops dropped dropping fall falls fell falling fallen decline declines declined decrease decreases decreased "
          "down slip slips slipped sink sinks sank shrink shrinks shrank plunge plunges plunged",
    "es": "baja bajan bajó bajado bajando cae caen cayó caído disminuye disminuyen disminuyó desciende descienden descendió",
    "fr": "baisse baissent baissé chute chutent chuté descend descendent descendu recule reculent reculé diminue diminuent "
          "diminué tombe tombent tombé",
    "pt": "cai caem caiu desce descem desceu diminui diminuem diminuiu baixa baixam baixou recua recuam recuou",
}
UP = {k: set(v.split()) for k, v in UP.items()}
DOWN = {k: set(v.split()) for k, v in DOWN.items()}
# words that put a value in time: "now it's 4.3" must be the recent value, "they were allowing 10.7"
# the earlier one
NOW_WORDS = {"en": "now currently lately recently", "es": "ahora ya actualmente últimamente",
             "fr": "maintenant désormais actuellement aujourd'hui", "pt": "agora já atualmente"}
THEN_WORDS = {"en": "were was before earlier previously", "es": "antes era eran estaba estaban dejaban",
              "fr": "avant était étaient auparavant", "pt": "antes era eram estava estavam"}
NOW_WORDS = {k: set(v.split()) for k, v in NOW_WORDS.items()}
THEN_WORDS = {k: set(v.split()) for k, v in THEN_WORDS.items()}
FROM_ONLY = {"en": r"from", "es": r"desde", "fr": r"contre|depuis", "pt": r"desde"}  # never "to" words
FROM = {"en": r"from", "es": r"de|desde", "fr": r"de|d'|du", "pt": r"de|do|da"}
TO = {"en": r"to", "es": r"a|al|hasta", "fr": r"à|a", "pt": r"para|a|até"}
NUM = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?(?![.,]?\d)")
PLACE = re.compile(r"\b(?:down|up)\s+(?:the|that|this|their|his|her|its|a|our|your)\b")
SENTENCE = re.compile(r"(?<=[.!?])\s+")  # a stop then a space: "76.9" stays whole


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _pairs(seg: dict) -> list[tuple[float, float]]:
    """(before, now) value pairs for the segment's talking point, from the candidate's facts."""
    cand = seg.get("provenance", {}).get("candidate") or {}
    return pairs_for(seg.get("kind") or cand.get("kind"), cand.get("facts") or [])


def pairs_for(kind: str, facts: list[dict]) -> list[tuple[float, float]]:
    out = []
    for a, b, field in PAIRS.get(kind, []):
        if b >= len(facts):
            continue
        va = facts[a]["value"] if field is None else facts[a].get("detail", {}).get(field)
        vb = facts[b]["value"] if field is None else facts[b].get("detail", {}).get(field)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va != vb:
            out.append((float(va), float(vb)))
    return out


def _role(n: float, pairs: list[tuple[float, float]]) -> tuple[int, str] | None:
    """Which pair a number belongs to, and whether it is the 'before' or the 'now' value.
    None when it belongs to no pair, or to more than one (ambiguous, so not judged)."""
    hits = [(i, "before" if abs(n - a) < 0.0051 else "now")
            for i, (a, b) in enumerate(pairs) if abs(n - a) < 0.0051 or abs(n - b) < 0.0051]
    return hits[0] if len(hits) == 1 else None


def claim_errors(text: str, lang: str, pairs: list[tuple[float, float]], where: str) -> list[str]:
    """Order and direction of every before/now pair quoted in a piece of text."""
    if not pairs:
        return []
    errs = []
    up, down = UP.get(lang, set()), DOWN.get(lang, set())
    for sent in SENTENCE.split(text):
        nums = [(m, _role(_num(m.group(0)), pairs)) for m in NUM.finditer(sent)]
        # "from X to Y": X must be the before value and Y the now value
        for (m1, r1), (m2, r2) in zip(nums, nums[1:]):
            if not (r1 and r2 and r1[0] == r2[0] and r1[1] != r2[1]):
                continue
            head = sent[max(0, m1.start() - 14):m1.start()].lower()
            mid = sent[m1.end():m2.start()].lower()
            if (re.search(rf"\b({FROM.get(lang, 'from')})\s*$", head) and len(mid) <= 40
                    and re.search(rf"(^|\W)({TO.get(lang, 'to')})\W*$", mid)) or "→" in mid:
                if r1[1] == "now":
                    errs.append(f"{where}: \"{sent.strip()[:70]}\" quotes {m1.group(0)} → {m2.group(0)}, "
                                f"but {m2.group(0)} came first and {m1.group(0)} is now")
        # a time word just before a value must match it: "now" with the recent value, "were" with the earlier
        prev = 0
        for m, r in nums:
            window = sent[prev:m.start()].lower()[-40:]
            prev = m.end()
            if not r:
                continue
            ws = set(re.findall(r"[^\W\d_']+(?:'[^\W\d_]+)?", window))
            if r[1] == "now" and re.search(rf"(^|\W)({FROM_ONLY.get(lang, 'from')})\s*$", window):
                errs.append(f"{where}: \"{sent.strip()[:70]}\" says from {m.group(0)}, but that is the value now")
            if r[1] == "before" and ws & NOW_WORDS.get(lang, set()):
                errs.append(f"{where}: \"{sent.strip()[:70]}\" says {m.group(0)} is the value now, but that was the value before")
            if r[1] == "now" and ws & THEN_WORDS.get(lang, set()):
                errs.append(f"{where}: \"{sent.strip()[:70]}\" says {m.group(0)} was the earlier value, but it is the value now")
        # a direction verb must agree with what the pair did
        quoted = {r[0] for _, r in nums if r}
        # "down the left", "up that side": places, not directions
        plain = PLACE.sub(" ", sent.lower()) if lang == "en" else sent.lower()
        words = set(re.findall(r"[^\W\d_]+", plain))
        said_up, said_down = bool(words & up), bool(words & down)
        if said_up == said_down:
            continue  # no direction word, or both: not judged
        for i in quoted:
            if {r[1] for _, r in nums if r and r[0] == i} != {"before", "now"}:
                continue  # only judge a pair when both values are in the sentence
            before, now = pairs[i]
            if said_up and now < before or said_down and now > before:
                verb = sorted(words & (up if said_up else down))[0]
                errs.append(f"{where}: \"{sent.strip()[:70]}\" says \"{verb}\", but the value went "
                            f"{'down' if now < before else 'up'} ({before:g} → {now:g})")
    return errs


# ---- depth: a casual fan and an analyst get genuinely different segments ----
CASUAL_MAX_NUMBERS = 3
JARGON = re.compile(r"\b(ppda|xg|field tilt|dominio territorial|domination territoriale|domínio territorial)\b", re.I)


def depth_errors(lines: list[str], rows: list[list[str]], depth: str, n_facts: int, where: str = "") -> list[str]:
    """Casual: plain words, a light touch on numbers (at most two in a line, a before and a now, and
    at most three in the whole script), no abbreviations, a short stat card.
    Analyst: the stat card carries the evidence (two rows or more when there are facts for them)."""
    errs = []
    p = f"{where}: " if where else ""
    if depth == "casual":
        total = sum(len(NUM.findall(t)) for t in lines)
        if total > CASUAL_MAX_NUMBERS:
            errs.append(f"{p}{total} numbers in the script; a casual fan gets at most {CASUAL_MAX_NUMBERS}: "
                        f"keep the one or two that tell the story and say the rest in words")
        for i, t in enumerate(lines):
            n = len(NUM.findall(t))
            if n > 2:
                errs.append(f"{p}line {i} has {n} numbers; a casual fan gets at most two in a line")
            if JARGON.search(t):
                errs.append(f"{p}line {i} uses \"{JARGON.search(t).group(0)}\"; say it in plain words for a casual fan")
        if len(rows) > 2:
            errs.append(f"{p}{len(rows)} stat card rows; a casual fan gets at most 2")
        for j, r in enumerate(rows):
            if JARGON.search(r[0]):
                errs.append(f"{p}stat card row {j} label uses \"{JARGON.search(r[0]).group(0)}\"; use plain words")
    elif depth == "analyst" and n_facts >= 2 and len(rows) < 2:
        errs.append(f"{p}an analyst gets at least 2 stat card rows")
    return errs


def segment_depth_errors(seg: dict) -> list[str]:
    card = next((q for q in seg.get("cues", []) if q.get("kind") == "statcard"), {})
    return depth_errors([ln["text"] for ln in seg.get("script", [])], card.get("rows", []),
                        seg.get("depth", "analyst"), len(seg.get("facts", [])))


def segment_claim_errors(seg: dict) -> list[str]:
    """Every spoken line, the subtitle and the stat card rows of a finished segment."""
    lang, pairs = seg.get("lang", "en"), _pairs(seg)
    errs = []
    for i, ln in enumerate(seg.get("script", [])):
        errs += claim_errors(ln["text"], lang, pairs, f"line {i}")
    for q in seg.get("cues", []):
        if q.get("kind") == "label" and q.get("sub"):
            errs += claim_errors(q["sub"], lang, pairs, "subtitle")
        if q.get("kind") == "statcard":
            for j, row in enumerate(q.get("rows", [])):
                errs += claim_errors(f"{row[0]}: {row[1]}", lang, pairs, f"stat card row {j}")
    return errs


def _allowed(md: MatchData, seg: dict, keys: list[str] | None = None) -> list[float]:
    """Every number a segment may say: its facts' values and details (only the facts in `keys`
    when given), the minutes it is about (as clock minutes and as broadcast minutes, one higher),
    and the recent window's length."""
    vals: list[float] = [10.0]  # the detectors' recent window, "the last 10 minutes"
    for f in seg.get("facts", []):
        if keys and f["key"] not in keys:
            continue
        for v in [f.get("value")] + list((f.get("detail") or {}).values()):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
        eid = (f.get("args") or {}).get("event_id")
        if eid in md.by_id:
            m = int(md.by_id[eid]["minute"])
            vals += [m, m + 1]
    cand = (seg.get("provenance") or {}).get("candidate") or {}
    for t in [cand.get("t"), seg.get("clip", {}).get("t0")]:
        if isinstance(t, (int, float)):
            vals += [int(t // 60), int(t // 60) + 1, round(t / 60)]
    return vals


def _said(n_text: str, allowed: list[float]) -> bool:
    n = _num(n_text)
    dec = len(n_text.replace(",", ".").partition(".")[2])
    tol = 0.5 * 10 ** -dec + 1e-9  # "0.26" may stand for 0.261; "33" for 33.3 is a rounding, not a new number
    return any(abs(n - v) <= tol for v in allowed)


def number_errors(md: MatchData, seg: dict) -> list[str]:
    """The last gate before air: every number in the spoken lines, the subtitle and the stat card
    is one the segment's facts hold. A number typed or changed after the Writer's gate fails here."""
    allowed = _allowed(md, seg)
    # a line that cites facts may only say their numbers: a value moved into the wrong sentence fails
    texts = [(f"line {i}", ln["text"], _allowed(md, seg, ln["fact_keys"]) if ln.get("fact_keys") else allowed)
             for i, ln in enumerate(seg.get("script", []))]
    for q in seg.get("cues", []):
        # the title's subtitle carries facts; a label drawn from its own events (a passing move's
        # length) is counted by code from those events, not written by an agent
        if q.get("kind") == "label" and q.get("sub") and "fact_keys" in q:
            texts.append(("subtitle", q["sub"], allowed))
        if q.get("kind") == "statcard":
            texts += [(f"stat card row {j}", f"{r[0]} {r[1]}", allowed) for j, r in enumerate(q.get("rows", []))]
    errs = []
    for where, text, ok in texts:
        for m in NUM.finditer(text):
            if not _said(m.group(0), ok):
                errs.append(f"{where}: {m.group(0)} in \"{text[:60]}\" is not a value the engine computed")
    return errs


def segment_errors(md: MatchData, seg: dict) -> list[str]:
    """The finished segment: schema, every cited fact recomputed from the engine, every event id
    real, every cue and line inside the segment (the checks of 22 Oct), and since 25 Oct every
    number said is one the engine computed, every before/now claim is the right way round, and
    the depth is what the fan asked for."""
    return (base_errors(md, seg) + number_errors(md, seg) + segment_claim_errors(seg)
            + segment_depth_errors(seg))


def base_errors(md: MatchData, seg: dict) -> list[str]:
    """The Verifier as it was on 22 Oct: schema, facts recomputed, events real, timing inside."""
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
