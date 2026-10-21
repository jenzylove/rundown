"""Stats engine tests on small hand-built matches, so expected values can be
worked out by hand."""

import numpy as np
import pytest

from rundown.engine import stats as S
from rundown.engine.data import MatchData, from_match
from rundown.sim import simulate


def tiny_meta():
    def club(code, short):
        players = [dict(id=f"{code}{k:02d}", name=f"{short} {k}", number=k + 1, role="X") for k in range(11)]
        return dict(code=code, name=short, short=short, players=players, starters=[p["id"] for p in players])
    return dict(seed=0, home=club("AAA", "Alpha"), away=club("BBB", "Beta"))


def tiny_frames(seconds=60.0, period=1, mover=None):
    n = int(seconds * 10)
    f = np.zeros((n, 49), dtype=np.float32)
    f[:, 0] = period
    f[:, 1] = np.arange(n) / 10.0
    f[:, 2:4] = [52.5, 34.0]
    xy = f[:, 5:].reshape(n, 22, 2)
    xy[:, :11] = [30.0, 34.0]
    xy[:, 11:] = [75.0, 34.0]
    for k in range(22):
        xy[:, k, 1] = 2 + 3 * k
    if mover:
        mover(xy)
    f[:, 5:] = xy.reshape(n, 44)
    return f


def ev(i, t, type_, team, **kw):
    base = dict(id=f"e{i:05d}", t=t, period=1, minute=t / 60, type=type_, team=team, player=f"{team}05", x=50.0, y=34.0)
    base.update(kw)
    return base


def test_pass_completion_counts():
    events = [ev(1, 1, "pass", "AAA", outcome="complete", end_x=60, end_y=34, recipient="AAA06"),
              ev(2, 2, "pass", "AAA", outcome="complete", end_x=60, end_y=34, recipient="AAA06"),
              ev(3, 3, "pass", "AAA", outcome="intercepted", end_x=60, end_y=34, recipient="AAA06"),
              ev(4, 4, "pass", "BBB", outcome="complete", end_x=60, end_y=34, recipient="BBB06")]
    md = MatchData(tiny_meta(), events, tiny_frames())
    f = S.passes(md, "AAA", 1, 0, 60)
    assert f.value == 66.7 and f.detail == dict(attempted=3, completed=2)
    assert f.evidence == ["e00001", "e00002", "e00003"]


def test_ppda_counts_only_the_right_zones():
    # away (BBB) attacks toward x=0, so its own 60% is absolute x > 42
    events = [ev(i, i, "pass", "BBB", x=80.0, y=30.0, outcome="complete", end_x=70, end_y=30, recipient="BBB06")
              for i in range(1, 11)]
    events += [ev(11, 11, "pass", "BBB", x=20.0, y=30.0, outcome="complete", end_x=10, end_y=30, recipient="BBB06")]
    events += [ev(12, 12, "tackle", "AAA", x=80.0, y=30.0, outcome="won"),
               ev(13, 13, "interception", "AAA", x=70.0, y=30.0),
               ev(14, 14, "tackle", "AAA", x=20.0, y=30.0, outcome="won")]  # deep in own half: not counted
    md = MatchData(tiny_meta(), events, tiny_frames())
    f = S.ppda(md, "AAA", 1, 0, 60)
    assert f.detail == dict(opp_passes=10, defensive_actions=2)
    assert f.value == 5.0


def test_field_tilt_uses_each_teams_attacking_third():
    events = [ev(1, 1, "pass", "AAA", x=80.0, outcome="complete", end_x=85, end_y=34, recipient="AAA06"),
              ev(2, 2, "pass", "AAA", x=90.0, outcome="complete", end_x=95, end_y=34, recipient="AAA06"),
              ev(3, 3, "pass", "BBB", x=20.0, outcome="complete", end_x=15, end_y=34, recipient="BBB06"),
              ev(4, 4, "pass", "BBB", x=80.0, outcome="complete", end_x=85, end_y=34, recipient="BBB06")]
    md = MatchData(tiny_meta(), events, tiny_frames())
    assert S.field_tilt(md, "AAA", 1, 0, 60).value == pytest.approx(66.7)


def test_sprint_needs_a_full_second():
    def mover(xy):
        # home slot 5 runs at 8 m/s (28.8 km/h) for 1.5 s from t=10 s, then 0.5 s from t=20 s
        x = np.full(len(xy), 30.0)
        for t0, dur in ((100, 15), (200, 5)):
            for k in range(dur):
                x[t0 + k + 1:] += 0.8
        xy[:, 5, 0] = x
    md = MatchData(tiny_meta(), [], tiny_frames(mover=mover))
    f = S.player_physical(md, "AAA05", 1, 0, 59.9)
    assert f.value == 1
    assert f.detail["top_speed_kmh"] == pytest.approx(28.8, abs=0.1)
    assert f.detail["distance_m"] == 16


def test_pass_difficulty_rises_with_pressure_and_length():
    easy = dict(length=8, pressure=8, lane=8, space=8, lofted=False)
    pressured = dict(easy, pressure=0.8)
    longer = dict(easy, length=40)
    blocked = dict(easy, lane=0.5)
    e = S.pass_difficulty_score(easy)
    assert e < 20
    assert S.pass_difficulty_score(pressured) > e
    assert S.pass_difficulty_score(longer) > e
    assert S.pass_difficulty_score(blocked) > e


def test_facts_recompute_exactly():
    m = simulate(3, period_minutes=6.0)
    md = from_match(m)
    for fact in (S.passes(md, md.home, 1, 0, 300), S.ppda(md, md.away, 1, 0, 300),
                 S.overloads(md, md.home, 1, 0, 300, side="right"), S.tempo(md, md.home, 1, 0, 300)):
        again = S.recompute(md, fact.stat, fact.args)
        assert again.value == fact.value and again.evidence == fact.evidence
