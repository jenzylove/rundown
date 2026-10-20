# Rundown: product requirements

**One liner:** an autonomous studio producer that watches the whole half and has a
finished, animated tactical analysis segment ready the moment the whistle blows,
voiced, timed and in any language.

**Reframe:** studio analysis doesn't have an insight problem, it has a turnaround
problem. The best story of the half is spotted at minute 44 and has to be on air
at minute 47.

**Hackathon:** Microsoft + Premier League, *Inside the Game: Developer Hackathon*.
Submission closes **27 Oct 2026, 11:59 pm PT (28 Oct, 06:59 UTC)**. Entries go in
on the "Projects" page of https://aka.ms/insidethegame.
Rules: https://github.com/microsoft/insidethegamehackathon/blob/main/OFFICIAL%20RULES.md

**Entering:** Grand Prize, and one Category Prize, **Best Multi-Agent System**.
Best Enterprise Solution is the fallback category if judges reassign us.

---

## 1. Problem

- Half-time analysis has to be built while the match is still being played, in
  a window of minutes. Telestration (the arrows and zones drawn over a replay)
  needs dedicated graphics operators, and fast turnaround is the reason
  broadcasters pick their telestration tools ([SportsVideo, ESPN and Paint](https://www.sportsvideo.org/2014/12/12/espn-brings-advanced-telestration-to-monday-night-football-with-chyronhego-paint/);
  [Football365 on Sky's MNF](https://www.football365.com/news/a-football365-love-letter-to-monday-night-football)).
- That workflow only exists where there is a full studio crew. A broadcaster in
  a smaller market that takes the world feed gets the match and the scoreline,
  not the analysis.
- AI match tools today write text: recaps, commentary lines, captions. A
  paragraph cannot go on air. A studio needs a timed, rendered segment.
- The hack itself asks for output "timed and machine-readable enough to be
  rendered as synchronized graphical overlays on a live match feed".

To verify during the build: one more sourced fact on studio turnaround or crew size,
used in the README. Until it's found, the README makes no number claim.

## 2. User and front door

**User:** a studio producer or match-day editor at a broadcaster or club media
team. A second user is a viewer who watches the segment that comes out.

**First ten seconds:** the site opens on a match already playing on a 2D pitch,
with no sign-up. Beside it, the **rundown** builds live: talking points appear
with a strength bar, get promoted into the segment, and get replaced when the
match changes.

**Demo path, click by click:**
1. Open `/` and the match plays at 8× speed. The rundown fills as the agents work.
2. Click any talking point to see its evidence: the events it came from, and why
   the Analyst thinks it matters.
3. At half-time the Producer locks a 90-second rundown. The approval desk shows
   three segments with "approve / swap / drop".
4. Press **Go to studio**. The segment plays: animated telestration on the
   pitch, voice-over and captions, all generated.
5. Switch to **Español · away fan**. The same half is re-cut, with a different
   lead story, in Spanish.
6. Press **New match** to get a fresh seed nobody has seen. The whole loop runs
   again live, which proves nothing is pre-made.
7. Open `/proof`: the agent trace for that run, every claim with its source
   events, and the eval scores.

## 3. Full scope

Everything below ships. Nothing is held back for a "v2".

**Match simulator (`sim/`)**
- Fictional clubs, players and stadium. No Premier League names, crests or marks.
- A full 90 minutes at 10 Hz: positions for 22 players and the ball, plus an
  event stream (pass, carry, shot, tackle, interception, pressure, clearance,
  foul, card, substitution, goal, possession change, period start and end), each
  with an id, time, x/y and players.
- Each seed **plants tactical stories** (for example: the home side switches to a
  high press at 30', the away left back tires after 60', one team overloads the
  right flank). Planted stories are saved apart from the stream as the answer key.
  The agents never see them.
- It streams in real time (1×–16×) to Event Hubs, and to a local queue when running
  offline.

**Stats engine (`engine/`)**: deterministic, unit tested, no AI.
- Pass distance, completion and a difficulty rating; ball and shot speed; player
  speed and distance with threshold triggers; pressure intensity (PPDA style);
  field tilt; possession rhythm; momentum swings. All computed in rolling windows.
- Every number the product shows comes from here, carrying the ids of the
  events behind it.

**Agent crew (`agents/`)**: Microsoft Agent Framework, models on Microsoft Foundry.

| Agent | Job | Output |
|---|---|---|
| Spotter | Watches the stats windows and raises candidate talking points as they appear | candidates with evidence ids |
| Analyst | Decides whether a candidate matters and why (control vs chaos, pressure shifts, rhythm) | scored talking point + "why it matters" |
| Telestrator | Turns a talking point into an animated pitch sequence | Overlay Script JSON (below) |
| Writer | Presenter script per language and per side (neutral, home, away) | timed script lines |
| Producer | Plans the rundown into the slot (e.g. 90 s), re-plans when the story changes, hands off to the human | rundown |
| Verifier | Checks every number, name, time and arrow against the stats engine and the events before anything is shown | pass/fail with reasons |

- Shared state: the rundown and talking points live in Cosmos DB. Agents hand off
  through it, and every write is recorded (who, what, why, which events).
- Failure recovery: if an agent times out or returns invalid output, the Producer
  retries it, routes around it, or drops that segment. The trace shows this, and
  `/proof` has a "kill the Telestrator" button that does it live.
- Agents never write numbers. They choose and explain. The Verifier rejects any
  claim whose number does not match the engine.

**Overlay Script (`schema/overlay-script.v1.json`)**
- A published JSON schema for a timed segment. It is a list of cues (`t`,
  `duration`, `kind`: freeze, play, arrow, run, zone, highlight-player, label,
  stat-card, caption), each with the `event_ids` it proves.
- It is machine-readable so a rendering partner could play it on a real feed.
  We ship our own browser renderer.

**Renderer and studio (`web/`)**
- A 2D pitch that replays the 10 Hz tracking. It plays Overlay Scripts with
  animated arrows, runs, zones and labels. Dark and light themes.
- Voice-over from **Azure AI Speech** neural voices (English, Spanish, French and
  Portuguese at minimum), with word timings driving the captions.
- The approval desk: approve, swap or drop each segment; trim to the slot.
- An MP4 export of a segment, rendered in the browser, for a broadcaster to download.

**Azure**
- Foundry: model deployments, agents, tracing and evaluations.
- Event Hubs carries the live match stream. Container Apps runs the API, agents and
  simulator. Cosmos DB holds the rundown, audit trail and fan cuts. Application
  Insights collects traces.
- Infrastructure as code (Bicep) and a GitHub Actions deploy.

**Proof**
- `/demo`: no sign-up, live run on a fresh seed.
- `/proof`: the agent trace for a real run, labelled **live** or **replay**;
  evidence for every claim; eval results.
- Evals (Foundry Evaluations plus our own scorer), published with the first misses:
  - **Story recall:** the share of planted stories the rundown finds by half-time, and
    how many minutes after planting it finds them.
  - **Precision:** the share of aired talking points that match a planted or
    stat-supported story.
  - **Grounding:** the share of numbers and claims the Verifier passes. The target
    is 100% on air, and every rejection is logged.
  - **Slot fit:** how far the segment's length is from the slot, in seconds.
  - **Baseline:** the same scores with the agents switched off (rules-only
    "biggest stat change" picker), so the agents' value is measured, not claimed.

**Cut, and why**
- Real match footage and real Premier League data are not allowed by the rules.
- Auto-eventing from video is listed in the brief. It is cut because our input is
  synthetic tracking, so computer vision would be a stunt.

## 4. How each judging criterion is met (20% each)

| Criterion | How Rundown meets it |
|---|---|
| Technological Implementation | A realistic simulator with planted ground truth ("optimized synthetic data creation"), a tested stats engine, a published overlay schema, a renderer, voice, Bicep and CI. Hero tech: Foundry, Agent Framework, Azure Speech, Event Hubs, Container Apps, Cosmos DB, plus GitHub Copilot agent mode during the build. |
| Agentic Design & Innovation | Six agents with distinct roles, shared state and handoffs. The Producer plans to a deadline and re-plans live, and the crew recovers from failure on camera. |
| Real-World Impact & Applicability | Built for a studio producer, with a human approval desk, an audit trail and a machine-readable output a broadcaster could put on air. |
| User Experience & Presentation | The demo is a TV segment that is understandable in seconds, and re-cut per language and per side. |
| Adherence to Category | Every stage of the brief is covered: ingest, interpret, explain, render, personalize. Best Multi-Agent is met by roles, handoffs, recovery, and an outcome one agent can't match under a deadline. |

## 5. Claims and how each is proven

| Claim | Proof |
|---|---|
| "Segment ready at the whistle" | The `/proof` trace shows the timestamp of the half-time whistle event and of the rundown lock |
| "Every number on screen is verified" | Verifier log for the run, with each claim linked to its events; 0 unverified items aired |
| "Finds the real story of the half" | Story recall against the planted answer key, published per seed |
| "Better than a stats-only picker" | Baseline vs agents on the same seeds, in the eval table |
| "Recovers when an agent fails" | The kill-the-Telestrator run in the trace, and in the video |
| "Fresh match, nothing pre-made" | The New match button on a random seed; the trace shows the seed |
| "In any language" | Only the languages shipped are named, with no "any language" in copy |

## 6. What it does not do

- It does not read real match video or real Premier League data.
- It does not replace the human producer. Nothing airs without approval, or an
  explicit auto mode the producer switches on.
- It does not give betting or predictive odds.
- The renderer is 2D. It produces an overlay script for real feeds but does not
  composite onto real broadcast video.

## 7. Acceptance tests (the final scorecard)

1. A fresh seed plays live from kickoff to half-time with no manual step.
2. At the half-time whistle, a locked rundown fits the slot within ±3 s.
3. Each segment plays with animated telestration, voice and captions in EN and ES.
4. Home and away cuts of the same half lead with different stories.
5. Every on-screen number traces to engine output and to source events.
6. Killing the Telestrator mid-half still produces a rundown, and the trace shows recovery.
7. The eval table is published for at least 10 seeds, with the baseline alongside.
8. The live URL works with no sign-up. The repo is public with a judge fast path.
9. The demo video is under 2:00 and public on YouTube.

## 8. Sponsor checklist

Copied into `docs/SUBMISSION_CHECKLIST.md`, and ticked only from the live app.
