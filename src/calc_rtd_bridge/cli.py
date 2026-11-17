"""Command line: ``calc-rtd-bridge serve | monitor | watch``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time

from calc_rtd import __version__, monitor
from calc_rtd.backends import Runtime
from calc_rtd.engine import Engine, Hub

from .protocol import DEFAULT_HOST, DEFAULT_PORT, load_or_create_token

log = logging.getLogger("calc_rtd")


def _env_port():
    try:
        return int(os.environ.get("CALC_RTD_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def cmd_serve(args):
    from .server import BridgeServer

    hub = Hub()
    runtime = Runtime(throttle=args.throttle / 1000.0)
    token = None if args.no_auth else load_or_create_token()
    try:
        server = BridgeServer(hub, args.host, args.port, runtime.com, token)
    except OSError as exc:
        log.error("cannot listen on %s:%s (%s). Is another bridge already running?",
                  args.host, args.port, exc)
        return 2
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    print("calc-rtd bridge %s (%d-bit Python) listening on %s:%s - COM %s. Ctrl+C to stop."
          % (__version__, bits, server.address[0], server.address[1],
             "enabled" if runtime.com else "disabled"), flush=True)
    stop = threading.Event()
    if args.idle_exit:
        def watchdog():
            while not stop.wait(1.0):
                if server.idle_seconds() >= args.idle_exit:
                    log.info("no clients for %ss; exiting", args.idle_exit)
                    stop.set()
        threading.Thread(target=watchdog, name="rtd-idle", daemon=True).start()
    # RTD servers are hosted on this (main) thread.
    runtime.enter()
    try:
        Engine(hub, runtime.backends, wait=runtime.wait).run(stop)
    except KeyboardInterrupt:
        stop.set()
        print("\ninterrupted", file=sys.stderr)
    finally:
        server.close()
        runtime.leave()
    return 0


def cmd_watch(args):
    from .client import BridgeClient

    try:
        requests = monitor.requests_from(args)
        client = BridgeClient(args.host, args.port)
    except (OSError, ValueError) as exc:
        log.error("%s", exc)
        return 2
    labels = {}
    with client:
        print("connected: %s" % client.hello)
        for progid, server, topics in requests:
            labels[client.subscribe(progid, server, topics)] = monitor.label(progid, topics)
        deadline = time.monotonic() + args.seconds if args.seconds else None
        try:
            while deadline is None or time.monotonic() < deadline:
                message = client.read(timeout=0.5)
                if message is None:
                    continue
                if message["op"] == "update":
                    for client_id, value in message["values"]:
                        monitor.print_line(labels.get(client_id, "?"), value)
                elif message["op"] == "error":
                    monitor.print_line(labels.get(message.get("id"), "?"), error=message["message"])
        except (EOFError, KeyboardInterrupt) as exc:
            print("\nstopped: %s" % (exc or "interrupted"), file=sys.stderr)
    return 0


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
    serve.add_argument("--port", type=int, default=_env_port())
    serve.add_argument("--idle-exit", type=float, default=0, metavar="SECONDS",
                       help="exit after this long without any connected client (used when the "
                            "Calc extension starts the bridge itself)")
    serve.add_argument("--no-auth", action="store_true",
                       help="accept any local connection (default: require the per-user token)")
    serve.set_defaults(func=cmd_serve)

    mon = sub.add_parser(
        "monitor", help="load an RTD server directly and print its updates (no Calc needed)")
    monitor.add_topic_arguments(mon)
    mon.set_defaults(func=monitor.run)

    watch = sub.add_parser("watch", help="subscribe through a running bridge and print updates")
    monitor.add_topic_arguments(watch)
    watch.add_argument("--host", default=DEFAULT_HOST)
    watch.add_argument("--port", type=int, default=_env_port())
    watch.set_defaults(func=cmd_watch)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
