"""The open API's guards: only the browser that started a match may change it for everyone,
one address can't fill the server with matches, and the paid endpoints have a budget."""

import os

import pytest

os.environ["RUNDOWN_CREW"] = "rules"  # offline: no models
from fastapi.testclient import TestClient  # noqa: E402

from rundown.api import app as app_mod  # noqa: E402


@pytest.fixture(scope="module")
def app_client():
    with TestClient(app_mod.app) as c:  # the MCP session manager starts once per process
        yield c


@pytest.fixture()
def client(app_client):
    app_mod._hits.clear()
    yield app_client
    for r in list(app_mod.runs.runs.values()):
        r.stop = True
        r.resume.set()
    app_mod.runs.runs.clear()


def test_only_the_starter_can_skip_or_restart(client):
    snap = client.post("/api/runs", json={"speed": 16}).json()
    assert snap["owner"]
    assert client.post(f"/api/runs/{snap['id']}/skip").status_code == 403
    assert client.post(f"/api/runs/{snap['id']}/skip", headers={"x-run-owner": "guess"}).status_code == 403
    assert client.post(f"/api/runs/{snap['id']}/skip", headers={"x-run-owner": snap["owner"]}).status_code == 200
    assert client.post(f"/api/runs/{snap['id']}/crew/kill", json={"role": "writer"}).status_code == 403
    assert "owner" not in client.get(f"/api/runs/{snap['id']}").json()  # never shown to anyone else
    assert all("owner" not in r for r in client.get("/api/proof/runs").json()["runs"])


def test_one_address_cannot_fill_the_server(client):
    codes = [client.post("/api/runs", json={"speed": 16}).status_code for _ in range(7)]
    assert codes[:6] == [200] * 6 and codes[6] == 429


def test_paid_endpoints_have_a_budget(client):
    rid = client.post("/api/runs", json={"speed": 16}).json()["id"]
    codes = [client.post(f"/api/runs/{rid}/watch", json={"cut": "home"}).status_code for _ in range(42)]
    assert codes.count(200) == 40 and codes[-1] == 429
