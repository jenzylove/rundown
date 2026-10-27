# Submission checklist

Ticked only from the live app or the submitted form, never from intent.
Source: [Official Rules](https://github.com/microsoft/insidethegamehackathon/blob/main/OFFICIAL%20RULES.md).

## Eligibility and entry
- [x] Registered on https://aka.ms/insidethegame (before the 20 Oct cutoff; portal login works, 27 Oct)
- [x] Project created after 29 Sep 2026 (repo first commit 20 Oct 2026)
- [ ] All required fields completed on the "Projects" page before 27 Oct 2026, 11:59 pm PT

## Challenge fit (4.1)
- [x] Ingest: synthetic events streamed live (Event Hubs)
- [x] Interpret: stats, patterns and context
- [x] Explain: why a moment matters
- [x] Render: timed, machine-readable overlays on screen
- [x] Personalize: analyst vs casual fan as genuinely different experiences (language, club side, depth)
- [x] Synthetic, football-realistic data only; no real PL footage or data
- [x] Multi-language output

## Submission requirements (4.4)
- [ ] Elevator pitch: what it is, which Microsoft/Azure tech, what it does, what problem it solves
- [ ] Demo video under 2:00, showing it working, public on YouTube or Vimeo, link on project page
- [ ] No third-party trademarks or copyrighted music in the video (fictional clubs, licensed or generated audio only)
- [ ] Public GitHub repo URL
- [x] Working project link for judges, free, no sign-up (one warm replica; keep the resource group until 10 Nov 2026)
- [x] Testing instructions (credentials if any login)
- [x] All materials in English

## Category: Best Multi-Agent System
- [x] Specialized agents with distinct roles
- [x] Coordination through shared state and handoffs
- [x] Recovery from failures, shown (live: `/proof`, Kill the Telestrator)
- [x] End-to-end outcome a single agent could not achieve as well

## Hero technologies used (Technological Implementation)
- [x] Microsoft Foundry: gpt-4.1-mini deployment for every agent; Azure AI Evaluation groundedness judge (results in the repo, not logged to the project: the deploy identity lacks the data action)
- [x] Application Insights: Agent Framework spans per agent (`invoke_agent writer`, `producer`, ...), confirmed by query 27 Oct
- [x] MCP server at `/mcp/` (five tools, checked with the MCP Python client)
- [x] Microsoft Agent Framework
- [x] Azure AI Speech
- [x] Azure Event Hubs
- [x] Azure Container Apps
- [x] Azure Cosmos DB
