#!/usr/bin/env python3
"""Launcher for the cross-platform GPU Monitor tray app.

    python run.py              # start the tray monitor
    python run.py --install    # add to login items / startup folder
    python run.py --uninstall  # remove it again
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from desktop.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
