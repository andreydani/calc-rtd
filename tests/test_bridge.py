import importlib.util
import threading
import time
from pathlib import Path

import pytest

from calc_rtd import parse_formula
from calc_rtd.engine import Engine, Hub
from calc_rtd.host import RtdHostBackend, pairs_from_refresh
from calc_rtd.sim import SimBackend
from calc_rtd_bridge.client import BridgeClient
from calc_rtd_bridge.protocol import load_or_create_token, to_wire
from calc_rtd_bridge.server import BridgeServer

from fakes import FakeDriver, Recorder

ROOT = Path(__file__).resolve().parent.parent


# -- protocol ----------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ('=RTD("my.server";;"ACME";"LAST")', ("my.server", "", ["ACME", "LAST"])),
    ('=RTD("my.server",,"ACME","LAST")', ("my.server", "", ["ACME", "LAST"])),
    ("=RTD(my.server;;ACME; LAST )", ("my.server", "", ["ACME", "LAST"])),
    ('RTD("p";"host";"a;b";"say ""hi""")', ("p", "host", ["a;b", 'say "hi"'])),
    ('=rtd("p";;"a";;"c")', ("p", "", ["a", "", "c"])),
])
def test_parse_formula(text, expected):
    assert parse_formula(text) == expected


@pytest.mark.parametrize("text", ["hello", '=RTD("p")', '=RTD("p";;"a)', "=RTD(;;a)"])
def test_parse_formula_rejects(text):
    with pytest.raises(ValueError):
        parse_formula(text)


def test_to_wire():
    import datetime
    import decimal
    assert to_wire(decimal.Decimal("1.5")) == 1.5
    assert to_wire(float("nan")) is None
    assert to_wire(datetime.date(2026, 1, 2)) == "2026-01-02"
    assert to_wire(True) is True and to_wire(3) == 3 and to_wire("x") == "x"


def test_pairs_from_refresh():
    assert pairs_from_refresh(((1, 2), (10.5, "x")), 2) == [(1, 10.5), (2, "x")]
    assert pairs_from_refresh(((1, 2, 3), ("a", "b", "c")), 2) == [(1, "a"), (2, "b")]
    assert pairs_from_refresh(((1, "a"), (2, "b"), (3, "c")), 0) == [(1, "a"), (2, "b"), (3, "c")]
    assert pairs_from_refresh((), 0) == []


# -- hub + RTD host with a fake driver ---------------------------------------
@pytest.fixture
def rig():
    FakeDriver.instances.clear()
    hub = Hub()
    backend = RtdHostBackend(FakeDriver, throttle=0.5, clock=lambda: 0.0)
    engine = Engine(hub, [backend], wait=lambda timeout: None, clock=lambda: 0.0)
    return hub, backend, engine


def test_subscribe_shares_one_topic_between_clients(rig):
    hub, backend, engine = rig
    a, b = Recorder(), Recorder()
    hub.subscribe(a, 1, "Vendor.Rtd", "", ["ACME", "LAST"])
    engine.step()
    hub.subscribe(b, 9, "vendor.rtd", "", ["ACME", "LAST"])  # ProgID is case-insensitive
    engine.step()
    driver, = FakeDriver.instances
    assert [c for c in driver.calls if c != "start"] == [("connect", 1, ("ACME", "LAST"))]
    assert a.values(1) == ["init:ACME"] and b.values(9) == ["init:ACME"]

    driver.push(1, 38.5)
    backend.tick(100.0, hub)
    assert a.values(1)[-1] == 38.5 and b.values(9)[-1] == 38.5

    hub.unsubscribe(a, 1)
    engine.step()
    assert ("disconnect", 1) not in driver.calls  # b still listens
    hub.drop_client(b)
    engine.step()
    assert driver.calls[-2:] == [("disconnect", 1), "terminate"]
    assert hub.topic_count() == 0


def test_refresh_is_throttled_and_deduplicated(rig):
    hub, backend, engine = rig
    client = Recorder()
    hub.subscribe(client, 1, "Vendor.Rtd", "", ["A"])
    engine.step()
    driver, = FakeDriver.instances
    driver.push(1, 1.0)
    backend.tick(10.0, hub)
    driver.push(1, 2.0)
    backend.tick(10.2, hub)  # inside the 0.5 s throttle window
    assert client.values(1) == ["init:A", 1.0]
    backend.tick(10.6, hub)
    assert client.values(1) == ["init:A", 1.0, 2.0]
    driver.push(1, 2.0)  # unchanged value is not re-sent
    driver.push(99, "stray")  # unknown topic id is ignored
    backend.tick(11.2, hub)
    assert client.values(1) == ["init:A", 1.0, 2.0]


def test_errors_reach_the_client(rig):
    hub, backend, engine = rig
    client = Recorder()
    hub.subscribe(client, 1, "Vendor.Rtd", "", ["BOOM"])
    engine.step()
    assert "bad topic" in client.errors()[0]["message"]
    assert FakeDriver.instances[0].calls[-1] == "terminate"  # nothing left to host
    hub.subscribe(client, 2, "Vendor.Rtd", "", [])
    assert "topic" in client.errors()[-1]["message"]


def test_server_start_failure(rig):
    hub, backend, engine = rig

    class Refusing(FakeDriver):
        def start(self):
            return 0

    backend._factory = Refusing
    client = Recorder()
    hub.subscribe(client, 1, "Vendor.Rtd", "", ["A"])
    engine.step()
    assert "refused to start" in client.errors()[0]["message"]


def test_dead_server_is_restarted(rig):
    hub, backend, engine = rig
    client = Recorder()
    hub.subscribe(client, 1, "Vendor.Rtd", "", ["A"])
    engine.step()
    first, = FakeDriver.instances
    backend.tick(100.0, hub)
    assert "heartbeat" in first.calls
    first.alive = False
    backend.tick(200.0, hub)
    assert len(FakeDriver.instances) == 2 and first.calls[-1] == "terminate"
    assert FakeDriver.instances[1].connected == {1: ("A",)}
    FakeDriver.instances[1].push(1, 7)
    backend.tick(300.0, hub)
    assert client.values(1)[-1] == 7


def test_sim_backend():
    FakeDriver.instances.clear()
    hub = Hub()
    engine = Engine(hub, [SimBackend(), RtdHostBackend(FakeDriver)], wait=lambda timeout: None)
    client = Recorder()
    hub.subscribe(client, 1, "calcrtd.sim", "", ["counter", "0.05"])
    hub.subscribe(client, 2, "CalcRTD.Sim", "", ["echo", "hi"])
    hub.subscribe(client, 3, "calcrtd.sim", "", ["nope"])
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        engine.step()
        time.sleep(0.01)
    assert client.values(1)[:3] == [1, 2, 3]
    assert client.values(2) == ["hi"]
    assert "unknown simulated topic" in client.errors()[0]["message"]
    assert not FakeDriver.instances


# -- over a real socket -------------------------------------------------------
def test_tcp_round_trip():
    hub = Hub()
    engine = Engine(hub, [SimBackend()])
    stop = threading.Event()
    thread = threading.Thread(target=engine.run, args=(stop,), daemon=True)
    thread.start()
    server = BridgeServer(hub, "127.0.0.1", 0, com_available=False)
    try:
        with BridgeClient(port=server.address[1]) as client:
            assert client.hello["op"] == "hello" and client.hello["com"] is False
            counter = client.subscribe("calcrtd.sim", "", ["counter", "0.05"])
            echo = client.subscribe("calcrtd.sim", "", ["echo", "olá"])
            seen = {counter: [], echo: []}
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and (len(seen[counter]) < 3 or not seen[echo]):
                message = client.read(timeout=1)
                if message and message["op"] == "update":
                    for client_id, value in message["values"]:
                        seen[client_id].append(value)
            assert seen[counter][:3] == [1, 2, 3]
            assert seen[echo] == ["olá"]
        deadline = time.monotonic() + 2
        while hub.topic_count() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert hub.topic_count() == 0  # disconnecting releases every topic
    finally:
        server.close()
        stop.set()
        thread.join(2)


def test_token_file(tmp_path):
    path = str(tmp_path / "nested" / "token")
    token = load_or_create_token(path)
    assert len(token) == 64 and load_or_create_token(path) == token


def test_tcp_requires_token():
    hub = Hub()
    server = BridgeServer(hub, "127.0.0.1", 0, com_available=False, token="s3cret")
    try:
        with BridgeClient(port=server.address[1], token="wrong") as intruder:
            assert intruder.hello["auth"] is True
            # The bridge answers "authentication failed" and hangs up; depending on timing
            # the client sees that message first or just the closed connection.
            with pytest.raises((EOFError, OSError)):
                intruder.subscribe("calcrtd.sim", "", ["clock"])
                for _ in range(3):
                    message = intruder.read(timeout=2)
                    assert message is not None and message["op"] == "error"
                    assert message["message"] == "authentication failed"
        assert hub.topic_count() == 0
        assert server.client_count() == 0  # a rejected client never counts as connected
        with BridgeClient(port=server.address[1], token="s3cret") as client:
            client.subscribe("calcrtd.sim", "", ["echo", "x"])
            deadline = time.monotonic() + 2
            while hub.topic_count() == 0 and time.monotonic() < deadline:
                time.sleep(0.02)
            assert hub.topic_count() == 1 and server.client_count() == 1
            assert server.idle_seconds() == 0.0
    finally:
        server.close()


# -- extension type library ---------------------------------------------------
def test_rdb_layout():
    spec = importlib.util.spec_from_file_location("build_oxt", ROOT / "tools" / "build_oxt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rdb = module.build_rdb({module.INTERFACE_NAME: module.INTERFACE})
    assert rdb[:8] == b"UNOIDL\xff\x00"
    root_offset = int.from_bytes(rdb[8:12], "little")
    assert int.from_bytes(rdb[12:16], "little") == 1
    assert root_offset + 8 == len(rdb)
    name_offset = int.from_bytes(rdb[root_offset:root_offset + 4], "little")
    assert rdb[name_offset:name_offset + 4] == b"com\0"
    for needle in (b"XCalcRtd\0", b"com.sun.star.sheet.XVolatileResult", b"[]any"):
        assert needle in rdb
