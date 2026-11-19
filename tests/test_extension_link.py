"""The Calc add-in's connection code, run outside LibreOffice.

calcrtd.py normally runs inside LibreOffice's Python, whose version changes
with each LibreOffice release (3.9 in 24.8, 3.13 in 26.8). Here the UNO
modules it imports are replaced by stubs so the link to a real bridge can be
exercised on every Python version in the CI matrix.
"""

import importlib.util
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from calc_rtd.engine import Engine, Hub
from calc_rtd.sim import SimBackend
from calc_rtd_bridge.protocol import load_or_create_token
from calc_rtd_bridge.server import BridgeServer

ADDIN = Path(__file__).resolve().parent.parent / "extension" / "src" / "calcrtd.py"


def install_uno_stubs(monkeypatch):
    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    class Base:
        pass

    class ImplementationHelper:
        def addImplementation(self, *args):
            pass

    class ResultEvent:
        Source = None
        Value = None

    module("uno")
    module("unohelper", Base=Base, ImplementationHelper=ImplementationHelper)
    for package in ("com", "com.github", "com.sun", "com.sun.star"):
        module(package)
    module("com.github.calcrtd", XCalcRtd=type("XCalcRtd", (), {}))
    module("com.sun.star.lang", XServiceInfo=type("XServiceInfo", (), {}))
    module("com.sun.star.sheet", ResultEvent=ResultEvent, XVolatileResult=type("XVolatileResult", (), {}))


class Listener:
    """Stands in for the Calc cell: records every value it is told about."""

    def __init__(self):
        self.values = []

    def modified(self, event):
        self.values.append(event.Value)


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    token_file = str(tmp_path / "token")
    monkeypatch.setenv("CALC_RTD_TOKEN_FILE", token_file)
    monkeypatch.setenv("CALC_RTD_AUTOSTART", "0")
    hub = Hub()
    stop = threading.Event()
    engine_thread = threading.Thread(target=Engine(hub, [SimBackend()]).run, args=(stop,), daemon=True)
    engine_thread.start()
    server = BridgeServer(hub, "127.0.0.1", 0, com_available=False, token=load_or_create_token(token_file))
    monkeypatch.setenv("CALC_RTD_PORT", str(server.address[1]))
    yield server
    server.close()
    stop.set()
    engine_thread.join(2)


def load_addin(monkeypatch):
    install_uno_stubs(monkeypatch)
    spec = importlib.util.spec_from_file_location("calcrtd_under_test", ADDIN)
    addin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(addin)
    return addin


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_cells_receive_values_from_the_bridge(bridge, monkeypatch, tmp_path):
    addin = load_addin(monkeypatch)
    calc = addin.CalcRtd(None)

    echo, counter = Listener(), Listener()
    calc.rtd("calcrtd.sim", "", ("echo", "hello")).addResultListener(echo)
    calc.rtd("calcrtd.sim", None, ("counter", 0.05)).addResultListener(counter)

    log = tmp_path / "bridge.log"
    assert wait_for(lambda: "hello" in echo.values), (echo.values, log.read_text() if log.exists() else "")
    assert wait_for(lambda: len([v for v in counter.values if isinstance(v, float)]) >= 3)
    numbers = [v for v in counter.values if isinstance(v, float)]
    assert numbers == sorted(numbers) and numbers[0] == 1.0  # ints arrive as floats, as Calc wants
    assert not log.exists() or "unexpected error" not in log.read_text()


def test_same_formula_shares_one_result(bridge, monkeypatch):
    addin = load_addin(monkeypatch)
    calc = addin.CalcRtd(None)
    first = calc.rtd("calcrtd.sim", "", ("echo", 12.0))
    second = calc.rtd("CalcRTD.Sim", "", ("echo", "12"))
    assert first is second  # numbers become text as in Excel: 12.0 -> "12"


def test_offline_then_recovers(tmp_path, monkeypatch):
    monkeypatch.setenv("CALC_RTD_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setenv("CALC_RTD_AUTOSTART", "0")
    monkeypatch.setenv("CALC_RTD_PORT", "1")  # nothing listens there
    addin = load_addin(monkeypatch)
    monkeypatch.setattr(addin, "RETRY_SECONDS", 0.1)
    cell = Listener()
    addin.CalcRtd(None).rtd("calcrtd.sim", "", ("clock",)).addResultListener(cell)
    assert wait_for(lambda: addin.OFFLINE in cell.values)
