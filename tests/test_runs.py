"""A live run end to end over the in-process stream: the studio side sees exactly what the
simulator played, locks the rundown at the whistle, restarts on cue, and archives it all."""

import time

import numpy as np
import pytest

from rundown.api import archive as archive_mod
from rundown.api.bus import LocalBus
from rundown.api.runs import RunManager
from rundown.sim import teams
from rundown.sim.match import Sim
from rundown.studio.profile import MASTER, Profile


class ListArchive(archive_mod.MemoryArchive):
    name = "test"

    def __init__(self):
        self.saved = {"run": [], "doc": [], "trail": []}

    def save_run(self, run_id, doc):
        self.saved["run"].append(archive_mod.plain(doc))

    def save_doc(self, run_id, kind, key, doc):
        self.saved["doc"].append((kind, key))

    def save_trail(self, run_id, item):
        self.saved["trail"].append(item)


def wait(cond, timeout=120):
    t0 = time.time()
    while not cond():
        assert time.time() - t0 < timeout, "timed out"
        time.sleep(0.1)


@pytest.fixture(scope="module")
def played(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("RUNDOWN_CREW", "rules")  # offline: no models
    archive = ListArchive()
    rm = RunManager(bus=LocalBus(), archive=archive)
    run = rm.create(5, 16)
    run.skip = True
    wait(lambda: run.locked_at is not None)
    yield rm, run, archive
    run.stop = True
    mp.undo()


def test_studio_sees_exactly_what_the_simulator_played(played):
    _, run, _ = played
    ref = Sim(5, *teams.fixture(5))
    frames = []
    for f in ref.run():
        if f[0] == 2:
            break
        frames.append(f)
    first_half = [f for f in run.frames if f[0] == 1]
    assert len(first_half) == len(frames)
    assert np.allclose(np.stack(first_half), np.stack(frames), atol=0.006)  # sent to 2 decimals (1 cm)
    assert [e["id"] for e in run.events] == [e["id"] for e in ref.match.events]


def test_rundown_locks_at_the_whistle(played):
    rm, run, _ = played
    assert run.status == "half_time"
    assert run.locked_at - run.whistle_at < 10
    r = rm.rundown(run, MASTER)
    assert len(r["segments"]) >= 1 and 40 <= r["duration_s"] <= 93
    assert any(f["kind"] == "locked" for f in run.feed)


def test_archive_gets_the_run_and_its_rundown(played):
    _, run, archive = played
    last = archive.saved["run"][-1]
    assert last["runId"] == run.id and last["locked_at"] == run.locked_at and last["candidates"]
    assert ("rundown", "ht|" + MASTER.key) in archive.saved["doc"]
    assert last["stream"]["name"] == "local" and last["stream"]["messages"] > 0


def test_every_fan_gets_their_own_cut(played):
    rm, run, _ = played
    home = run.meta["home"]["code"]
    player = next(p["id"] for p in run.meta["home"]["players"][1:])
    fan = Profile("home", "en", "casual", player)
    rm.watch(run, fan)
    r = rm.rundown(run, fan)
    assert r["profile"]["key"] == fan.key and r["depth"] == "casual"
    assert r["segments"][-1]["kind"] == "player_focus" and r["segments"][-1]["provenance"]["candidate"]["player"] == player
    away = rm.rundown(run, Profile("away", "en", "analyst"))
    assert [s["kind"] for s in away["segments"]] != [s["kind"] for s in r["segments"]]
    assert all(s["provenance"]["verified"] for s in r["segments"] + away["segments"])
    wait(lambda: len(run.wall) >= 7, 120)  # the master, the fan's cut and the wall of six
    item = run.wall["ht|" + fan.key]
    assert item["segments"] == len(r["segments"]) and item["player_name"]


def test_second_half_restarts_on_cue(played):
    rm, run, _ = played
    n = len(run.frames)
    time.sleep(0.5)
    assert len(run.frames) == n  # held at half-time
    assert rm.second_half(run) == "second_half"
    wait(lambda: run.period == 2, 30)
    assert run.status == "second_half"


def test_stream_down_fails_closed(monkeypatch):
    monkeypatch.setenv("EVENTHUB_CONNECTION", "Endpoint=sb://nowhere.invalid/;SharedAccessKeyName=x;SharedAccessKey=y")

    def boom():
        raise ConnectionError("unreachable")

    monkeypatch.setattr("rundown.api.bus.from_env", boom)
    rm = RunManager(archive=ListArchive())
    with pytest.raises(RuntimeError, match="stream is down"):
        rm.create(5, 8)


def test_catch_up_on_what_you_missed(played):
    rm, run, _ = played
    wait(lambda: run.clock > 2700 + 900, 120)  # fifteen minutes into the second half
    r = rm.catch_up(run, Profile("away", "es", "casual"), run.clock - 600)
    assert r["phase"] == "catchup" and r["window"]["to_s"] - r["window"]["from_s"] >= 590
    assert len(r["segments"]) <= 1
    for seg in r["segments"]:
        assert seg["lang"] == "es" and seg["duration_s"] <= 40
