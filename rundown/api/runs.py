"""Live runs. The simulator plays a match in (accelerated) real time and publishes it to the
match stream (`bus.py`: Event Hubs, or an in-process queue offline). The studio side consumes
that stream and works only from what arrives: it runs the talking-point detectors every match
minute, locks the rundown at the half-time whistle and plays the match to the browser. Every
run, crew document, trail item and rundown goes to the archive (`archive.py`: Cosmos DB)."""

from __future__ import annotations

import asyncio
import os
import queue
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from ..agents import llm
from ..agents.crew import Crew, cand_key
from ..agents.state import Store
from ..engine import detect
from ..engine.data import MatchData
from ..sim.match import Sim
from ..studio import producer, telestrate, voice
from . import archive as archive_mod
from . import bus as bus_mod

SCAN_EVERY_S = 60.0
MAX_RUNS = 12  # in memory; older runs live on in the archive
SEND_EVERY_S = 0.1  # how often the simulator publishes a batch of frames
CUSHION_S = 0.4  # playback runs this far behind the simulator, to absorb the stream's jitter


@dataclass
class Run:
    id: str
    seed: int
    speed: float
    sim: Sim  # the simulator side only; the studio side reads the stream
    meta: dict = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    frames: list[np.ndarray] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    clock: float = 0.0
    period: int = 1
    score: list[int] = field(default_factory=lambda: [0, 0])
    status: str = "first_half"  # first_half | half_time | second_half | full_time | failed
    error: str | None = None
    candidates: dict = field(default_factory=dict)
    feed: list[dict] = field(default_factory=list)  # rundown activity for the side panel
    rundowns: dict = field(default_factory=dict)
    swapped: dict = field(default_factory=dict)
    locked_at: float | None = None
    whistle_at: float | None = None
    skip: bool = False
    stop: bool = False
    resume: threading.Event = field(default_factory=threading.Event)  # the second half may start
    inbox: queue.Queue = field(default_factory=queue.Queue)
    lags: list[float] = field(default_factory=list)  # stream lag per message, seconds
    lock: threading.Lock = field(default_factory=threading.Lock)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    crew: Crew | None = None
    store: Store | None = None
    audio: dict = field(default_factory=dict)  # voice key -> mp3 bytes

    def match_data(self) -> MatchData:
        with self.lock:
            frames = np.stack(self.frames) if self.frames else np.zeros((1, 49), dtype=np.float32)
            events = list(self.events)
        return MatchData(meta=self.meta, events=events, frames=frames)

    def crew_info(self) -> dict:
        return dict(mode="agents" if self.crew else "rules", model=self.crew.models.model if self.crew else None,
                    killed=sorted(self.crew.killed) if self.crew else [])

    def snapshot(self) -> dict:
        return dict(id=self.id, seed=self.seed, speed=self.speed, status=self.status, error=self.error,
                    clock=round(self.clock, 1), period=self.period, score=list(self.score),
                    meta=self.meta, feed=self.feed[-60:], locked_at=self.locked_at, whistle_at=self.whistle_at,
                    candidates=[c.to_dict() for c in self.candidates.values()], crew=self.crew_info())

    def stream_info(self, name: str) -> dict:
        lags = sorted(self.lags)
        return dict(name=name, messages=len(lags), lag_ms_first=round(self.lags[0] * 1000) if lags else None,
                    lag_ms_p50=round(statistics.median(lags) * 1000) if lags else None,
                    lag_ms_p95=round(lags[int(len(lags) * 0.95)] * 1000) if lags else None)


# Trail statuses worth a line in the live feed; everything goes out on the "trail" channel.
FEED_STATUSES = {"fallback", "killed", "failed"}


class RunManager:
    def __init__(self, bus=None, archive=None):
        self.runs: dict[str, Run] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._models: llm.Models | None = None
        self.bus_error: str | None = None
        try:
            self.bus = bus or bus_mod.from_env()
        except Exception as e:  # configured but unreachable: fail closed, never fall back to the local queue
            self.bus, self.bus_error = None, f"{type(e).__name__}: {str(e)[:200]}"
        self.archive = archive or archive_mod.from_env()

    def models(self) -> llm.Models | None:
        """The crew's models, when Azure is configured and RUNDOWN_CREW isn't 'rules'."""
        if self._models is None and llm.configured() and os.environ.get("RUNDOWN_CREW") != "rules":
            self._models = llm.Models()
        return self._models

    def create(self, seed: int | None, speed: float) -> Run:
        if self.bus is None:
            raise RuntimeError(f"the match stream is down ({self.bus_error})")
        if not self.bus.ready(30):
            raise RuntimeError("the match stream is not ready yet")
        seed = int(seed if seed is not None else np.random.default_rng().integers(1000, 99999))
        sim = Sim(seed)
        run = Run(id=uuid.uuid4().hex[:10], seed=seed, speed=speed, sim=sim, meta=archive_mod.plain(sim.match.meta()))
        run.store = Store(on_record=lambda item: self._trail(run, item),
                          on_put=lambda coll, key, doc: self.archive.save_doc(run.id, "crew", f"{coll}/{key}", doc))
        models = self.models()
        if models:
            run.crew = Crew(models, run.store)
        self._evict()
        self.runs[run.id] = run
        self.bus.subscribe(run.id, lambda msg: self._arrived(run, msg))
        self.save(run)
        threading.Thread(target=self._consume, args=(run,), daemon=True, name=f"studio-{run.id}").start()
        threading.Thread(target=self._play, args=(run,), daemon=True, name=f"sim-{run.id}").start()
        return run

    def _arrived(self, run: Run, msg: dict):
        run.lags.append(max(0.0, time.time() - msg["sent"]))  # transit only, before playback's cushion
        run.inbox.put(msg)

    def _evict(self):
        """Keep memory bounded: the oldest runs leave memory (they stay in the archive)."""
        while len(self.runs) >= MAX_RUNS:
            old = min(self.runs.values(), key=lambda r: r.started_at)
            old.stop = True
            old.resume.set()
            old.inbox.put(dict(k="stop"))
            self.bus.unsubscribe(old.id)
            del self.runs[old.id]

    def summary(self, run: Run) -> dict:
        """The run as /proof and the archive see it."""
        home, away = run.meta["home"], run.meta["away"]
        return dict(runId=run.id, seed=run.seed, speed=run.speed, status=run.status, error=run.error,
                    started_at=run.started_at, whistle_at=run.whistle_at, locked_at=run.locked_at,
                    score=list(run.score), clock=round(run.clock, 1), period=run.period,
                    clubs=[dict(code=c["code"], short=c["short"], colour=c["colour"]) for c in (home, away)],
                    crew=run.crew_info(), stream=run.stream_info(self.bus.name if self.bus else "down"),
                    feed=[f for f in run.feed if f["kind"] != "candidate"][-100:],
                    candidates=[c.to_dict() for c in run.candidates.values()])

    def save(self, run: Run):
        self.archive.save_run(run.id, self.summary(run))

    # ---- pub/sub to websockets ----
    def publish(self, run: Run, msg: dict):
        if not self.loop:
            return
        for q in list(run.subscribers):
            self.loop.call_soon_threadsafe(_put, q, msg)

    def _trail(self, run: Run, item: dict):
        self.archive.save_trail(run.id, item)
        self.publish(run, dict(type="trail", item=item))
        if item["status"] in FEED_STATUSES:
            self._log(run, "agent", f"{item['agent'].capitalize()} {item['status']}: {item['action']}",
                      agent=item["agent"], status=item["status"], why=item["why"])

    def _log(self, run: Run, kind: str, text: str, **kw):
        item = dict(kind=kind, text=text, clock=round(run.clock, 1), at=time.time(), **kw)
        run.feed.append(item)
        self.publish(run, dict(type="feed", item=item))

    def _fail(self, run: Run, where: str, e: Exception):
        run.status, run.error = "failed", f"{where}: {type(e).__name__}: {str(e)[:200]}"
        self._log(run, "error", f"The match stream failed ({where}). This run has stopped; start a new match.")
        self.publish(run, dict(type="status", status=run.status, error=run.error))
        self.save(run)

    # ---- the simulator side: play the match and publish it ----
    def _play(self, run: Run):
        sim = run.sim
        frames: list[list[float]] = []
        events: list[list] = []
        n_events = 0
        last_send = time.time()
        t_wall, sim_t0 = time.time(), 0.0

        def send(msg: dict):
            self.bus.send(run.id, dict(msg, sent=time.time()))

        def flush(fast: bool):
            nonlocal frames, events, last_send
            if frames:
                send(dict(k="frames", frames=frames, events=events, fast=fast))
            frames, events, last_send = [], [], time.time()

        try:
            for frame in sim.run():
                if run.stop:
                    return
                t = float(frame[1])
                new = archive_mod.plain(sim.match.events[n_events:])
                n_events = len(sim.match.events)
                fast = run.skip and int(frame[0]) == 1
                if any(e["type"] == "period_end" and e["period"] == 1 for e in new):
                    # the whistle; this frame is already the second-half lineup, so it waits for the restart
                    flush(fast)
                    send(dict(k="whistle", events=new))
                    new = []
                    while not run.resume.wait(0.2):
                        if run.stop:
                            return
                    t_wall, sim_t0 = time.time(), t
                if new:
                    events.append([len(frames), new])
                frames.append([round(float(v), 2) for v in frame])
                if len(frames) >= 200 or time.time() - last_send >= SEND_EVERY_S:
                    flush(fast)
                if fast:
                    t_wall, sim_t0 = time.time(), t
                else:
                    ahead = (t - sim_t0) / run.speed - (time.time() - t_wall)
                    if ahead > 0:
                        time.sleep(ahead)
            flush(False)
            send(dict(k="end"))
        except Exception as e:
            self._fail(run, "publishing", e)

    # ---- the studio side: consume the stream ----
    def _consume(self, run: Run):
        next_scan = SCAN_EVERY_S
        period_seen = 1
        out: list[list[float]] = []
        try:
            while True:
                msg = run.inbox.get()
                if run.stop or msg["k"] == "stop":
                    return
                if msg["k"] == "frames":
                    evs = {i: e for i, e in msg["events"]}
                    for i, f in enumerate(msg["frames"]):
                        period, t = int(f[0]), float(f[1])
                        if period != period_seen:  # second half kicked off
                            period_seen = period
                            run.status = "second_half"
                            next_scan = 2700.0 + SCAN_EVERY_S
                        new = evs.get(i, [])
                        with run.lock:
                            run.frames.append(np.asarray(f, dtype=np.float32))
                            run.events.extend(new)
                        run.clock, run.period = t, period
                        for e in new:
                            if e["type"] == "goal":
                                run.score = [int(x) for x in str(e["score"]).split("-")]
                        if new:
                            self.publish(run, dict(type="events", events=new))
                        if t >= next_scan:
                            self._scan(run, period, t)
                            next_scan += SCAN_EVERY_S
                        if msg["fast"]:
                            out = []
                            continue
                        out.append(f[1:])
                        if len(out) >= 2:
                            # messages arrive in bursts; play each frame when the simulator made it, plus
                            # the cushion. A late message plays at once, so a delay never carries over.
                            made = msg["sent"] - (float(msg["frames"][-1][1]) - t) / run.speed
                            wait = made + CUSHION_S - time.time()
                            if wait > 0:
                                time.sleep(wait)
                            self.publish(run, dict(type="frames", frames=out, period=period))
                            out = []
                elif msg["k"] == "whistle":
                    with run.lock:
                        run.events.extend(msg["events"])
                        last = run.frames[-1] if run.frames else None
                    if msg["events"]:
                        self.publish(run, dict(type="events", events=msg["events"]))
                    if last is not None:  # the whistle frame, not the second-half lineup
                        self.publish(run, dict(type="frames", frames=[[round(float(v), 2) for v in last[1:]]], period=1))
                    out = []
                    self._half_time(run)
                elif msg["k"] == "end":
                    run.status = "full_time"
                    self.publish(run, dict(type="status", status=run.status))
                    self.save(run)
                    self.bus.unsubscribe(run.id)
                    return
        except Exception as e:
            self._fail(run, "consuming", e)

    def second_half(self, run: Run) -> str:
        if run.status == "half_time":
            run.status = "second_half"
            run.resume.set()
            self.save(run)
        return run.status

    def _scan(self, run: Run, period: int, t: float):
        md = run.match_data()
        found = detect.scan(md, period, t) + detect.moments(md, period, t - SCAN_EVERY_S, t)
        new = []
        for c in found:
            key = cand_key(c)
            if key not in run.candidates:
                run.candidates[key] = c
                new.append(c)
                self._log(run, "candidate", c.headline, candidate=c.to_dict())
        if period == 1:
            pick = producer.pick(md, [c for c in run.candidates.values() if c.period == 1], t)
            self.publish(run, dict(type="provisional", picks=[c.to_dict() for c in pick]))
            if run.crew:  # score new points and start writing the likely segments now, not at the whistle
                if new:
                    run.crew.analyse(md, new)
                run.crew.draft(md, [c for c in run.candidates.values() if c.period == 1], t)
        self.save(run)  # each match minute, so a replay shows how far a run got

    def _half_time(self, run: Run):
        run.status = "half_time"
        run.whistle_at = time.time()
        self.publish(run, dict(type="status", status=run.status))
        md = run.match_data()
        t_end = float(md.frames[md.frame_index(1, 1e9), 1])
        self._scan(run, 1, t_end)
        r = self.rundown(run, "neutral", "en", md)
        run.locked_at = time.time()
        self._log(run, "locked", f"Rundown locked: {len(r['segments'])} segments, {r['duration_s']} s",
                  seconds_after_whistle=round(run.locked_at - run.whistle_at, 2))
        self.publish(run, dict(type="rundown", rundown=r))
        self.save(run)

    def rundown(self, run: Run, cut: str, lang: str, md: MatchData | None = None) -> dict:
        key = (cut, lang)
        if key not in run.rundowns:
            md = md or run.match_data()
            t_end = float(md.frames[md.frame_index(1, 1e9), 1])
            cands = [c for c in run.candidates.values() if c.period == 1]
            r = None
            if run.crew:
                try:
                    r = run.crew.rundown(md, cands, t_end, 90.0, cut, lang)
                except Exception as e:  # the crew as a whole failed: the rules crew takes the half
                    run.store.record("producer", f"{cut} rundown in {lang}", "fallback",
                                     why=f"crew failed ({type(e).__name__}); rules rundown")
            run.rundowns[key] = r or producer.rundown(md, cands, t_end, 90.0, cut, lang)
            self.archive.save_doc(run.id, "rundown", f"{cut}|{lang}", run.rundowns[key])
        return run.rundowns[key]


    def swap(self, run: Run, cut: str, lang: str, segment_id: str) -> dict:
        """Replace one segment with the best talking point not already in the rundown."""
        r = self.rundown(run, cut, lang)
        idx = next((i for i, s in enumerate(r["segments"]) if s["segment_id"] == segment_id), None)
        if idx is None:
            raise KeyError(segment_id)
        md = run.match_data()
        t_end = float(md.frames[md.frame_index(1, 1e9), 1])
        used = {(s["provenance"]["candidate"]["kind"], s["provenance"]["candidate"]["team"],
                 s["provenance"]["candidate"]["t"]) for s in r["segments"]}
        used |= run.swapped.setdefault((cut, lang), set())
        cands = [c for c in run.candidates.values() if c.period == 1]
        old = r["segments"][idx]
        others = [s for k, s in enumerate(r["segments"]) if k != idx]
        kinds = {s["kind"] for s in others}
        has_moment = any(k in ("goal", "big_chance") for k in kinds)
        for _, c in producer.rank(md, cands, t_end, cut):
            if (c.kind, c.team, c.t) in used or c.kind in kinds:
                continue
            if has_moment and c.kind in ("goal", "big_chance"):
                continue  # same variety rules as the Producer
            run.swapped[(cut, lang)].add((old["provenance"]["candidate"]["kind"], old["provenance"]["candidate"]["team"],
                                          old["provenance"]["candidate"]["t"]))
            seg = None
            if run.crew:
                try:
                    seg = run.crew.segment(md, c, lang, cut, old["duration_s"], segment_id)
                except Exception as e:
                    run.store.record("producer", f"swap {segment_id}", "fallback", why=f"{type(e).__name__}; rules segment")
            seg = seg or telestrate.build(md, c, lang=lang, target_s=old["duration_s"], seg_id=segment_id)
            r["segments"][idx] = seg
            r["duration_s"] = round(producer.INTRO_S + sum(s["duration_s"] for s in r["segments"]), 2)
            self._log(run, "swap", f"Swapped in: {c.headline}")
            self.archive.save_doc(run.id, "rundown", f"{cut}|{lang}", r)
            return r
        raise LookupError("no other talking points")


    def voice(self, run: Run, cut: str, lang: str) -> dict:
        """Voice every segment of a cut that has no voice yet, and re-time it to the audio."""
        r = self.rundown(run, cut, lang)
        todo = [s for s in r["segments"] if s.get("voice", {}).get("key") != voice.key(s, lang)]

        def one(seg):
            k = voice.key(seg, lang)
            v = voice.synthesise([ln["text"] for ln in seg["script"]], lang)
            track = voice.fit(seg, v)
            run.audio[k] = v["audio"]
            seg["voice"] = dict(track, key=voice.key(seg, lang), url=f"/api/runs/{run.id}/audio/{voice.key(seg, lang)}.mp3")
        if todo:
            t0 = time.time()
            with ThreadPoolExecutor(3) as ex:
                list(ex.map(one, todo))
            r["duration_s"] = round(producer.INTRO_S + sum(s["duration_s"] for s in r["segments"]), 2)
            run.store.record("voice", f"voiced {len(todo)} segments ({cut}, {lang})", "ok", ms=(time.time() - t0) * 1000,
                             why=f"Azure AI Speech, {voice.VOICES[lang][1]}")
            self.archive.save_doc(run.id, "rundown", f"{cut}|{lang}", r)
        return r


def _put(q: asyncio.Queue, msg: dict):
    if q.qsize() < 2000:
        q.put_nowait(msg)
