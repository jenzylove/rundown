"""The live match stream. The simulator publishes each run's frames and events; the studio
consumes them and works only from what arrives. Azure Event Hubs carries it when
EVENTHUB_CONNECTION is set; otherwise an in-process queue does (offline and in tests). The
messages and their encoding are the same either way.

Messages (gzipped JSON, one run per partition key so they arrive in order):
    {"k": "frames", "frames": [[period, t, x, y, ...], ...], "events": [[i, [event, ...]], ...],
     "fast": bool, "sent": unix time}   events[i] were emitted with frames[i]
    {"k": "whistle", "events": [...], "sent": ...}   the half-time whistle
    {"k": "end", "sent": ...}                        full time
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import threading
import time
import urllib.parse
from typing import Callable

Handler = Callable[[dict], None]


def encode(msg: dict) -> bytes:
    return gzip.compress(json.dumps(msg, separators=(",", ":")).encode())


def decode(body: bytes) -> dict:
    return json.loads(gzip.decompress(body))


class LocalBus:
    name = "local"
    stats: dict = {}

    def __init__(self):
        self.handlers: dict[str, Handler] = {}

    def ready(self, timeout: float = 0) -> bool:
        return True

    def subscribe(self, run_id: str, fn: Handler):
        self.handlers[run_id] = fn

    def unsubscribe(self, run_id: str):
        self.handlers.pop(run_id, None)

    def send(self, run_id: str, msg: dict):
        fn = self.handlers.get(run_id)
        if fn:
            fn(decode(encode(msg)))  # the same round trip as the wire


class EventHubBus:
    """One producer and one consumer per process, on the async client in a loop of their own.
    (The sync client's partition threads take turns on the connection: about one delivery per
    second, and multi-second stalls on the live app.) Whatever has queued for a run goes out as
    one batch, so a slower link means bigger batches, not a backlog. The consumer reads every
    partition from "now" and hands each message to the run it belongs to."""

    name = "eventhubs"

    def __init__(self, conn: str, hub: str):
        self.handlers: dict[str, Handler] = {}
        self.stats = dict(sends=0, send_ms_max=0, send_ms_last=None, first_send_ms=None)
        self._failed: dict[str, Exception] = {}
        self._error: str | None = None
        self._ready = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._out: asyncio.Queue | None = None
        started = threading.Event()
        threading.Thread(target=self._run, args=(conn, hub, started), daemon=True, name="eventhubs").start()
        started.wait(30)
        if self._out is None:
            raise RuntimeError(f"Event Hubs did not start: {self._error}")

    def ready(self, timeout: float = 0) -> bool:
        return self._ready.wait(timeout)

    def subscribe(self, run_id: str, fn: Handler):
        self.handlers[run_id] = fn

    def unsubscribe(self, run_id: str):
        self.handlers.pop(run_id, None)

    def send(self, run_id: str, msg: dict):
        if self._error:
            raise RuntimeError(f"Event Hubs stopped: {self._error}")
        if run_id in self._failed:  # an earlier send for this run failed: stop the run
            raise self._failed.pop(run_id)
        self._loop.call_soon_threadsafe(self._out.put_nowait, (run_id, encode(msg)))

    def _run(self, conn: str, hub: str, started: threading.Event):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main(conn, hub, started))
        except Exception as e:
            self._error = self.stats["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            self._ready.clear()
            started.set()

    async def _main(self, conn: str, hub: str, started: threading.Event):
        from azure.eventhub.aio import EventHubConsumerClient, EventHubProducerClient

        kw = _transport()
        producer = EventHubProducerClient.from_connection_string(conn, eventhub_name=hub, **kw)
        consumer = EventHubConsumerClient.from_connection_string(conn, "$Default", eventhub_name=hub, **kw)
        partitions = set(await producer.get_partition_ids())
        claimed: set[str] = set()
        self._out = asyncio.Queue()
        started.set()

        async def on_init(pc):
            claimed.add(pc.partition_id)
            if claimed >= partitions:
                self._ready.set()

        async def on_event(pc, ev):
            if ev is None:
                return
            fn = self.handlers.get((ev.properties or {}).get(b"run_id", b"").decode())
            if fn:  # other runs' messages (another replica's, or a finished run's) are not ours
                fn(decode(b"".join(ev.body)))

        receiving = asyncio.create_task(consumer.receive(on_event, starting_position="@latest",
                                                         on_partition_initialize=on_init))
        while True:
            await self._send_all(producer)
            if receiving.done():  # the receiver gave up: surface it, don't go quiet
                raise receiving.exception() or RuntimeError("Event Hubs receiver stopped")

    async def _send_all(self, producer):
        from azure.eventhub import EventData

        items = [await self._out.get()]
        while not self._out.empty():
            items.append(self._out.get_nowait())
        by_run: dict[str, list[bytes]] = {}
        for run_id, body in items:
            by_run.setdefault(run_id, []).append(body)
        for run_id, bodies in by_run.items():
            try:
                batch = await producer.create_batch(partition_key=run_id)
                for body in bodies:
                    ev = EventData(body)
                    ev.properties = {"run_id": run_id}
                    try:
                        batch.add(ev)
                    except ValueError:  # batch full
                        await producer.send_batch(batch)
                        batch = await producer.create_batch(partition_key=run_id)
                        batch.add(ev)
                t0 = time.time()
                await producer.send_batch(batch)
                ms = round((time.time() - t0) * 1000)
                st = self.stats
                st["sends"] += 1
                st["send_ms_last"], st["send_ms_max"] = ms, max(st["send_ms_max"], ms)
                if st["first_send_ms"] is None:
                    st["first_send_ms"] = ms
            except Exception as e:
                self._failed[run_id] = e


def _transport() -> dict:
    """AMQP over TCP by default. EVENTHUB_TRANSPORT=websocket goes over 443 instead, through
    HTTPS_PROXY and trusting SSL_CERT_FILE when set (for networks that only allow HTTPS)."""
    if os.environ.get("EVENTHUB_TRANSPORT") != "websocket":
        return {}
    from azure.eventhub import TransportType

    kw: dict = dict(transport_type=TransportType.AmqpOverWebsocket)
    if os.environ.get("HTTPS_PROXY"):
        px = urllib.parse.urlparse(os.environ["HTTPS_PROXY"])
        kw["http_proxy"] = dict(proxy_hostname=px.hostname, proxy_port=px.port)
    if os.environ.get("SSL_CERT_FILE"):
        kw["connection_verify"] = os.environ["SSL_CERT_FILE"]
    return kw


def from_env():
    conn = os.environ.get("EVENTHUB_CONNECTION")
    if not conn:
        return LocalBus()
    return EventHubBus(conn, os.environ.get("EVENTHUB_NAME", "match-events"))
