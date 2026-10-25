"""Rules Writer profiles: four languages x casual/analyst. Every finished segment must pass the
Verifier (claims, depth, schema, facts) and every spoken line must read as the requested language."""

import pytest

from rundown.agents import verify
from rundown.engine import detect
from rundown.engine.data import from_match
from rundown.sim import simulate
from rundown.studio import telestrate

LANGS = ("en", "es", "fr", "pt")


def candidates(seed):
    md = from_match(simulate(seed, period_minutes=25.0))
    t_end = float(md.frames[md.frame_index(1, 1e9), 1])
    cands, t = {}, 60.0
    while t <= t_end:
        for c in detect.scan(md, 1, t):
            cands.setdefault((c.kind, c.team, c.side, c.player), c)
        t += 60
    for c in detect.moments(md, 1, 0, t_end):
        cands.setdefault((c.kind, c.team, c.side, c.player, c.t), c)
    for team in (md.home, md.away):
        pid = next(p for p, v in md.players.items() if v["team"] == team)
        pf = detect.player_focus(md, pid, 1, 0.0, t_end)
        if pf:
            cands.setdefault((pf.kind, pf.team, None, pf.player), pf)
    return md, list(cands.values())


@pytest.mark.parametrize("seed", [21, 34, 55])
def test_every_profile_passes_the_verifier(seed):
    md, cands = candidates(seed)
    assert cands
    kinds = set()
    for c in cands:
        kinds.add(c.kind)
        scripts = {}
        for lang in LANGS:
            for depth in ("casual", "analyst"):
                seg = telestrate.build(md, c, lang=lang, target_s=28.0, depth=depth)
                where = (c.kind, lang, depth)
                assert verify.segment_errors(md, seg) == [], where
                lines = [ln["text"] for ln in seg["script"]]
                assert lines
                for line in lines:
                    assert verify.text_errors(line, lang, "x") == [], (where, line)
                scripts[lang, depth] = lines
        for lang in LANGS:
            assert scripts[lang, "casual"] != scripts[lang, "analyst"], (c.kind, lang)
    assert {"player_focus"} <= kinds or len(kinds) >= 3
