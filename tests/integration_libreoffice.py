#!/usr/bin/env python3
"""End-to-end check against a real LibreOffice (not run by pytest).

Prerequisites: the extension is installed (unopkg add dist/calc-rtd.oxt), the
bridge is installed (pip install .) so ``calc-rtd-bridge`` is on PATH, and
this script runs with a Python that can ``import uno`` (LibreOffice's own, or
the system one with python3-uno).

    python3 tests/integration_libreoffice.py
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNO_PORT = 2002
RTD_PORT = 47399
failures = []


def check(label, condition, detail=""):
    print("%s  %s %s" % ("ok  " if condition else "FAIL", label, detail), flush=True)
    if not condition:
        failures.append(label)


def port_open():
    try:
        socket.create_connection(("127.0.0.1", RTD_PORT), timeout=0.5).close()
        return True
    except OSError:
        return False


class Office:
    """A headless soffice with one hidden spreadsheet."""

    def __init__(self, env):
        import uno
        from com.sun.star.beans import PropertyValue

        self.process = subprocess.Popen(
            ["soffice", "--headless", "--norestore", "--nologo",
             "--accept=socket,host=127.0.0.1,port=%d;urp;" % UNO_PORT], env=env)
        resolver = uno.getComponentContext().ServiceManager.createInstance(
            "com.sun.star.bridge.UnoUrlResolver")
        for _ in range(80):
            try:
                ctx = resolver.resolve(
                    "uno:socket,host=127.0.0.1,port=%d;urp;StarOffice.ComponentContext" % UNO_PORT)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("could not connect to soffice")
        self.desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        hidden = PropertyValue()
        hidden.Name, hidden.Value = "Hidden", True
        self.doc = self.desktop.loadComponentFromURL("private:factory/scalc", "_blank", 0, (hidden,))
        self.sheet = self.doc.Sheets.getByIndex(0)

    def formula(self, name, text):
        self.sheet.getCellRangeByName(name).Formula = text

    def cell(self, name):
        c = self.sheet.getCellRangeByName(name)
        return c.String, c.Value, c.getError()

    def close(self):
        try:
            self.doc.close(True)
            self.desktop.terminate()
        except Exception:
            pass
        try:
            self.process.wait(20)
        except subprocess.TimeoutExpired:
            self.process.kill()


def start_bridge(env):
    return subprocess.Popen([os.environ.get("BRIDGE_PYTHON", sys.executable), "-m", "calc_rtd_bridge",
                             "serve", "--port", str(RTD_PORT)], env=env)


def manual_bridge_session(env):
    print("-- bridge started by hand, auto-start disabled")
    office = Office(dict(env, CALC_RTD_AUTOSTART="0"))
    bridge = None
    try:
        office.formula("A1", '=RTD("calcrtd.sim";;"counter";0.2)')
        office.formula("A2", '=RTD("calcrtd.sim";;"echo";"hello")')
        office.formula("A3", '=RTD("calcrtd.sim";;"counter";0.2)*10')
        office.formula("A4", '=RTD("calcrtd.sim";;"walk";"ACME";38.5)')
        office.formula("A5", '=RTD("No.Such.Server";;"X")')
        office.formula("A6", '=_xlfn.RTD("calcrtd.sim";;"echo";"pasted")')
        # A hidden headless document computes cells lazily; a visible one does it on paint.
        office.doc.calculateAll()
        time.sleep(3)
        check("function is registered", office.cell("A1")[2] != 525, "(A1 error code %s)" % office.cell("A1")[2])
        check("offline message while the bridge is down", "offline" in office.cell("A1")[0], repr(office.cell("A1")[0]))

        bridge = start_bridge(env)
        time.sleep(5)
        first = office.cell("A1")[1]
        check("counter has a value after the bridge starts", first >= 1, "(A1=%s)" % first)
        check("string topic", office.cell("A2")[0] == "hello", repr(office.cell("A2")[0]))
        time.sleep(2)
        second = office.cell("A1")[1]
        check("counter keeps ticking without user action", second > first, "(%s -> %s)" % (first, second))
        check("dependent formula follows", abs(office.cell("A3")[1] - office.cell("A1")[1] * 10) <= 20)
        check("numeric walk", 30 < office.cell("A4")[1] < 50, "(A4=%s)" % office.cell("A4")[1])
        check("unknown server reports an error text", office.cell("A5")[0].startswith("#RTD:"))
        check("_xlfn.RTD alias (formula pasted from Excel)", office.cell("A6")[0] == "pasted",
              "%r error %s" % (office.cell("A6")[0], office.cell("A6")[2]))

        with socket.create_connection(("127.0.0.1", RTD_PORT), timeout=3) as intruder:
            intruder.settimeout(3)
            intruder.sendall(b'{"op":"subscribe","id":1,"progid":"calcrtd.sim","server":"","topics":["clock"]}\n')
            data = b""
            try:
                while True:
                    chunk = intruder.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            except socket.timeout:
                pass
            ops = [json.loads(line) for line in data.splitlines() if line.strip()]
            check("client without the token is rejected",
                  any(m.get("message") == "authentication failed" for m in ops)
                  and not any(m["op"] == "update" for m in ops))

        bridge.terminate()
        bridge.wait(10)
        bridge = None
        time.sleep(3)
        check("offline again after the bridge stops", "offline" in office.cell("A1")[0], repr(office.cell("A1")[0]))
        bridge = start_bridge(env)
        time.sleep(5)
        check("recovers when the bridge returns", office.cell("A1")[1] >= 1, "(A1=%s)" % office.cell("A1")[1])
        office.formula("A1", "")
        office.formula("A3", "")
        time.sleep(1)
        check("other cells survive removing a formula", office.cell("A2")[0] == "hello")
    finally:
        if bridge is not None:
            bridge.terminate()
            bridge.wait(10)
        office.close()


def autostart_session(env, scratch):
    print("-- bridge started by the extension")
    check("no bridge is running beforehand", not port_open())
    # LibreOffice on Windows puts its own Python's library folders in PYTHONPATH, and the
    # bridge used to inherit them. Reproduce that with a folder whose calc_rtd_bridge
    # package cannot be imported: the bridge must not see it.
    poison = os.path.join(scratch, "poison")
    os.makedirs(os.path.join(poison, "calc_rtd_bridge"))
    with open(os.path.join(poison, "calc_rtd_bridge", "__init__.py"), "w") as handle:
        handle.write("raise ImportError('PYTHONPATH from LibreOffice leaked into the bridge')\n")
    office = Office(dict(env, CALC_RTD_IDLE_EXIT="3",
                         PYTHONPATH=os.pathsep.join([poison, env.get("PYTHONPATH", "")])))
    try:
        office.formula("A1", '=RTD("calcrtd.sim";;"counter";0.2)')
        office.sheet.getCellRangeByName("A2").FormulaLocal = '=_xlfn.RTD("calcrtd.sim";;"echo";"typed")'
        office.doc.calculateAll()
        seen = set()
        deadline = time.time() + 8
        while time.time() < deadline:
            seen.add(office.cell("A1")[0])
            time.sleep(0.1)
        check("no 'offline' flash while the bridge is starting", not any("offline" in text for text in seen),
              repr(sorted(seen))[:80])
        check("_xlfn.RTD typed in the cell (UI grammar)", office.cell("A2")[0] == "typed",
              "%r error %s" % (office.cell("A2")[0], office.cell("A2")[2]))
        check("extension started the bridge by itself", port_open())
        check("values arrive through the auto-started bridge", office.cell("A1")[1] >= 1,
              "(A1=%r)" % (office.cell("A1"),))
    finally:
        office.close()
    time.sleep(8)  # CALC_RTD_IDLE_EXIT=3 plus margin
    check("auto-started bridge exits after LibreOffice closes", not port_open())
    try:
        log = open(os.path.join(scratch, "bridge.log"), encoding="utf-8", errors="replace").read()
    except OSError:
        log = ""
    print("---- bridge.log (end) ----\n%s\n--------------------------" % log[-8000:])
    check("bridge.log records the start and the bridge's own output",
          "extension: starting bridge:" in log and "listening on" in log, repr(log[-300:]))


def bridge_not_found_session(env, scratch):
    print("-- no bridge available")
    if os.environ.get("CALC_RTD_BUNDLED") == "1":
        print("skip  the installed extension bundles its own bridge")
        return
    program = shutil.which("calc-rtd-bridge")
    office_dir = os.path.dirname(shutil.which("soffice") or "")
    if not program or os.path.dirname(program) == office_dir:
        print("skip  calc-rtd-bridge and soffice share a folder; cannot hide one from PATH")
        return
    hidden = os.path.dirname(program)
    path = os.pathsep.join(d for d in env["PATH"].split(os.pathsep) if os.path.normpath(d) != os.path.normpath(hidden))
    office = Office(dict(env, PATH=path))
    try:
        office.formula("A1", '=RTD("calcrtd.sim";;"clock")')
        office.doc.calculateAll()
        time.sleep(6)
        check("cell says the bridge was not found", office.cell("A1")[0] == "#RTD: bridge not found",
              repr(office.cell("A1")[0]))
    finally:
        office.close()
    log = open(os.path.join(scratch, "bridge.log"), encoding="utf-8", errors="replace").read()
    check("bridge.log explains why", "calc-rtd-bridge not found on PATH" in log)


def main():
    with tempfile.TemporaryDirectory() as scratch:
        env = dict(os.environ, CALC_RTD_PORT=str(RTD_PORT),
                   CALC_RTD_TOKEN_FILE=os.path.join(scratch, "token"),
                   PYTHONPATH=os.path.join(ROOT, "src"))
        manual_bridge_session(env)
        time.sleep(1)
        autostart_session(env, scratch)
        time.sleep(1)
        bridge_not_found_session(env, scratch)
    print("\n%s" % ("ALL PASSED" if not failures else "FAILED: %s" % ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
