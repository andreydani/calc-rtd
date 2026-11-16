"""Command line: ``calc-rtd-bridge serve | monitor | watch``."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from . import __version__
from .core import Engine, Hub
from .protocol import DEFAULT_HOST, DEFAULT_PORT, SIM_PROGID, parse_formula
from .sim import SimBackend

log = logging.getLogger("calc_rtd")


def _load_com():
    """Return (driver_factory, pump) or (None, None) with the reason logged."""
    if sys.platform != "win32":
        log.warning("not running on Windows: only the simulated ProgID %r is available", SIM_PROGID)
        return None, None
    try:
        from . import com_driver
    except ImportError as exc:
        log.warning("COM support is disabled (%s). Install it with: pip install comtypes", exc)
        return None, None
    if com_driver._is_elevated():
        log.warning("running as administrator: RTD servers fed by a non-elevated application "
                    "(the usual case) will fail to start. Prefer a normal prompt.")
    return com_driver.ComDriver, com_driver.pump


def _build_engine(hub, throttle_ms):
    from .rtd_host import RtdHostBackend

    factory, pump = _load_com()
    backends = [SimBackend()]
    if factory is not None:
        backends.append(RtdHostBackend(factory, throttle=throttle_ms / 1000.0))
    return Engine(hub, backends, wait=pump), factory is not None


def _run(engine, stop, seconds=None):
    if seconds:
        timer = threading.Timer(seconds, stop.set)
        timer.daemon = True
        timer.start()
    try:
        engine.run(stop)
    except KeyboardInterrupt:
        stop.set()
        print("\ninterrupted", file=sys.stderr)


def cmd_serve(args):
    from .server import BridgeServer

    hub = Hub()
    engine, com = _build_engine(hub, args.throttle)
    try:
        server = BridgeServer(hub, args.host, args.port, com)
    except OSError as exc:
        log.error("cannot listen on %s:%s (%s). Is another bridge already running?",
                  args.host, args.port, exc)
        return 2
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    print("calc-rtd bridge %s (%d-bit Python) listening on %s:%s - COM %s. Ctrl+C to stop."
          % (__version__, bits, server.address[0], server.address[1],
             "enabled" if com else "disabled"), flush=True)
    try:
        _run(engine, threading.Event())
    finally:
        server.close()
    return 0


class _Printer:
    """A fake client that prints what Calc would receive."""

    def __init__(self, labels):
        self.labels = labels
        self.counts = dict.fromkeys(labels, 0)
        self.errors = 0

    def send(self, message):
        stamp = time.strftime("%H:%M:%S")
        if message["op"] == "update":
            for client_id, value in message["values"]:
                self.counts[client_id] += 1
                print("%s  %-40s %r" % (stamp, self.labels[client_id], value), flush=True)
        elif message["op"] == "error":
            self.errors += 1
            print("%s  %-40s ERROR: %s" % (stamp, self.labels.get(message.get("id"), "?"),
                                          message["message"]), flush=True)


def _requests(args):
    formulas = list(args.formula)
    if args.file:
        with open(args.file, encoding="utf-8-sig") as handle:
            formulas += [line.strip() for line in handle if line.strip() and not line.startswith("#")]
    requests = [parse_formula(text) for text in formulas]
    if args.progid:
        if not args.topics:
            raise ValueError("--progid needs --topics")
        requests.append((args.progid, args.server, args.topics))
    if not requests:
        raise ValueError("nothing to monitor: give a formula, --file or --progid/--topics")
    return requests


def cmd_monitor(args):
    try:
        requests = _requests(args)
    except (OSError, ValueError) as exc:
        log.error("%s", exc)
        return 2
    hub = Hub()
    engine, _ = _build_engine(hub, args.throttle)
    labels = {i: "%s | %s" % (progid, ";".join(topics))
              for i, (progid, _, topics) in enumerate(requests, 1)}
    printer = _Printer(labels)
    for i, (progid, server, topics) in enumerate(requests, 1):
        hub.subscribe(printer, i, progid, server, topics)
    print("monitoring %d topic(s)%s. Ctrl+C to stop." %
          (len(requests), " for %ss" % args.seconds if args.seconds else ""), flush=True)
    _run(engine, threading.Event(), args.seconds)
    print("\nupdates received:")
    for i, label in labels.items():
        print("  %6d  %s" % (printer.counts[i], label))
    return 1 if printer.errors or not any(printer.counts.values()) else 0


def cmd_watch(args):
    from .client import BridgeClient

    try:
        requests = _requests(args)
        client = BridgeClient(args.host, args.port)
    except (OSError, ValueError) as exc:
        log.error("%s", exc)
        return 2
    labels = {}
    with client:
        print("connected: %s" % client.hello)
        for progid, server, topics in requests:
            labels[client.subscribe(progid, server, topics)] = "%s | %s" % (progid, ";".join(topics))
        printer = _Printer(labels)
        deadline = time.monotonic() + args.seconds if args.seconds else None
        try:
            while deadline is None or time.monotonic() < deadline:
                message = client.read(timeout=0.5)
                if message is not None:
                    printer.send(message)
        except (EOFError, KeyboardInterrupt) as exc:
            print("\nstopped: %s" % (exc or "interrupted"), file=sys.stderr)
    return 0


def _add_topic_args(parser):
    parser.add_argument("formula", nargs="*",
                        help='RTD formula as typed in Excel, e.g. "=RTD(my.progid;;ACME;LAST)"')
    parser.add_argument("-f", "--file", help="text file with one RTD formula per line")
    parser.add_argument("--progid", help="RTD server ProgID (alternative to a formula)")
    parser.add_argument("--server", default="", help="remote machine name (usually empty)")
    parser.add_argument("--topics", nargs="+", help="topic strings for --progid")
    parser.add_argument("--seconds", type=float, default=0, help="stop after this many seconds")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="calc-rtd-bridge",
        description="Hosts Excel RTD servers and feeds their data to LibreOffice Calc.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--throttle", type=int, default=500, metavar="MS",
                        help="minimum interval between RefreshData calls (default 500; Excel uses 2000)")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the bridge for the Calc extension")
    serve.add_argument("--host", default=DEFAULT_HOST)
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.set_defaults(func=cmd_serve)

    monitor = sub.add_parser(
        "monitor", help="load an RTD server directly and print its updates (no Calc needed)")
    _add_topic_args(monitor)
    monitor.set_defaults(func=cmd_monitor)

    watch = sub.add_parser("watch", help="subscribe through a running bridge and print updates")
    _add_topic_args(watch)
    watch.add_argument("--host", default=DEFAULT_HOST)
    watch.add_argument("--port", type=int, default=DEFAULT_PORT)
    watch.set_defaults(func=cmd_watch)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
