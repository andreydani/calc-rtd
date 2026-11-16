"""Minimal blocking client for the bridge protocol (used by ``watch`` and tests)."""

from __future__ import annotations

import socket

from .protocol import DEFAULT_HOST, DEFAULT_PORT, decode, encode, load_or_create_token


class BridgeClient:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=5.0, token=None):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._buffer = b""
        self._next_id = 1
        self.hello = self.read()
        if self.hello and self.hello.get("auth"):
            self._sock.sendall(encode({"op": "auth", "token": token or load_or_create_token()}))

    def subscribe(self, progid, server, topics):
        client_id = self._next_id
        self._next_id += 1
        self._sock.sendall(encode({"op": "subscribe", "id": client_id, "progid": progid,
                                   "server": server, "topics": list(topics)}))
        return client_id

    def unsubscribe(self, client_id):
        self._sock.sendall(encode({"op": "unsubscribe", "id": client_id}))

    def read(self, timeout=5.0):
        """Return the next message, or None on timeout. Raises EOFError when closed."""
        self._sock.settimeout(timeout)
        while b"\n" not in self._buffer:
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                return None
            if not chunk:
                raise EOFError("bridge closed the connection")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return decode(line)

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
