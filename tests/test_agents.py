"""The crew's plumbing, offline: placeholders, the Verifier, retries, fallbacks and the
kill switch. A fake model answers each role from its prompt, so nothing here calls Azure."""

import asyncio
import json

import jsonschema
import pytest

from rundown.agents import crew as crew_mod
from rundown.agents import verify
from rundown.agents.crew import Crew
from rundown.agents.state import Store
from rundown.agents.words import Table
from rundown.engine import detect
from rundown.engine.data import from_match
from rundown.sim import simulate
from rundown.studio import telestrate


@pytest.fixture(scope="module")
def half():
    md = from_match(simulate(21, period_minutes=20.0))
    t_end = float(md.frames[md.frame_index(1, 1e9), 1])
    cands, t = {}, 60.0
    while t <= t_end:
        for c in detect.scan(md, 1, t):
            cands.setdefault((c.kind, c.team, c.side, c.player), c)
        t += 60
    cands = list(cands.values()) + detect.moments(md, 1, 0, t_end)
    assert len({c.kind for c in cands}) >= 2
    return md, cands, t_end


class FakeModels:
    """Answers like a well-behaved model, unless told to misbehave for a role."""

    def __init__(self, bad: dict | None = None, slow: dict | None = None):
        self.bad = bad or {}
        self.slow = slow or {}
        self.calls: list[str] = []

    def session(self, role, instructions):
        return object()

    async def ask(self, role, instructions, session, text, schema):
        self.calls.append(role)
        await asyncio.sleep(self.slow.get(role, 0))
        if role in self.bad:
            return schema.model_validate(self.bad[role]), {}
        p = json.loads(text)
        if role == "analyst":
            out = dict(verdicts=[dict(id=t["id"], matters=0.5, why=f"It changed after minute {{MIN}}.")
                                 for t in p["talking_points"]])
        elif role == "producer":
            picks, kinds = [], set()
            for t in p["talking_points"]:
                if t["kind"] not in kinds and t["kind"] not in ("goal", "big_chance"):
                    picks.append(dict(id=t["id"], reason="test"))
                    kinds.add(t["kind"])
            out = dict(picks=picks[:p["segments_wanted"]])
        elif role == "telestrator":
            clip = p["clip"]
            arrows = [e["id"] for e in clip["events"] if "to_xy" in e][:1]
            out = dict(focus="test", freezes=[dict(at=clip["key_moment_clip_t"], hold=3.0)],
                       highlights=[dict(slot=3, freeze=0, show_name=True)], arrows=arrows,
                       zones=[dict(name=p["options"]["zones"][0], freeze=0)])
        else:
            a = next(f["alias"] for f in p["facts"] if isinstance(f["value"], (int, float)))
            out = dict(title="Test segment", sub=f"Now {{{a}}}", card_title="Test",
                       rows=[dict(label="Value", facts=[a])], lines=["Look at this.", f"It is {{{a}}} now."])
        return schema.model_validate(out), {}


def test_placeholders_fill_from_the_engine_in_the_language(half):
    md, cands, _ = half
    c = next(c for c in cands if any(isinstance(f.value, float) for f in c.facts))
    t = Table(c)
    i = next(i for i, f in enumerate(c.facts) if isinstance(f.value, float))
    a, v = f"F{i + 1}", c.facts[i].value
    text, keys, errs = t.fill(f"It is {{{a}}}.", "es")
    assert not errs and keys == [c.facts[i].key]
    assert text == "It is " + f"{v:g}".replace(".", ",") + "."
    typed, keys, errs = t.fill(f"It is {v:g}.", "es")
    assert not errs and typed == text and keys == [c.facts[i].key], "a typed fact value is checked and cited"
    assert t.fill("It went up by 12345.", "en")[2], "a number that is no fact's value must be rejected"
    assert t.fill("It is {F99}.", "en")[2], "an unknown placeholder must be rejected"
    assert verify.text_errors("PPDA fell from four point eight", "en", "line"), "spelled-out numbers count as typed"
    assert verify.text_errors("Bajó de ocho coma tres", "es", "line")
    assert not verify.text_errors("Harbour are pressing higher.", "en", "line")


def test_verifier_catches_bad_plans_and_tampered_facts(half):
    md, cands, _ = half
    c = cands[0]
    anchor, t0, t1 = telestrate.moment(md, c)
    cl = telestrate.clip(md, c.period, t0, t1)
    opts = telestrate.options(md, c, cl, anchor)
    bad = dict(freezes=[dict(at=999.0, hold=2)], highlights=[dict(slot=30, freeze=0)], arrows=["nope"],
               zones=[dict(name="the moon", freeze=0)])
    errs = verify.layout_errors(md, bad, cl, opts)
    assert len(errs) >= 4
    seg = telestrate.build(md, c, target_s=28.0)
    assert verify.segment_errors(md, seg) == []
    seg["facts"][0]["value"] = 12345
    assert any("the engine says" in e for e in verify.segment_errors(md, seg))


def test_crew_builds_a_verified_rundown(half):
    md, cands, t_end = half
    store = Store()
    crew = Crew(FakeModels(), store)
    r = crew.rundown(md, cands, t_end, 90.0)
    assert r["made_by"] == "crew" and r["segments"]
    for s in r["segments"]:
        jsonschema.validate(s, verify.SCHEMA)
        assert s["provenance"]["verified"]
        assert s["provenance"]["crew"] == dict(telestrator="agent", writer="agent")
    assert abs(r["duration_s"] - 90.0) <= 3.0
    assert {it["agent"] for it in store.trail} >= {"analyst", "producer", "telestrator", "writer", "verifier"}


def test_killed_telestrator_is_routed_around(half):
    md, cands, t_end = half
    store = Store()
    crew = Crew(FakeModels(), store)
    crew.kill("telestrator")
    r = crew.rundown(md, cands, t_end, 90.0, lang="es")
    assert all(s["provenance"]["crew"]["telestrator"] == "rules" for s in r["segments"])
    assert all(s["provenance"]["verified"] for s in r["segments"])
    statuses = [it["status"] for it in store.trail if it["agent"] == "telestrator"]
    assert "killed" in statuses and "fallback" in statuses


def test_writer_that_types_numbers_gets_one_retry_then_the_rules_writer(half):
    md, cands, t_end = half
    bad = dict(title="Up 3 places", sub="From 1 to 2", card_title="X", rows=[dict(label="Y", facts=["F1"])],
               lines=["It went from 1 to 2.", "Big."])
    store = Store()
    models = FakeModels(bad=dict(writer=bad))
    crew = Crew(models, store)
    r = crew.rundown(md, cands, t_end, 90.0)
    assert all(s["provenance"]["crew"]["writer"] == "rules" for s in r["segments"])
    w = [it["status"] for it in store.trail if it["agent"] == "writer"]
    assert w.count("retry") == len(r["segments"]) and w.count("fallback") == len(r["segments"])
    assert models.calls.count("writer") == 2 * len(r["segments"])


def test_a_segment_that_misses_the_lock_deadline_goes_out_as_rules(half, monkeypatch):
    md, cands, t_end = half
    monkeypatch.setattr(crew_mod, "LOCK_DEADLINE_S", 0.5)
    store = Store()
    crew = Crew(FakeModels(slow=dict(writer=3.0)), store)
    r = crew.rundown(md, cands, t_end, 90.0)
    assert all(s["provenance"]["crew"] == dict(telestrator="rules", writer="rules") for s in r["segments"])
    assert all(s["provenance"]["verified"] for s in r["segments"])
    assert any("not ready" in it["why"] for it in store.trail if it["status"] == "fallback")
