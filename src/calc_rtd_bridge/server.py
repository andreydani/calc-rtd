"""Localhost TCP front end of the bridge."""

from __future__ import annotations

import hmac
import logging
import queue
import socket
import threading
import time

from . import __version__
from .protocol import MAX_LINE, decode, encode, to_wire

log = logging.getLogger("calc_rtd.server")


class ClientConnection:
    def __init__(self, sock, address, hub, hello, token=None, on_ready=None, on_close=None):
        self._sock = sock
        self.address = address
        self._hub = hub
        self._token = token
        self._authenticated = token is None
        self._on_ready = on_ready
        self._on_close = on_close
        if self._authenticated and on_ready is not None:
            on_ready(self)
        self._out = queue.Queue(maxsize=10000)
        self._closed = threading.Event()
        self.send(hello)
        threading.Thread(target=self._writer, name="rtd-writer", daemon=True).start()
        threading.Thread(target=self._reader, name="rtd-reader", daemon=True).start()

    def send(self, message):
        if self._closed.is_set():
            return
        try:
            self._out.put_nowait(encode(message))
        except queue.Full:
            log.warning("client %s is not reading; dropping it", self.address)
            self.close()

    # Hub client interface (see calc_rtd.engine.Hub)
    def on_update(self, pairs):
        self.send({"op": "update", "values": [[client_id, to_wire(value)] for client_id, value in pairs]})

    def on_error(self, client_id, message):
        self.send({"op": "error", "id": client_id, "message": message})

    def _writer(self):
        try:
            while not self._closed.is_set():
                try:
                    data = self._out.get(timeout=0.5)
                except queue.Empty:
                    continue
                self._sock.sendall(data)
        except OSError:
            pass
        finally:
            self.close()

    def _reader(self):
        buffer = b""
        try:
            while not self._closed.is_set():
                chunk = self._sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_LINE:
                    raise ValueError("line too long")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.strip():
                        self._dispatch(line)
        except (OSError, ValueError) as exc:
            log.debug("client %s: %s", self.address, exc)
        finally:
            self.close()

    def _dispatch(self, line):
        try:
            message = decode(line)
            op = message["op"]
            if not self._authenticated:
                supplied = str(message.get("token", "")) if op == "auth" else ""
                if not hmac.compare_digest(supplied.encode(), self._token.encode()):
                    log.warning("client %s failed authentication", self.address)
                    self._reject()
                    return
                self._authenticated = True
                if self._on_ready is not None:
                    self._on_ready(self)
            elif op == "auth":
                pass
            elif op == "subscribe":
                topics = message.get("topics") or []
                if not isinstance(topics, list):
                    raise ValueError("'topics' must be a list")
                self._hub.subscribe(self, message["id"], message["progid"],
                                    message.get("server", ""), topics)
            elif op == "unsubscribe":
                self._hub.unsubscribe(self, message["id"])
            elif op == "ping":
                self.send({"op": "pong"})
            else:
                raise ValueError("unknown op %r" % op)
        except (KeyError, TypeError, ValueError) as exc:
            self.send({"op": "error", "id": None, "message": "bad request: %s" % exc})

    def _reject(self):
        try:
            self._sock.sendall(encode({"op": "error", "id": None, "message": "authentication failed"}))
        except OSError:
            pass
        self.close()

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        self._hub.drop_client(self)
        if self._on_close is not None:
            self._on_close(self)
        try:
            self._sock.close()
        except OSError:
            pass
        log.info("client %s disconnected", self.address)


class BridgeServer:
    def __init__(self, hub, host, port, com_available, token=None):
        self._hub = hub
        self._token = token
        self._hello = {"op": "hello", "version": __version__, "com": bool(com_available),
                       "auth": token is not None}
        self._clients = set()
        self._lock = threading.Lock()
        self.idle_since = time.monotonic()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(16)
        self.address = self._sock.getsockname()
        self._stopped = False
        threading.Thread(target=self._accept, name="rtd-accept", daemon=True).start()

    def _accept(self):
        while not self._stopped:
            try:
                sock, address = self._sock.accept()
            except OSError:
                break
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            log.info("client %s connected", address)
            ClientConnection(sock, address, self._hub, self._hello, self._token,
                             self._remember, self._forget)

    def _remember(self, client):
        # Only authenticated clients keep an auto-started bridge alive.
        with self._lock:
            self.idle_since = None
            self._clients.add(client)

    def _forget(self, client):
        with self._lock:
            self._clients.discard(client)
            if not self._clients:
                self.idle_since = time.monotonic()

    def client_count(self):
        with self._lock:
            return len(self._clients)

    def idle_seconds(self):
        """Seconds without any connected client (0 while someone is connected)."""
        with self._lock:
            return 0.0 if self.idle_since is None else time.monotonic() - self.idle_since

    def close(self):
        self._stopped = True
        try:
            self._sock.close()
        except OSError:
            pass
