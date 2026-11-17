"""Print every update of a few RTD topics.

    python print_updates.py
    python print_updates.py "=RTD(Vendor.ProgID;;ACME;LAST)" --seconds 30

Without arguments it uses the built-in simulated source, so it runs anywhere.
"""

import argparse
import time

from calc_rtd import RtdSession

DEFAULT = ['=RTD("calcrtd.sim";;"walk";"ACME";38.5)', '=RTD("calcrtd.sim";;"clock")']


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("formula", nargs="*", default=DEFAULT)
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()

    with RtdSession() as rtd:
        for formula in args.formula:
            rtd.subscribe_formula(formula)
        received = 0
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            update = rtd.get(timeout=0.5)
            if update is None:
                continue
            received += 1
            name = " ".join(update.subscription.topics)
            if update.error:
                print("%-20s ERROR %s" % (name, update.error))
            else:
                print("%-20s %s" % (name, update.value))
    return 0 if received else 1


if __name__ == "__main__":
    raise SystemExit(main())
