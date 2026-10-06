"""Application bootstrap."""
from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from autosim.context import AppContext
    from autosim.ui.main_window import MainWindow
    from autosim.ui.qt_loop import qt_thread_factory
    from autosim.ui.theme import apply_theme

    app = QApplication(sys.argv)
    app.setApplicationName("AutoSim Studio")
    from PySide6.QtCore import QSettings
    mode = str(QSettings("AutoSim", "AutoSimStudio").value("theme", "dark"))
    dark = apply_theme(app, mode)
    ctx = AppContext(qt_thread_factory)
    ctx.start()
    win = MainWindow(ctx)
    win.plot.apply_theme(dark)
    win.show()
    return app.exec()
