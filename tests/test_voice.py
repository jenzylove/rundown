"""Re-timing a segment to its voice-over, offline (a made-up synthesis result)."""

from rundown.agents import verify
from rundown.engine import detect
from rundown.engine.data import from_match
from rundown.sim import simulate
from rundown.studio import telestrate, voice


def test_script_follows_the_voice_and_a_long_voice_grows_the_end_hold():
    md = from_match(simulate(21, period_minutes=20.0))
    t_end = float(md.frames[md.frame_index(1, 1e9), 1])
    c = next(c for t in range(600, int(t_end), 60) for c in detect.scan(md, 1, float(t)))
    seg = telestrate.build(md, c, target_s=20.0)
    n = len(seg["script"])
    audio_s = seg["duration_s"] + 5.0  # the voice runs five seconds past the segment
    marks = [i * audio_s / n for i in range(n)]
    words = [dict(text="word", t=m + 0.1, d=0.3) for m in marks]
    track = voice.fit(seg, dict(audio_s=audio_s, marks=marks, words=words))

    assert [ln["t"] for ln in seg["script"]] == [round(voice.LEAD_S + m, 2) for m in marks]
    assert seg["duration_s"] == round(voice.LEAD_S + audio_s + voice.TAIL_S, 2)
    assert verify.segment_errors(md, seg) == [], "the grown end hold must still fit the segment"
    assert track["words"][0]["t"] == round(words[0]["t"] + voice.LEAD_S, 3)


def test_a_short_voice_starts_where_the_script_planned_the_talk():
    md = from_match(simulate(21, period_minutes=20.0))
    t_end = float(md.frames[md.frame_index(1, 1e9), 1])
    c = next(c for t in range(600, int(t_end), 60) for c in detect.scan(md, 1, float(t)))
    seg = telestrate.build(md, c, target_s=30.0)
    planned = seg["script"][0]["t"]
    n = len(seg["script"])
    gaps = voice.gaps(seg)
    assert len(gaps) == n - 1 and all(voice.GAP_MS <= g <= voice.MAX_GAP_MS for g in gaps)
    audio_s = 6.0
    marks = [i * audio_s / n for i in range(n)]
    words = [dict(text="word", t=m + 0.1, d=0.3) for m in marks]
    track = voice.fit(seg, dict(audio_s=audio_s, marks=marks, words=words))
    assert track["start"] == round(max(voice.LEAD_S, planned), 3)
    assert seg["script"][0]["t"] == round(track["start"], 2)
    assert verify.segment_errors(md, seg) == []
