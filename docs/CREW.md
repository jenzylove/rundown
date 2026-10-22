# The agent crew

Code: `rundown/agents/` · Tests: `tests/test_agents.py` (offline) · Eval: `python -m rundown.evals.crew`

## Who does what

| Role | Made of | Decides | Never does |
|---|---|---|---|
| Spotter | code (`engine/detect.py`) | which changes in the stats are talking points | — |
| Analyst | agent | how much each point matters at half-time (0–1), and why | write a number that isn't a fact |
| Producer | agent | which points make the rundown for this slot and cut, in what order, and why | break the variety rules (checked) |
| Telestrator | agent | the picture: freezes, players to ring, passes to draw, a zone or line from named options | write coordinates or timings |
| Writer | agent | title, lower third, stat card and presenter lines, per language and cut | write a number that isn't a fact |
| Verifier | code (`agents/verify.py`) | pass or fail, with reasons | — |

The agents run on **Microsoft Agent Framework** (`agent_framework.Agent`) with structured output
(Pydantic). Today they reach the Foundry resource's **gpt-4.1-mini** deployment by key; with a managed
identity, `RUNDOWN_FOUNDRY_CLIENT=1` switches to `FoundryChatClient`. When
`APPLICATIONINSIGHTS_CONNECTION_STRING` is set, Agent Framework's OpenTelemetry traces go to
Application Insights, which Foundry tracing reads.

## The rules that make it reliable
Each came out of spike S2 or a failed run during the build.

1. **Agents choose; code computes.** The Telestrator names clip times, slots, event ids and options
   ("left channel"); `studio/telestrate.plan_layout` turns them into timed cues and coordinates.
   The model never does timeline arithmetic, which was S2's biggest failure.
2. **Every number is checked against the engine.** An agent writes a fact's value or a `{F1}`
   placeholder; `agents/words.Table` checks each number against the talking point's facts, records
   the citation, and formats it for the language (`8,3` in Spanish). A number that matches no fact
   fails, and so does a number spelled out in words. Stat-card rows are aliases only, so the card's
   numbers can't be wrong.
3. **Enums, not instructions.** Ids and fact aliases are per-call `Literal` types in the
   structured-output schema, so the model can't return an id that doesn't exist.
4. **One retry, then route around.** A rejected answer goes back once with the Verifier's errors. If
   it fails again, or the agent is switched off, the crew uses the rules version of that piece
   (rules Writer, rules Telestrator, rules ranking) and says so in the trail.
5. **The finished segment is verified as a whole.** Schema, every cited fact recomputed by the
   engine, every event id real, every cue inside the segment. A segment that fails **never goes on
   air**: the Producer swaps in the next point that passes, or drops the slot.
6. **Code screens the evidence; the Producer chooses.** Each talking point keeps its scan history.
   The Producer only sees points the detectors saw steadily, recently and clearly (plus every goal
   and chance), strongest first, with how each held up since it was spotted. Without this it chose
   well-told one-off swings over steady stories (tuned on practice seeds; `docs/EVALS.md`).
7. **Write early, lock fast.** The Analyst scores points as they are spotted. Segments for the
   likely picks (rules picks, the Analyst's top three and the three with the clearest evidence) are
   drafted during the half. The
   Telestrator's plan has no words in it, so every language and cut reuses it and a re-cut only
   needs the Writer.
8. **A deadline at the whistle.** A segment not ready 20 s after the whistle goes out as the rules
   version; the agent draft keeps going for swaps and other cuts.
9. **Rate limits are not mistakes.** A 429 is waited out (honouring `retry after`) without spending
   the Verifier retry. All crews share one event loop and one client, capped at 8 calls in flight.

## The trail
Every agent call, retry, fallback, kill and Verifier check is a trail item (`agents/state.Store`):
who, what, status, time taken, why, and the event ids involved. Live runs stream it over the
WebSocket (`type: "trail"`), `GET /api/runs/{id}/trail` returns it, and the desk shows the crew at
work. The crew reads from memory; the API mirrors every `put` and `record` to Cosmos DB
(`api/archive.py`), and `/proof` shows the trail of any run, live or replayed from there.

`POST /api/runs/{id}/crew/kill {"role": "telestrator"}` switches an agent off mid-run, and
`/crew/revive` switches it back on. Work already finished stays in use: to see the reroute, kill
before or during the first half.

## Results (21 Oct, gpt-4.1-mini, four recorded matches)
`python -m rundown.evals.crew out/matches/{3,11,21,42}` · raw: `docs/evals/crew-reliability.json`

| | jobs | passed first try | passed after one retry | routed around |
|---|---|---|---|---|
| Analyst | 46 | 41 | 43 | 3 |
| Producer | 8 | 7 | 8 | 0 |
| Telestrator | 45 | 45 | 45 | 0 |
| Writer | 45 | 21 | 41 | 4 |

On air, across 8 rundowns (neutral English and away-fan Spanish for each match): **24/24 pictures**
and **21/24 scripts** by the agents, **24/24 segments verified**, every rundown 90.0 s for a 90 s slot.
**Lock after the whistle: median 1.9 s, max 8.3 s.**

With the Telestrator switched off (`--kill telestrator`, seeds 3 and 42, raw:
`docs/evals/crew-kill-telestrator.json`): 12/12 segments verified with rules pictures and mostly
agent words, lock median 3.9 s.

## Found and fixed while building it
- **Shared client across event loops.** Each crew had its own event loop, but runs share the model
  client, whose connections belong to the first loop that used them. Two concurrent runs broke it
  (`'NoneType' object is not callable`). Now there is one crew loop per process.
- **Rate limits ate the retries.** Four parallel runs hit the deployment's 429s, and each 429 used
  up the Verifier retry. Now they are waited out.
- **Banning digits backfired.** Told never to type a number, the model spelled numbers out ("four
  point eight") or wrote them anyway; the Writer passed first time in 3 of 42 jobs. Checking typed
  numbers against the facts instead took it to 21 of 45 first time and 41 after the retry.
- **Rules Telestrator bug.** Its press segment could highlight a ball regain outside its own clip,
  which overran the segment. The agent route hid it; the kill eval found it; the Verifier rejected
  it. Fixed, and all 46 rules segments on the four seeds now verify.
- **Stoppage time.** A goal at 47' was written as "just after half-time". Agents now get the period,
  and minutes past 45 are labelled first-half stoppage time.

## Known gaps
- The Writer still loses about one in ten jobs to the rules Writer, mostly over length limits.
- "Valid" isn't "good": checks catch wrong numbers, wrong language and bad timing, not loose
  wording (Spanish "presión" for a field-tilt fact, "early" for a 47th-minute goal). Phase 5 scores
  agent segments against rules segments.
- French and Portuguese work in the Writer, but there is no rules Writer to fall back to, so the
  API offers English and Spanish only.
- Foundry tracing needs the project's Application Insights connection string in the environment.
