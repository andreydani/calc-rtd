#!/usr/bin/env python3
"""Build dist/calc-rtd.oxt with nothing but the Python standard library.

A Calc add-in needs a UNO type library (.rdb) describing its interface.
That normally requires ``unoidl-write`` from the LibreOffice SDK; because
the interface here is tiny, this script writes the binary UNOIDL format
directly (format documented in LibreOffice's unoidl/source/unoidlprovider.cxx).
The human-readable definition lives in extension/src/XCalcRtd.idl - keep
the INTERFACE table below in sync with it.
"""

from __future__ import annotations

import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "extension" / "src"
OUTPUT = ROOT / "dist" / "calc-rtd.oxt"

INTERFACE_NAME = "com.github.calcrtd.XCalcRtd"
INTERFACE = {
    "bases": ["com.sun.star.uno.XInterface"],
    "methods": [
        # (name, return type, [(direction, name, type)])  direction 0 = in
        ("rtd", "com.sun.star.sheet.XVolatileResult", [
            (0, "progId", "string"),
            (0, "server", "any"),
            (0, "topics", "[]any"),
        ]),
    ],
}


def _u32(value):
    return struct.pack("<I", value)


def _string(text):
    data = text.encode("ascii")
    return _u32(len(data)) + data


def _interface_payload(spec):
    out = bytearray([5])  # kind 5 = interface type; not published, not annotated
    out += _u32(len(spec["bases"]))
    for base in spec["bases"]:
        out += _string(base)
    out += _u32(0)  # optional bases
    out += _u32(0)  # attributes
    out += _u32(len(spec["methods"]))
    for name, returns, params in spec["methods"]:
        out += _string(name) + _string(returns) + _u32(len(params))
        for direction, param_name, param_type in params:
            out += bytes([direction]) + _string(param_name) + _string(param_type)
        out += _u32(0)  # exceptions
    return bytes(out)


def build_rdb(entities):
    """entities: {"dotted.Name": interface spec}. Returns the .rdb bytes."""
    tree = {}
    for dotted, spec in entities.items():
        node = tree
        *modules, leaf = dotted.split(".")
        for module in modules:
            node = node.setdefault(module, {})
        node[leaf] = _interface_payload(spec)

    blob = bytearray(b"UNOIDL\xff\x00" + _u32(0) + _u32(0))

    def append(data):
        offset = len(blob)
        blob.extend(data)
        return offset

    def write_map(node):
        """Write children, return the packed (name offset, payload offset) entries."""
        entries = bytearray()
        for name in sorted(node):
            child = node[name]
            if isinstance(child, dict):
                child_entries = write_map(child)
                payload = append(bytes([0]) + _u32(len(child_entries) // 8) + child_entries)
            else:
                payload = append(child)
            name_offset = append(name.encode("ascii") + b"\0")
            entries += _u32(name_offset) + _u32(payload)
        return bytes(entries)

    root_entries = write_map(tree)
    root_offset = append(root_entries)
    blob[8:12] = _u32(root_offset)
    blob[12:16] = _u32(len(root_entries) // 8)
    return bytes(blob)


def main():
    OUTPUT.parent.mkdir(exist_ok=True)
    rdb = build_rdb({INTERFACE_NAME: INTERFACE})
    files = sorted(p for p in SOURCE.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("XCalcRtd.rdb", rdb)
        for path in files:
            archive.write(path, path.relative_to(SOURCE).as_posix())
        archive.write(ROOT / "LICENSE", "LICENSE.txt")
    print("wrote %s (%d bytes, type library %d bytes)" % (OUTPUT, OUTPUT.stat().st_size, len(rdb)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
