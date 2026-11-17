"""calc-rtd bridge: serves RTD data to the LibreOffice Calc extension.

The RTD client itself lives in the ``calc_rtd`` package.
"""

from calc_rtd import __version__

__all__ = ["__version__"]
