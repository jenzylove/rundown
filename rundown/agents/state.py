"""Shared state for one run: the documents agents hand off through (talking points,
analyses, plans, scripts, rundowns) and the audit trail of every write — who, what,
why, which events.

Reads come from memory. The API mirrors every write to Cosmos DB through `on_put` and
`on_record` (`api/archive.py`), so a run's documents and trail outlive the process.
"""

from __future__ import annotations

import threading
import time
from typing import Callable


class Store:
    def __init__(self, on_record: Callable[[dict], None] | None = None,
                 on_put: Callable[[str, str, dict], None] | None = None):
        self.docs: dict[str, dict[str, dict]] = {}
        self.trail: list[dict] = []
        self.on_record = on_record
        self.on_put = on_put
        self._lock = threading.Lock()

    def put(self, collection: str, doc_id: str, doc: dict, *, agent: str, why: str = "",
            event_ids: list[str] | None = None) -> None:
        with self._lock:
            self.docs.setdefault(collection, {})[doc_id] = doc
        if self.on_put:
            self.on_put(collection, doc_id, doc)
        self.record(agent, f"wrote {collection}/{doc_id}", "ok", why=why, event_ids=event_ids)

    def get(self, collection: str, doc_id: str) -> dict | None:
        return self.docs.get(collection, {}).get(doc_id)

    def record(self, agent: str, action: str, status: str, *, ms: float | None = None, why: str = "",
               event_ids: list[str] | None = None, detail: dict | None = None) -> dict:
        """status: ok | retry | fallback | killed | failed | skipped"""
        item = dict(at=time.time(), agent=agent, action=action, status=status,
                    ms=None if ms is None else round(ms), why=why, event_ids=(event_ids or [])[:40],
                    detail=detail or {})
        with self._lock:
            item["seq"] = len(self.trail)
            self.trail.append(item)
        if self.on_record:
            self.on_record(item)
        return item
