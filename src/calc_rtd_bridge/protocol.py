"""Wire protocol between the bridge and its clients (the Calc extension).

Newline-delimited JSON over a TCP socket bound to localhost.

Client -> bridge
    {"op": "subscribe", "id": 7, "progid": "My.Rtd", "server": "", "topics": ["ACME", "LAST"]}
    {"op": "unsubscribe", "id": 7}
    {"op": "ping"}

    {"op": "auth", "token": "..."}      must be the first message when the bridge requires it

Bridge -> client
    {"op": "hello", "version": "0.1.0", "com": true, "auth": true}
    {"op": "update", "values": [[7, 38.52], [9, "text"]]}
    {"op": "error", "id": 7, "message": "..."}
    {"op": "pong"}

Authentication: both sides read a random token from a file only the current
user can read (see ``token_path``); whichever starts first creates it.  This
keeps other accounts on the same machine from subscribing through the bridge.

``id`` is chosen by the client and is only meaningful on that connection.
Values are JSON numbers, strings, booleans or null (null = no data yet).
"""

from __future__ import annotations

import datetime
import decimal
import json
import os
import secrets
import sys

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 47365
MAX_LINE = 1 << 20


def token_path() -> str:
    """Location of the shared secret (the Calc extension computes the same path)."""
    override = os.environ.get("CALC_RTD_TOKEN_FILE")
    if override:
        return override
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "calc-rtd", "token")


def load_or_create_token(path=None) -> str:
    path = path or token_path()
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
            continue  # the other side just created it
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            token = secrets.token_hex(32)
            handle.write(token)
        return token
    raise OSError("could not read or create token file %s" % path)


def encode(message: dict) -> bytes:
    return (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes) -> dict:
    message = json.loads(line.decode("utf-8"))
    if not isinstance(message, dict) or "op" not in message:
        raise ValueError("message must be an object with an 'op' field")
    return message


def to_wire(value):
    """Convert whatever an RTD server returned into a JSON-friendly value."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    return str(value)
