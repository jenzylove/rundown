"""HTTP + WebSocket API.

    uvicorn rundown.api.app:app --port 8000
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agents.crew import ROLES
from ..studio import voice
from ..studio.profile import Profile
from . import mcp_server
from .runs import RunManager

runs = RunManager()
mcp = mcp_server.attach(runs)
mcp_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def lifespan(_app):
    runs.loop = asyncio.get_running_loop()
    import threading
    threading.Thread(target=lambda: showcase(), daemon=True, name="showcase").start()  # warm the landing page's hero
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Rundown", lifespan=lifespan)
app.mount("/mcp", mcp_app)

# Every match, cut and catch-up spends model and voice calls, and the app is open with no sign-up,
# so each address gets a budget per ten minutes. Generous for a judge clicking around; a loop stops.
LIMITS = {("POST", "/api/runs"): 15, ("POST", "catchup"): 20, ("POST", "watch"): 40,
          ("GET", "rundown"): 120, ("POST", "voice"): 40, ("POST", "swap"): 40, ("POST", "/mcp"): 120}
WINDOW_S = 600.0
_hits: dict = {}


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    path, method = request.url.path, request.method
    rule = next((k for k in LIMITS if k[0] == method and (path == k[1] or path.endswith("/" + k[1])
                                                          or (k[1] == "/mcp" and path.startswith("/mcp")))), None)
    if rule:
        now, key = time.time(), (_client(request), rule)
        recent = [t for t in _hits.get(key, []) if now - t < WINDOW_S]
        if len(recent) >= LIMITS[rule]:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Too many requests from here; try again in a few minutes"}, status_code=429)
        recent.append(now)
        _hits[key] = recent
        if len(_hits) > 50000:
            _hits.clear()
    return await call_next(request)
WEB = Path(__file__).resolve().parents[2] / "web" / "dist"
EVALS = Path(__file__).resolve().parents[2] / "docs" / "evals"


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
def create_run(body: NewRun, request: Request):
    if not 1 <= body.speed <= 32:
        raise HTTPException(400, "speed must be 1-32")
    try:
        run = runs.create(body.seed, body.speed, _client(request))
    except PermissionError as e:
        raise HTTPException(429, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return dict(run.snapshot(), owner=run.owner)  # the owner token goes only to the browser that started it


def _owned(run, owner: str | None):
    """Skipping, restarting and switching agents off change the match for everyone watching it,
    so only the browser that started the run may do them."""
    if not owner or owner != run.owner:
        raise HTTPException(403, "only the browser that started this match can do that")


def _get(run_id: str):
    run = runs.runs.get(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    return run


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return _get(run_id).snapshot()


@app.post("/api/runs/{run_id}/skip")
def skip_to_half_time(run_id: str, x_run_owner: str | None = Header(None)):
    run = _get(run_id)
    _owned(run, x_run_owner)
    run.skip = True
    return dict(ok=True)


@app.post("/api/runs/{run_id}/second-half")
def second_half(run_id: str, x_run_owner: str | None = Header(None)):
    run = _get(run_id)
    _owned(run, x_run_owner)
    return dict(status=runs.second_half(run))


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
def kill_agent(run_id: str, body: Switch, x_run_owner: str | None = Header(None)):
    """Switch an agent off mid-run; the crew routes around it and the trail shows how."""
    run = _get(run_id)
    _owned(run, x_run_owner)
    if not run.crew:
        raise HTTPException(409, "this run uses the rules crew")
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of {', '.join(ROLES)}")
    run.crew.kill(body.role)
    return dict(killed=sorted(run.crew.killed))


@app.post("/api/runs/{run_id}/crew/revive")
def revive_agent(run_id: str, body: Switch, x_run_owner: str | None = Header(None)):
    run = _get(run_id)
    _owned(run, x_run_owner)
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


LIVE_FOR_S = 3 * 3600  # a match in memory longer than this has stalled; it isn't live


def _is_live(run) -> bool:
    """Live means the match is still in play on this server, not just held in memory."""
    return run.status in ("first_half", "half_time", "second_half") and time.time() - run.started_at < LIVE_FOR_S


@app.get("/api/proof/runs")
def proof_runs():
    live = [dict(runs.summary(r), live=_is_live(r)) for r in sorted(runs.runs.values(), key=lambda r: -r.started_at)]
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
        return dict(live=_is_live(run), now=time.time(), run=runs.summary(run), trail=run.store.trail, rundowns=rundowns)
    try:
        saved = runs.archive.load(run_id)
    except Exception as e:
        raise HTTPException(502, f"the archive can't be read ({type(e).__name__})")
    if not saved:
        raise HTTPException(404, "no such run")
    return dict(live=False, now=time.time(), run=saved["run"], trail=saved["trail"],
                rundowns={k: _light(r) for k, r in saved["rundowns"].items()})


_showcase: dict = {}
_showcase_audio: dict = {}
_showcase_lock = __import__("threading").Lock()
SHOWCASE_SEED = 38920  # a first half with a headed goal from a cross, a press and a flank story


@app.get("/api/showcase")
def showcase():
    """The landing page's hero: one fixed first half cut for two different fans, by the rules crew
    (no model calls), through the same Verifier. Made once, then served from memory."""
    with _showcase_lock:
        if not _showcase:
            from ..agents import verify
            from ..evals.rundown import first_half, spot
            from ..studio import producer
            from ..studio.profile import Profile
            md, _ = first_half(SHOWCASE_SEED)
            cands, t_end = spot(md)
            away_mid = next(p for p in md.meta["away"]["starters"] if md.players[p]["role"] in ("LCM", "RCM", "CM"))
            cuts = {}
            for prof in (Profile("neutral", "en", "analyst"), Profile("away", "fr", "casual", away_mid)):
                r = producer.rundown(md, cands, t_end, 90.0, prof.side, prof.lang, prof.depth, prof.player)
                for seg in r["segments"]:
                    seg["provenance"]["verified"] = not verify.segment_errors(md, seg)
                cuts[prof.key] = dict(r, profile=prof.to_dict())
            if voice.configured():
                for key, r in cuts.items():
                    for seg in r["segments"]:
                        try:
                            v = voice.synthesise([ln["text"] for ln in seg["script"]], r["lang"])
                            track = voice.fit(seg, v)
                            k = voice.key(seg, r["lang"])
                            _showcase_audio[k] = v["audio"]
                            seg["voice"] = dict(track, key=k, url=f"/api/showcase/audio/{k}.mp3")
                        except Exception:
                            pass  # the hero still plays, captions only
            _showcase.update(seed=SHOWCASE_SEED, meta=md.meta, cuts=cuts)
    return _showcase


@app.get("/api/showcase/audio/{key}.mp3")
def showcase_audio(key: str):
    data = _showcase_audio.get(key)
    if data is None:
        raise HTTPException(404, "no such audio")
    return Response(data, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/proof/evals")
def proof_evals():
    """The published eval summaries (raw files in docs/evals)."""
    return {f.stem: json.loads(f.read_text())["summary"] for f in sorted(EVALS.glob("*.json"))}


if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    PAGES = {"", "watch", "demo", "proof"}

    @app.get("/{path:path}")
    def spa(path: str):
        f = WEB / path
        if path and f.is_file():
            return FileResponse(f)
        if path.strip("/").split("/")[0] in PAGES:
            return FileResponse(WEB / "index.html")
        raise HTTPException(404, "no such page")
