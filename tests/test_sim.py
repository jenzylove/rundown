import json

import numpy as np
import pytest

from rundown.engine.data import from_match
from rundown.sim import simulate
from rundown.sim.match import xg_model


@pytest.fixture(scope="module")
def short_match():
    return simulate(11, period_minutes=8.0)


def test_same_seed_same_match():
    a = simulate(5, period_minutes=3.0)
    b = simulate(5, period_minutes=3.0)
    assert a.events == b.events
    assert np.array_equal(a.frames, b.frames)


def test_players_and_ball_stay_on_the_pitch(short_match):
    f = short_match.frames
    xy = f[:, 5:].reshape(-1, 22, 2)
    assert xy[..., 0].min() >= 0 and xy[..., 0].max() <= 105
    assert xy[..., 1].min() >= 0 and xy[..., 1].max() <= 68


def test_no_teleporting(short_match):
    md = from_match(short_match)
    xy = md.frames[:, 5:].reshape(-1, 22, 2)
    step = np.linalg.norm(np.diff(xy, axis=0), axis=2)
    same_period = md.frames[1:, 0] == md.frames[:-1, 0]
    assert step[same_period].max() < 1.2  # < 43 km/h between 0.1 s frames


def test_events_are_ordered_and_complete(short_match):
    ev = short_match.events
    keys = [(e["period"], e["t"]) for e in ev]
    assert keys == sorted(keys)
    for e in ev:
        if e["type"] == "pass":
            assert e["recipient"] and e["outcome"] in ("complete", "intercepted", "incomplete")
    goals = [e for e in ev if e["type"] == "goal"]
    assert len(goals) == sum(short_match.score)


def test_answer_key_is_kept_apart(short_match):
    blob = json.dumps(short_match.meta()) + json.dumps(short_match.events)
    for s in short_match.answer_key():
        assert s["description"] not in blob
    assert "stories" not in short_match.meta()


def test_xg_is_higher_closer_to_goal():
    assert xg_model(99, 34) > xg_model(90, 34) > xg_model(80, 34)
    assert xg_model(99, 34) > xg_model(99, 10)
    assert xg_model(95, 34, header=True) < xg_model(95, 34)
