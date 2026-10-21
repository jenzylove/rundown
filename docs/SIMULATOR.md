# Match simulator and stats engine

## Simulator (`rundown/sim/`)

- Fictional clubs (Harbour City, Eastmoor Athletic, Ravensholt, Kingsgate Rovers)
  with generated squads. No real club, player or league names.
- Two 45-minute halves plus 1–4 minutes of added time each, at 10 Hz: 22 player
  positions and the ball (x, y, height) on a 105 × 68 m pitch.
- Off the ball, each player moves toward a target built from their 4-3-3 slot,
  the ball, their team's phase and the live tactic (press, block depth, width,
  tempo, directness), with speed, acceleration and stamina limits.
- On the ball, the carrier passes, carries, crosses or shoots. Outcomes are drawn
  from distance, pressure, passing lane, skill and an xG model. The ball travels
  in real time, so pass and shot speeds can be measured from the tracking.
- Events: pass, carry, cross, shot, goal, save, claim, clearance, tackle,
  interception, recovery, pressure, foul, card, substitution, possession_change,
  and period markers. Each has an id, clock time, position and players.
- **Planted stories** (`stories.py`): each seed plants 4–5 tactical changes
  (press switch, flank overload, a tiring player, a deep block, a tempo rise),
  each from a given minute. They change the simulation, and are exported only to
  `answer_key.json`. Agents never see them.

```
python -m rundown.sim.cli --seed 1 2 3 --out out/matches
```

### Realism, 10 seeds (1–10)

| Per match | Simulator mean (range) |
|---|---|
| Goals | 2.2 (1–4) |
| Shots | 24.7 (19–30) |
| xG | 2.45 (1.7–3.6) |
| Passes per team | 545 (522–572) |
| Pass completion | 81.9% (78.2–83.2) |
| Fouls | 11.3 (6–20) |
| Cards | 2.6 (0–7) |
| Crosses (incl. corners) | 30.4 (24–40) |

For reference, the 2024/25 Premier League averaged 2.97 goals a match
([1,129 goals in 380 matches](https://extratime.com/stats/2129/2287/premier-league/)).
The simulator runs a little low on goals and fouls. We don't claim realism
figures beyond this table.

## Stats engine (`rundown/engine/`)

Pure functions, no AI. Each returns a `Fact` with the stat name and arguments
(so the Verifier can recompute it exactly), the value, its unit, and the event
ids or frame range it came from.

| Stat | Definition |
|---|---|
| `passes` | completion % of a team's passes in a window |
| `pass_difficulty` | 0–100 from tracking: length, distance to the nearest presser, opponent closest to the lane, space at the receiving end |
| `hardest_pass` | highest-difficulty completed open-play pass in a window |
| `shot_speed` | median ball speed over the first 0.4 s after the shot, from tracking |
| `player_physical` | distance, top speed (best 0.5 s average), sprints (≥ 25.2 km/h held ≥ 1 s) |
| `speed_triggers` | moments a player first passes a speed threshold (default 30 km/h) |
| `ppda` | opponent passes in their own 60% ÷ our tackles, interceptions and fouls in that zone |
| `pressures` | pressures per minute, and how many were in the opponent's half |
| `field_tilt` | our share of all final-third passes |
| `defensive_line` | mean height of the back four out of possession |
| `possession`, `tempo` | time share; passes per minute of own possession |
| `flank_share` | share of final-third passes and crosses starting in a channel |
| `overloads` | episodes with more of us than them in a flank channel near the ball |
| `shots`, `shot_xg`, `momentum` | shot counts and xG; per-minute threat swing |

## Detectors (`rundown/engine/detect.py`)

Compare the last 10 minutes with the earlier part of the half for press,
block depth, tempo, flank use and each player's physical output. They produce
candidate talking points with their facts. Run on their own, they are the
**rules-only baseline** the agents are scored against.

### Baseline, rules only (no agents), seeds 1–10

```
python -m rundown.evals.stories out/matches/{1..10}
recall 30/46 = 65%, precision 30/97 = 31%, median delay 5.1 min
```

The most-missed stories: tempo rises (7 missed) and tiring players (6 missed).
"Precision" here counts every candidate the detectors raise, not what airs. The
rundown only airs the best few, and that is scored separately in Phase 5.
