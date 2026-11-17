"""Parsing of RTD formulas as they are written in a spreadsheet."""

from __future__ import annotations

import re

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
