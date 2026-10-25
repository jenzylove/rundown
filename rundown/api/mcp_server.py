"""Rundown over MCP: any agent (GitHub Copilot, a broadcaster's own assistant, a fan app's bot)
can find a live match, ask the crew for a fan's own show or a catch-up, and read the whistle
wall. Mounted at /mcp on the API (streamable HTTP). Every segment it returns is the same
verified Overlay Script the studio plays, minus the tracking frames."""

from __future__ import annotations

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ..studio.profile import Profile

mcp = FastMCP(
    "Rundown",
    instructions=(
        "Rundown makes a personal, verified football analysis segment for any fan of a live synthetic "
        "match: their side, language (en, es, fr, pt), depth (casual or analyst) and a player to follow. "
        "Start with list_matches, then get_match for player ids, then get_fan_show or catch_me_up. "
        "Every number in a segment was recomputed by the Verifier from the stats engine."),
    stateless_http=True,
    streamable_http_path="/",
    # a public, read-only server behind Container Apps' ingress: the DNS-rebinding guard is for
    # servers on a user's own machine, and here it would reject the public host name
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
_runs = None  # the API's RunManager, set by attach()


def attach(runs) -> FastMCP:
    global _runs
    _runs = runs
    return mcp


def _run(run_id: str):
    run = _runs.runs.get(run_id)
    if run is None:
        raise ValueError(f"no live match {run_id}; call list_matches")
    return run


def _profile(run, side: str, lang: str, depth: str, player: str | None) -> Profile:
    prof = Profile(side, lang, depth, player or None)
    if prof.player and not any(p["id"] == prof.player for s in ("home", "away") for p in run.meta[s]["players"]):
        raise ValueError(f"no player {prof.player} in this match; call get_match for the squads")
    return prof


def _segment(s: dict) -> dict:
    card = next((q for q in s["cues"] if q["kind"] == "statcard"), {})
    return dict(
        title=s["title"], kind=s["kind"], minute=int(s["clip"]["t0"] // 60) + 1, duration_s=s["duration_s"],
        verified=bool(s["provenance"].get("verified")), made_by=s["provenance"].get("crew"),
        script=[ln["text"] for ln in s["script"]],
        stat_card=dict(title=card.get("text"), rows=card.get("rows", [])),
        facts=[dict(label=f["label"], value=f["value"], unit=f["unit"], source_events=f["evidence"][:12])
               for f in s["facts"]],
        overlay_cues=[{k: v for k, v in q.items() if k != "rows"} for q in s["cues"]],
    )


def _show(r: dict) -> dict:
    return dict(profile=r.get("profile"), phase=r.get("phase"), window=r.get("window"), score=r.get("score"),
                duration_s=r.get("duration_s"), made_by=r.get("made_by"),
                segments=[_segment(s) for s in r["segments"]])


@mcp.tool()
def list_matches() -> list[dict]:
    """Matches on this server, newest first: id, clubs, score, clock (match seconds) and status."""
    out = []
    for run in sorted(_runs.runs.values(), key=lambda r: -r.started_at):
        out.append(dict(run_id=run.id, home=run.meta["home"]["name"], away=run.meta["away"]["name"],
                        score=f"{run.score[0]}-{run.score[1]}", clock_s=round(run.clock), status=run.status))
    return out


@mcp.tool()
def get_match(run_id: str) -> dict:
    """One match: both squads with player ids (to follow a player), score, clock and status."""
    run = _run(run_id)
    squad = lambda side: [dict(id=p["id"], name=p["name"], number=p["number"], role=p["role"])
                          for p in run.meta[side]["players"]]
    return dict(run_id=run.id, status=run.status, score=f"{run.score[0]}-{run.score[1]}", clock_s=round(run.clock),
                home=dict(name=run.meta["home"]["name"], players=squad("home")),
                away=dict(name=run.meta["away"]["name"], players=squad("away")))


@mcp.tool()
async def get_fan_show(run_id: str, side: str = "neutral", lang: str = "en", depth: str = "casual",
                       player: str | None = None, phase: str = "ht") -> dict:
    """A fan's own half-time (phase "ht") or full-time ("ft") show: up to three verified segments,
    each with its spoken script, stat card, facts with their source events, and overlay cues.
    side: neutral | home | away. lang: en | es | fr | pt. depth: casual | analyst. player: an id from get_match."""
    run = _run(run_id)
    if phase == "ht" and run.locked_at is None or phase == "ft" and run.status != "full_time":
        raise ValueError("that show locks at the whistle; the match is still in play (try catch_me_up)")
    prof = _profile(run, side, lang, depth, player)
    r = await anyio.to_thread.run_sync(lambda: _runs.rundown(run, prof, phase=phase))
    return _show(r)


@mcp.tool()
async def catch_me_up(run_id: str, since_minute: float, side: str = "neutral", lang: str = "en",
                      depth: str = "casual", player: str | None = None) -> dict:
    """What a fan missed since a match minute, as one short verified segment for their profile
    (empty when nothing worth a segment happened). Works while the match is in play."""
    run = _run(run_id)
    if run.status not in ("first_half", "second_half"):
        raise ValueError("catch-up works while the match is in play")
    prof = _profile(run, side, lang, depth, player)
    r = await anyio.to_thread.run_sync(lambda: _runs.catch_up(run, prof, since_minute * 60.0))
    return _show(r)


@mcp.tool()
def whistle_wall(run_id: str) -> list[dict]:
    """Every cut the crew made for this match: who it was for, seconds after the whistle it was
    ready, the lead story, and how many of its segments the Verifier passed."""
    return sorted(_run(run_id).wall.values(), key=lambda w: (w["phase"], w["ready_s"]))
