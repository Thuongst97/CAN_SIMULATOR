"""ThemeManager: runtime switching between the light and dark design systems (styles/tokens.py + themes.py).

    ThemeManager.instance().apply(app, "dark" | "light" | "auto")
    ThemeManager.instance().changed.connect(widget_refresh)      # repaint custom-painted widgets

Module-level helpers (`palette()`, `token()`, `badge_style()`, `ui_font()` ...) read the current theme so
custom-painted widgets, item models and plots pick the right colours and fonts.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QAbstractButton

from autosim.ui.styles import tokens as T
from autosim.ui.styles.themes import DARK, FONT_UI, LIGHT, build_qss  # noqa: F401

MODES = ("dark", "light", "auto")
FONT_DIR = Path(__file__).parent / "assets" / "fonts"
_fonts_loaded: list[str] | None = None


def load_fonts() -> list[str]:
    """Register the bundled Inter files once; returns the families now available."""
    global _fonts_loaded
    if _fonts_loaded is None:
        fams: set[str] = set()
        for f in sorted(FONT_DIR.glob("*.ttf")):
            fid = QFontDatabase.addApplicationFont(str(f))
            if fid >= 0:
                fams.update(QFontDatabase.applicationFontFamilies(fid))
        _fonts_loaded = sorted(fams)
    return _fonts_loaded


class _ButtonFocusPolicy(QObject):
    """Buttons take focus from the keyboard (Tab) only, so the focus ring marks keyboard navigation and a mouse
    click on a button does not pull focus out of the field being edited."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Polish and isinstance(obj, QAbstractButton) and obj.focusPolicy() == Qt.StrongFocus:
            obj.setFocusPolicy(Qt.TabFocus)
        return False


class ThemeManager(QObject):
    changed = Signal(bool)                      # True = dark
    _instance: "ThemeManager | None" = None

    def __init__(self) -> None:
        super().__init__()
        self.mode = "dark"
        self.dark = True
        self._focus_filter: _ButtonFocusPolicy | None = None

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
        load_fonts()
        if self._focus_filter is None:
            self._focus_filter = _ButtonFocusPolicy(app)
            app.installEventFilter(self._focus_filter)
            for w in app.allWidgets():                         # buttons created before the first apply()
                if isinstance(w, QAbstractButton) and w.focusPolicy() == Qt.StrongFocus:
                    w.setFocusPolicy(Qt.TabFocus)
        app.setStyle("Fusion")
        app.setPalette(_qt_palette(pal))
        app.setFont(ui_font())
        app.setStyleSheet(build_qss(pal, build_qss_assets(pal["TEXT_MUTED"])))
        self.changed.emit(self.dark)
        return self.dark


def _resolve(mode: str) -> bool:
    if mode == "auto":
        try:
            from PySide6.QtGui import QGuiApplication
            return QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light
        except Exception:
            return True
    return mode == "dark"


def _qt_palette(pal: dict[str, str]) -> QPalette:
    """Native palette matching the stylesheet (used by Fusion for anything QSS does not cover)."""
    p = QPalette()
    c = lambda k: QColor(pal[k])  # noqa: E731
    for role, key in ((QPalette.Window, "SURFACE_0"), (QPalette.Base, "SURFACE_1"), (QPalette.AlternateBase, "ROW_ALT"),
                      (QPalette.Button, "SURFACE_2"), (QPalette.ToolTipBase, "SURFACE_3"), (QPalette.Text, "TEXT_PRIMARY"),
                      (QPalette.WindowText, "TEXT_PRIMARY"), (QPalette.ButtonText, "TEXT_PRIMARY"),
                      (QPalette.ToolTipText, "TEXT_PRIMARY"), (QPalette.BrightText, "ERROR"),
                      (QPalette.Highlight, "ACCENT_STRONG"), (QPalette.HighlightedText, "ON_ACCENT"),
                      (QPalette.Link, "ACCENT"), (QPalette.PlaceholderText, "TEXT_MUTED")):
        p.setColor(role, c(key))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c("TEXT_DISABLED"))
    return p


# ---- convenience API used across the UI ------------------------------------------------------------
def apply_theme(app, mode: str = "dark") -> bool:
    """Apply `mode` ('dark' | 'light' | 'auto'); returns True if the applied theme is dark."""
    return ThemeManager.instance().apply(app, mode)


def is_dark(app=None) -> bool:
    return ThemeManager.instance().dark


def palette() -> dict[str, str]:
    return ThemeManager.instance().palette


def token(name: str) -> str:
    """One colour token of the active theme, e.g. token("TEXT_SECONDARY")."""
    return palette()[name]


def accent() -> str:
    return token("ACCENT")


def tx_color() -> str:
    return token("TX")


def rx_color() -> str:
    return token("RX")


def id_color() -> str:
    """IDs are neutral (colour is reserved for meaning)."""
    return token("TEXT_PRIMARY")


def data_color() -> str:
    """Payload bytes are neutral text, never red / orange (that would read as an error)."""
    return token("TEXT_PRIMARY")


def icon_color() -> str:
    return token("TEXT_SECONDARY")


def empty_icon_color() -> str:
    return token("TEXT_DISABLED")


def series_colors() -> list[str]:
    return T.series(is_dark())


def state_color(state: str) -> str | None:
    """Text colour of a node state in tables (running / error / transitional)."""
    return {"running": token("SUCCESS"), "error": token("ERROR"), "hello": token("WARNING"),
            "configured": token("WARNING")}.get(state)


def _rgba(hex_color: str, alpha: int) -> tuple[int, int, int, int]:
    c = QColor(hex_color)
    return (c.red(), c.green(), c.blue(), alpha)


def badge_style(state: str, dark: bool | None = None) -> tuple[str, tuple, tuple, str]:
    """Status pill: (text colour, background RGBA, border RGBA, dot colour) from the semantic tokens."""
    t = T.colors(is_dark() if dark is None else dark)
    soft, edge = (38, 120) if (is_dark() if dark is None else dark) else (26, 130)
    semantic = {"online": "SUCCESS", "connecting": "WARNING", "error": "ERROR"}.get(state)
    if semantic is None:                                       # offline: neutral pill, muted dot (red = errors only)
        return (t["TEXT_SECONDARY"], _rgba(t["SURFACE_2"], 255), _rgba(t["BORDER_STRONG"], 255), t["TEXT_MUTED"])
    col = t[semantic]
    return (col, _rgba(col, soft), _rgba(col, edge), col)


def _font(families: list[str], kind: str) -> QFont:
    px, weight = T.TYPE[kind]
    f = QFont()
    f.setFamilies(families)
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(weight))
    return f


def ui_font(kind: str = "body") -> QFont:
    """Inter at the given type-scale step, with tabular figures (digits of equal width line up in columns)."""
    f = _font(T.FONT_UI_FAMILIES, kind)
    f.setFeature(QFont.Tag("tnum"), 1)                         # Qt >= 6.7 (PySide6 6.11 here)
    return f


def font_report() -> tuple[bool, str]:
    """(ok, text) describing which fonts the app actually resolved - logged at startup."""
    from PySide6.QtGui import QFontInfo
    from PySide6.QtWidgets import QApplication
    loaded = load_fonts()
    app_font = QApplication.instance().font()
    info = QFontInfo(app_font)
    heads = QFontInfo(ui_font("panel_title"))
    ok = info.family() == "Inter" and "Inter" in loaded
    files = sorted(p.name for p in FONT_DIR.glob("*.ttf"))
    text = (f"Fonts: UI text resolves to '{info.family()}' {info.styleName()} {app_font.pixelSize()} px, headers "
            f"'{heads.family()}' {heads.styleName()} {ui_font('panel_title').pixelSize()} px; bundled families "
            f"registered: {', '.join(loaded) or 'none'} ({len(files)} files in {FONT_DIR})")
    if not ok:
        text += " - WARNING: Inter not in use, the UI falls back to a system font"
    return ok, text


def data_font() -> QFont:
    """Font for IDs, payload bytes, timestamps and counters: the same Inter body font (tabular figures)."""
    return ui_font("body")
