"""Verifier v2: a sentence built only from the engine's numbers can still be false. These
cover the claim checks: before/now order and direction words, in four languages."""

import pytest

from rundown.agents import verify
from rundown.engine import detect
from rundown.engine.data import from_match
from rundown.sim import simulate
from rundown.studio import telestrate

# deep_block facts: line 36.3 -> 32.3 m, opponent field tilt 55.2 -> 76.9 %
PAIRS = [(36.3, 32.3), (55.2, 76.9)]


def errs(text, lang="en"):
    return verify.claim_errors(text, lang, PAIRS, "line")


def test_the_live_bug_is_caught():
    # aired on 25 Oct and marked verified: control went UP from 55.2 to 76.9
    e = errs("We see Eastmoor's field control drop from 76.9% to 55.2%.")
    assert any("came first" in x for x in e)
    assert any("says \"drop\"" in x for x in e)


@pytest.mark.parametrize("text", [
    "Their back line has dropped from 36.3 to 32.3 metres from goal.",
    "Eastmoor's field tilt rises from 55.2% to 76.9%.",
    "Eastmoor now have 76.9% of the passes in the final third, up from 55.2%.",
    "The line sits at 32.3 m now.",
    "From 55.2% to 76.9%: Eastmoor own the ball.",
    "Attacks down that side: 55.2% → 76.9%",
    "They go down the left: from 55.2% to 76.9% of attacks.",
])
def test_true_claims_pass(text):
    assert errs(text) == []


@pytest.mark.parametrize("text", [
    "Eastmoor's field tilt falls from 55.2% to 76.9%.",
    "The line climbs from 36.3 to 32.3 metres.",
    "Eastmoor's share went from 76.9% to 55.2%.",
])
def test_false_claims_fail(text):
    assert errs(text)


@pytest.mark.parametrize("lang,good,bad", [
    ("es", "El dominio de Eastmoor sube de 55,2% a 76,9%.", "El dominio de Eastmoor baja de 76,9% a 55,2%."),
    ("fr", "La domination d'Eastmoor augmente de 55,2 % à 76,9 %.", "La domination d'Eastmoor baisse de 76,9 % à 55,2 %."),
    ("pt", "O domínio do Eastmoor sobe de 55,2% para 76,9%.", "O domínio do Eastmoor cai de 76,9% para 55,2%."),
])
def test_other_languages(lang, good, bad):
    assert errs(good, lang) == []
    assert errs(bad, lang)


def test_stat_card_row_order():
    assert verify.claim_errors("Line height: 36.3 → 32.3 m", "en", PAIRS, "row") == []
    assert verify.claim_errors("Line height: 32.3 → 36.3 m", "en", PAIRS, "row")


@pytest.mark.parametrize("seed", [21, 34, 55])
def test_rules_segments_raise_no_false_alarms(seed):
    """The rules Writer's templates are right by construction; the claim check must agree."""
    md = from_match(simulate(seed, period_minutes=25.0))
    t_end = float(md.frames[md.frame_index(1, 1e9), 1])
    cands, t = {}, 60.0
    while t <= t_end:
        for c in detect.scan(md, 1, t):
            cands.setdefault((c.kind, c.team, c.side, c.player), c)
        t += 60
    assert cands
    for c in cands.values():
        for lang in ("en", "es"):
            seg = telestrate.build(md, c, lang=lang, target_s=28.0)
            assert verify.segment_claim_errors(seg) == [], (c.kind, lang)


@pytest.mark.parametrize("text", [
    "The line was at 32.3 metres before the break.",
    "Now it's 36.3 metres.",
    "Eastmoor have 55.2% of the passes, up from 76.9%.",
])
def test_time_words_must_match_the_value(text):
    assert errs(text)
