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

from ..agents import llm, verify
from ..agents.crew import Crew
from ..agents.state import Store
from ..engine import detect
from ..engine.data import MatchData
from ..sim import teams
from ..sim.match import Sim
from ..studio import producer, telestrate, voice
from ..studio.profile import DEPTHS, MASTER, SIDES, Profile
from . import archive as archive_mod
from . import bus as bus_mod

SCAN_EVERY_S = 60.0
MAX_RUNS = 24  # in memory; older runs live on in the archive
MAX_PLAYING_PER_CLIENT = 6  # matches in play started from one address (every page load starts one)
PARKED_S = 600.0  # a match left at half-time this long stops counting against its address
SEND_EVERY_S = 0.1  # how often the simulator publishes a batch of frames
CUSHION_S = 0.4  # playback runs this far behind the simulator, to absorb the stream's jitter
# At the whistle the crew makes the viewers' own cuts first, then this wall of cuts in English: every
# side at both depths. Other languages and followed players are made on request.
WALL = [Profile(side, "en", depth) for depth in DEPTHS for side in SIDES]
MAX_VIEWERS = 6  # viewer profiles drafted during the half
FANOUT = 4  # cuts made at once after the whistle
CATCHUP_S = 35.0


@dataclass
class Run:
    id: str
    seed: int
    speed: float
    sim: Sim  # the simulator side only; the studio side reads the stream
    owner: str = ""  # handed only to the browser that started the run; it may skip, restart or kill agents
    client: str = ""
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
    rundowns: dict = field(default_factory=dict)  # "ht|<profile key>" or "ft|<profile key>" -> rundown
    swapped: dict = field(default_factory=dict)
    viewers: dict = field(default_factory=dict)  # profile key -> Profile, for the fans watching
    wall: dict = field(default_factory=dict)  # "ht|<profile key>" -> how that cut was made
    catchups: dict = field(default_factory=dict)  # id -> catch-up rundown
    building: dict = field(default_factory=dict)  # rundown key -> threading.Event, set when ready
    locked_at: float | None = None
    whistle_at: float | None = None
    ft_at: float | None = None  # the full-time whistle
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
                    candidates=[c.to_dict() for c in self.candidates.values()], crew=self.crew_info(),
                    wall=list(self.wall.values()))

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

    def playing_for(self, client: str) -> int:
        t = time.time()
        return sum(1 for r in self.runs.values() if r.client == client and (
            r.status in ("first_half", "second_half")
            or r.status == "half_time" and t - (r.whistle_at or t) < PARKED_S))

    def create(self, seed: int | None, speed: float, client: str = "") -> Run:
        if client and self.playing_for(client) >= MAX_PLAYING_PER_CLIENT:
            raise PermissionError(f"{MAX_PLAYING_PER_CLIENT} matches are already playing from here; finish one or wait")
        if self.bus is None:
            raise RuntimeError(f"the match stream is down ({self.bus_error})")
        if not self.bus.ready(30):
            raise RuntimeError("the match stream is not ready yet")
        seed = int(seed if seed is not None else np.random.default_rng().integers(1000, 99999))
        sim = Sim(seed, *teams.fixture(seed))  # a different fixture from the six fictional clubs
        run = Run(id=uuid.uuid4().hex[:10], seed=seed, speed=speed, sim=sim, meta=archive_mod.plain(sim.match.meta()),
                  owner=uuid.uuid4().hex, client=client)
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
        """Keep memory bounded: finished runs leave memory first, then the oldest (all stay in the archive)."""
        while len(self.runs) >= MAX_RUNS:
            old = min(self.runs.values(), key=lambda r: (r.status in ("first_half", "half_time", "second_half"), r.started_at))
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
                    candidates=[c.to_dict() for c in run.candidates.values()], wall=list(run.wall.values()))

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
                            if run.status != "second_half":
                                run.status = "second_half"
                                self.publish(run, dict(type="status", status=run.status))
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
                    if msg["fast"] and msg["frames"]:  # skipping: one frame a batch, so the clock is seen racing
                        last = msg["frames"][-1]
                        self.publish(run, dict(type="frames", frames=[last[1:]], period=int(last[0])))
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
                    self._full_time(run)
                    self.save(run)
                    self.bus.unsubscribe(run.id)
                    return
        except Exception as e:
            self._fail(run, "consuming", e)

    def second_half(self, run: Run) -> str:
        if run.status == "half_time":
            run.status = "second_half"
            run.resume.set()
            self.publish(run, dict(type="status", status=run.status))
            self.save(run)
        return run.status

    def _scan(self, run: Run, period: int, t: float):
        md = run.match_data()
        found = detect.scan(md, period, t) + detect.moments(md, period, t - SCAN_EVERY_S, t)
        new = detect.track(run.candidates, found, t)
        for c in new:
            self._log(run, "candidate", c.headline, candidate=c.to_dict())
        if period == 1:
            pick = producer.pick(md, [c for c in run.candidates.values() if c.period == 1], t)
            self.publish(run, dict(type="provisional", picks=[c.to_dict() for c in pick]))
            if run.crew:  # score new points and start writing the likely segments now, not at the whistle
                if new:
                    run.crew.analyse(md, new)
                half = [c for c in run.candidates.values() if c.period == 1]
                for prof in [MASTER] + list(run.viewers.values())[:MAX_VIEWERS]:
                    run.crew.draft(md, half, t, prof.lang, prof.side, prof.depth, prof.player)
        self.save(run)  # each match minute, so a replay shows how far a run got

    def _half_time(self, run: Run):
        run.status = "half_time"
        run.whistle_at = time.time()
        self.publish(run, dict(type="status", status=run.status))
        md = run.match_data()
        t_end = float(md.frames[md.frame_index(1, 1e9), 1])
        self._scan(run, 1, t_end)
        r = self.rundown(run, MASTER, md)
        run.locked_at = time.time()
        self._log(run, "locked", f"Rundown locked: {len(r['segments'])} segments, {r['duration_s']} s",
                  seconds_after_whistle=round(run.locked_at - run.whistle_at, 2))
        self.publish(run, dict(type="rundown", key=MASTER.key, phase="ht", rundown=r))
        self.save(run)
        self._fan_out(run, "ht", md)

    def _full_time(self, run: Run):
        """The full-time show: the second half's story, for the studio and every viewer."""
        run.ft_at = time.time()
        md = run.match_data()
        r = self.rundown(run, MASTER, md, "ft")
        self._log(run, "locked", f"Full-time rundown locked: {len(r['segments'])} segments, {r['duration_s']} s")
        self.publish(run, dict(type="rundown", key=MASTER.key, phase="ft", rundown=r))
        self._fan_out(run, "ft", md)

    def _fan_out(self, run: Run, phase: str, md: MatchData):
        """Make every viewer's cut, then the wall, a few at a time, in the background. Each one is
        published the moment it is ready, and the wall records how long it took and what aired."""
        viewers = [p for p in run.viewers.values() if p.key != MASTER.key]
        wall = [p for p in (WALL if phase == "ht" else []) if p.key != MASTER.key and p.key not in run.viewers]

        def one(prof: Profile):
            r = self.rundown(run, prof, md, phase)
            self.publish(run, dict(type="rundown", key=prof.key, phase=phase, rundown=r))
            self.save(run)

        def go():  # the people watching first, then the wall
            with ThreadPoolExecutor(FANOUT) as ex:
                list(ex.map(one, viewers))
                list(ex.map(one, wall))
        threading.Thread(target=go, daemon=True, name=f"fanout-{run.id}").start()

    def watch(self, run: Run, prof: Profile) -> dict:
        """A fan says who they are. During the first half the crew drafts their cut as the match
        goes; after a whistle it is made at once (or already exists)."""
        if prof.key not in run.viewers and len(run.viewers) < 200:
            run.viewers[prof.key] = prof
        phase = "ft" if run.status == "full_time" else "ht" if run.locked_at else None
        if phase and f"{phase}|{prof.key}" not in run.rundowns:
            md = run.match_data()
            threading.Thread(target=lambda: self.publish(run, dict(
                type="rundown", key=prof.key, phase=phase, rundown=self.rundown(run, prof, md, phase))),
                daemon=True).start()
        return dict(profile=prof.to_dict(), phase=phase)

    def _window(self, run: Run, md: MatchData, phase: str) -> tuple[list, float, float | None]:
        period = 1 if phase == "ht" else 2
        t_end = float(md.frames[md.frame_index(period, 1e9), 1])
        return [c for c in run.candidates.values() if c.period == period], t_end, None

    def rundown(self, run: Run, prof: Profile, md: MatchData | None = None, phase: str = "ht") -> dict:
        key = f"{phase}|{prof.key}"
        ev = run.building.setdefault(key, threading.Event())
        if key in run.rundowns:
            return run.rundowns[key]
        with run.lock:
            mine = not getattr(ev, "claimed", False)
            ev.claimed = True
        if not mine:  # someone else is making this cut: wait for theirs
            ev.wait(180)
            if key in run.rundowns:
                return run.rundowns[key]
        t0 = time.time()
        try:
            md = md or run.match_data()
            cands, t_end, t_from = self._window(run, md, phase)
            r = None
            if run.crew:
                try:
                    r = run.crew.rundown(md, cands, t_end, 90.0, prof.side, prof.lang, depth=prof.depth,
                                         player=prof.player, t_from=t_from)
                except Exception as e:  # the crew as a whole failed: the rules crew takes the half
                    run.store.record("producer", f"{prof.key} rundown", "fallback",
                                     why=f"crew failed ({type(e).__name__}); rules rundown")
            r = r or _checked(md, producer.rundown(md, cands, t_end, 90.0, prof.side, prof.lang, prof.depth,
                                                   prof.player, t_from))
            r.update(profile=prof.to_dict(), phase=phase)
            run.rundowns[key] = r
            run.wall[key] = self._wall_item(run, prof, phase, r, time.time() - t0)
            self.archive.save_doc(run.id, "rundown", key, r)
            self.publish(run, dict(type="wall", item=run.wall[key]))
            return r
        finally:
            ev.set()

    def _wall_item(self, run: Run, prof: Profile, phase: str, r: dict, took_s: float) -> dict:
        segs = r["segments"]
        return dict(key=f"{phase}|{prof.key}", phase=phase, profile=prof.to_dict(),
                    player_name=run.meta and _name(run.meta, prof.player),
                    ready_s=round(time.time() - ((run.ft_at if phase == "ft" else run.whistle_at) or time.time()), 2),
                    took_s=round(took_s, 2),
                    segments=len(segs), verified=sum(1 for s in segs if s["provenance"].get("verified")),
                    lead=segs[0]["title"] if segs else None, kinds=[s["kind"] for s in segs],
                    made_by=r.get("made_by"),
                    agent_words=sum(1 for s in segs if (s["provenance"].get("crew") or {}).get("writer") == "agent"))

    def catch_up(self, run: Run, prof: Profile, since_s: float) -> dict:
        """What a fan missed since `since_s` (match clock), as one short voiced segment for their profile."""
        md = run.match_data()
        period, t_now = run.period, float(run.clock)
        start = 0.0 if period == 1 else md.meta.get("period2_start", 2700.0)
        since = min(max(float(since_s), start), t_now)
        cands = [c for c in run.candidates.values() if c.period == period and c.last_t >= since]
        cands += [c for c in detect.moments(md, period, since, t_now)
                  if detect.key(c) not in {detect.key(x) for x in cands}]
        t0 = time.time()
        r = None
        if run.crew:
            try:
                r = run.crew.rundown(md, cands, t_now, CATCHUP_S, prof.side, prof.lang, depth=prof.depth,
                                     player=prof.player, t_from=since)
            except Exception as e:
                run.store.record("producer", f"catch-up for {prof.key}", "fallback",
                                 why=f"crew failed ({type(e).__name__}); rules catch-up")
        r = r or _checked(md, producer.rundown(md, cands, t_now, CATCHUP_S, prof.side, prof.lang, prof.depth,
                                               prof.player, since))
        cid = uuid.uuid4().hex[:8]
        r.update(id=cid, profile=prof.to_dict(), phase="catchup", window=dict(from_s=round(since, 1), to_s=round(t_now, 1)),
                 built_ms=round((time.time() - t0) * 1000))
        run.catchups[cid] = r
        run.store.record("producer", f"caught a fan up on {round((t_now - since) / 60)} min ({prof.key})", "ok",
                         ms=(time.time() - t0) * 1000, why=" / ".join(s["title"] for s in r["segments"]) or "nothing new")
        self.archive.save_doc(run.id, "rundown", f"catchup|{cid}", r)
        if voice.configured() and r["segments"]:
            self._voice(run, r, prof.lang)
        return r


    def swap(self, run: Run, prof: Profile, segment_id: str, phase: str = "ht") -> dict:
        """Replace one segment with the best talking point not already in the rundown."""
        cut, lang = prof.side, prof.lang
        r = self.rundown(run, prof, phase=phase)
        idx = next((i for i, s in enumerate(r["segments"]) if s["segment_id"] == segment_id), None)
        if idx is None:
            raise KeyError(segment_id)
        md = run.match_data()
        cands, t_end, _ = self._window(run, md, phase)
        used = {(s["provenance"]["candidate"]["kind"], s["provenance"]["candidate"]["team"],
                 s["provenance"]["candidate"]["t"]) for s in r["segments"]}
        used |= run.swapped.setdefault((phase, prof.key), set())
        old = r["segments"][idx]
        others = [s for k, s in enumerate(r["segments"]) if k != idx]
        kinds = {s["kind"] for s in others}
        has_moment = any(k in ("goal", "big_chance") for k in kinds)
        for _, c in producer.rank(md, cands, t_end, cut, prof.player):
            if (c.kind, c.team, c.t) in used or c.kind in kinds:
                continue
            if has_moment and c.kind in ("goal", "big_chance"):
                continue  # same variety rules as the Producer
            run.swapped[(phase, prof.key)].add((old["provenance"]["candidate"]["kind"], old["provenance"]["candidate"]["team"],
                                          old["provenance"]["candidate"]["t"]))
            seg = None
            if run.crew:
                try:
                    seg = run.crew.segment(md, c, lang, cut, old["duration_s"], segment_id, depth=prof.depth,
                                           player=prof.player)
                except Exception as e:
                    run.store.record("producer", f"swap {segment_id}", "fallback", why=f"{type(e).__name__}; rules segment")
            seg = seg or telestrate.build(md, c, lang=lang, target_s=old["duration_s"], seg_id=segment_id, depth=prof.depth)
            r["segments"][idx] = seg
            r["duration_s"] = round(producer.INTRO_S + sum(s["duration_s"] for s in r["segments"]), 2)
            self._log(run, "swap", f"Swapped in: {c.headline}")
            self.archive.save_doc(run.id, "rundown", f"{phase}|{prof.key}", r)
            return r
        raise LookupError("no other talking points")


    def voice(self, run: Run, prof: Profile, phase: str = "ht") -> dict:
        """Voice every segment of a cut that has no voice yet, and re-time it to the audio."""
        r = self.rundown(run, prof, phase=phase)
        self._voice(run, r, prof.lang)
        self.archive.save_doc(run.id, "rundown", f"{phase}|{prof.key}", r)
        return r

    def _voice(self, run: Run, r: dict, lang: str):
        todo = [s for s in r["segments"] if s.get("voice", {}).get("key") != voice.key(s, lang)]

        def one(seg):
            k = voice.key(seg, lang)
            v = voice.synthesise([ln["text"] for ln in seg["script"]], lang, voice.gaps(seg))
            track = voice.fit(seg, v)
            run.audio[k] = v["audio"]
            seg["voice"] = dict(track, key=voice.key(seg, lang), url=f"/api/runs/{run.id}/audio/{voice.key(seg, lang)}.mp3")
        if todo:
            t0 = time.time()
            with ThreadPoolExecutor(3) as ex:
                list(ex.map(one, todo))
            r["duration_s"] = round(producer.INTRO_S + sum(s["duration_s"] for s in r["segments"]), 2)
            run.store.record("voice", f"voiced {len(todo)} segments ({lang})", "ok", ms=(time.time() - t0) * 1000,
                             why=f"Azure AI Speech, {voice.VOICES[lang][1]}")


def _checked(md: MatchData, r: dict) -> dict:
    """The rules crew's rundown, through the same Verifier as the agents'. A segment that fails
    stays off air."""
    keep = []
    for seg in r["segments"]:
        errs = verify.segment_errors(md, seg)
        seg["provenance"].update(verified=not errs, crew=dict(telestrator="rules", writer="rules"))
        if errs:
            seg["provenance"]["errors"] = errs[:5]
        else:
            keep.append(seg)
    if len(keep) < len(r["segments"]):
        r["segments"] = keep
        r["duration_s"] = round(producer.INTRO_S + sum(s["duration_s"] for s in keep), 2) if keep else 0.0
    return r


def _name(meta: dict, pid: str | None) -> str | None:
    if not pid:
        return None
    for side in ("home", "away"):
        for p in meta[side]["players"]:
            if p["id"] == pid:
                return p["name"]
    return None


def _put(q: asyncio.Queue, msg: dict):
    if q.qsize() < 2000:
        q.put_nowait(msg)
