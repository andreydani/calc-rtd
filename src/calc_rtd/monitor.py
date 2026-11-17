"""``monitor``: load RTD servers directly and print what they send."""

from __future__ import annotations

import sys
import time

from .formula import parse_formula
from .session import RtdSession


def add_topic_arguments(parser):
    parser.add_argument("formula", nargs="*",
                        help='RTD formula as typed in a spreadsheet, e.g. "=RTD(my.progid;;ACME;LAST)"')
    parser.add_argument("-f", "--file", help="text file with one RTD formula per line")
    parser.add_argument("--progid", help="RTD server ProgID (alternative to a formula)")
    parser.add_argument("--server", default="", help="remote machine name (usually empty)")
    parser.add_argument("--topics", nargs="+", help="topic strings for --progid")
    parser.add_argument("--seconds", type=float, default=0, help="stop after this many seconds")


def requests_from(args):
    """[(progid, server, topics), ...] from parsed command-line arguments."""
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


def label(progid, topics):
    return "%s | %s" % (progid, ";".join(topics))


def print_line(text, value=None, error=None):
    stamp = time.strftime("%H:%M:%S")
    if error is not None:
        print("%s  %-40s ERROR: %s" % (stamp, text, error), flush=True)
    else:
        print("%s  %-40s %r" % (stamp, text, value), flush=True)


def run(args) -> int:
    """Entry point shared by ``python -m calc_rtd`` and ``calc-rtd-bridge monitor``."""
    try:
        requests = requests_from(args)
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    counts, errors = {}, 0
    deadline = time.monotonic() + args.seconds if args.seconds else None
    with RtdSession(throttle=args.throttle / 1000.0) as session:
        for progid, server, topics in requests:
            counts[session.subscribe(progid, topics, server)] = 0
        print("monitoring %d topic(s)%s. Ctrl+C to stop." %
              (len(requests), " for %ss" % args.seconds if args.seconds else ""), flush=True)
        try:
            while deadline is None or time.monotonic() < deadline:
                wait = 0.25 if deadline is None else max(0.0, min(0.25, deadline - time.monotonic()))
                update = session.get(timeout=wait)
                if update is None:
                    continue
                name = label(update.subscription.progid, update.subscription.topics)
                if update.error is not None:
                    errors += 1
                    print_line(name, error=update.error)
                else:
                    counts[update.subscription] += 1
                    print_line(name, update.value)
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
    print("\nupdates received:")
    for subscription, count in counts.items():
        print("  %6d  %s" % (count, label(subscription.progid, subscription.topics)))
    return 1 if errors or not any(counts.values()) else 0
