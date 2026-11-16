#!/usr/bin/env python3
"""End-to-end check against a real LibreOffice (not run by pytest).

Prerequisites: the extension is installed (unopkg add dist/calc-rtd.oxt) and
this script runs with a Python that can ``import uno`` (LibreOffice's own, or
the system one with python3-uno).  It starts the bridge and a headless
soffice, puts =RTD() formulas in a sheet and checks that cells tick.

    python3 tests/integration_libreoffice.py
"""

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNO_PORT = 2002
RTD_PORT = 47399


def main():
    import uno
    from com.sun.star.beans import PropertyValue

    env = dict(os.environ, CALC_RTD_PORT=str(RTD_PORT), PYTHONPATH=os.path.join(ROOT, "src"))
    bridge_python = os.environ.get("BRIDGE_PYTHON", sys.executable)
    office = subprocess.Popen(
        ["soffice", "--headless", "--norestore", "--nologo",
         "--accept=socket,host=127.0.0.1,port=%d;urp;" % UNO_PORT], env=env)
    bridge = None
    failures = []

    def check(label, condition, detail=""):
        print("%s  %s %s" % ("ok  " if condition else "FAIL", label, detail))
        if not condition:
            failures.append(label)

    try:
        resolver = uno.getComponentContext().ServiceManager.createInstance(
            "com.sun.star.bridge.UnoUrlResolver")
        for _ in range(60):
            try:
                ctx = resolver.resolve(
                    "uno:socket,host=127.0.0.1,port=%d;urp;StarOffice.ComponentContext" % UNO_PORT)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("could not connect to soffice")
        desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        hidden = PropertyValue()
        hidden.Name, hidden.Value = "Hidden", True
        doc = desktop.loadComponentFromURL("private:factory/scalc", "_blank", 0, (hidden,))
        sheet = doc.Sheets.getByIndex(0)

        def cell(name):
            c = sheet.getCellRangeByName(name)
            return c.String, c.Value, c.getError()

        sheet.getCellRangeByName("A1").Formula = '=RTD("calcrtd.sim";;"counter";0.2)'
        sheet.getCellRangeByName("A2").Formula = '=RTD("calcrtd.sim";;"echo";"hello")'
        sheet.getCellRangeByName("A3").Formula = '=RTD("calcrtd.sim";;"counter";0.2)*10'
        sheet.getCellRangeByName("A4").Formula = '=RTD("calcrtd.sim";;"walk";"ACME";38.5)'
        sheet.getCellRangeByName("A5").Formula = '=RTD("No.Such.Server";;"X")'
        # A hidden headless document computes cells lazily; a visible one does it on paint.
        doc.calculateAll()
        time.sleep(3)
        check("function is registered", cell("A1")[2] != 525, "(A1 error code %s)" % cell("A1")[2])
        check("offline message while the bridge is down", "offline" in cell("A1")[0], repr(cell("A1")[0]))

        bridge = subprocess.Popen(
            [bridge_python, "-m", "calc_rtd_bridge", "serve", "--port", str(RTD_PORT)], env=env)
        time.sleep(5)
        first = cell("A1")[1]
        check("counter has a value after the bridge starts", first >= 1, "(A1=%s)" % first)
        check("string topic", cell("A2")[0] == "hello", repr(cell("A2")[0]))
        time.sleep(2)
        second = cell("A1")[1]
        check("counter keeps ticking without user action", second > first, "(%s -> %s)" % (first, second))
        check("dependent formula follows", abs(cell("A3")[1] - cell("A1")[1] * 10) <= 20, "(A3=%s)" % cell("A3")[1])
        check("numeric walk", 30 < cell("A4")[1] < 50, "(A4=%s)" % cell("A4")[1])
        check("unknown server reports an error text", cell("A5")[0].startswith("#RTD:"), repr(cell("A5")[0])[:70])

        bridge.terminate()
        bridge.wait(10)
        bridge = None
        time.sleep(3)
        check("offline again after the bridge stops", "offline" in cell("A1")[0], repr(cell("A1")[0]))
        bridge = subprocess.Popen(
            [bridge_python, "-m", "calc_rtd_bridge", "serve", "--port", str(RTD_PORT)], env=env)
        time.sleep(5)
        check("recovers when the bridge returns", cell("A1")[1] >= 1, "(A1=%s)" % cell("A1")[1])

        sheet.getCellRangeByName("A1").Formula = ""
        sheet.getCellRangeByName("A3").Formula = ""
        time.sleep(1)
        check("other cells survive removing a formula", cell("A2")[0] == "hello")
        doc.close(True)
    finally:
        if bridge is not None:
            bridge.terminate()
        try:
            desktop.terminate()
        except Exception:
            pass
        try:
            office.wait(15)
        except subprocess.TimeoutExpired:
            office.kill()
    print("\n%s" % ("ALL PASSED" if not failures else "FAILED: %s" % ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
