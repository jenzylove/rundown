# Rundown: product requirements

**One liner:** at half-time every fan gets their own verified analysis segment, made for their
club, their player, their language and their depth, by a crew of agents in the minutes of the
break. A fan who joins late presses **Catch me up**; the broadcaster's producer keeps the master
rundown and an approval desk.

**Reframe:** personalized sports shows don't have a writing problem, they have a checking
problem. ESPN's AI-assisted SportsCenter For You still has its team review every script before
it goes out ([ESPN Front Row](https://www.espnfrontrow.com/2025/08/chris-fowler-us-open-sportscenter-for-you/)),
so it can't be live and can't be per match. A machine checker that recomputes every claim is
what makes a live show per fan possible.

**Hackathon:** Microsoft + Premier League, *Inside the Game: Developer Hackathon*.
Submission closes **27 Oct 2026, 11:59 pm PT (28 Oct, 06:59 UTC)**. Entries go in
on the "Projects" page of https://aka.ms/insidethegame.
Rules: https://github.com/microsoft/insidethegamehackathon/blob/main/OFFICIAL%20RULES.md

**Entering:** Grand Prize, and one Category Prize, **Best Multi-Agent System**.
Best Enterprise Solution is the fallback category if judges reassign us.

**History:** locked on 20 Oct as a studio producer (one neutral rundown at the whistle). On
25 Oct a review against the rubric found the brief's fifth stage, personalization, met only
thinly, and the live Verifier passing a script that said a stat fell when it rose. The product
was pointed at every fan (this document), and the Verifier now checks what each sentence claims.

---

## 1. Problem

- Over a billion fans in nearly 190 territories get the same studio analysis, made for one market
  in one language, or none: a broadcaster that takes the world feed gets the match and the
  scoreline, not the analysis.
- Half-time analysis is built while the match is still being played, in a window of minutes.
  Telestration needs graphics operators, and fast turnaround is the reason broadcasters pick their
  telestration tools ([SportsVideo, ESPN and Paint](https://www.sportsvideo.org/2014/12/12/espn-brings-advanced-telestration-to-monday-night-football-with-chyronhego-paint/);
  [Football365 on Sky's MNF](https://www.football365.com/news/a-football365-love-letter-to-monday-night-football)).
- Personalized shows exist, but daily and checked by hand (ESPN SC For You). Late joiners get a
  clip reel with no explanation (Peacock's Catch Up With Key Plays,
  [World Soccer Talk](https://worldsoccertalk.com/2022/08/22/peacock-major-new-features-for-premier-league-games/)).
- AI written text invents numbers and gets directions wrong; a paragraph cannot go on air, and an
  unchecked one should not.
- The brief asks for the same intelligence to reach "a data-hungry analyst and a casual fan as two
  genuinely different experiences", by favourite club, favourite player and language, and for
  output "timed and machine-readable enough to be rendered as synchronized graphical overlays".

## 2. User and front door

**Users:** a fan watching the match (casual or analyst, any side, four languages), and the studio
producer or match-day editor who owns the master rundown and the approval desk.

**First ten seconds:** the site opens on a match already playing on a 2D pitch, with no sign-up.
Beside it, **Who's watching?**: pick your side, Casual or Analyst, a language, and a player to
follow. Press **Make my show**; the crew drafts your cut while the half plays.

**Demo path, click by click:**
1. Open `/`: the match plays at 8x speed. Pick a fan card (for example an Eastmoor fan, Casual,
   Français, following their striker).
2. Watch the rundown build live: talking points with strength bars, the crew's trail.
3. Press **Catch me up** at any point: a short voiced segment on what you missed, for your profile.
4. At the whistle, **your half-time show** is ready: three segments, the last one on your player.
   Press **Watch it**: telestration, voice-over and word-by-word captions in your language.
5. Flip to another fan (Harbour, Analyst, English): a different lead, different numbers, different
   words, from the same match.
6. Kick off the second half; at full time the full-time show locks the same way.
7. Open `/proof`: the whistle wall (every cut made, when it was ready, what led, what verified),
   the crew's trail, every claim with its source events, and the evals.
8. Any agent can do the same over MCP at `/mcp` (list_matches, get_match, get_fan_show,
   catch_me_up, whistle_wall).

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

- Shared state: agents hand off through one store per match (talking points, the Analyst's
  verdicts, the Telestrator's plans, the Writer's scripts, every cut); every write is recorded
  (who, what, why, which events) and mirrored to Cosmos DB, which /proof replays.
- Failure recovery: if an agent times out or returns invalid output, the Producer
  retries it, routes around it, or drops that segment. The trace shows this, and
  `/proof` has a "kill the Telestrator" button that does it live.
- Agents never write numbers. They choose and explain. The Verifier rejects any
  claim whose number does not match the engine.

**Fan cuts (`studio/profile.py`, `api/runs.py`)**
- A fan profile is a side (neutral, home, away), a language (EN, ES, FR, PT), a depth (casual or
  analyst) and an optional followed player. Every rundown is made for one profile.
- The Producer chooses for the profile (a casual fan gets clear, visible stories; an analyst the
  tactical shifts; a club's fans what matters to their club). The Writer writes for it. A
  followed player gets his own segment (his passing, his running, his best moment, drawn on the
  pitch). The Telestrator's picture is made once per talking point and shared by every cut.
- The fans watching register their profile; their cut is drafted during the half and made first
  at the whistle, then a wall of cuts (every side at both depths). Any other cut is made on request.
- **Catch me up:** at any minute of either half, one short voiced segment on what the fan missed,
  for their profile; offered automatically when they come back to the tab.
- **Full time:** the same crew locks a full-time show on the second half.

**Verifier v2 (`agents/verify.py`)**: code, the last gate before air, for every cut.
- Every fact recomputed by the engine, every event id real, the Overlay Script valid (since 20 Oct).
- Every number said (spoken lines, subtitle, stat card) is a value of the facts that line cites.
- Every before/now claim the right way round: "from X to Y" order, rise and fall words, and time
  words ("now", "were", "up from") must match what the values did.
- Depth: a casual cut has at most one number a line and no PPDA or xG; an analyst cut carries
  the evidence on its stat card.
- A segment that fails gets one retry with the errors, then the rules version, then is replaced or
  dropped. Nothing unverified airs.

**MCP (`api/mcp_server.py`)**: the same crew for any agent, at `/mcp` (streamable HTTP):
`list_matches`, `get_match`, `get_fan_show`, `catch_me_up`, `whistle_wall`.

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
  - **Fans (`evals/fans.py`):** eight fan cuts per held-out seed, both crews: segments verified,
    home and away cuts that differ, casual and analyst scripts that differ, casual rule breaks,
    followed players with their segment, seconds to make each cut.
  - **Planted errors:** one error planted into copies of every clean aired segment (a pair the
    wrong way round, a rise said as a fall, a number changed in a line or on the stat card),
    checked by the Verifier of 22 Oct and by today's.

**Cut, and why**
- Real match footage and real Premier League data are not allowed by the rules.
- Auto-eventing from video is listed in the brief. It is cut because our input is
  synthetic tracking, so computer vision would be a stunt.
- MP4 export of a segment (planned 20 Oct): cut on 25 Oct in favour of the fan cuts. The
  broadcaster's export is the Overlay Script JSON, which any renderer can play on its own feed;
  our studio plays it live in the browser.

## 4. How each judging criterion is met (20% each)

| Criterion | How Rundown For You meets it |
|---|---|
| Technological Implementation | A simulator with planted ground truth ("optimized synthetic data creation"), a tested stats engine, a published Overlay Script schema, a renderer, voice, Bicep and CI. Hero tech doing real work: Foundry (models, evaluations), Agent Framework (the crew), Azure AI Speech (voices and word timings in four languages), Event Hubs (the live match), Container Apps (the burst at the whistle), Cosmos DB (every run, cut and trail), Application Insights (traces), and an MCP server. |
| Agentic Design & Innovation | Specialised agents with distinct roles hand off through shared state; the Producer plans each fan's cut to a deadline; a code Verifier gates every hand-over and the crew retries, routes around a failed agent, or drops a segment. One crew makes dozens of different, verified cuts in the minutes of a break: an outcome one agent can't match. Exposed to other agents over MCP. |
| Real-World Impact & Applicability | Fans in every market get analysis made for them, live, in their language; broadcasters keep a master rundown with an approval desk and an audit trail, and get a machine-readable output a rendering partner could put on air. It removes the human script review that keeps personalized shows daily. |
| User Experience & Presentation | Pick a fan card in ten seconds, watch your own show at the whistle, flip to another fan and see a different one; catch up any time. |
| Adherence to Category | Every stage of the brief: ingest (Event Hubs), interpret (stats engine), explain (Analyst, Writer), render (Overlay Script, studio), personalize (side, player, language, analyst vs casual). Best Multi-Agent: roles, shared state, handoffs, recovery, and fan cuts no single agent could make as well in the time. |

## 5. Claims and how each is proven

| Claim | Proof |
|---|---|
| "Your show is ready at the whistle" | The whistle wall on `/proof`: seconds after the whistle each cut was ready |
| "Every number on air is verified" | Verifier v2 on every segment of every cut; the planted-error eval (22 Oct Verifier vs today's) |
| "A casual fan and an analyst get different shows" | Fans eval: casual and analyst scripts that differ, casual rule breaks (target 0) |
| "Home and away fans get different shows" | Fans eval: home and away cuts that differ |
| "Follow a player" | Fans eval: followed players with their own segment |
| "Finds the real story of the half" | Story recall against the planted answer key, agents vs the rules baseline |
| "Recovers when an agent fails" | The kill-the-Telestrator run on `/proof`, and in the video |
| "Fresh match, nothing pre-made" | New match on a random seed; the trace shows the seed |
| "In four languages" | Only EN, ES, FR and PT are named; no "any language" in copy |

## 6. What it does not do

- It does not read real match video or real Premier League data.
- A fan's cut airs to that fan automatically once every segment passes the Verifier; there is no
  human review per fan (that is the point). The studio's master rundown has the approval desk.
- It does not give betting or predictive odds.
- The renderer is 2D. It produces an overlay script for real feeds but does not composite onto
  real broadcast video.
- The Verifier checks numbers, their order and direction, and depth; it does not judge tone.

## 7. Acceptance tests (the final scorecard)

1. A fresh seed plays live from kickoff to half-time with no manual step.
2. A fan picks a profile in under ten seconds, with no sign-up.
3. At the half-time whistle the fan's own cut is ready, every segment verified, within ±3 s of the
   90 s slot (or short, when too few points qualify).
4. Each segment plays with animated telestration, voice and captions in EN, ES, FR and PT.
5. Home and away cuts differ; casual and analyst cuts differ; a followed player gets a segment.
6. Catch me up returns a voiced segment for the fan's profile in either half.
7. Every on-screen number traces to engine output and source events; planted errors are caught.
8. Killing the Telestrator mid-half still produces a rundown, and the trace shows recovery.
9. The evals are published for at least 10 seeds, with the rules baseline alongside.
10. The live URL works with no sign-up; `/mcp` answers an MCP client; the repo is public with a
    judge fast path.
11. The demo video is under 2:00 and public on YouTube.

## 8. Sponsor checklist

Copied into `docs/SUBMISSION_CHECKLIST.md`, and ticked only from the live app.
