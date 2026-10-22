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

import gzip
import json
import os
import queue
import threading
import urllib.parse
from typing import Callable

Handler = Callable[[dict], None]


def encode(msg: dict) -> bytes:
    return gzip.compress(json.dumps(msg, separators=(",", ":")).encode())


def decode(body: bytes) -> dict:
    return json.loads(gzip.decompress(body))


class LocalBus:
    name = "local"

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
    """One producer and one consumer per process. Sends are queued, and whatever has queued
    for a run goes out as one batch, so a slower link means bigger batches, not a backlog. The
    consumer reads every partition from "now" and hands each message to the run it belongs to."""

    name = "eventhubs"

    def __init__(self, conn: str, hub: str):
        from azure.eventhub import EventHubConsumerClient, EventHubProducerClient

        kw = _transport()
        self.producer = EventHubProducerClient.from_connection_string(conn, eventhub_name=hub, **kw)
        self.consumer = EventHubConsumerClient.from_connection_string(conn, "$Default", eventhub_name=hub, **kw)
        self.partitions = set(self.producer.get_partition_ids())
        self.handlers: dict[str, Handler] = {}
        self._started: set[str] = set()
        self._ready = threading.Event()
        self._out: queue.Queue = queue.Queue()
        self._failed: dict[str, Exception] = {}
        threading.Thread(target=self._receive, daemon=True, name="eventhubs-receive").start()
        threading.Thread(target=self._send_loop, daemon=True, name="eventhubs-send").start()

    def ready(self, timeout: float = 0) -> bool:
        return self._ready.wait(timeout)

    def subscribe(self, run_id: str, fn: Handler):
        self.handlers[run_id] = fn

    def unsubscribe(self, run_id: str):
        self.handlers.pop(run_id, None)

    def send(self, run_id: str, msg: dict):
        if run_id in self._failed:  # an earlier send for this run failed: stop the run
            raise self._failed.pop(run_id)
        self._out.put((run_id, encode(msg)))

    def _send_loop(self):
        from azure.eventhub import EventData

        while True:
            items = [self._out.get()]
            while True:
                try:
                    items.append(self._out.get_nowait())
                except queue.Empty:
                    break
            by_run: dict[str, list[bytes]] = {}
            for run_id, body in items:
                by_run.setdefault(run_id, []).append(body)
            for run_id, bodies in by_run.items():
                try:
                    batch = self.producer.create_batch(partition_key=run_id)
                    for body in bodies:
                        ev = EventData(body)
                        ev.properties = {"run_id": run_id}
                        try:
                            batch.add(ev)
                        except ValueError:  # batch full
                            self.producer.send_batch(batch)
                            batch = self.producer.create_batch(partition_key=run_id)
                            batch.add(ev)
                    self.producer.send_batch(batch)
                except Exception as e:
                    self._failed[run_id] = e

    def _receive(self):
        def on_init(pc):
            self._started.add(pc.partition_id)
            if self._started >= self.partitions:
                self._ready.set()

        def on_batch(pc, evs):
            for ev in evs:
                run_id = (ev.properties or {}).get(b"run_id", b"").decode()
                fn = self.handlers.get(run_id)
                if fn:  # other runs' messages (another replica's, or a finished run's) are not ours
                    fn(decode(b"".join(ev.body)))

        # batches, not one event per callback: the per-event receiver lagged ~1 s per message over
        # websockets; a short max_wait_time keeps the batches small and the latency low
        self.consumer.receive_batch(on_batch, starting_position="@latest", on_partition_initialize=on_init,
                                    max_batch_size=100, max_wait_time=0.05, prefetch=300)


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
