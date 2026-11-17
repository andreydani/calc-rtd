"""Append every update to a CSV file: time, topic, value.

    python log_to_csv.py quotes.csv
    python log_to_csv.py quotes.csv "=RTD(Vendor.ProgID;;ACME;LAST)" --seconds 3600
"""

import argparse
import csv
import datetime
import time

from calc_rtd import RtdSession

DEFAULT = ['=RTD("calcrtd.sim";;"walk";"ACME";38.5)', '=RTD("calcrtd.sim";;"walk";"GLOBEX";112.4)']


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path")
    parser.add_argument("formula", nargs="*", default=DEFAULT)
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()

    rows = 0
    deadline = time.monotonic() + args.seconds
    with open(args.path, "a", newline="", encoding="utf-8") as handle, RtdSession() as rtd:
        writer = csv.writer(handle)
        for formula in args.formula:
            rtd.subscribe_formula(formula)
        while time.monotonic() < deadline:
            update = rtd.get(timeout=0.5)
            if update is None or update.error:
                continue
            writer.writerow([datetime.datetime.now().isoformat(timespec="milliseconds"),
                             "/".join(update.subscription.topics), update.value])
            rows += 1
    print("wrote %d rows to %s" % (rows, args.path))
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
