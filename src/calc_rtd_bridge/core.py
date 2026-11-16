"""Topic bookkeeping (Hub) and the single-threaded main loop (Engine).

Threading model: client sockets live on their own threads and only talk to
the Hub.  Everything that touches an RTD server happens on the thread that
calls ``Engine.run`` - on Windows that thread is a COM single-threaded
apartment with a message pump, which is what RTD servers expect from Excel.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

from .protocol import make_key, to_wire

log = logging.getLogger("calc_rtd.core")

_UNSET = object()


class Topic:
    __slots__ = ("id", "progid", "server", "topics", "key", "value", "error", "subs")

    def __init__(self, topic_id, progid, server, topics, key):
        self.id = topic_id
        self.progid = progid
        self.server = server
        self.topics = list(topics)
        self.key = key
        self.value = _UNSET
        self.error = None
        self.subs = set()  # {(client, client_id)}

    def __repr__(self):
        return "<Topic %d %s %r>" % (self.id, self.progid, self.topics)


class Hub:
    """Thread-safe registry of topics and their subscribers.

    A *client* is any object with ``send(message: dict)``.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._by_key = {}
        self._by_id = {}
        self._client_subs = {}  # client -> {client_id: Topic}
        self._next_id = 1
        self.commands = queue.Queue()

    # -- called from client threads ------------------------------------
    def subscribe(self, client, client_id, progid, server, topics):
        if not topics:
            client.send({"op": "error", "id": client_id, "message": "at least one topic is required"})
            return
        key = make_key(progid, server, topics)
        with self._lock:
            subs = self._client_subs.setdefault(client, {})
            if client_id in subs:
                self._detach(client, client_id)
            topic = self._by_key.get(key)
            created = topic is None
            if created:
                topic = Topic(self._next_id, str(progid).strip(), str(server or "").strip(), topics, key)
                self._next_id += 1
                self._by_key[key] = topic
                self._by_id[topic.id] = topic
            topic.subs.add((client, client_id))
            subs[client_id] = topic
            value, error = topic.value, topic.error
        if created:
            self.commands.put(("connect", topic))
        elif error is not None:
            client.send({"op": "error", "id": client_id, "message": error})
        elif value is not _UNSET:
            client.send({"op": "update", "values": [[client_id, value]]})

    def unsubscribe(self, client, client_id):
        with self._lock:
            self._detach(client, client_id)

    def drop_client(self, client):
        with self._lock:
            for client_id in list(self._client_subs.get(client, {})):
                self._detach(client, client_id)
            self._client_subs.pop(client, None)

    def _detach(self, client, client_id):
        topic = self._client_subs.get(client, {}).pop(client_id, None)
        if topic is None:
            return
        topic.subs.discard((client, client_id))
        if not topic.subs:
            self._by_key.pop(topic.key, None)
            self._by_id.pop(topic.id, None)
            self.commands.put(("disconnect", topic))

    # -- called from the engine thread ---------------------------------
    def publish(self, pairs):
        """pairs: iterable of (topic_id, value). Only changes are forwarded."""
        outgoing = {}
        with self._lock:
            for topic_id, value in pairs:
                topic = self._by_id.get(topic_id)
                if topic is None:
                    continue
                value = to_wire(value)
                if topic.error is None and topic.value is not _UNSET \
                        and type(topic.value) is type(value) and topic.value == value:
                    continue
                topic.value, topic.error = value, None
                for client, client_id in topic.subs:
                    outgoing.setdefault(client, []).append([client_id, value])
        for client, values in outgoing.items():
            client.send({"op": "update", "values": values})

    def fail(self, topic_id, message):
        with self._lock:
            topic = self._by_id.get(topic_id)
            if topic is None:
                return
            topic.error = message
            targets = list(topic.subs)
        for client, client_id in targets:
            client.send({"op": "error", "id": client_id, "message": message})

    def topic_count(self):
        with self._lock:
            return len(self._by_id)


class Backend:
    """Interface the Engine drives. All methods run on the engine thread."""

    def handles(self, progid: str) -> bool:
        raise NotImplementedError

    def connect(self, topic: Topic, hub: Hub):
        raise NotImplementedError

    def disconnect(self, topic: Topic):
        raise NotImplementedError

    def tick(self, now: float, hub: Hub):
        pass

    def close(self):
        pass


class Engine:
    def __init__(self, hub, backends, wait=None, tick=0.02, clock=time.monotonic):
        self.hub = hub
        self.backends = list(backends)
        self._wait = wait
        self._tick = tick
        self._clock = clock
        self._owners = {}  # topic id -> backend

    def run(self, stop: threading.Event):
        try:
            while not stop.is_set():
                self.step()
        finally:
            for backend in self.backends:
                try:
                    backend.close()
                except Exception:
                    log.exception("error closing backend %r", backend)

    def step(self):
        first = None
        if self._wait is not None:
            self._wait(self._tick)
        else:
            try:
                first = self.hub.commands.get(timeout=self._tick)
            except queue.Empty:
                pass
        if first is not None:
            self._handle(first)
        while True:
            try:
                command = self.hub.commands.get_nowait()
            except queue.Empty:
                break
            self._handle(command)
        now = self._clock()
        for backend in self.backends:
            try:
                backend.tick(now, self.hub)
            except Exception:
                log.exception("backend tick failed")

    def _handle(self, command):
        op, topic = command
        try:
            if op == "connect":
                for backend in self.backends:
                    if backend.handles(topic.progid):
                        self._owners[topic.id] = backend
                        backend.connect(topic, self.hub)
                        return
                self.hub.fail(topic.id, "no backend can host ProgID %r on this system "
                                        "(COM RTD servers need Windows + comtypes)" % topic.progid)
            elif op == "disconnect":
                backend = self._owners.pop(topic.id, None)
                if backend is not None:
                    backend.disconnect(topic)
        except Exception as exc:
            log.exception("%s failed for %r", op, topic)
            self.hub.fail(topic.id, "%s: %s" % (type(exc).__name__, exc))
