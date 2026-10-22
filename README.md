# Rundown

An autonomous studio producer that has the half-time analysis ready at the whistle. A synthetic match
streams in live; a crew of agents on Microsoft Agent Framework spots the talking points, draws the
telestration, writes and voices the script, and locks a 90-second rundown when the half ends. Every
number on air is recomputed from the stats engine before it is shown.

- **Live:** https://rundown.mangowave-7522f242.swedencentral.azurecontainerapps.io (no sign-up; `/demo` starts a fresh match)
- **Proof:** https://rundown.mangowave-7522f242.swedencentral.azurecontainerapps.io/proof: every run's agent trail, the evidence behind each number, and the evals

## Evals
On 12 held-out seeds, against the stories each match plants and hides from the agents
([method, every miss and the fixes](docs/EVALS.md)):

| | Agent crew | Rules-only baseline |
|---|---|---|
| Planted stories on air (recall) | 19/33 (58%) | 17/33 (52%) |
| Aired points that are a planted story or a real moment | 27/34 (79%) | 25/34 (74%) |
| Segments whose every fact the Verifier recomputes | 34/34 (100%) | 34/34 (100%) |
| Foundry groundedness judge, scripts scoring 4 or 5 | 34/34 (100%) | 30/34 (88%) |
| Locked after the whistle (median) | 1.97 s | at once |

The detectors spot 23/33 (70%) of planted first-half stories by half-time. Nothing
unverified goes on air. The agents' lead in picking stories is small (two stories out of 33) and
comes mostly from code screening the evidence they choose from: before that change, tuned on separate
practice seeds, they picked worse than the baseline. A simple persistence rule picks 18/33 (55%).

```bash
python -m rundown.evals.rundown --seeds 101-112 --rules-only   # no keys needed
```

## How it runs
Container Apps runs the simulator, the agent crew and the web app. Event Hubs carries every live match;
Cosmos DB archives every run, crew document and trail; Azure AI Speech voices the rundown; the models
are on Microsoft Foundry. Details: [docs/DEPLOY.md](docs/DEPLOY.md), [docs/CREW.md](docs/CREW.md),
[docs/PRD.md](docs/PRD.md), [docs/BUILD_LOG.md](docs/BUILD_LOG.md).
