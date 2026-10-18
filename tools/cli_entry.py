"""Entry point for the console build (PyInstaller --console).

The GUI executable (``main.py``) is windowed, which means stdout is discarded on
Windows.  The batch/audit workflow therefore ships as a second, console-mode
executable built from this module.
"""

from __future__ import annotations

import sys


def main() -> int:
    from cli import main as cli_main

    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
