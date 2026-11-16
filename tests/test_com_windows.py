"""Real COM round trip against samples/rtd-server (Windows only).

Enabled by CALC_RTD_SAMPLE_SERVER=1 once the sample server is registered
(samples/rtd-server/build.ps1).  The bridge runs in a subprocess so COM is
initialised on its own main thread, exactly as in production.
"""

import os
import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("CALC_RTD_SAMPLE_SERVER") != "1",
    reason="needs Windows and the registered sample RTD server")

LINE = re.compile(r"^\d\d:\d\d:\d\d  (?P<label>.+?)\s{2,}(?P<value>.+)$")


def run_monitor(*args):
    command = [sys.executable, "-m", "calc_rtd_bridge", "-v", "--throttle", "100", "monitor"]
    completed = subprocess.run(command + list(args), capture_output=True, text=True, timeout=60)
    values = {}
    for line in completed.stdout.splitlines():
        match = LINE.match(line)
        if match:
            values.setdefault(match.group("label").strip(), []).append(match.group("value").strip())
    return completed, values


def test_updates_flow_from_a_real_rtd_server():
    completed, values = run_monitor(
        "=RTD(CalcRtd.Sample;;counter)", '=RTD("CalcRtd.Sample";;"echo";"hello")', "--seconds", "4")
    report = completed.stdout + completed.stderr
    assert completed.returncode == 0, report
    counter = [float(v) for v in values["CalcRtd.Sample | counter"]]
    assert len(counter) >= 5, report               # pushed by UpdateNotify + RefreshData
    assert counter == sorted(counter) and counter[-1] > counter[0], report
    assert values["CalcRtd.Sample | echo;hello"] == ["'hello'"], report
    assert "started RTD server CalcRtd.Sample" in report
    assert "terminated RTD server CalcRtd.Sample" in report


def test_unknown_progid_is_reported():
    completed, values = run_monitor("=RTD(CalcRtd.DoesNotExist;;x)", "--seconds", "1")
    assert completed.returncode == 1
    assert "ERROR" in completed.stdout and "not registered" in completed.stdout
