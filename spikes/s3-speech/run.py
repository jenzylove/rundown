"""Spike S3: do Azure AI Speech neural voices give word timings good enough to sync captions?

    python spikes/s3-speech/run.py

Synthesises one presenter line per language with word-boundary events, then
transcribes the audio back with word-level timestamps and compares the two
clocks: the caption sync error is |boundary offset - recognised offset| per word.

Needs AZURE_SPEECH_REGION and a key for the AI Services resource
(AZURE_SPEECH_KEY, or AZURE_OPENAI_API_KEY when it is the same multi-service resource).
"""

from __future__ import annotations

import difflib
import json
import os
import re
import statistics
import time
import unicodedata
from pathlib import Path

import azure.cognitiveservices.speech as sdk

OUT = Path(__file__).resolve().parent / "out"
LINES = {
    "en-GB": ("en-GB-RyanNeural", "Harbour were allowing 8.3 passes per defensive action. Now it's 5.8, and Eastmoor have no time to play out."),
    "es-ES": ("es-ES-AlvaroNeural", "Harbour dejaban 8,3 pases por acción defensiva. Ahora solo 5,8, y Eastmoor no tiene tiempo para salir."),
    "fr-FR": ("fr-FR-HenriNeural", "Harbour laissaient 8,3 passes par action défensive. C'est maintenant 5,8, et Eastmoor n'a plus le temps de relancer."),
    "pt-BR": ("pt-BR-AntonioNeural", "O Harbour permitia 8,3 passes por ação defensiva. Agora são 5,8, e o Eastmoor não tem tempo para sair jogando."),
}


def norm(w: str) -> str:
    w = unicodedata.normalize("NFKD", w.lower())
    return re.sub(r"[^\w]", "", "".join(c for c in w if not unicodedata.combining(c)))


def config() -> sdk.SpeechConfig:
    key = os.environ.get("AZURE_SPEECH_KEY") or os.environ["AZURE_OPENAI_API_KEY"]
    return sdk.SpeechConfig(subscription=key, region=os.environ["AZURE_SPEECH_REGION"])


def synth(locale: str, voice: str, text: str) -> dict:
    cfg = config()
    cfg.speech_synthesis_voice_name = voice
    cfg.set_speech_synthesis_output_format(sdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm)
    wav = OUT / f"{locale}.wav"
    s = sdk.SpeechSynthesizer(speech_config=cfg, audio_config=sdk.audio.AudioOutputConfig(filename=str(wav)))
    words = []

    def on_word(e):
        if e.boundary_type == sdk.SpeechSynthesisBoundaryType.Word:
            words.append(dict(text=e.text, t=e.audio_offset / 1e7, d=e.duration.total_seconds()))
    s.synthesis_word_boundary.connect(on_word)
    t0 = time.time()
    r = s.speak_text_async(text).get()
    assert r.reason == sdk.ResultReason.SynthesizingAudioCompleted, r.cancellation_details.error_details
    return dict(latency_s=round(time.time() - t0, 2), audio_s=round(r.audio_duration.total_seconds(), 2),
                words=words, wav=wav)


def recognise(locale: str, wav: Path) -> list[dict]:
    cfg = config()
    cfg.speech_recognition_language = locale
    cfg.request_word_level_timestamps()
    cfg.output_format = sdk.OutputFormat.Detailed
    rec = sdk.SpeechRecognizer(speech_config=cfg, audio_config=sdk.audio.AudioConfig(filename=str(wav)))
    words, done = [], []
    rec.recognized.connect(lambda e: words.extend(
        dict(text=w["Word"], t=w["Offset"] / 1e7, d=w["Duration"] / 1e7)
        for w in json.loads(e.result.json)["NBest"][0]["Words"]))
    rec.session_stopped.connect(lambda e: done.append(1))
    rec.canceled.connect(lambda e: done.append(1))
    rec.start_continuous_recognition()
    while not done:
        time.sleep(0.1)
    rec.stop_continuous_recognition()
    return words


def align(tts: list[dict], asr: list[dict]) -> list[float]:
    """Errors (s) between a word's boundary offset and where the recogniser heard the same word.
    Words are aligned as sequences, so a misheard name or a number the recogniser writes
    differently is skipped rather than throwing the rest out of step."""
    a, b = [norm(w["text"].split()[0]) for w in tts], [norm(w["text"]) for w in asr]
    errs = []
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        errs += [abs(tts[blk.a + i]["t"] - asr[blk.b + i]["t"]) for i in range(blk.size)]
    return errs


def main():
    OUT.mkdir(exist_ok=True)
    rows = []
    for locale, (voice, text) in LINES.items():
        s = synth(locale, voice, text)
        asr = recognise(locale, s["wav"])
        errs = align([w for w in s["words"] if norm(w["text"])], asr)
        n_text = len([w for w in text.split() if norm(w)])
        rows.append(dict(locale=locale, voice=voice, latency_s=s["latency_s"], audio_s=s["audio_s"],
                         words_in_text=n_text, boundaries=len(s["words"]), matched=len(errs),
                         err_median_ms=round(1000 * statistics.median(errs)) if errs else None,
                         err_max_ms=round(1000 * max(errs)) if errs else None,
                         monotonic=all(a["t"] <= b["t"] for a, b in zip(s["words"], s["words"][1:])),
                         heard=" ".join(w["text"] for w in asr)))
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    (OUT.parent / "results.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
