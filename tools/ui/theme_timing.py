"""Measure how long a light <-> dark theme switch takes on a populated main window.

    uv run python tools/ui/theme_timing.py
"""
from __future__ import annotations

import cProfile
import pstats
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp(prefix="autosim-theme-"))
    from autosim.context import AppContext
    from autosim.hardware.esp32_gateway.manager import NodeManager
    from autosim.ui.main_window import MainWindow
    from autosim.ui.theme import apply_theme
    NodeManager.scan = lambda self: []
    app = QApplication(sys.argv)
    apply_theme(app, "dark")
    ctx = AppContext()
    ctx.start()
    win = MainWindow(ctx)
    win.resize(1366, 768)
    win.show()
    win.dbc.load_file(str(ROOT / "tests" / "data" / "real_dbc" / "hyundai_canfd.dbc"))
    expand = "--expanded" in sys.argv                  # default: signal rows collapsed (as in normal use)
    for m in [m for m in ctx.can.registry.messages() if m.length == 32][:3]:
        win.signals_panel.add_message(m.name).set_all_expanded(expand)
    for _ in range(30):
        app.processEvents()
    times = []
    prof = cProfile.Profile()
    for i, mode in enumerate(("light", "dark", "light", "dark")):
        t0 = time.perf_counter()
        if i == 2:
            prof.enable()
        win.set_theme(mode)
        app.processEvents()                       # include the repaint
        if i == 2:
            prof.disable()
        times.append(round((time.perf_counter() - t0) * 1000))
    print("switch ms:", times, "widgets:", len(win.findChildren(object)))
    pstats.Stats(prof).sort_stats("cumulative").print_stats(14)
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
