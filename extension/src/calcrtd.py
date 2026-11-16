"""LibreOffice Calc add-in: =RTD(ProgID; server; topic1; [topic2]; ...)

The function returns an XVolatileResult, the Calc mechanism for values
that change by themselves.  A background thread keeps one TCP connection
to the calc-rtd bridge (which hosts the real RTD server) and pushes every
update into the matching result object; Calc then recalculates the cells.

Standard library only - it runs on the Python bundled with LibreOffice.

Environment variables (optional):
    CALC_RTD_HOST / CALC_RTD_PORT   where the bridge listens (127.0.0.1:47365)
    CALC_RTD_BRIDGE_CMD             command to start the bridge if it is not running
"""

import json
import os
import shlex
import socket
import subprocess
import threading
import time

import uno  # noqa: F401  (installs the UNO import hook)
import unohelper
from com.github.calcrtd import XCalcRtd
from com.sun.star.lang import XServiceInfo
from com.sun.star.sheet import ResultEvent, XVolatileResult

IMPLEMENTATION_NAME = "com.github.calcrtd.CalcRtd"
SERVICE_NAMES = ("com.sun.star.sheet.AddIn",)

HOST = os.environ.get("CALC_RTD_HOST", "127.0.0.1")
PORT = int(os.environ.get("CALC_RTD_PORT", "47365"))
OFFLINE = "#RTD: bridge offline"
RETRY_SECONDS = 2.0


class RtdResult(unohelper.Base, XVolatileResult):
    """One distinct RTD topic; shared by every cell that asks for it."""

    def __init__(self, link, key, request_id):
        self.link = link
        self.key = key
        self.id = request_id
        self.value = None  # None shows as #N/A, like Excel before the first tick
        self._listeners = []
        self._lock = threading.Lock()

    def _event(self):
        event = ResultEvent()
        event.Source = self
        event.Value = self.value
        return event

    # XVolatileResult
    def addResultListener(self, listener):
        with self._lock:
            self._listeners.append(listener)
        listener.modified(self._event())

    def removeResultListener(self, listener):
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)
            unused = not self._listeners
        if unused:
            self.link.release(self)

    def set(self, value):
        """Called from the link thread. Never holds a lock while calling Calc."""
        if isinstance(value, bool):
            value = float(value)
        elif isinstance(value, int):
            value = float(value)
        if type(value) is type(self.value) and value == self.value:
            return
        self.value = value
        with self._lock:
            listeners = list(self._listeners)
        event = self._event()
        for listener in listeners:
            try:
                listener.modified(event)
            except Exception:
                pass  # document is closing


class BridgeLink(threading.Thread):
    """Owns the socket to the bridge and the table of live results."""

    def __init__(self):
        threading.Thread.__init__(self, name="calc-rtd-link", daemon=True)
        self._lock = threading.Lock()
        self._by_key = {}
        self._by_id = {}
        self._next_id = 1
        self._sock = None
        self._spawned = False

    # -- called from Calc's thread -----------------------------------------
    def result_for(self, progid, server, topics):
        key = (progid.lower(), server.lower(), tuple(topics))
        with self._lock:
            result = self._by_key.get(key)
            if result is not None:
                return result
            result = RtdResult(self, key, self._next_id)
            self._next_id += 1
            self._by_key[key] = result
            self._by_id[result.id] = result
            result.request = {"op": "subscribe", "id": result.id, "progid": progid,
                              "server": server, "topics": list(topics)}
            if self._sock is None:
                result.value = OFFLINE
            else:
                self._send(result.request)
        return result

    def release(self, result):
        with self._lock:
            if self._by_key.get(result.key) is not result:
                return
            del self._by_key[result.key]
            del self._by_id[result.id]
            self._send({"op": "unsubscribe", "id": result.id})

    def _send(self, message):
        """Caller holds self._lock."""
        if self._sock is None:
            return
        try:
            self._sock.sendall((json.dumps(message) + "\n").encode("utf-8"))
        except OSError:
            pass  # the reader loop notices the broken socket and reconnects

    # -- link thread --------------------------------------------------------
    def run(self):
        while True:
            try:
                sock = socket.create_connection((HOST, PORT), timeout=3.0)
            except OSError:
                self._maybe_spawn()
                time.sleep(RETRY_SECONDS)
                continue
            try:
                sock.settimeout(None)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self._lock:
                    self._sock = sock
                    pending = list(self._by_id.values())
                    for result in pending:
                        self._send(result.request)
                for result in pending:
                    if result.value == OFFLINE:
                        result.set(None)
                self._read(sock)
            except (OSError, ValueError):
                pass
            finally:
                with self._lock:
                    self._sock = None
                    stale = list(self._by_id.values())
                try:
                    sock.close()
                except OSError:
                    pass
                for result in stale:
                    result.set(OFFLINE)
            time.sleep(RETRY_SECONDS)

    def _read(self, sock):
        buffer = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line.strip():
                    self._handle(json.loads(line.decode("utf-8")))

    def _handle(self, message):
        op = message.get("op")
        if op == "update":
            for request_id, value in message.get("values", ()):
                with self._lock:
                    result = self._by_id.get(request_id)
                if result is not None:
                    result.set(value)
        elif op == "error":
            with self._lock:
                result = self._by_id.get(message.get("id"))
            if result is not None:
                result.set("#RTD: %s" % message.get("message", "error"))

    def _maybe_spawn(self):
        command = os.environ.get("CALC_RTD_BRIDGE_CMD")
        if self._spawned or not command:
            return
        self._spawned = True
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(shlex.split(command, posix=(os.name != "nt")),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=flags)
        except Exception:
            pass


_link = None
_link_lock = threading.Lock()


def _get_link():
    global _link
    with _link_lock:
        if _link is None:
            _link = BridgeLink()
            _link.start()
        return _link


def _text(value):
    """Excel hands every RTD argument to the server as a string."""
    if value is None:
        return ""
    if isinstance(value, (tuple, list)):  # a cell range: use its first cell
        return _text(value[0]) if value else ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return str(int(value)) if value == int(value) and abs(value) < 1e15 else repr(value)
    return str(value)


class CalcRtd(unohelper.Base, XCalcRtd, XServiceInfo):
    def __init__(self, ctx):
        self.ctx = ctx

    # XCalcRtd
    def rtd(self, progId, server, topics):
        topics = [_text(topic) for topic in (topics or ())]
        return _get_link().result_for(_text(progId).strip(), _text(server).strip(), topics)

    # XServiceInfo
    def getImplementationName(self):
        return IMPLEMENTATION_NAME

    def supportsService(self, name):
        return name in SERVICE_NAMES

    def getSupportedServiceNames(self):
        return SERVICE_NAMES


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(CalcRtd, IMPLEMENTATION_NAME, SERVICE_NAMES)
