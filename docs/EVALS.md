# Evals

Does the studio put the right stories on air, are its numbers true, and do the agents do better than
a rules-only picker? Measured on held-out seeds (101 to 112) against the stories each match plants and
keeps apart as an answer key. The agents never see the key; only the scorer reads it. Changes are
tuned on separate practice seeds (201 to 240), never on the held-out ones.

Rerun it (needs the `AZURE_OPENAI_*` settings; `--rules-only` needs nothing):

```bash
python -m rundown.evals.rundown --seeds 101-112 --out docs/evals/rundown.json
python -m rundown.evals.foundry docs/evals/rundown.json --out docs/evals/foundry-groundedness.json
python -m rundown.evals.dev cache && python -m rundown.evals.dev picks --agents   # practice seeds
```

## Method
For each seed the first half is played as a live run plays it: the detectors scan every match
minute and spot talking points. At the whistle the neutral English rundown for a 90 s slot is made
twice from the same talking points, once by the agent crew (Analyst, Producer, Telestrator, Writer,
gated by the Verifier) and once by the rules-only baseline (the "biggest stat change" picker).

- **Spotted:** planted first-half stories the detectors found by half-time. Shared by both systems:
  detection is code, not agents.
- **Story recall:** planted first-half stories that went on air.
- **Precision:** aired talking points that are a planted story or a real goal or big chance. Every
  aired point is a real, recomputable stat change; the rest are changes the answer key doesn't plant.
- **Grounding:** aired segments whose every fact the Verifier recomputes to the same value from the
  stats engine, with every cited event real. Both systems are checked the same way.
- **Foundry groundedness:** Azure AI Evaluation's GroundednessEvaluator (a model judge, 1 to 5)
  reads each aired script against the facts and detail the writer had.
- **Slot fit:** distance from the 90 s slot.

## Results (22 Oct, gpt-4.1-mini, held-out seeds 101 to 112)
12 seeds, 33 planted first-half stories. The detectors spotted **23/33 (70%)**
by half-time, a median 6.8 min after each started: that is the ceiling for any picker.

| | Agent crew | Rules-only baseline |
|---|---|---|
| Story recall | **19/33 (58%)** | 17/33 (52%) |
| Precision | **27/34 (79%)** | 25/34 (74%) |
| Grounding (Verifier) | 34/34 (100%) | 34/34 (100%) |
| Foundry groundedness, scripts scoring 4 or 5 | 34/34 (100%), mean 4.79 | 30/34 (88%), mean 4.53 |
| Slot fit (median, worst) | 0.01 s, 26.0 s | 0.01 s, 26.0 s |
| Locked after the whistle (median, worst) | 1.97 s, 7.85 s | at once |
| Answers the Verifier rejected before air | 39 (6 routed to the rules crew) | - |

Reference, not a product: a simple rule that prefers talking points still showing at the whistle and
seen in many scans picks 18/33 (55%) of the planted stories, precision 26/34 (76%).

**What it says.** Nothing unverified went on air from either system. The agent crew now puts more
planted stories on air than the rules baseline and makes fewer off-key picks, but the margin is
small: two stories out of 33, one more than the persistence rule. Most of the gain comes from
code screening the evidence the Producer chooses from (below), not from better editorial judgement
by the model. The Foundry judge rates the agents' scripts a little higher than the rules templates.

The worst slot fit (26 s) is seed 101 for both: one talking point qualified, so the rundown is one
60 s segment, 64 s for a 90 s slot.

## Tuning the Producer
Before tuning, on these same held-out seeds, the agents' story recall was 52%, 48%, 39% over three
runs (precision 62% in the last), below the baseline's 52%.

On practice seeds 201 to 240 (105 planted stories, 73% spotted), planted stories and noise separate
on information the studio has live at the whistle: planted stories showed up in a median 11 scans
against 3, peaked at strength 1.0 against 0.48, and were still showing 3 minutes before the whistle
against 13. The agent Producer saw none of this, and its picks showed it: it chose well-told
one-off swings ("dropping deeper", seen in 1 of 29 scans) and skipped steady stories (a player fading,
seen in 6 of 9).

| Practice seeds 201 to 240 | Story recall | Precision |
|---|---|---|
| Agents, before | 44% | 65% |
| Agents, given how each point held up | 48 to 50% | 68 to 72% |
| Agents, with code screening the evidence | 55% (two runs) | 76% |
| Rules-only baseline | 51% | 72% |
| Persistence rule (reference) | 57% | 76% |

The change that worked: each talking point keeps its scan history, and code screens the Producer's
options (seen in at least half the scans since spotted, seen in the last five minutes, peak strength
at least 0.6; goals and chances always pass; the next strongest come back if too few kinds remain).
The Producer still decides what airs and in what order, and sees how each point held up
(`agents/crew.py`: `_clear`, `_held_up`). Then the held-out seeds were run once.

## First misses and fixes
1. **A lone talking point broke the schema** (first run). Seed 101 had one talking point; both
   Producers gave it the whole 86 s, past the Overlay Script's 60 s segment limit. The agents' segment
   failed the Verifier, every replacement failed the same way, and the agents put **nothing** on air
   (slot fit 86 s). The rules segment aired without being checked. Fix: segments are capped at 60 s
   and the rundown runs short (`studio/producer.segment_length`, `tests/test_producer.py`).
2. **The judge was missing what the writer had** (Foundry, first pass). Given only each fact's
   value, the judge marked true lines as unsupported (a goal's minute and a player's top speed live in
   the fact's detail) and the agents looked better (29/34 against 21/34). With the
   detail and the talking point's headline in the context, the untuned agents and the rules scored
   about equally (32/34 against 31/34). All passes are published.
3. **The agent Producer picked noise** (above). Fixed on practice seeds by screening the evidence.
4. **Not fixed: detection.** The detectors miss 30% of planted stories (mostly deep blocks, tempo
   changes and late fatigue). No picker can air a story that isn't spotted.

Judge caveats: the judge is gpt-4.1-mini, the model that writes the agents' scripts. All four
scores below 4 are the rules crew's goal segments; the judge's reasons disagree with the scripts on
the scoring minute or on whether the chance went in, and they haven't been checked against the
match data line by line. The run could not be logged to the Foundry project: the deploy identity lacks
`Microsoft.CognitiveServices/accounts/AIServices/assets/write` on the Foundry resource.

## Every miss (held-out run)
- seed 101: not spotted by half-time: Harbour drop into a deep block from 15'
- seed 102: not spotted by half-time: Eastmoor drop into a deep block from 9'
- seed 102 agents: aired a point not in the answer key: Harbour dropping deeper: line 44.1 m to 38.3 m (19.0')
- seed 102 rules: aired a point not in the answer key: Harbour dropping deeper: line 44.1 m to 38.3 m (19.0')
- seed 103: not spotted by half-time: Ugo Esposito (Harbour RW) tires from 30'
- seed 103: not spotted by half-time: Eastmoor raise the tempo from 31'
- seed 103 agents: spotted but not aired: Harbour switch to a high press from 28'
- seed 103 agents: aired a point not in the answer key: Eastmoor dropping deeper: line 34.3 m to 29.9 m (36.0')
- seed 103 agents: aired a point not in the answer key: Harbour moving it quicker: 11.5 to 14.0 passes a minute on the ball (44.0')
- seed 103 rules: spotted but not aired: Harbour switch to a high press from 28'
- seed 103 rules: aired a point not in the answer key: Eastmoor dropping deeper: line 34.3 m to 29.9 m (36.0')
- seed 103 rules: aired a point not in the answer key: Harbour moving it quicker: 11.5 to 14.0 passes a minute on the ball (44.0')
- seed 104: not spotted by half-time: Harbour switch to a high press from 16'
- seed 104: not spotted by half-time: Harbour raise the tempo from 32'
- seed 104 agents: aired a point not in the answer key: Eastmoor pressing higher: PPDA 8.0 to 4.8 (42.0')
- seed 104 rules: aired a point not in the answer key: Eastmoor dropping deeper: line 36.3 m to 30.9 m (18.0')
- seed 105 agents: spotted but not aired: Harbour switch to a high press from 10'
- seed 105 rules: spotted but not aired: Harbour switch to a high press from 10'
- seed 106 agents: spotted but not aired: Harbour switch to a high press from 21'
- seed 106 agents: aired a point not in the answer key: Harbour going down the right: 25.9% to 39.0% of attacks (20.0')
- seed 106 agents: aired a point not in the answer key: Harbour moving it quicker: 11.2 to 13.7 passes a minute on the ball (19.0')
- seed 106 rules: spotted but not aired: Harbour switch to a high press from 21'
- seed 106 rules: aired a point not in the answer key: Harbour going down the right: 25.9% to 39.0% of attacks (20.0')
- seed 106 rules: aired a point not in the answer key: Harbour moving it quicker: 11.2 to 13.7 passes a minute on the ball (19.0')
- seed 107: not spotted by half-time: Eastmoor raise the tempo from 30'
- seed 108: not spotted by half-time: Eastmoor drop into a deep block from 25'
- seed 108 rules: spotted but not aired: Harbour overload their left flank from 20'
- seed 108 rules: aired a point not in the answer key: Eastmoor going down the left: 20.3% to 44.8% of attacks (41.0')
- seed 109 agents: spotted but not aired: Eastmoor raise the tempo from 18'
- seed 109 rules: spotted but not aired: Eastmoor raise the tempo from 18'
- seed 111: not spotted by half-time: Harbour switch to a high press from 19'
- seed 111 agents: aired a point not in the answer key: Harbour moving it quicker: 11.3 to 13.7 passes a minute on the ball (36.0')
- seed 111 rules: aired a point not in the answer key: Harbour moving it quicker: 11.3 to 13.7 passes a minute on the ball (36.0')
- seed 112: not spotted by half-time: Jonah Novak (Harbour RCM) tires from 30'
- seed 112 rules: spotted but not aired: Harbour switch to a high press from 17'
- seed 112 rules: aired a point not in the answer key: Eastmoor pressing higher: PPDA 8.8 to 5.6 (38.0')

## Files
- `docs/evals/rundown.json`: the held-out run, every aired segment with its script, facts and score.
- `docs/evals/rundown-first.json`, `rundown-second.json`, `rundown-untuned.json`: the runs before the
  Producer was tuned (the first is also before fix 1).
- `docs/evals/foundry-groundedness.json`: the judge on the held-out run; `-untuned` and `-facts-only`
  are the earlier passes.
- `docs/evals/crew-reliability.json`, `crew-kill-telestrator.json`: per-agent pass rates (`docs/CREW.md`).
