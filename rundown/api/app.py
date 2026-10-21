"""HTTP + WebSocket API.

    uvicorn rundown.api.app:app --port 8000
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agents.crew import ROLES
from .runs import RunManager

app = FastAPI(title="Rundown")
runs = RunManager()
WEB = Path(__file__).resolve().parents[2] / "web" / "dist"


@app.on_event("startup")
async def _startup():
    runs.loop = asyncio.get_running_loop()


class NewRun(BaseModel):
    seed: int | None = None
    speed: float = 8.0


@app.get("/api/health")
def health():
    return dict(ok=True, runs=len(runs.runs), crew="agents" if runs.models() else "rules")


@app.post("/api/runs")
def create_run(body: NewRun):
    if not 1 <= body.speed <= 32:
        raise HTTPException(400, "speed must be 1-32")
    run = runs.create(body.seed, body.speed)
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
    run = _get(run_id)
    if run.status == "half_time":
        run.status = "second_half"
    return dict(status=run.status)


@app.get("/api/runs/{run_id}/rundown")
def get_rundown(run_id: str, cut: str = "neutral", lang: str = "en"):
    run = _get(run_id)
    if cut not in ("neutral", "home", "away") or lang not in ("en", "es"):
        raise HTTPException(400, "unsupported cut or language")
    if run.locked_at is None:
        raise HTTPException(409, "rundown locks at the half-time whistle")
    return runs.rundown(run, cut, lang)


class Swap(BaseModel):
    cut: str = "neutral"
    lang: str = "en"
    segment_id: str


@app.post("/api/runs/{run_id}/rundown/swap")
def swap_segment(run_id: str, body: Swap):
    run = _get(run_id)
    if run.locked_at is None:
        raise HTTPException(409, "rundown locks at the half-time whistle")
    try:
        return runs.swap(run, body.cut, body.lang, body.segment_id)
    except KeyError:
        raise HTTPException(404, "no such segment")
    except LookupError as e:
        raise HTTPException(409, str(e))


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


if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = WEB / path
        return FileResponse(f if path and f.is_file() else WEB / "index.html")
