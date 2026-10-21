# Build log

| Date (UTC) | What now works |
|---|---|
| 20 Oct | Idea locked; PRD, plan and submission checklist written and approved. |
| 20 Oct | Spike S1 passed: a browser renderer plays a timed overlay script over 10 Hz tracking at 60 fps and exports MP4. |
| 21 Oct | Match simulator: fictional clubs, 90 minutes at 10 Hz, full event stream, planted stories kept apart as the answer key. Realism table in `docs/SIMULATOR.md`. |
| 21 Oct | Stats engine: 16 deterministic stats as recomputable Facts; change detectors; story eval. Rules-only baseline: 65% of planted stories found, median 5.1 min after they start. 12 tests passing. |
| 21 Oct | Studio (rules crew): Overlay Script v1 schema; rules Telestrator, Writer (EN/ES) and Producer; rundown fits the 90 s slot (±3 s, tested). Live API streams a match over WebSocket, scans every match minute, locks the rundown about 0.2 s after the half-time whistle. Web app: live pitch, building rundown, approval desk (approve, swap, drop, evidence, cut and language), studio playback with captions, dark and light. Driven end to end in headless Chromium with no console errors. |
