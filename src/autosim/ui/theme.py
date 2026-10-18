"""ThemeManager: runtime switching between the light and dark design systems (styles/tokens.py + themes.py).

    ThemeManager.instance().apply(app, "dark" | "light" | "auto")
    ThemeManager.instance().changed.connect(widget_refresh)      # repaint custom-painted widgets

Module-level helpers (`palette()`, `token()`, `badge_style()`, `ui_font()` ...) read the current theme so
custom-painted widgets, item models and plots pick the right colours and fonts.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (QAbstractButton, QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QApplication,
                               QComboBox, QScrollBar, QWidget)

from autosim.ui.styles import tokens as T
from autosim.ui.styles.themes import DARK, FONT_UI, LIGHT, build_qss  # noqa: F401

MODES = ("dark", "light", "auto")
UI_FAMILY = T.FONT_UI_FAMILIES[0]


class _InputPolicy(QObject):
    """Application-wide input rules, installed once by ThemeManager.apply():

    * buttons take focus from the keyboard (Tab) only, so the focus ring marks keyboard navigation and a mouse
      click on a button does not pull focus out of the field being edited;
    * the mouse wheel never changes a value (spin box, combo box, slider): it scrolls the panel underneath, so
      scrolling through the Signals panel cannot silently modify a signal.
    """

    VALUE_WIDGETS = (QAbstractSpinBox, QComboBox, QAbstractSlider)
    _forwarding = False

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Polish and isinstance(obj, QAbstractButton) and obj.focusPolicy() == Qt.StrongFocus:
            obj.setFocusPolicy(Qt.TabFocus)
        elif t == QEvent.Wheel and isinstance(obj, QWidget) and not self._forwarding:
            if isinstance(obj, QScrollBar):                          # scroll bars keep their wheel
                return False
            target = obj
            while target is not None and not isinstance(target, self.VALUE_WIDGETS):
                if isinstance(target, QAbstractScrollArea):          # an ordinary scrollable view: leave it
                    return False
                target = target.parentWidget()
            if target is None or (isinstance(target, QComboBox) and target.view().isVisible()):
                return False
            area = target.parentWidget()
            while area is not None and not isinstance(area, QAbstractScrollArea):
                area = area.parentWidget()
            if area is not None:                                     # scroll the panel instead
                bar = area.verticalScrollBar() if ev.angleDelta().y() else area.horizontalScrollBar()
                self._forwarding = True
                try:
                    QApplication.sendEvent(bar, ev)
                finally:
                    self._forwarding = False
            return True
        return False


class ThemeManager(QObject):
    changed = Signal(bool)                      # True = dark
    _instance: "ThemeManager | None" = None

    def __init__(self) -> None:
        super().__init__()
        self.mode = "dark"
        self.dark = True
        self._focus_filter: _InputPolicy | None = None
        self._app_filters: list[QObject] = []           # app-wide event filters, paused during a theme switch
        self._qss_cache: dict[bool, str] = {}
        self._style_set = False

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
        if self._focus_filter is None:
            self._focus_filter = _InputPolicy(app)
            self.install_app_filter(self._focus_filter)
            for w in app.allWidgets():                         # buttons created before the first apply()
                if isinstance(w, QAbstractButton) and w.focusPolicy() == Qt.StrongFocus:
                    w.setFocusPolicy(Qt.TabFocus)
        qss = self._qss_cache.get(self.dark)
        if qss is None:                                        # built (and its PNG assets painted) once per theme
            qss = self._qss_cache[self.dark] = build_qss(pal, build_qss_assets(pal["TEXT_MUTED"]))
        # A theme switch restyles every widget: thousands of polish / palette events. Python event filters on the
        # application would see each of them, so they are paused meanwhile; the Qt style is only set once.
        for f in self._app_filters:
            app.removeEventFilter(f)
        frozen = [w for w in app.topLevelWidgets() if w.isVisible() and w.updatesEnabled()]
        for w in frozen:                                       # no half-styled frame on screen
            w.setUpdatesEnabled(False)
        try:
            if not self._style_set:                            # the Qt style never changes: set it once
                app.setStyle("Fusion")
                self._style_set = True
            if app.font() != ui_font():
                app.setFont(ui_font())
            app.setPalette(_qt_palette(pal))
            # Replacing one stylesheet by another makes Qt diff every widget's old and new rules (~4x slower
            # than clearing first and applying the new sheet to unstyled widgets: ~100 ms vs ~380 ms here).
            if app.styleSheet():
                app.setStyleSheet("")
            app.setStyleSheet(qss)
        finally:
            for f in self._app_filters:
                app.installEventFilter(f)
            for w in frozen:
                w.setUpdatesEnabled(True)
        self.changed.emit(self.dark)
        return self.dark

    def install_app_filter(self, f: QObject) -> None:
        """Install an application-wide event filter (paused automatically during theme switches)."""
        from PySide6.QtWidgets import QApplication
        if f not in self._app_filters:
            self._app_filters.append(f)
            QApplication.instance().installEventFilter(f)

    def remove_app_filter(self, f: QObject) -> None:
        from PySide6.QtWidgets import QApplication
        if f in self._app_filters:
            self._app_filters.remove(f)
        QApplication.instance().removeEventFilter(f)


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
    """Segoe UI at the given type-scale step (its digits are tabular: numbers line up in columns)."""
    return _font(T.FONT_UI_FAMILIES, kind)


def app_icon():
    """The app icon (CAN_H / CAN_L waveform) in every rendered size: window title bar, taskbar, Alt+Tab."""
    from pathlib import Path
    from PySide6.QtGui import QIcon
    icon = QIcon()
    for png in sorted((Path(__file__).parent / "assets" / "icon").glob("app_*.png")):
        icon.addFile(str(png))
    return icon


def font_report() -> tuple[bool, str]:
    """(ok, text) describing which fonts the app actually resolved - logged at startup."""
    from PySide6.QtGui import QFontInfo
    from PySide6.QtWidgets import QApplication
    app_font = QApplication.instance().font()
    info = QFontInfo(app_font)
    heads = QFontInfo(ui_font("panel_title"))
    ok = info.family() == UI_FAMILY
    text = (f"Fonts: UI text resolves to '{info.family()}' {info.styleName()} {app_font.pixelSize()} px, headers "
            f"'{heads.family()}' {heads.styleName()} {ui_font('panel_title').pixelSize()} px")
    if not ok:
        text += f" - WARNING: {UI_FAMILY} is not available, the UI falls back to another font"
    return ok, text


def data_font() -> QFont:
    """Font for IDs, payload bytes, timestamps and counters: the same body font (tabular digits)."""
    return ui_font("body")
