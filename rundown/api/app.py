"""HTTP + WebSocket API.

    uvicorn rundown.api.app:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agents.crew import ROLES
from ..studio import voice
from ..studio.profile import Profile
from .runs import RunManager

app = FastAPI(title="Rundown")
runs = RunManager()
WEB = Path(__file__).resolve().parents[2] / "web" / "dist"
EVALS = Path(__file__).resolve().parents[2] / "docs" / "evals"


@app.on_event("startup")
async def _startup():
    runs.loop = asyncio.get_running_loop()


class NewRun(BaseModel):
    seed: int | None = None
    speed: float = 8.0


@app.get("/api/health")
def health():
    a = runs.archive
    return dict(ok=runs.bus is not None, runs=len(runs.runs), crew="agents" if runs.models() else "rules",
                voice=voice.configured(), stream=runs.bus.name if runs.bus else f"down ({runs.bus_error})",
                stream_stats=runs.bus.stats if runs.bus else None,
                store=a.name, store_errors=a.errors, store_last_error=a.last_error)


@app.post("/api/runs")
def create_run(body: NewRun):
    if not 1 <= body.speed <= 32:
        raise HTTPException(400, "speed must be 1-32")
    try:
        run = runs.create(body.seed, body.speed)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return run.snapshot()


def _get(run_id: str):
    run = runs.runs.get(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    return run


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return _get(run_id).snapshot()


@app.post("/api/runs/{run_id}/skip")
def skip_to_half_time(run_id: str):
    run = _get(run_id)
    run.skip = True
    return dict(ok=True)


@app.post("/api/runs/{run_id}/second-half")
def second_half(run_id: str):
    return dict(status=runs.second_half(_get(run_id)))


class Fan(BaseModel):
    """Who is watching: their side, language, depth, and the player they follow (an id from the run's meta)."""
    cut: str = "neutral"
    lang: str = "en"
    depth: str = "analyst"
    player: str | None = None


def _profile(run, cut: str, lang: str, depth: str, player: str | None) -> Profile:
    try:
        prof = Profile(cut, lang, depth, player or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if prof.player and not any(p["id"] == prof.player for side in ("home", "away") for p in run.meta[side]["players"]):
        raise HTTPException(400, "no such player in this match")
    return prof


def _phase(run, phase: str) -> str:
    if phase not in ("ht", "ft"):
        raise HTTPException(400, "phase is ht or ft")
    if phase == "ht" and run.locked_at is None:
        raise HTTPException(409, "rundown locks at the half-time whistle")
    if phase == "ft" and run.status != "full_time":
        raise HTTPException(409, "the full-time rundown locks at the final whistle")
    return phase


@app.get("/api/runs/{run_id}/rundown")
def get_rundown(run_id: str, cut: str = "neutral", lang: str = "en", depth: str = "analyst",
                player: str | None = None, phase: str = "ht"):
    run = _get(run_id)
    prof = _profile(run, cut, lang, depth, player)
    return runs.rundown(run, prof, phase=_phase(run, phase))


@app.post("/api/runs/{run_id}/watch")
def watch(run_id: str, body: Fan):
    """A fan's profile: their cut is drafted during the half and made first at the whistle."""
    run = _get(run_id)
    return runs.watch(run, _profile(run, body.cut, body.lang, body.depth, body.player))


class CatchUp(Fan):
    since_s: float  # match clock (s) the fan stopped watching


@app.post("/api/runs/{run_id}/catchup")
def catch_up(run_id: str, body: CatchUp):
    """One short segment, voiced, on what the fan missed since `since_s`, made for their profile."""
    run = _get(run_id)
    if run.status not in ("first_half", "second_half"):
        raise HTTPException(409, "catch-up works while the match is playing")
    if run.clock - body.since_s < 60:
        raise HTTPException(409, "less than a minute to catch up on")
    try:
        return runs.catch_up(run, _profile(run, body.cut, body.lang, body.depth, body.player), body.since_s)
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.get("/api/runs/{run_id}/wall")
def wall(run_id: str):
    """Every cut the crew has made for this run: who it was for, when it was ready, what led, what verified."""
    run = _get(run_id)
    return dict(whistle_at=run.whistle_at, ft_at=run.ft_at, items=sorted(run.wall.values(), key=lambda w: w["ready_s"]))


class Swap(Fan):
    segment_id: str
    phase: str = "ht"


@app.post("/api/runs/{run_id}/rundown/swap")
def swap_segment(run_id: str, body: Swap):
    run = _get(run_id)
    phase = _phase(run, body.phase)
    try:
        return runs.swap(run, _profile(run, body.cut, body.lang, body.depth, body.player), body.segment_id, phase)
    except KeyError:
        raise HTTPException(404, "no such segment")
    except LookupError as e:
        raise HTTPException(409, str(e))


@app.post("/api/runs/{run_id}/rundown/voice")
def voice_rundown(run_id: str, cut: str = "neutral", lang: str = "en", depth: str = "analyst",
                  player: str | None = None, phase: str = "ht"):
    """Voice-over for every segment of a cut: audio URLs and word timings, script re-timed to the voice."""
    run = _get(run_id)
    prof = _profile(run, cut, lang, depth, player)
    phase = _phase(run, phase)
    if not voice.configured():
        raise HTTPException(503, "voice-over needs AZURE_SPEECH_REGION and a key")
    try:
        return runs.voice(run, prof, phase)
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.get("/api/runs/{run_id}/audio/{key}.mp3")
def audio(run_id: str, key: str):
    run = _get(run_id)
    data = run.audio.get(key)
    if data is None:
        raise HTTPException(404, "no such audio")
    return Response(data, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/runs/{run_id}/trail")
def get_trail(run_id: str, after: int = -1):
    """The crew's audit trail: every agent call, retry, fallback and Verifier check."""
    run = _get(run_id)
    return dict(mode="agents" if run.crew else "rules", trail=[it for it in run.store.trail if it["seq"] > after])


class Switch(BaseModel):
    role: str


@app.post("/api/runs/{run_id}/crew/kill")
def kill_agent(run_id: str, body: Switch):
    """Switch an agent off mid-run; the crew routes around it and the trail shows how."""
    run = _get(run_id)
    if not run.crew:
        raise HTTPException(409, "this run uses the rules crew")
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of {', '.join(ROLES)}")
    run.crew.kill(body.role)
    return dict(killed=sorted(run.crew.killed))


@app.post("/api/runs/{run_id}/crew/revive")
def revive_agent(run_id: str, body: Switch):
    run = _get(run_id)
    if not run.crew:
        raise HTTPException(409, "this run uses the rules crew")
    run.crew.revive(body.role)
    return dict(killed=sorted(run.crew.killed))


@app.websocket("/api/runs/{run_id}/ws")
async def stream(ws: WebSocket, run_id: str):
    run = runs.runs.get(run_id)
    if not run:
        await ws.close(code=4404)
        return
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue()
    run.subscribers.append(q)
    try:
        await ws.send_json(dict(type="snapshot", run=run.snapshot()))
        while True:
            msg = await q.get()
            await ws.send_json(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        run.subscribers.remove(q)


# ---- /proof: what each run did, live from memory or replayed from the archive ----

def _light(rundown: dict) -> dict:
    """A rundown without its tracking frames, which /proof doesn't draw."""
    return dict(rundown, segments=[dict(s, clip={k: v for k, v in s["clip"].items() if k != "frames"})
                                   for s in rundown["segments"]])


@app.get("/api/proof/runs")
def proof_runs():
    live = [dict(runs.summary(r), live=True) for r in sorted(runs.runs.values(), key=lambda r: -r.started_at)]
    seen = {r["runId"] for r in live}
    error = None
    try:
        past = [dict(r, live=False) for r in runs.archive.runs(30) if r["runId"] not in seen]
    except Exception as e:  # the archive can't be read: say so, and still show the live runs
        past, error = [], f"{type(e).__name__}: {str(e)[:200]}"
    for r in live:
        r.pop("feed"), r.pop("candidates")
    return dict(store=runs.archive.name, error=error, now=time.time(), runs=live + past)


@app.get("/api/proof/runs/{run_id}")
def proof_run(run_id: str):
    run = runs.runs.get(run_id)
    if run:
        rundowns = {k: _light(r) for k, r in run.rundowns.items()}
        rundowns.update({f"catchup|{k}": _light(r) for k, r in run.catchups.items()})
        return dict(live=True, now=time.time(), run=runs.summary(run), trail=run.store.trail, rundowns=rundowns)
    try:
        saved = runs.archive.load(run_id)
    except Exception as e:
        raise HTTPException(502, f"the archive can't be read ({type(e).__name__})")
    if not saved:
        raise HTTPException(404, "no such run")
    return dict(live=False, now=time.time(), run=saved["run"], trail=saved["trail"],
                rundowns={k: _light(r) for k, r in saved["rundowns"].items()})


@app.get("/api/proof/evals")
def proof_evals():
    """The published eval summaries (raw files in docs/evals)."""
    return {f.stem: json.loads(f.read_text())["summary"] for f in sorted(EVALS.glob("*.json"))}


if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = WEB / path
        return FileResponse(f if path and f.is_file() else WEB / "index.html")
