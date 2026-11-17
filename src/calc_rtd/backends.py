"""Chooses the data sources available on this machine."""

from __future__ import annotations

import logging
import sys

from .host import RtdHostBackend
from .sim import SIM_PROGID, SimBackend

log = logging.getLogger("calc_rtd")


class Runtime:
    """What the engine thread needs: backends, a wait function and COM set-up."""

    def __init__(self, throttle=0.5, simulated=True):
        self.backends = [SimBackend()] if simulated else []
        self.wait = None
        self.com = False
        self._driver = None
        self._throttle = throttle
        if sys.platform != "win32":
            self.reason = "not running on Windows: only the simulated ProgID %r is available" % SIM_PROGID
            return
        try:
            from . import com_driver
        except ImportError as exc:
            self.reason = "COM support is disabled (%s). Install it with: pip install comtypes" % exc
            return
        self.reason = None
        self._driver = com_driver
        self.com = True

    def enter(self):
        """Call on the thread that will run the engine, before Engine.run."""
        if self._driver is None:
            log.warning("%s", self.reason)
            return
        self._driver.enter_apartment()
        if self._driver._is_elevated():
            log.warning("running as administrator: RTD servers fed by a non-elevated application "
                        "(the usual case) will fail to start. Prefer a normal prompt.")
        self.backends.append(RtdHostBackend(self._driver.ComDriver, throttle=self._throttle))
        self.wait = self._driver.pump

    def leave(self):
        """Call on the same thread after the engine has closed its backends."""
        if self._driver is not None:
            import gc

            self.backends = []
            gc.collect()  # release COM pointers before the apartment goes away
            self._driver.pump(0.05)  # let pending cross-apartment releases arrive
            self._driver.leave_apartment()
