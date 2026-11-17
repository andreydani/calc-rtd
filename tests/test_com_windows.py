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


@pytest.mark.parametrize("attempt", range(6))
def test_process_exits_cleanly_after_hosting_a_dotnet_server(attempt):
    """The server keeps a reference to our callback until its runtime shuts down, at process
    exit. That used to crash the interpreter now and then, hence the repetitions."""
    completed, values = run_monitor("=RTD(CalcRtd.Sample;;counter)", "--seconds", "1.5")
    assert completed.returncode == 0, "exit code 0x%08X\n%s" % (
        completed.returncode & 0xFFFFFFFF, completed.stdout[-600:] + completed.stderr[-1200:])
    assert len(values["CalcRtd.Sample | counter"]) >= 2


def test_unknown_progid_is_reported():
    completed, values = run_monitor("=RTD(CalcRtd.DoesNotExist;;x)", "--seconds", "1")
    assert completed.returncode == 1
    assert "ERROR" in completed.stdout and "not registered" in completed.stdout


# -- the public API, in process ------------------------------------------------
def collect_counter(rtd, counter, wanted=5, timeout=10):
    values = []
    for update in rtd.updates(timeout=timeout):
        assert update.error is None, update.error
        if update.subscription is counter:
            values.append(update.value)
            if len(values) >= wanted:
                break
    return values


def test_session_receives_updates_from_a_real_server():
    from calc_rtd import RtdSession

    with RtdSession(throttle=0.1) as rtd:
        assert rtd.com_available
        counter = rtd.subscribe("CalcRtd.Sample", ["counter"])
        echo = rtd.subscribe_formula('=RTD("CalcRtd.Sample";;"echo";"hello")')
        assert echo.wait(timeout=15) == "hello"
        values = collect_counter(rtd, counter)
        assert len(values) == 5 and values == sorted(values) and values[-1] > values[0]
        assert isinstance(counter.value, float)

    # A second session creates a fresh COM apartment and a fresh server instance.
    with RtdSession(throttle=0.1) as rtd:
        counter = rtd.subscribe("CalcRtd.Sample", ["counter"])
        assert counter.wait(timeout=15) == 0.0
        assert len(collect_counter(rtd, counter, wanted=3)) == 3


def test_session_used_from_a_worker_thread():
    import threading

    from calc_rtd import RtdSession

    result = {}

    def work():
        try:
            with RtdSession(throttle=0.1) as rtd:
                counter = rtd.subscribe("CalcRtd.Sample", ["counter"])
                result["values"] = collect_counter(rtd, counter, wanted=3)
        except Exception as exc:  # pragma: no cover - reported by the assert below
            result["error"] = exc

    worker = threading.Thread(target=work)
    worker.start()
    worker.join(40)
    assert not worker.is_alive() and "error" not in result, result
    assert len(result["values"]) == 3


def test_session_reports_unregistered_server():
    from calc_rtd import RtdError, RtdSession

    with RtdSession() as rtd:
        missing = rtd.subscribe("CalcRtd.DoesNotExist", ["x"])
        with pytest.raises(RtdError, match="not registered"):
            missing.wait(timeout=15)
