"""Live runs: a simulated match streamed in (accelerated) real time, with the
talking-point detectors running every match minute and the rundown locked at
the half-time whistle."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field

import numpy as np

from ..engine import detect
from ..engine.data import MatchData
from ..sim.match import Sim
from ..studio import producer, telestrate

SCAN_EVERY_S = 60.0


@dataclass
class Run:
    id: str
    seed: int
    speed: float
    sim: Sim
    frames: list[np.ndarray] = field(default_factory=list)
    status: str = "first_half"  # first_half | half_time | second_half | full_time
    candidates: dict = field(default_factory=dict)
    feed: list[dict] = field(default_factory=list)  # rundown activity for the side panel
    rundowns: dict = field(default_factory=dict)
    swapped: dict = field(default_factory=dict)
    locked_at: float | None = None
    whistle_at: float | None = None
    skip: bool = False
    stop: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    subscribers: list[asyncio.Queue] = field(default_factory=list)

    def match_data(self) -> MatchData:
        with self.lock:
            frames = np.stack(self.frames) if self.frames else np.zeros((1, 49), dtype=np.float32)
            events = list(self.sim.match.events)
        return MatchData(meta=self.sim.match.meta(), events=events, frames=frames)

    def snapshot(self) -> dict:
        m = self.sim.match
        return dict(id=self.id, seed=self.seed, speed=self.speed, status=self.status,
                    clock=round(self.sim.clock, 1), period=self.sim.period, score=list(m.score),
                    meta=m.meta(), feed=self.feed[-60:], locked_at=self.locked_at, whistle_at=self.whistle_at,
                    candidates=[c.to_dict() for c in self.candidates.values()])


class RunManager:
    def __init__(self):
        self.runs: dict[str, Run] = {}
        self.loop: asyncio.AbstractEventLoop | None = None

    def create(self, seed: int | None, speed: float) -> Run:
        seed = int(seed if seed is not None else np.random.default_rng().integers(1000, 99999))
        run = Run(id=uuid.uuid4().hex[:10], seed=seed, speed=speed, sim=Sim(seed))
        self.runs[run.id] = run
        threading.Thread(target=self._drive, args=(run,), daemon=True).start()
        return run

    # ---- pub/sub to websockets ----
    def publish(self, run: Run, msg: dict):
        if not self.loop:
            return
        for q in list(run.subscribers):
            self.loop.call_soon_threadsafe(_put, q, msg)

    def _log(self, run: Run, kind: str, text: str, **kw):
        item = dict(kind=kind, text=text, clock=round(run.sim.clock, 1), at=time.time(), **kw)
        run.feed.append(item)
        self.publish(run, dict(type="feed", item=item))

    # ---- the driver thread ----
    def _drive(self, run: Run):
        sim = run.sim
        gen = sim.run()
        next_scan = SCAN_EVERY_S
        n_events = 0
        batch: list[list[float]] = []
        t_wall = time.time()
        sim_t0 = 0.0
        period_seen = 1
        last_frame = None
        for frame in gen:
            if run.stop:
                return
            with run.lock:
                run.frames.append(frame)
            period = int(frame[0])
            if period != period_seen:  # second half kicked off
                period_seen = period
                run.status = "second_half"
                next_scan = 2700.0 + SCAN_EVERY_S
                t_wall, sim_t0 = time.time(), float(frame[1])
            batch.append([round(float(v), 2) for v in frame[1:]])
            evs = sim.match.events
            if len(evs) > n_events:
                new = evs[n_events:]
                n_events = len(evs)
                self.publish(run, dict(type="events", events=new))
                if any(e["type"] == "period_end" and e["period"] == 1 for e in new):
                    if last_frame is not None:  # the whistle frame, not the second-half lineup
                        self.publish(run, dict(type="frames", frames=[[round(float(v), 2) for v in last_frame[1:]]], period=1))
                    self._half_time(run)
                    t_wall, sim_t0 = time.time(), float(frame[1])
                    batch = []
                    # hold the break until the studio asks for the second half
                    while run.status == "half_time" and not run.stop:
                        time.sleep(0.2)
                    continue
            last_frame = frame
            if float(frame[1]) >= next_scan:
                self._scan(run, period, float(frame[1]))
                next_scan += SCAN_EVERY_S
            fast = run.skip and run.status == "first_half"
            if len(batch) >= (20 if fast else 2):
                if not fast:
                    self.publish(run, dict(type="frames", frames=batch, period=period))
                batch = []
            if not fast:
                ahead = (float(frame[1]) - sim_t0) / run.speed - (time.time() - t_wall)
                if ahead > 0:
                    time.sleep(ahead)
            else:
                t_wall, sim_t0 = time.time(), float(frame[1])
        run.status = "full_time"
        self.publish(run, dict(type="status", status=run.status))

    def _scan(self, run: Run, period: int, t: float):
        md = run.match_data()
        found = detect.scan(md, period, t) + detect.moments(md, period, t - SCAN_EVERY_S, t)
        for c in found:
            key = (c.kind, c.team, c.side, c.player, period, c.evidence[0] if c.kind in ("goal", "big_chance") else None)
            if key not in run.candidates:
                run.candidates[key] = c
                self._log(run, "candidate", c.headline, candidate=c.to_dict())
        if period == 1:
            pick = producer.pick(md, [c for c in run.candidates.values() if c.period == 1], t)
            self.publish(run, dict(type="provisional", picks=[c.to_dict() for c in pick]))

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

    def rundown(self, run: Run, cut: str, lang: str, md: MatchData | None = None) -> dict:
        key = (cut, lang)
        if key not in run.rundowns:
            md = md or run.match_data()
            t_end = float(md.frames[md.frame_index(1, 1e9), 1])
            cands = [c for c in run.candidates.values() if c.period == 1]
            run.rundowns[key] = producer.rundown(md, cands, t_end, 90.0, cut, lang)
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
            seg = telestrate.build(md, c, lang=lang, target_s=old["duration_s"], seg_id=segment_id)
            r["segments"][idx] = seg
            r["duration_s"] = round(producer.INTRO_S + sum(s["duration_s"] for s in r["segments"]), 2)
            self._log(run, "swap", f"Swapped in: {c.headline}")
            return r
        raise LookupError("no other talking points")


def _put(q: asyncio.Queue, msg: dict):
    if q.qsize() < 2000:
        q.put_nowait(msg)
