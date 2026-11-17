"""``python -m calc_rtd "=RTD(...)"``: print the updates of RTD topics."""

import argparse
import logging
import sys

from . import __version__, monitor


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m calc_rtd",
        description="Load Excel RTD (RealTimeData) servers and print the updates they send.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--throttle", type=int, default=500, metavar="MS",
                        help="minimum interval between refreshes (default 500; Excel uses 2000)")
    monitor.add_topic_arguments(parser)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    return monitor.run(args)


if __name__ == "__main__":
    sys.exit(main())
