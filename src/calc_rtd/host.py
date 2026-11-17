"""Plays Excel's part of the RTD contract, independent of the COM plumbing.

A *driver* wraps one live RTD server instance and exposes:

    start() -> int                      ServerStart; > 0 means success
    connect_data(topic_id, strings)     -> initial value
    refresh_data()                      -> [(topic_id, value), ...]
    disconnect_data(topic_id)
    heartbeat() -> int                  > 0 means alive
    terminate()
    notified: bool                      set when the server calls UpdateNotify
    heartbeat_interval: float seconds   may be changed by the server

``com_driver.ComDriver`` is the real one; tests use a fake.
"""

from __future__ import annotations

import logging
import time

from .engine import Backend
from .sim import SIM_PROGID

log = logging.getLogger("calc_rtd.host")


class _Session:
    def __init__(self, progid, server, driver, now):
        self.progid = progid
        self.server = server
        self.driver = driver
        self.topics = {}  # id -> Topic
        self.last_refresh = now - 3600.0  # the first notification is served at once
        self.last_activity = now


class RtdHostBackend(Backend):
    """Hosts any number of RTD servers, one session per (ProgID, machine)."""

    def __init__(self, driver_factory, throttle=0.5, clock=time.monotonic):
        self._factory = driver_factory
        self._throttle = throttle
        self._clock = clock
        self._sessions = {}

    def handles(self, progid):
        return progid.strip().lower() != SIM_PROGID

    # -- engine callbacks ----------------------------------------------
    def connect(self, topic, hub):
        key = (topic.progid.lower(), topic.server.lower())
        session = self._sessions.get(key)
        if session is None:
            session = self._open(topic.progid, topic.server)
            self._sessions[key] = session
        try:
            value = session.driver.connect_data(topic.id, topic.topics)
        except Exception:
            if not session.topics:
                self._close(key)
            raise
        session.topics[topic.id] = topic
        hub.publish([(topic.id, value)])

    def disconnect(self, topic):
        key = (topic.progid.lower(), topic.server.lower())
        session = self._sessions.get(key)
        if session is None or topic.id not in session.topics:
            return
        del session.topics[topic.id]
        try:
            session.driver.disconnect_data(topic.id)
        except Exception:
            log.exception("DisconnectData failed for %r", topic)
        if not session.topics:
            self._close(key)

    def tick(self, now, hub):
        for key, session in list(self._sessions.items()):
            driver = session.driver
            if driver.notified and now - session.last_refresh >= self._throttle:
                driver.notified = False
                session.last_refresh = session.last_activity = now
                try:
                    pairs = driver.refresh_data()
                except Exception:
                    log.exception("RefreshData failed for %s", session.progid)
                    continue
                known = [(tid, value) for tid, value in pairs if tid in session.topics]
                if known:
                    hub.publish(known)
            elif now - session.last_activity >= max(1.0, driver.heartbeat_interval):
                session.last_activity = now
                try:
                    alive = driver.heartbeat() > 0
                except Exception:
                    log.exception("Heartbeat failed for %s", session.progid)
                    alive = False
                if not alive:
                    log.warning("RTD server %s stopped answering; restarting it", session.progid)
                    self._restart(key, session, hub, now)

    def close(self):
        for key in list(self._sessions):
            self._close(key)

    # -- helpers ---------------------------------------------------------
    def _open(self, progid, server):
        driver = self._factory(progid, server)
        try:
            result = driver.start()
        except Exception:
            _quiet(driver.terminate)
            raise
        if result <= 0:
            _quiet(driver.terminate)
            raise RuntimeError("RTD server %s refused to start (ServerStart returned %s)" % (progid, result))
        log.info("started RTD server %s%s", progid, " on " + server if server else "")
        return _Session(progid, server, driver, self._clock())

    def _close(self, key):
        session = self._sessions.pop(key, None)
        if session is None:
            return
        for topic_id in list(session.topics):
            _quiet(session.driver.disconnect_data, topic_id)
        _quiet(session.driver.terminate)
        log.info("terminated RTD server %s", session.progid)

    def _restart(self, key, session, hub, now):
        topics = list(session.topics.values())
        _quiet(session.driver.terminate)
        del self._sessions[key]
        try:
            fresh = self._open(session.progid, session.server)
        except Exception as exc:
            for topic in topics:
                hub.fail(topic.id, "RTD server stopped and could not be restarted: %s" % exc)
            return
        self._sessions[key] = fresh
        for topic in topics:
            try:
                value = fresh.driver.connect_data(topic.id, topic.topics)
            except Exception as exc:
                hub.fail(topic.id, "%s: %s" % (type(exc).__name__, exc))
                continue
            fresh.topics[topic.id] = topic
            hub.publish([(topic.id, value)])


def pairs_from_refresh(data, count):
    """RefreshData returns a 2 x N array: row 0 topic ids, row 1 values."""
    if not data:
        return []
    if len(data) == 2 and isinstance(data[0], (tuple, list)):
        ids, values = data
    else:  # defensive: N x 2 layout
        ids = [row[0] for row in data]
        values = [row[1] for row in data]
    pairs = []
    for topic_id, value in zip(ids, values):
        try:
            pairs.append((int(topic_id), value))
        except (TypeError, ValueError):
            continue
    return pairs[:count] if count > 0 else pairs


def _quiet(function, *args):
    try:
        function(*args)
    except Exception:
        log.debug("ignored error in %s", getattr(function, "__name__", function), exc_info=True)
