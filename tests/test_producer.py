"""The rules Producer keeps every segment inside the Overlay Script schema, whatever the slot."""

from rundown.agents import verify
from rundown.engine import detect
from rundown.engine.data import from_match
from rundown.sim import simulate
from rundown.studio import producer


def test_one_talking_point_never_breaks_the_schema():
    # found by the half-time eval: seed 101 had one talking point, which got all 86 s of the slot
    md = from_match(simulate(21, period_minutes=20.0))
    t_end = float(md.frames[md.frame_index(1, 1e9), 1])
    c = (detect.scan(md, 1, t_end) + detect.moments(md, 1, 0, t_end))[0]
    r = producer.rundown(md, [c], t_end, 90.0)
    assert len(r["segments"]) == 1
    assert r["segments"][0]["duration_s"] <= producer.MAX_SEGMENT_S
    assert verify.segment_errors(md, r["segments"][0]) == []
