"""LibreOffice Calc add-in: =RTD(ProgID; server; topic1; [topic2]; ...)

The function returns an XVolatileResult, the Calc mechanism for values
that change by themselves.  A background thread keeps one TCP connection
to the calc-rtd bridge (which hosts the real RTD server) and pushes every
update into the matching result object; Calc then recalculates the cells.

Standard library only - it runs on the Python bundled with LibreOffice.

If the bridge is not running the extension starts it, hidden, trying in order:
the command in CALC_RTD_BRIDGE_CMD, the executable bundled with the extension
(bridge/calc-rtd-bridge.exe) and ``calc-rtd-bridge`` found on PATH.

Environment variables (optional):
    CALC_RTD_HOST / CALC_RTD_PORT   where the bridge listens (127.0.0.1:47365)
    CALC_RTD_BRIDGE_CMD             command line used to start the bridge
    CALC_RTD_AUTOSTART=0            never start the bridge automatically
    CALC_RTD_TOKEN_FILE             location of the shared authentication token
"""

import json
import os
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
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
SPAWN_INTERVAL = 30.0
STARTUP_GRACE = 20.0  # show #N/A, not "offline", while a freshly started bridge comes up
IDLE_EXIT_SECONDS = os.environ.get("CALC_RTD_IDLE_EXIT", "60")


def _token_path():
    # Must stay identical to calc_rtd_bridge.protocol.token_path().
    override = os.environ.get("CALC_RTD_TOKEN_FILE")
    if override:
        return override
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "calc-rtd", "token")


def _load_or_create_token():
    path = _token_path()
    for _ in range(2):
        try:
            with open(path, encoding="ascii") as handle:
                token = handle.read().strip()
            if token:
                return token
            os.remove(path)
        except FileNotFoundError:
            pass
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            token = secrets.token_hex(32)
            handle.write(token)
        return token
    return ""


def _bridge_command():
    """Command line that starts the bridge, or None if there is no way to."""
    if os.environ.get("CALC_RTD_AUTOSTART", "1") == "0":
        return None
    custom = os.environ.get("CALC_RTD_BRIDGE_CMD")
    if custom:
        return shlex.split(custom, posix=(os.name != "nt"))
    here = os.path.dirname(os.path.abspath(globals().get("__file__", "")))
    bundled = os.path.join(here, "bridge", "calc-rtd-bridge.exe")
    program = bundled if os.path.isfile(bundled) else shutil.which("calc-rtd-bridge")
    if not program:
        return None
    return [program, "serve", "--port", str(PORT), "--idle-exit", IDLE_EXIT_SECONDS]


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
        self._last_spawn = 0.0
        self._starting_until = 0.0
        self._offline = False

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
                if self._offline:
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
                if time.monotonic() < self._starting_until:
                    time.sleep(0.5)  # the bridge we just started is still loading
                else:
                    self._mark_offline()
                    time.sleep(RETRY_SECONDS)
                continue
            try:
                sock.settimeout(None)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.sendall((json.dumps({"op": "auth", "token": _load_or_create_token()})
                              + "\n").encode("utf-8"))
                with self._lock:
                    self._sock = sock
                    self._offline = False
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
                try:
                    sock.close()
                except OSError:
                    pass
                self._mark_offline()
            time.sleep(RETRY_SECONDS)

    def _mark_offline(self):
        with self._lock:
            self._offline = True
            stale = list(self._by_id.values())
        for result in stale:
            result.set(OFFLINE)

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
        now = time.monotonic()
        if self._last_spawn and now - self._last_spawn < SPAWN_INTERVAL:
            return
        command = _bridge_command()
        if not command:
            return
        self._last_spawn = now
        try:
            _load_or_create_token()  # make sure the file exists before the bridge reads it
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._starting_until = now + STARTUP_GRACE
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

    def xlfnRtd(self, progId, server, topics):
        """Alias: Excel puts =_xlfn.RTD(...) on the clipboard."""
        return self.rtd(progId, server, topics)

    # XServiceInfo
    def getImplementationName(self):
        return IMPLEMENTATION_NAME

    def supportsService(self, name):
        return name in SERVICE_NAMES

    def getSupportedServiceNames(self):
        return SERVICE_NAMES


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(CalcRtd, IMPLEMENTATION_NAME, SERVICE_NAMES)
