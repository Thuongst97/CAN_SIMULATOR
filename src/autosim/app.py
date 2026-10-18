"""Application bootstrap."""
from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from autosim.context import AppContext
    from autosim.ui.main_window import MainWindow
    from autosim.ui.theme import apply_theme

    if sys.platform == "win32":                 # own taskbar identity: our icon, not python.exe's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AutoSim.AutoSimStudio")
    app = QApplication(sys.argv)
    app.setApplicationName("AutoSim Studio")
    from autosim.ui.theme import app_icon
    app.setWindowIcon(app_icon())
    from autosim.ui.settings_store import app_settings
    mode = str(app_settings().value("theme", "dark"))
    dark = apply_theme(app, mode)
    ctx = AppContext()
    ctx.start()
    win = MainWindow(ctx)
    win.plot.apply_theme(dark)
    win.show()
    return app.exec()
