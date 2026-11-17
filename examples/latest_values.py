"""Poll the latest values once a second instead of handling every update.

Each Subscription always holds its most recent value, so a program that
only needs "the current price" can ignore the stream of updates.

    python latest_values.py --seconds 5
"""

import argparse
import time

from calc_rtd import RtdError, RtdSession


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--progid", default="calcrtd.sim")
    parser.add_argument("--seconds", type=float, default=5)
    args = parser.parse_args()

    with RtdSession() as rtd:
        acme = rtd.subscribe(args.progid, ["walk", "ACME", 38.5])
        globex = rtd.subscribe(args.progid, ["walk", "GLOBEX", 112.4])
        try:
            acme.wait(timeout=10)      # block until the first value is in
            globex.wait(timeout=10)
        except (RtdError, TimeoutError) as exc:
            print("could not get a first value:", exc)
            return 1
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            print("ACME %8.2f   GLOBEX %8.2f   spread %8.2f" % (acme.value, globex.value,
                                                             globex.value - acme.value))
            time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
