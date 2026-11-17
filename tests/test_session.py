"""The public API (calc_rtd.RtdSession), using the simulated source."""

import threading
import time

import pytest

from calc_rtd import RtdError, RtdSession, SIM_PROGID
from calc_rtd.host import RtdHostBackend

from fakes import FakeDriver


def take(session, count, timeout=3.0):
    """Collect `count` updates or fail."""
    out = []
    deadline = time.monotonic() + timeout
    while len(out) < count and time.monotonic() < deadline:
        update = session.get(timeout=0.2)
        if update is not None:
            out.append(update)
    assert len(out) == count, "only %d of %d updates arrived" % (len(out), count)
    return out


def test_iterate_updates():
    with RtdSession() as rtd:
        counter = rtd.subscribe(SIM_PROGID, ["counter", 0.05])
        values = []
        for update in rtd.updates(timeout=2):
            assert update.subscription is counter and update.error is None
            values.append(update.value)
            if len(values) == 3:
                break
    assert values == [1, 2, 3]
    assert counter.progid == SIM_PROGID and counter.topics == ("counter", "0.05")


def test_wait_and_latest_value():
    with RtdSession() as rtd:
        echo = rtd.subscribe(SIM_PROGID, ["echo", "hello"])
        assert echo.wait(timeout=2) == "hello"
        assert echo.value == "hello" and echo.has_value and echo.error is None
        single = rtd.subscribe(SIM_PROGID, "clock")  # a bare string is one topic
        assert len(single.wait(timeout=2)) == 8


def test_formula_and_numeric_topics():
    with RtdSession() as rtd:
        walk = rtd.subscribe_formula('=RTD("calcrtd.sim";;"walk";"ACME";38.5)')
        assert 30 < walk.wait(timeout=2) < 50
        assert walk.topics == ("walk", "ACME", "38.5")


def test_errors_are_reported_three_ways():
    with RtdSession() as rtd:
        bad = rtd.subscribe(SIM_PROGID, ["no-such-topic"])
        with pytest.raises(RtdError, match="unknown simulated topic"):
            bad.wait(timeout=2)
        assert "unknown simulated topic" in bad.error and not bad.has_value
        update, = take(rtd, 1)
        assert update.subscription is bad and update.value is None and update.error == bad.error


def test_wait_times_out():
    with RtdSession(_backends=[]) as rtd:  # nothing can answer, but errors still arrive
        sub = rtd.subscribe("Some.Server", ["x"])
        with pytest.raises(RtdError, match="no backend"):
            sub.wait(timeout=2)
    with RtdSession(_backends=[RtdHostBackend(SilentDriver)]) as rtd:
        sub = rtd.subscribe("Some.Server", ["x"])
        take(rtd, 1)  # the initial value
        assert rtd.get(timeout=0.2) is None
        assert list(rtd.updates(timeout=0.2)) == []


class SilentDriver(FakeDriver):
    """Connects fine and then never sends anything."""


def test_callback_runs_and_its_errors_do_not_break_the_session():
    seen = []

    def callback(update):
        seen.append(update.value)
        raise ValueError("user bug")

    with RtdSession() as rtd:
        rtd.subscribe(SIM_PROGID, ["counter", 0.05], callback=callback)
        take(rtd, 3)
    assert seen[:3] == [1, 2, 3]


def test_unsubscribe_stops_the_flow():
    with RtdSession() as rtd:
        counter = rtd.subscribe(SIM_PROGID, ["counter", 0.05])
        echo = rtd.subscribe(SIM_PROGID, ["echo", "x"])
        take(rtd, 3)
        counter.unsubscribe()
        assert not counter.active and rtd.subscriptions == [echo]
        time.sleep(0.2)
        while rtd.get(timeout=0.05) is not None:
            pass
        assert rtd.get(timeout=0.3) is None


def test_two_subscriptions_to_the_same_topic_share_it():
    FakeDriver.instances.clear()
    with RtdSession(_backends=[RtdHostBackend(FakeDriver, throttle=0.0)]) as rtd:
        first = rtd.subscribe("Vendor.Rtd", ["ACME", "LAST"])
        second = rtd.subscribe("vendor.rtd", ["ACME", "LAST"])
        assert first.wait(2) == "init:ACME" and second.wait(2) == "init:ACME"
        driver, = FakeDriver.instances
        assert [c for c in driver.calls if isinstance(c, tuple) and c[0] == "connect"] \
            == [("connect", 1, ("ACME", "LAST"))]
        take(rtd, 2)
        driver.push(1, 38.5)
        updates = take(rtd, 2)
        assert {u.subscription for u in updates} == {first, second}
        assert first.value == second.value == 38.5
    assert driver.calls[-2:] == [("disconnect", 1), "terminate"]  # closed cleanly


def test_close_ends_iteration_in_another_thread():
    rtd = RtdSession().start()
    rtd.subscribe(SIM_PROGID, ["echo", "x"])
    collected, done = [], threading.Event()

    def consume():
        for update in rtd.updates():
            collected.append(update.value)
        done.set()

    threading.Thread(target=consume, daemon=True).start()
    time.sleep(0.3)
    rtd.close()
    assert done.wait(2) and collected == ["x"]
    assert rtd.get(timeout=0.1) is None
    rtd.close()  # idempotent
    with pytest.raises(RuntimeError):
        rtd.subscribe(SIM_PROGID, ["clock"])


def test_slow_consumer_drops_oldest_updates():
    with RtdSession(queue_size=5) as rtd:
        counter = rtd.subscribe(SIM_PROGID, ["counter", 0.05])
        time.sleep(0.8)
        assert rtd.dropped > 0
        queued = [rtd.get(timeout=0.01).value for _ in range(5)]
        assert queued == sorted(queued) and queued[-1] >= 10  # the newest ones survive
        assert counter.value >= queued[-1]
