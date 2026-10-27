"""Voice-over: Azure AI Speech neural voices read a segment's script.

Each segment's lines are read as one SSML document, with a bookmark before every line
and word-boundary events for the captions. The script is then re-timed to the audio:
a line starts where its bookmark fell, and if the voice runs past the segment, the end
hold (the stat card) grows to cover it. Spike S3 measured the word timings within
150 ms of the heard word in EN/ES/FR/PT.
"""

from __future__ import annotations

import hashlib
import os
import threading
from xml.sax.saxutils import escape

VOICES = {"en": ("en-GB", "en-GB-RyanNeural"), "es": ("es-ES", "es-ES-AlvaroNeural"),
          "fr": ("fr-FR", "fr-FR-HenriNeural"), "pt": ("pt-BR", "pt-BR-AntonioNeural")}
LEAD_S = 0.4  # the voice starts this far into the segment
GAP_MS = 350  # shortest pause between lines
MAX_GAP_MS = 2500  # longest: the script's own spacing decides in between
TAIL_S = 0.6  # silence kept after the last word before the segment ends


def configured() -> bool:
    return bool(os.environ.get("AZURE_SPEECH_REGION")
                and (os.environ.get("AZURE_SPEECH_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")))


def gaps(seg: dict) -> list[int]:
    """The pauses the script planned between its lines, in ms, so the voice keeps the segment's pace."""
    sc = seg["script"]
    return [int(min(MAX_GAP_MS, max(GAP_MS, 1000 * (b["t"] - a["t"] - a["d"])))) for a, b in zip(sc, sc[1:])]


def ssml(lines: list[str], lang: str, pauses: list[int] | None = None) -> str:
    locale, voice = VOICES[lang]
    pauses = pauses or [GAP_MS] * (len(lines) - 1)
    body = "".join((f'<break time="{pauses[i - 1]}ms"/>' if i else "") + f'<bookmark mark="L{i}"/>{escape(t)}'
                   for i, t in enumerate(lines))
    return (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{locale}">'
            f'<voice name="{voice}">{body}</voice></speak>')


def key(seg: dict, lang: str) -> str:
    text = "\n".join(ln["text"] for ln in seg["script"])
    return hashlib.sha1(f"{lang}|{text}".encode()).hexdigest()[:16]


def synthesise(lines: list[str], lang: str, pauses: list[int] | None = None) -> dict:
    """-> {audio (mp3 bytes), audio_s, marks [line start s], words [{text, t, d}]}"""
    import azure.cognitiveservices.speech as sdk
    cfg = sdk.SpeechConfig(subscription=os.environ.get("AZURE_SPEECH_KEY") or os.environ["AZURE_OPENAI_API_KEY"],
                           region=os.environ["AZURE_SPEECH_REGION"])
    cfg.set_speech_synthesis_output_format(sdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3)
    s = sdk.SpeechSynthesizer(speech_config=cfg, audio_config=None)
    words, marks, lock = [], {}, threading.Lock()

    def on_word(e):
        if e.boundary_type == sdk.SpeechSynthesisBoundaryType.Word:
            with lock:
                words.append(dict(text=e.text, t=round(e.audio_offset / 1e7, 3), d=round(e.duration.total_seconds(), 3)))

    def on_mark(e):
        marks[e.text] = e.audio_offset / 1e7
    s.synthesis_word_boundary.connect(on_word)
    s.bookmark_reached.connect(on_mark)
    r = s.speak_ssml_async(ssml(lines, lang, pauses)).get()
    if r.reason != sdk.ResultReason.SynthesizingAudioCompleted:
        raise RuntimeError(f"speech synthesis failed: {r.cancellation_details.error_details}")
    return dict(audio=bytes(r.audio_data), audio_s=r.audio_duration.total_seconds(),
                marks=[marks.get(f"L{i}", 0.0) for i in range(len(lines))], words=sorted(words, key=lambda w: w["t"]))


def fit(seg: dict, v: dict) -> dict:
    """Re-time the segment's script to the voice; grow the end hold if the voice runs long.
    Returns the voice track for the client: start, length and word timings, in segment time."""
    lines = seg["script"]
    # the voice starts where the script put the talk (on the moment, after the build-up), if it still fits
    lead = max(LEAD_S, min(lines[0]["t"], seg["duration_s"] - v["audio_s"] - TAIL_S))
    starts = [lead + m for m in v["marks"]] + [lead + v["audio_s"]]
    for i, ln in enumerate(lines):
        ln["t"] = round(starts[i], 2)
        # a line ends at its last word, not at the next line's bookmark
        last = [w for w in v["words"] if starts[i] - lead <= w["t"] < starts[i + 1] - lead]
        end = (last[-1]["t"] + last[-1]["d"] + lead) if last else starts[i + 1]
        ln["d"] = round(max(end - ln["t"], 0.5), 2)
    need = lead + v["audio_s"] + TAIL_S
    over = need - seg["duration_s"]
    if over > 0:  # the stat card and its freeze are the last cues; hold them longer
        for q in seg["cues"]:
            if q["t"] + q["d"] >= seg["duration_s"] - 0.05 and q["kind"] in ("statcard", "freeze"):
                q["d"] = round(q["d"] + over, 2)
        seg["duration_s"] = round(need, 2)
    return dict(start=round(lead, 3), audio_s=round(v["audio_s"], 2),
                words=[dict(text=w["text"], t=round(w["t"] + lead, 3), d=w["d"]) for w in v["words"]])
