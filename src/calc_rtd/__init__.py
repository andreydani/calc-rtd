"""calc-rtd: use Excel RTD (RealTimeData) servers outside Excel.

The public, stable API is what this module exports.  Everything else in the
package (engine, host, com_driver, ...) is internal and may change.
"""

__version__ = "0.2.0"

from .formula import parse_formula
from .session import RtdError, RtdSession, Subscription, Update
from .sim import SIM_PROGID

__all__ = ["RtdSession", "Subscription", "Update", "RtdError", "parse_formula", "SIM_PROGID",
           "__version__"]
