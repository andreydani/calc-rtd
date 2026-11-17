"""React to a value with a callback, here: warn when a price leaves a band.

Callbacks run on the session's own thread, so keep them short. Anything
slow (sending an e-mail, writing to a database) should be handed to another
thread or a queue.

    python alert_callback.py --low 38.45 --high 38.55 --seconds 10
"""

import argparse

from calc_rtd import RtdSession


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--low", type=float, default=38.45)
    parser.add_argument("--high", type=float, default=38.55)
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()

    seen = []

    def check(update):
        if update.error:
            print("ERROR", update.error)
            return
        seen.append(update.value)
        if not args.low <= update.value <= args.high:
            print("ALERT: ACME at %.2f is outside %.2f - %.2f" % (update.value, args.low, args.high))

    with RtdSession() as rtd:
        rtd.subscribe("calcrtd.sim", ["walk", "ACME", 38.5], callback=check)
        rtd.run_for(args.seconds)
    print("%d updates checked" % len(seen))
    return 0 if seen else 1


if __name__ == "__main__":
    raise SystemExit(main())
