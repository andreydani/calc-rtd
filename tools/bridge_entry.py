"""Entry point for the standalone bridge executable (PyInstaller)."""

import sys

from calc_rtd_bridge.cli import main

if __name__ == "__main__":
    sys.exit(main())
