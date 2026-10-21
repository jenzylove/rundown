# Spike S3: Azure AI Speech word timings for captions

**Date:** 21 Oct 2026 · **Code:** `spikes/s3-speech/run.py` (throwaway) · **Raw:** `spikes/s3-speech/results.json`

## Question
Do Azure AI Speech neural voices return word timings in EN/ES/FR/PT that are good enough to sync
captions (within 150 ms)?

## What was run
- Speech SDK 1.52, Sweden Central, the Foundry AI Services resource's key (no separate Speech
  resource needed).
- One presenter line per language, with a decimal in it, synthesised to 24 kHz WAV, collecting
  word-boundary events.
- To measure sync, not just trust it: each WAV is transcribed back with word-level timestamps,
  the two word lists are aligned as sequences, and the error per matched word is
  |boundary offset − recognised offset|.

## Output
| Voice | Synth time | Audio | Boundaries / words | Matched | Median error | Max error |
|---|---|---|---|---|---|---|
| en-GB-RyanNeural | 1.3 s | 8.8 s | 19 / 19 | 16 | 64 ms | 98 ms |
| es-ES-AlvaroNeural | 1.4 s | 8.5 s | 16 / 17 | 12 | 47 ms | 113 ms |
| fr-FR-HenriNeural | 1.1 s | 8.2 s | 18 / 18 | 13 | 50 ms | 150 ms |
| pt-BR-AntonioNeural | 2.0 s | 10.0 s | 19 / 20 | 14 | 37 ms | 100 ms |

Offsets are monotonic in every language. Decimals are read the local way ("ocho coma tres",
"huit virgule trois", "oito vírgula três").

## Verdict: **PASS**
Word boundaries land within 150 ms of where the word is heard in all four languages, and
synthesis takes about 1.5 s for 9 s of audio, so voicing a 90 s rundown is about 15 s, and
segments can be voiced in parallel.

## What the build must change (found here)
1. **One boundary can cover two tokens.** ES and PT return "8,3 pases" as a single boundary.
   Captions must highlight a boundary's text span, not assume one boundary per word.
2. **Club names get mispronounced in other languages.** "Eastmoor" came back as "smour" (ES) and
   "oyish moore" (PT); "Harbour" as "harber" or "arbour". Wrap team and player names in SSML
   `<lang xml:lang="en-GB">`, or give the fictional clubs names that read well in all four.
3. **Writers must use the language's decimal comma** in ES, FR and PT text. The voice reads it
   correctly, and it matches the on-screen numbers (see S2, finding 3).
4. The error measure includes the recogniser's own timing error, so the real caption error is at
   most what's shown.
