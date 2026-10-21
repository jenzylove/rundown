# Spike S2: agents on Foundry emit Overlay Script

**Date:** 21 Oct 2026 · **Code:** `spikes/s2-agents/run.py` (throwaway) · **Raw:** `spikes/s2-agents/results.json`

## Question
Can Agent Framework (Python) call a Foundry model with structured output reliably
enough to emit valid Overlay Script JSON?

## What was run
- Microsoft Agent Framework 1.22 (`agent-framework-core`, `agent-framework-openai`), an `Agent`
  on `OpenAIChatCompletionClient` routed to the Foundry resource's Azure OpenAI endpoint, with
  API-key auth. Model: **gpt-4.1-mini** (deployment `gpt-4.1-mini`, Sweden Central).
  `FoundryChatClient` needs an Entra credential; this session only has the key. Same model,
  same resource. Switch to the Foundry client when the deploy has a managed identity.
- Input: 4 simulated matches (seeds 3, 11, 21, 42). Every first-half talking point the
  detectors found, in English and Spanish, each run twice: **92 segments**.
- Per segment the rules Telestrator cuts the clip and computes the facts. The agent gets the
  talking point, the facts under short aliases (`F1`…), the 22 players, the clip's events and
  three position snapshots. It returns title, cues, stat card and script as a Pydantic
  `response_format`.
- Each result is assembled into an Overlay Script v1 segment and checked:
  1. JSON schema (`schema/overlay-script.v1.json`)
  2. fact keys and event ids exist; arrows match their event's coordinates
  3. cues fit in the segment; script lines don't overlap
  4. **grounding:** every number on screen or spoken is a value of a fact the item cites
     (the talking point's minute and "10/45/90 minutes" framing allowed)
- A failure goes back to the same agent session once, with the error list (the Verifier retry).

## Output
Three rounds, each fixing what the previous one showed:

| Round | Change | Valid first try | Valid after 1 retry | p50 / p95 per call |
|---|---|---|---|---|
| 1 | agent writes the whole timeline, stat card included | 34/92 (37%) | 74/92 (80%) | 7.7 s / 12.1 s |
| 2 | **code** places the stat card and end hold; title rule; checker fixes | 67/92 (73%) | 82/92 (89%) | 6.5 s / 9.7 s |
| 3 | stat card rows as `{label, value}`; only drawable events offer `to_xy`; Verifier re-cites a number that matches an uncited fact (4 repairs) | 67/92 (73%) | **88/92 (96%)** | **6.3 s / 9.1 s** |

Failures by type, round 1 → round 3 (first try): stat card past the segment end or missing (the
model can't do timeline arithmetic: removed by moving timing to code) · title over 48 characters ·
numbers the model computed itself ("almost six metres", "29% → 43%" rounded) · arrows on pressure
events, which have no end point · clip times read out ("at 7.5 seconds") · invented shirt numbers.

The 4 that still fail after the retry: an arrow with copied coordinates off by more than 1 m, two
stat cards with stray numbers ("1st half" → `1`), and one with **broken Unicode escapes**
(`presi\x0f3n` where it meant "presión").

## Verdict: **PASS, with conditions**
Structured output on gpt-4.1-mini is reliable enough when the agent decides **what** to show and
say, and code owns **where and when**. 96% pass after one retry; the rest drop to the rules
segment, as planned.

## What the build must change (found here)
1. **Split authorship.** The agent picks moments, players, events, facts and words; code does all
   timing (freezes → segment time, end hold, stat card placement) and all geometry (arrow
   coordinates filled from the event id, never copied by the model).
2. **The Verifier is code first.** Schema, ids, grounding and timing checks are deterministic; one
   retry with the error list; then fall back to the rules segment. Add: reject control characters
   and mojibake; re-cite numbers that match an uncited fact.
3. **"Valid" is not "good".** A Spanish segment that passed every check still had English in the
   stat card ("passes per action"), decimal points instead of commas, and its only "key moment"
   freeze at clip time 0. The Verifier needs a language check and a freeze-near-anchor check, and
   Phase 5 has to score agent segments against rules segments, not just count valid JSON.
4. **Latency.** 6–9 s per call, plus up to one retry. The rundown can't be written at the whistle;
   the crew drafts segments from the provisional picks during the half and only re-ranks and fills
   gaps at the whistle, so "ready at the whistle" still holds.
5. **Auth.** Key auth works today through the Azure OpenAI route. Container Apps should use a
   managed identity and `FoundryChatClient`, so traces land in the Foundry project.
