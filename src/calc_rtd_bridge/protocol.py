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
import re
import secrets
import sys

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 47365
SIM_PROGID = "calcrtd.sim"
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


def make_key(progid, server, topics) -> tuple:
    """Normalised identity of an RTD topic; ProgIDs are case-insensitive."""
    return (str(progid).strip().lower(), str(server or "").strip().lower(),
            tuple(str(t) for t in topics))


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


_CALL = re.compile(r"^\s*=?\s*[A-Za-z_.]+\s*\((.*)\)\s*$", re.S)


def parse_formula(text: str):
    """Parse ``=RTD("progid";"server";"t1";"t2")`` into (progid, server, topics).

    Accepts ``,`` or ``;`` as separators and quoted or bare arguments, so a
    formula can be pasted straight from Excel (any locale).
    """
    match = _CALL.match(text)
    if not match:
        raise ValueError("not an RTD formula: %r" % text)
    args, current, quoted, in_quotes = [], [], False, False
    body = match.group(1)
    i = 0
    while i < len(body):
        ch = body[i]
        if in_quotes:
            if ch == '"':
                if body[i + 1:i + 2] == '"':
                    current.append('"')
                    i += 1
                else:
                    in_quotes = False
            else:
                current.append(ch)
        elif ch == '"':
            in_quotes = quoted = True
        elif ch in ",;":
            args.append("".join(current) if quoted else "".join(current).strip())
            current, quoted = [], False
        else:
            current.append(ch)
        i += 1
    if in_quotes:
        raise ValueError("unterminated string in formula: %r" % text)
    args.append("".join(current) if quoted else "".join(current).strip())
    if len(args) < 3 or not args[0]:
        raise ValueError("RTD needs a ProgID, a server and at least one topic: %r" % text)
    return args[0], args[1], args[2:]
