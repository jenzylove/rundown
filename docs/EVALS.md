# Evals

Does the studio put the right stories on air, are its numbers true, and do the agents do better than
a rules-only picker? Measured on fresh seeds the build never used (101 to 112), against the stories
each match plants and keeps apart as an answer key. The agents never see the key; only the scorer
reads it.

Rerun it (needs the `AZURE_OPENAI_*` settings; `--rules-only` needs nothing):

```bash
python -m rundown.evals.rundown --seeds 101-112 --out docs/evals/rundown.json
python -m rundown.evals.foundry docs/evals/rundown.json --out docs/evals/foundry-groundedness.json
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

## Results (22 Oct, gpt-4.1-mini)
12 seeds, 33 planted first-half stories. The detectors spotted **23/33 (70%)**
by half-time, a median 6.8 min after each started.

| | Agent crew | Rules-only baseline |
|---|---|---|
| Story recall | 13/33 (39%) | 17/33 (52%) |
| Precision | 21/34 (62%) | 25/34 (74%) |
| Grounding (Verifier) | 34/34 (100%) | 34/34 (100%) |
| Foundry groundedness, scripts scoring 4 or 5 | 32/34 (94%), mean 4.53 | 31/34 (91%), mean 4.71 |
| Slot fit (median, worst) | 0.01 s, 26.0 s | 0.01 s, 26.0 s |
| Locked after the whistle (median, worst) | 2.14 s, 9.1 s | at once |
| Answers the Verifier rejected before air | 44 (8 routed to the rules crew) | - |

**What it says.** Nothing unverified went on air from either system, and the crew recovered from
every rejection. But on choosing which stories to air, the agent crew does **not** beat the rules
baseline: its picks vary from run to run and are worse on average (story recall 52%,
48% and 39% across three runs on the same seeds; the baseline is
deterministic at 52%). The Foundry judge rates both systems' scripts about equally. What this
eval shows for the agents is safe failure (every rejected answer was retried or routed around before
air), not better editorial judgement.

The worst slot fit (26 s) is seed 101 for both: one talking point qualified, so the rundown is one
60 s segment, 64 s for a 90 s slot.

## First misses and fixes
1. **A lone talking point broke the schema** (first run). Seed 101 had one talking point; both
   Producers gave it the whole 86 s, past the Overlay Script's 60 s segment limit. The agents' segment
   failed the Verifier, every replacement failed the same way, and the agents put **nothing** on air
   (slot fit 86 s). The rules segment aired without being checked. Fix: segments are capped at 60 s
   and the rundown runs short (`studio/producer.segment_length`, `tests/test_producer.py`).
2. **The judge was missing what the writer had** (Foundry, first pass). Given only each fact's
   value, the judge marked true lines as unsupported (a goal's minute and a player's top speed live in
   the fact's detail) and the agents looked better (29/34 against 21/34). With the
   detail and the talking point's headline in the context, the gap is gone. Both passes are published.
3. **Not fixed, by design: detection and the agent Producer.** The detectors miss 30% of planted
   stories (mostly deep blocks, tempo changes and late fatigue), and the agent Producer picks worse
   than the baseline. Tuning either on these seeds would make these numbers flatter the product; any
   change has to be made on other seeds and re-measured here.

Judge caveats: the judge is gpt-4.1-mini, the model that writes the agents' scripts. Most remaining
scores below 4 are the judge's own misreadings (it marks "the 38th minute" wrong for a goal at
37.0 minutes). The run could not be logged to the Foundry project: the deploy identity lacks
`Microsoft.CognitiveServices/accounts/AIServices/assets/write` on the Foundry resource.

## Every miss (final run)
- seed 101: not spotted by half-time: Harbour drop into a deep block from 15'
- seed 102: not spotted by half-time: Eastmoor drop into a deep block from 9'
- seed 102 agents: aired a point not in the answer key: Harbour dropping deeper: line 44.1 m to 38.3 m (19.0')
- seed 102 rules: aired a point not in the answer key: Harbour dropping deeper: line 44.1 m to 38.3 m (19.0')
- seed 103: not spotted by half-time: Ugo Esposito (Harbour RW) tires from 30'
- seed 103: not spotted by half-time: Eastmoor raise the tempo from 31'
- seed 103 agents: spotted but not aired: Harbour switch to a high press from 28'
- seed 103 agents: aired a point not in the answer key: Eastmoor dropping deeper: line 34.3 m to 29.9 m (36.0')
- seed 103 agents: aired a point not in the answer key: Harbour going down the right: 13.3% to 31.8% of attacks (19.0')
- seed 103 rules: spotted but not aired: Harbour switch to a high press from 28'
- seed 103 rules: aired a point not in the answer key: Eastmoor dropping deeper: line 34.3 m to 29.9 m (36.0')
- seed 103 rules: aired a point not in the answer key: Harbour moving it quicker: 11.5 to 14.0 passes a minute on the ball (44.0')
- seed 104: not spotted by half-time: Harbour switch to a high press from 16'
- seed 104: not spotted by half-time: Harbour raise the tempo from 32'
- seed 104 agents: spotted but not aired: Harbour overload their left flank from 25'
- seed 104 agents: aired a point not in the answer key: Eastmoor dropping deeper: line 36.3 m to 30.9 m (18.0')
- seed 104 agents: aired a point not in the answer key: Eastmoor pressing higher: PPDA 8.0 to 4.8 (42.0')
- seed 104 rules: aired a point not in the answer key: Eastmoor dropping deeper: line 36.3 m to 30.9 m (18.0')
- seed 105 agents: spotted but not aired: Eastmoor overload their left flank from 27'
- seed 105 rules: spotted but not aired: Harbour switch to a high press from 10'
- seed 106 agents: spotted but not aired: Malik Nunes (Eastmoor LB) tires from 30'
- seed 106 agents: spotted but not aired: Harbour switch to a high press from 21'
- seed 106 agents: aired a point not in the answer key: Eastmoor pressing higher: PPDA 11.0 to 7.6 (22.0')
- seed 106 agents: aired a point not in the answer key: Harbour going down the right: 25.9% to 39.0% of attacks (20.0')
- seed 106 agents: aired a point not in the answer key: Harbour moving it quicker: 11.2 to 13.7 passes a minute on the ball (19.0')
- seed 106 rules: spotted but not aired: Harbour switch to a high press from 21'
- seed 106 rules: aired a point not in the answer key: Harbour going down the right: 25.9% to 39.0% of attacks (20.0')
- seed 106 rules: aired a point not in the answer key: Harbour moving it quicker: 11.2 to 13.7 passes a minute on the ball (19.0')
- seed 107: not spotted by half-time: Eastmoor raise the tempo from 30'
- seed 107 agents: spotted but not aired: Harbour drop into a deep block from 28'
- seed 107 agents: aired a point not in the answer key: Harbour moving it quicker: 10.7 to 13.4 passes a minute on the ball (35.0')
- seed 108: not spotted by half-time: Eastmoor drop into a deep block from 25'
- seed 108 agents: spotted but not aired: Harbour overload their left flank from 20'
- seed 108 agents: aired a point not in the answer key: Eastmoor going down the left: 20.3% to 44.8% of attacks (41.0')
- seed 108 rules: spotted but not aired: Harbour overload their left flank from 20'
- seed 108 rules: aired a point not in the answer key: Eastmoor going down the left: 20.3% to 44.8% of attacks (41.0')
- seed 109 agents: spotted but not aired: Eastmoor raise the tempo from 18'
- seed 109 rules: spotted but not aired: Eastmoor raise the tempo from 18'
- seed 110 agents: spotted but not aired: Eastmoor switch to a high press from 11'
- seed 110 agents: aired a point not in the answer key: Harbour going down the left: 21.0% to 36.8% of attacks (36.0')
- seed 111: not spotted by half-time: Harbour switch to a high press from 19'
- seed 111 agents: spotted but not aired: Harbour drop into a deep block from 20'
- seed 111 agents: aired a point not in the answer key: Eastmoor pressing higher: PPDA 12.4 to 7.5 (23.0')
- seed 111 agents: aired a point not in the answer key: Harbour moving it quicker: 11.3 to 13.7 passes a minute on the ball (36.0')
- seed 111 rules: aired a point not in the answer key: Harbour moving it quicker: 11.3 to 13.7 passes a minute on the ball (36.0')
- seed 112: not spotted by half-time: Jonah Novak (Harbour RCM) tires from 30'
- seed 112 rules: spotted but not aired: Harbour switch to a high press from 17'
- seed 112 rules: aired a point not in the answer key: Eastmoor pressing higher: PPDA 8.8 to 5.6 (38.0')

## Files
- `docs/evals/rundown.json`: the final run, every aired segment with its script, facts and score.
- `docs/evals/rundown-first.json`, `rundown-second.json`: the earlier runs (the first is before fix 1).
- `docs/evals/foundry-groundedness.json`, `foundry-groundedness-facts-only.json`: the judge's scores and
  reasons, with and without fix 2.
- `docs/evals/crew-reliability.json`, `crew-kill-telestrator.json`: per-agent pass rates (`docs/CREW.md`).
