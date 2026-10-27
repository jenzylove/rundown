# Evals

Two questions, measured on held-out seeds (101 to 112) that no change was tuned on:

1. **Does every fan get a show that is true, and genuinely theirs?** (`rundown/evals/fans.py`)
2. **Does the studio put the right stories on air, and do the agents beat a rules-only picker?**
   (`rundown/evals/rundown.py`), scored against the stories each match plants and keeps apart as an
   answer key. The agents never see the key; only the scorer reads it.

Changes are tuned on separate practice seeds (201 to 240), never on the held-out ones. Rerun:

```bash
python -m rundown.evals.fans --seeds 101-112 --rules-only          # no keys needed
python -m rundown.evals.rundown --seeds 101-112 --rules-only       # no keys needed
python -m rundown.evals.fans --seeds 101-112 --out docs/evals/fans.json          # agents: AZURE_OPENAI_*
python -m rundown.evals.rundown --seeds 101-112 --out docs/evals/rundown.json
python -m rundown.evals.foundry docs/evals/rundown.json --out docs/evals/foundry-groundedness.json
```

## Fans (26 Oct, gpt-4.1-mini)
For each seed the first half is played as a live run plays it, then eight fan cuts are made from the
same talking points: every side (neutral, home, away) at both depths in English, plus two fans who
follow a player (a home fan in Spanish, casual; an away fan in French, analyst). Each cut is made by
the agent crew and by the rules crew and checked by the same Verifier.

12 held-out seeds (101 to 112), 8 fan profiles each.

| | Agent crew | Rules crew |
|---|---|---|
| Segments that pass the Verifier | 274/274 (100%) | 274/274 (100%) |
| Home and away cuts that differ | 16/24 (67%) | 22/24 (92%) |
| Casual and analyst scripts that differ | 36/36 (100%) | 36/36 (100%) |
| Casual segments that break the casual rules | 0 | 0 |
| Followed players with their own segment | 24/24 (100%) | 24/24 (100%) |
| Words written by the Writer agent | 203/274 (74%) | - |
| Seconds to make a cut (median, worst) | 11.55 s, 20.29 s | at once |

**What it says.** Every segment of every cut passed the Verifier, and the casual rules held. The
agents' casual and analyst scripts always differ, and every followed player got his own segment.
The agents' home and away cuts differ less often than the rules crew's (16 of 24 against 22 of 24):
the agent Producer weighs how strong a story is above whose story it is, even with the club rule
(below). Three in four segments air with the Writer agent's words; the rest fell back to the rules
Writer after a rejected retry, and the trail on `/proof` says which.

## Planted errors (26 Oct)
Every clean segment the agents aired in the fans eval was copied with one error planted in what it
says, and each copy went through the Verifier as it was on 22 Oct and as it is now. A copy that passes
would have gone on air.

| Error planted | Copies | Verifier of 22 Oct | Verifier now |
|---|---|---|---|
| number changed in a spoken line | 501 | 0/501 (0%) | 477/501 (95%) |
| pair the wrong way round | 177 | 0/177 (0%) | 171/177 (97%) |
| rise said as a fall, or a fall as a rise | 115 | 0/115 (0%) | 98/115 (85%) |
| number changed on the stat card | 274 | 0/274 (0%) | 261/274 (95%) |

The Verifier of 22 Oct recomputed every fact and checked every event, but read nothing a segment
*says*, so it caught none of these. The misses that remain: a changed number that lands on another
value the same line cites (a "12" changed to "15" when 15 is also in the line), and a rise word
swapped in a sentence that also says the opposite elsewhere, which the check leaves alone rather than
guess.

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

## Half-time eval (26 Oct, gpt-4.1-mini, held-out seeds 101 to 112)
12 held-out seeds (101 to 112), neutral English rundown, 90 s slot. Planted first-half stories spotted by half-time: **23/33 (70%)**, a median 6.8 min after they start.

| | Agent crew | Rules-only baseline |
|---|---|---|
| Story recall (planted stories on air) | 19/33 (58%) | 17/33 (52%) |
| Precision (aired points that are a planted story or a real moment) | 27/34 (79%) | 25/34 (74%) |
| Grounding (segments whose every fact the Verifier recomputes) | 34/34 (100%) | 34/34 (100%) |
| Slot fit (median, worst) | 0.01 s, 26.0 s | 0.01 s, 26.0 s |
| Locked after the whistle (median, worst) | 5.58 s, 14.05 s | at once |
| Answers the Verifier rejected before air | 76 (25 routed to the rules crew) | - |

Reference, not a product: a simple rule that prefers talking points still showing at the whistle and seen in many scans (set on practice seeds 201-240) picks 18/33 (55%) of the planted stories, precision 26/34 (76%).

**What it says.** Nothing that failed the Verifier went on air from either system. The agent crew
puts two more planted stories on air than the rules baseline and makes fewer off-key picks; one
more than the persistence rule. The margin is small, and most of it comes from code screening the
evidence the Producer chooses from (below). The stricter Verifier of 25 Oct rejects more first drafts
(76 rejections, 25 segments routed to the rules crew) and the lock takes longer (median 5.6 s).

**Foundry groundedness judge** (Azure AI Evaluation's GroundednessEvaluator, 1 to 5, reading each
aired script against the facts and detail the writer had): agents 32/34 scripts at 4 or 5, mean
4.59; rules crew 31/34, mean 4.71. Both agent scripts below 4 describe how a goal was scored ("a
header from Vidal's cross", "Halvorsen's precise cross"), and both are true: checked against the match
events, seed 112's 12th-minute goal and seed 105's 26th-minute goal were headers from those players'
crosses. The Writer knows it from the Telestrator's picture, which reads the events; the judge only
sees the facts table, so it marks the detail unsupported. The Verifier checks every number, its order
and its direction, not descriptive detail like that. The judge is gpt-4.1-mini,
the model that writes the agents' scripts. Results are in `docs/evals/foundry-groundedness.json`;
they are not logged in the Foundry project because the deploy identity lacks
`Microsoft.CognitiveServices/accounts/AIServices/assets/write` on the Foundry resource.

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

## Every miss (held-out half-time run)
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
- `docs/evals/fans.json`: the fans eval, every cut and the planted-error counts.
- `docs/evals/rundown.json`: the held-out run, every aired segment with its script, facts and score.
- `docs/evals/rundown-first.json`, `rundown-second.json`, `rundown-untuned.json`: the runs before the
  Producer was tuned (the first is also before fix 1).
- `docs/evals/foundry-groundedness.json`: the judge on the held-out run; `-untuned` and `-facts-only`
  are the earlier passes.
- `docs/evals/crew-reliability.json`, `crew-kill-telestrator.json`: per-agent pass rates (`docs/CREW.md`).
