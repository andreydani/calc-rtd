"""Simulated RTD source, available on every platform under ProgID ``calcrtd.sim``.

Lets you exercise the Calc extension and the bridge without any real RTD
server:

    =RTD("calcrtd.sim";;"clock")             current time, every second
    =RTD("calcrtd.sim";;"counter")           1, 2, 3 ... every second
    =RTD("calcrtd.sim";;"counter";0.2)       ... every 0.2 s
    =RTD("calcrtd.sim";;"random";10;20)      uniform number in [10, 20]
    =RTD("calcrtd.sim";;"walk";"ACME";38.5) random walk starting at 38.5
    =RTD("calcrtd.sim";;"echo";"hello")      static text
"""

from __future__ import annotations

import random
import time

from .core import Backend
from .protocol import SIM_PROGID


def _number(args, index, default):
    try:
        return float(str(args[index]).replace(",", "."))
    except (IndexError, ValueError):
        return default


class _Feed:
    def __init__(self, topic):
        args = topic.topics
        self.kind = str(args[0]).strip().lower()
        self.interval = 1.0
        self.due = 0.0
        self.state = 0.0
        self.args = args
        if self.kind == "counter":
            self.interval = max(0.05, _number(args, 1, 1.0))
        elif self.kind == "random":
            self.lo, self.hi = _number(args, 1, 0.0), _number(args, 2, 1.0)
        elif self.kind == "walk":
            self.state = _number(args, 2, 100.0)
            self.interval = 0.5
        elif self.kind == "echo":
            self.interval = None
        elif self.kind != "clock":
            raise ValueError("unknown simulated topic %r (use clock, counter, random, walk or echo)" % args[0])

    def next(self):
        if self.kind == "clock":
            return time.strftime("%H:%M:%S")
        if self.kind == "counter":
            self.state += 1
            return int(self.state)
        if self.kind == "random":
            return round(random.uniform(self.lo, self.hi), 4)
        if self.kind == "walk":
            self.state = round(max(0.01, self.state * (1 + random.gauss(0, 0.001))), 2)
            return self.state
        return str(self.args[1]) if len(self.args) > 1 else ""


class SimBackend(Backend):
    def __init__(self):
        self._feeds = {}

    def handles(self, progid):
        return progid.strip().lower() == SIM_PROGID

    def connect(self, topic, hub):
        feed = _Feed(topic)
        self._feeds[topic.id] = feed
        hub.publish([(topic.id, feed.next())])
        if feed.interval is not None:
            feed.due = time.monotonic() + feed.interval

    def disconnect(self, topic):
        self._feeds.pop(topic.id, None)

    def tick(self, now, hub):
        pairs = []
        for topic_id, feed in self._feeds.items():
            if feed.interval is not None and now >= feed.due:
                feed.due = now + feed.interval
                pairs.append((topic_id, feed.next()))
        if pairs:
            hub.publish(pairs)
