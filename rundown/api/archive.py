"""Where runs outlive the process: every run, the crew's documents, the trail and each rundown
cut go to Cosmos DB (database `rundown`, containers `docs` and `trail`, partitioned by run id)
when COSMOS_ENDPOINT and COSMOS_KEY are set. /proof replays past runs from here.

Writes are queued and made by one background thread, so the crew never waits on the database.
A write that still fails after three tries is counted and shown, never dropped silently.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time


def plain(x):
    """JSON-safe copy (numpy numbers become plain ones)."""
    return json.loads(json.dumps(x, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


def doc_id(*parts: str) -> str:
    return re.sub(r"[/\\?#]", "_", ":".join(parts))  # characters Cosmos ids can't hold


class MemoryArchive:
    """Nothing persists: runs live only as long as the process."""

    name = "memory"
    errors = 0
    last_error = None

    def save_run(self, run_id: str, doc: dict): ...
    def save_doc(self, run_id: str, kind: str, key: str, doc: dict): ...
    def save_trail(self, run_id: str, item: dict): ...

    def runs(self, limit: int = 20) -> list[dict]:
        return []

    def load(self, run_id: str) -> dict | None:
        return None


class CosmosArchive(MemoryArchive):
    name = "cosmos"

    def __init__(self, endpoint: str, key: str):
        from azure.cosmos import CosmosClient

        kw = {"connection_verify": os.environ["SSL_CERT_FILE"]} if os.environ.get("SSL_CERT_FILE") else {}
        db = CosmosClient(endpoint, key, **kw).get_database_client("rundown")
        self.docs = db.get_container_client("docs")
        self.trail = db.get_container_client("trail")
        self.q: queue.Queue = queue.Queue()
        self.errors = 0
        self.last_error: str | None = None
        self.written = 0
        threading.Thread(target=self._write, daemon=True, name="cosmos-write").start()

    def save_run(self, run_id: str, doc: dict):
        self.q.put((self.docs, dict(plain(doc), id="run", runId=run_id, type="run")))

    def save_doc(self, run_id: str, kind: str, key: str, doc: dict):
        self.q.put((self.docs, dict(id=doc_id(kind, key), runId=run_id, type=kind, key=key, doc=plain(doc))))

    def save_trail(self, run_id: str, item: dict):
        self.q.put((self.trail, dict(plain(item), id=str(item["seq"]), runId=run_id)))

    def _write(self):
        while True:
            container, item = self.q.get()
            for attempt in range(3):
                try:
                    container.upsert_item(item)
                    self.written += 1
                    break
                except Exception as e:  # throttled or a network blip: wait and retry
                    err = f"{type(e).__name__}: {str(e)[:200]}"
                    time.sleep(1 + 2 * attempt)
            else:
                self.errors += 1
                self.last_error = err

    def runs(self, limit: int = 20) -> list[dict]:
        q = ("SELECT TOP @n c.runId, c.seed, c.status, c.started_at, c.whistle_at, c.locked_at, c.score, "
             "c.clubs, c.crew, c.stream, c.clock FROM c WHERE c.type = 'run' ORDER BY c.started_at DESC")
        return list(self.docs.query_items(q, parameters=[dict(name="@n", value=limit)],
                                          enable_cross_partition_query=True))

    def load(self, run_id: str) -> dict | None:
        p = [dict(name="@r", value=run_id)]
        docs = list(self.docs.query_items("SELECT * FROM c WHERE c.runId = @r", parameters=p, partition_key=run_id))
        run = next((d for d in docs if d["type"] == "run"), None)
        if not run:
            return None
        trail = list(self.trail.query_items("SELECT * FROM c WHERE c.runId = @r ORDER BY c.seq",
                                            parameters=p, partition_key=run_id))
        return dict(run=run, rundowns={d["key"]: d["doc"] for d in docs if d["type"] == "rundown"},
                    trail=trail)


def from_env():
    if os.environ.get("COSMOS_ENDPOINT") and os.environ.get("COSMOS_KEY"):
        return CosmosArchive(os.environ["COSMOS_ENDPOINT"], os.environ["COSMOS_KEY"])
    return MemoryArchive()
