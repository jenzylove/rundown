# Build log

| Date (UTC) | What now works |
|---|---|
| 20 Oct | Idea locked; PRD, plan and submission checklist written and approved. |
| 20 Oct | Spike S1 passed: a browser renderer plays a timed overlay script over 10 Hz tracking at 60 fps and exports MP4. |
| 21 Oct | Match simulator: fictional clubs, 90 minutes at 10 Hz, full event stream, planted stories kept apart as the answer key. Realism table in `docs/SIMULATOR.md`. |
| 21 Oct | Stats engine: 16 deterministic stats as recomputable Facts; change detectors; story eval. Rules-only baseline: 65% of planted stories found, median 5.1 min after they start. 12 tests passing. |
| 21 Oct | Studio (rules crew): Overlay Script v1 schema; rules Telestrator, Writer (EN/ES) and Producer; rundown fits the 90 s slot (±3 s, tested). Live API streams a match over WebSocket, scans every match minute, locks the rundown about 0.2 s after the half-time whistle. Web app: live pitch, building rundown, approval desk (approve, swap, drop, evidence, cut and language), studio playback with captions, dark and light. Driven end to end in headless Chromium with no console errors. |
| 21 Oct | Azure connected: Foundry project with gpt-4.1-mini, and Speech, in Sweden Central. |
| 21 Oct | Spike S2 passed with conditions: an Agent Framework agent on gpt-4.1-mini writes Overlay Script segments for 92 real talking points (EN/ES); 73% valid first try, 96% after one Verifier retry, 6.3 s per call (p50). Code must own timing and geometry. |
| 21 Oct | Spike S3 passed: Azure Speech word timings in EN/ES/FR/PT land within 150 ms of the heard word (median 37 to 64 ms). |
| 21 Oct | Agent crew on Microsoft Agent Framework: Analyst, Producer, Telestrator and Writer agents on gpt-4.1-mini, gated by a code Verifier that recomputes every number from the engine. On 4 seeds × 2 cuts: 24/24 agent pictures, 21/24 agent scripts, 24/24 verified, lock median 1.9 s after the whistle. Kill-the-Telestrator recovers with every segment verified. Live API streams the crew's trail; the desk shows who made what and why. |
