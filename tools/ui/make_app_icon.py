"""Superseded by tools/ui/make_ican_icon.py (each icon size drawn on its own, so "ICAN" stays readable in the
Windows taskbar). Kept as an alias: running it runs the new script."""
from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("make_ican_icon.py")), run_name="__main__")
