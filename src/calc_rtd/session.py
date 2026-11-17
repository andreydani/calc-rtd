"""Public API: receive data from Excel RTD servers in any Python program.

    from calc_rtd import RtdSession

    with RtdSession() as rtd:
        last = rtd.subscribe("Vendor.ProgID", ["ACME", "LAST"])
        for update in rtd.updates():
            print(update.subscription.topics, update.value)

A session owns one background thread.  On Windows that thread is a COM
single-threaded apartment with a message loop, which is the environment RTD
servers expect from Excel; the servers are created, called and released
there, never on your threads.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Iterator, NamedTuple, Optional, Sequence

from .backends import Runtime
from .engine import Engine, Hub
from .formula import parse_formula

log = logging.getLogger("calc_rtd.session")

__all__ = ["RtdSession", "Subscription", "Update", "RtdError"]

_CLOSED = object()


class RtdError(Exception):
    """An RTD server could not be started, or rejected a topic."""


class Update(NamedTuple):
    """One change delivered by a session.

    ``value`` is what the server sent (number, text, bool, date or None);
    ``error`` is a message when the subscription failed, otherwise None.
    """

    subscription: "Subscription"
    value: Any
    error: Optional[str] = None


class Subscription:
    """One RTD topic: a ProgID plus its topic strings.

    Attributes are safe to read from any thread.
    """

    def __init__(self, session, key, progid, topics, server, callback):
        self._session = session
        self._key = key
        self.progid: str = progid
        self.topics: tuple = tuple(topics)
        self.server: str = server
        self._callback = callback
        self._value = None
        self._error = None
        self._ready = threading.Event()
        self.active = True

    @property
    def value(self):
        """Latest value, or None before the first one arrives."""
        return self._value

    @property
    def error(self) -> Optional[str]:
        """Error message if the server or the topic failed, else None."""
        return self._error

    @property
    def has_value(self) -> bool:
        return self._ready.is_set() and self._error is None

    def wait(self, timeout: Optional[float] = None):
        """Block until the first value arrives and return it.

        Raises RtdError if the subscription failed and TimeoutError if
        nothing arrived within ``timeout`` seconds.
        """
        if not self._ready.wait(timeout):
            raise TimeoutError("no value from %s %r within %s s" % (self.progid, self.topics, timeout))
        if self._error is not None:
            raise RtdError(self._error)
        return self._value

    def unsubscribe(self):
        """Stop receiving this topic (the server is told to disconnect it)."""
        self._session._unsubscribe(self)

    def __repr__(self):
        state = "error=%r" % self._error if self._error else "value=%r" % (self._value,)
        return "<Subscription %s %r %s>" % (self.progid, self.topics, state)


class RtdSession:
    """Hosts RTD servers and hands their updates to your code.

    throttle     minimum seconds between two refreshes of one server (Excel
                 uses 2.0; the default here is 0.5).
    simulated    also provide the built-in test source, ProgID "calcrtd.sim".
    queue_size   how many undelivered updates ``updates()``/``get()`` keep;
                 when your code falls behind, the oldest ones are dropped
                 (each Subscription still holds its latest value).
    """

    def __init__(self, throttle: float = 0.5, simulated: bool = True, queue_size: int = 10000,
                 _backends=None):
        self._throttle = throttle
        self._simulated = simulated
        self._custom_backends = _backends
        self._hub = Hub()
        self._queue: "queue.Queue" = queue.Queue(maxsize=queue_size)
        self._subs = {}
        self._next_key = 1
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._started = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._runtime = None
        self._closed = False
        self.dropped = 0  #: updates discarded because the queue was full
        self.com_available = False  #: True when real (COM) RTD servers can be hosted

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> "RtdSession":
        """Start the background thread. Called for you by ``with`` and ``subscribe``."""
        with self._lock:
            if self._closed:
                raise RuntimeError("this RtdSession was closed; create a new one")
            if self._thread is None:
                if self._custom_backends is None:
                    # Created here, on the caller's thread, on purpose: this is where the COM
                    # library gets imported. Importing it initialises COM for the importing
                    # thread, and the session thread must own exactly one initialisation so
                    # that leaving it really tears its apartment down (see _run).
                    self._runtime = Runtime(self._throttle, self._simulated)
                    self.com_available = self._runtime.com
                self._thread = threading.Thread(target=self._run, name="calc-rtd-session", daemon=True)
                self._thread.start()
        self._started.wait()
        return self

    def close(self, timeout: float = 10.0):
        """Disconnect every topic, terminate the servers and stop the thread."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
        self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._put(_CLOSED)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    def _run(self):
        runtime = self._runtime
        try:
            if runtime is None:
                backends, wait = self._custom_backends, None
            else:
                runtime.enter()
                backends, wait = runtime.backends, runtime.wait
            engine = Engine(self._hub, backends, wait=wait)
        except Exception:
            log.exception("could not start the RTD session thread")
            self._started.set()
            return
        self._started.set()
        try:
            engine.run(self._stop)
        finally:
            # Drop every reference to the servers, then leave the apartment while the
            # interpreter is still fully alive: the servers release our callback objects
            # at that point, not at some arbitrary moment during process exit.
            del engine, backends
            if runtime is not None:
                try:
                    runtime.leave()
                except Exception:
                    log.debug("error leaving the COM apartment", exc_info=True)

    # -- subscriptions -----------------------------------------------------
    def subscribe(self, progid: str, topics: Sequence[Any], server: str = "",
                  callback: Optional[Callable[[Update], None]] = None) -> Subscription:
        """Subscribe to one topic, like =RTD(progid, server, topic1, topic2, ...).

        ``topics`` are converted to text, as Excel does.  ``callback``, if
        given, is called with every Update on the session thread: keep it
        short and never block in it.
        """
        if isinstance(topics, (str, bytes)):
            topics = [topics]
        topics = [str(topic) for topic in topics]
        self.start()
        with self._lock:
            key = self._next_key
            self._next_key += 1
            subscription = Subscription(self, key, str(progid), topics, str(server or ""), callback)
            self._subs[key] = subscription
        self._hub.subscribe(self, key, progid, server, topics)
        return subscription

    def subscribe_formula(self, formula: str,
                          callback: Optional[Callable[[Update], None]] = None) -> Subscription:
        """Subscribe using a formula as written in a spreadsheet: '=RTD("p";;"a";"b")'."""
        progid, server, topics = parse_formula(formula)
        return self.subscribe(progid, topics, server, callback)

    @property
    def subscriptions(self):
        """The active subscriptions."""
        with self._lock:
            return list(self._subs.values())

    def _unsubscribe(self, subscription):
        with self._lock:
            if self._subs.pop(subscription._key, None) is None:
                return
        subscription.active = False
        self._hub.unsubscribe(self, subscription._key)

    # -- receiving ---------------------------------------------------------
    def get(self, timeout: Optional[float] = None) -> Optional[Update]:
        """Return the next Update, or None after ``timeout`` seconds or once closed."""
        try:
            item = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is _CLOSED:
            self._put(_CLOSED)  # keep other consumers from blocking forever
            return None
        return item

    def updates(self, timeout: Optional[float] = None) -> Iterator[Update]:
        """Iterate over updates as they arrive.

        Ends when the session is closed, or when ``timeout`` seconds pass
        without any update (never, by default).
        """
        while True:
            update = self.get(timeout)
            if update is None:
                return
            yield update

    def run_for(self, seconds: float):
        """Keep the calling thread busy for ``seconds`` while callbacks run."""
        deadline = time.monotonic() + seconds
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.1, remaining))

    # -- Hub client interface (engine thread) --------------------------------
    def on_update(self, pairs):
        for key, value in pairs:
            subscription = self._subs.get(key)
            if subscription is None:
                continue
            subscription._value, subscription._error = value, None
            subscription._ready.set()
            self._deliver(Update(subscription, value))

    def on_error(self, key, message):
        subscription = self._subs.get(key)
        if subscription is None:
            return
        subscription._error = message
        subscription._ready.set()
        self._deliver(Update(subscription, None, message))

    def _deliver(self, update):
        callback = update.subscription._callback
        if callback is not None:
            try:
                callback(update)
            except Exception:
                log.exception("error in RTD callback for %r", update.subscription)
        self._put(update)

    def _put(self, item):
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    pass
