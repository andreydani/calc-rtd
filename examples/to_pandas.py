"""Collect updates for a while and turn them into a pandas DataFrame.

    pip install pandas
    python to_pandas.py --seconds 5
"""

import argparse
import datetime
import time

import pandas as pd

from calc_rtd import RtdSession


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=5)
    args = parser.parse_args()

    rows = []
    deadline = time.monotonic() + args.seconds
    with RtdSession() as rtd:
        for symbol, start in (("ACME", 38.5), ("GLOBEX", 112.4)):
            rtd.subscribe("calcrtd.sim", ["walk", symbol, start])
        while time.monotonic() < deadline:
            update = rtd.get(timeout=0.5)
            if update is not None and not update.error:
                rows.append({"time": datetime.datetime.now(),
                             "symbol": update.subscription.topics[1],
                             "price": update.value})

    frame = pd.DataFrame(rows)
    if frame.empty:
        return 1
    # One column per symbol, one row per second, last price of each second.
    table = (frame.pivot_table(index="time", columns="symbol", values="price", aggfunc="last")
                  .resample("1s").last().ffill())
    print(table.tail())
    print("\n%d updates, %d seconds" % (len(frame), len(table)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
