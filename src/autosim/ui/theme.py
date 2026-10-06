"""ThemeManager: runtime switching between the light and dark design systems (styles/themes.py).

    ThemeManager.instance().apply(app, "dark" | "light" | "auto")
    ThemeManager.instance().changed.connect(widget_refresh)      # repaint custom-painted widgets

Module-level helpers (`is_dark()`, `palette()`, `badge_style()`, ...) read the current theme so
custom-painted widgets and models pick the right colours.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette

from autosim.ui.styles.themes import DARK, FONT_MONO, FONT_UI, LIGHT, build_qss  # noqa: F401

MODES = ("dark", "light", "auto")


class ThemeManager(QObject):
    changed = Signal(bool)                      # True = dark
    _instance: "ThemeManager | None" = None

    def __init__(self) -> None:
        super().__init__()
        self.mode = "dark"
        self.dark = True

    @classmethod
    def instance(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def palette(self) -> dict[str, str]:
        return DARK if self.dark else LIGHT

    def apply(self, app, mode: str = "dark") -> bool:
        from autosim.ui.icons import build_qss_assets
        if mode not in MODES:
            mode = "dark"
        self.mode = mode
        self.dark = _resolve(mode)
        pal = self.palette
        app.setStyle("Fusion")
        app.setPalette(_qt_palette(pal))
        app.setFont(ui_font())
        app.setStyleSheet(build_qss(pal, build_qss_assets(pal["MUTED"])))
        self.changed.emit(self.dark)
        return self.dark


def _resolve(mode: str) -> bool:
    if mode == "auto":
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QGuiApplication
            return QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light
        except Exception:
            return True
    return mode == "dark"


def _qt_palette(pal: dict[str, str]) -> QPalette:
    """Native palette matching the stylesheet (used by Fusion for anything QSS does not cover)."""
    p = QPalette()
    c = lambda k: QColor(pal[k])  # noqa: E731
    for role, key in ((QPalette.Window, "BASE"), (QPalette.Base, "INPUT"), (QPalette.AlternateBase, "ROW_ALT"),
                      (QPalette.Button, "BTN"), (QPalette.ToolTipBase, "SURFACE"), (QPalette.Text, "TEXT"),
                      (QPalette.WindowText, "TEXT"), (QPalette.ButtonText, "BTN_TEXT"), (QPalette.ToolTipText, "TEXT"),
                      (QPalette.BrightText, "DANGER_TEXT"), (QPalette.Highlight, "PRIMARY"),
                      (QPalette.HighlightedText, "PRIMARY_TEXT"), (QPalette.Link, "ACCENT"),
                      (QPalette.PlaceholderText, "MUTED")):
        p.setColor(role, c(key))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c("DISABLED"))
    return p


# ---- convenience API used across the UI ------------------------------------------------------------
def apply_theme(app, mode: str = "dark") -> bool:
    """Apply `mode` ('dark' | 'light' | 'auto'); returns True if the applied theme is dark."""
    return ThemeManager.instance().apply(app, mode)


def is_dark(app=None) -> bool:
    return ThemeManager.instance().dark


def palette() -> dict[str, str]:
    return ThemeManager.instance().palette


def accent() -> str:
    return palette()["ACCENT"]


def tx_color() -> str:
    """Direction column colour for transmitted frames."""
    return "#38BDF8" if is_dark() else "#0B5CAD"


def id_color() -> str:
    return "#7DD3FC" if is_dark() else "#0369A1"        # soft cyan


def data_color() -> str:
    return "#FDBA74" if is_dark() else "#C2410C"        # soft orange


def icon_color() -> str:
    return "#A1A4B3" if is_dark() else "#475569"


def empty_icon_color() -> str:
    return "#4A4C57" if is_dark() else "#94A3B8"


def state_color(state: str) -> str | None:
    """Text colour of a node state in tables (running / error / transitional)."""
    p = palette()
    return {"running": p["SUCCESS"], "error": p["DANGER_TEXT"], "hello": p["WARNING"],
            "configured": p["WARNING"]}.get(state)


# Status pill styles: (text, background, border, dot); RGBA tuples for the translucent parts.
_BADGE = {
    True: {   # dark
        "offline": ("#8F92A1", (26, 27, 32, 255), (39, 39, 43, 255), "#EF4444"),
        "error": ("#F87171", (239, 68, 68, 38), (239, 68, 68, 110), "#EF4444"),
        "connecting": ("#FBBF24", (245, 158, 11, 38), (245, 158, 11, 110), "#F59E0B"),
        "online": ("#34D399", (16, 185, 129, 38), (16, 185, 129, 140), "#10B981"),
    },
    False: {  # light
        "offline": ("#64748B", (241, 245, 249, 255), (226, 232, 240, 255), "#EF4444"),
        "error": ("#DC2626", (254, 242, 242, 255), (252, 165, 165, 255), "#EF4444"),
        "connecting": ("#B45309", (245, 158, 11, 30), (245, 158, 11, 120), "#F59E0B"),
        "online": ("#047857", (16, 185, 129, 30), (16, 185, 129, 140), "#10B981"),
    },
}


def badge_style(state: str, dark: bool | None = None) -> tuple[str, tuple, tuple, str]:
    d = is_dark() if dark is None else dark
    return _BADGE[d].get(state, _BADGE[d]["offline"])


def mono_font():
    from PySide6.QtGui import QFont
    f = QFont()
    f.setFamilies(["JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New"])
    f.setStyleHint(QFont.Monospace)
    f.setPointSizeF(9.5)
    return f


def ui_font():
    from PySide6.QtGui import QFont
    f = QFont()
    f.setFamilies(["Inter", "Segoe UI", "Roboto", "Arial"])
    f.setPointSizeF(9.5)
    return f
