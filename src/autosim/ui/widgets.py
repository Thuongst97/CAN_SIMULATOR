"""Reusable widgets: status pill, empty-state card, titled panel card, styling helpers."""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QRadialGradient
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QSplitter, QToolButton, QVBoxLayout, QWidget)

from autosim.ui.styles.tokens import CONTROL_H, TYPE
from autosim.ui.theme import ThemeManager, badge_style, empty_icon_color, icon_color, token

CONTROL_HEIGHT = CONTROL_H


class StatusBadge(QWidget):
    """Pill badge. OFFLINE / STOPPED: neutral pill, muted text, red dot. CONNECTED / RUNNING: translucent
    emerald pill, green border and text, softly glowing green dot. Colours follow the active theme."""

    def __init__(self, text: str = "Offline", state: str = "offline", parent=None) -> None:
        super().__init__(parent)
        self._text, self._state = text, state
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._tick)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        ThemeManager.instance().changed.connect(self._theme_changed)
        self.set_state(state, text)

    def _theme_changed(self, _dark: bool) -> None:
        self.update()

    @property
    def state(self) -> str:
        return self._state

    @property
    def text(self) -> str:
        return self._text

    def set_state(self, state: str, text: str | None = None) -> None:
        self._state = state if state in ("offline", "error", "connecting", "online") else "offline"
        if text is not None:
            self._text = text
        if self._state in ("online", "connecting"):
            self._timer.start()
        else:
            self._timer.stop()
            self._phase = 0.0
        self.updateGeometry()
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.1) % (2 * math.pi)
        self.update()

    def _font(self) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(TYPE["body_strong"][0])
        f.setWeight(QFont.Weight(TYPE["body_strong"][1]))
        return f

    def sizeHint(self) -> QSize:
        return QSize(QFontMetrics(self._font()).horizontalAdvance(self._text) + 40, 24)

    def paintEvent(self, _e) -> None:
        text_c, bg, border, dot = badge_style(self._state)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QColor(*border))
        p.setBrush(QColor(*bg))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        c = QPointF(r.left() + 14, r.center().y())
        dot_c = QColor(dot)
        if self._state in ("online", "connecting"):          # soft glow
            pulse = 0.5 + 0.5 * math.sin(self._phase)
            g = QRadialGradient(c, 8)
            glow = QColor(dot_c)
            glow.setAlpha(int(60 + 90 * pulse))
            g.setColorAt(0.0, glow)
            glow.setAlpha(0)
            g.setColorAt(1.0, glow)
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, 8, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(dot_c)
        p.drawEllipse(c, 3.5, 3.5)
        p.setPen(QColor(text_c))
        p.setFont(self._font())
        p.drawText(QRectF(r.left() + 26, r.top(), r.width() - 30, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   self._text)


class EmptyState(QFrame):
    """Centered card: icon, title, subtitle and optional action buttons."""

    def __init__(self, icon: str, title: str, subtitle: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("empty-state")
        self.setMaximumWidth(520)
        self.setMinimumWidth(300)
        self._icon_name = icon
        self.icon = QLabel(alignment=Qt.AlignCenter)
        self.title = QLabel(title, alignment=Qt.AlignCenter)
        self.title.setProperty("role", "title")
        self.subtitle = QLabel(subtitle, alignment=Qt.AlignCenter)
        self.subtitle.setProperty("role", "muted")
        self.subtitle.setWordWrap(True)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(10)
        self.buttons.setAlignment(Qt.AlignCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 32, 32, 32)
        lay.setSpacing(10)
        lay.addWidget(self.icon)
        lay.addWidget(self.title)
        lay.addWidget(self.subtitle)
        lay.addSpacing(8)
        lay.addLayout(self.buttons)
        ThemeManager.instance().changed.connect(self._theme_changed)
        self.refresh_icon()

    def _theme_changed(self, _dark: bool) -> None:
        self.refresh_icon()

    def refresh_icon(self) -> None:
        from autosim.ui.icons import make_icon
        self.icon.setPixmap(make_icon(self._icon_name, empty_icon_color()).pixmap(56, 56))

    def set_text(self, title: str, subtitle: str) -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)

    def add_button(self, text: str, kind: str = "primary") -> QPushButton:
        b = QPushButton(text)
        set_kind(b, kind)
        b.setMinimumWidth(150)
        self.buttons.addWidget(b)
        return b


class PanelCard(QFrame):
    """Titled card for use inside a QSplitter. The header's pop-out button floats it into its own window
    (e.g. a second monitor); closing that window puts it back where it was.

    API mirrors the QDockWidget parts the app uses: isFloating(), setFloating(), visibilityChanged.
    """

    visibilityChanged = Signal(bool)

    def __init__(self, title: str, body: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("panel-card")
        self.title = title
        self.body = body
        self._home: tuple | None = None

        header = QWidget()
        header.setObjectName("panel-header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 4, 6, 4)
        label = QLabel(title)
        label.setObjectName("panel-title")
        self.pop_btn = QToolButton()
        self.pop_btn.setObjectName("panel-btn")
        self.pop_btn.setToolTip("Pop out into its own window (close the window to put it back)")
        self.pop_btn.clicked.connect(self._toggle_float)
        hl.addWidget(label)
        hl.addStretch()
        hl.addWidget(self.pop_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        lay.addWidget(header)
        lay.addWidget(body, 1)
        ThemeManager.instance().changed.connect(self._theme_changed)
        self._theme_changed(True)

    def _theme_changed(self, _dark: bool) -> None:
        from autosim.ui.icons import make_icon
        self.pop_btn.setIcon(make_icon("popout", icon_color()))

    def _toggle_float(self) -> None:
        self.setFloating(not self.isFloating())

    def isFloating(self) -> bool:
        return self.isWindow()

    def setFloating(self, on: bool) -> None:
        if on == self.isFloating():
            return
        if on:
            sp = self.parentWidget()
            if isinstance(sp, QSplitter):
                self._home = (sp, sp.indexOf(self), sp.sizes())
            size = self.size()
            self.setParent(None)
            self.setWindowFlags(Qt.Window)
            self.setWindowTitle(self.title)
            self.resize(max(size.width(), 600), max(size.height(), 360))
            self.show()
        else:
            self.setWindowFlags(Qt.Widget)
            if self._home:
                sp, idx, sizes = self._home
                sp.insertWidget(idx, self)
                sp.setSizes(sizes)
            self.show()

    def closeEvent(self, e) -> None:
        if self.isFloating():                 # closing the pop-out window re-docks the panel
            e.ignore()
            self.setFloating(False)
        else:
            super().closeEvent(e)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.visibilityChanged.emit(True)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self.visibilityChanged.emit(False)


def centered(widget: QWidget) -> QWidget:
    """Wrap `widget` so it sits in the middle of a transparent, expanding area."""
    host = QWidget()
    outer = QVBoxLayout(host)
    outer.addStretch(1)
    row = QHBoxLayout()
    row.addStretch(1)
    row.addWidget(widget)
    row.addStretch(1)
    outer.addLayout(row)
    outer.addStretch(1)
    return host


def scrolling(widget: QWidget, min_height: int = 160) -> QWidget:
    """Wrap `widget` in a frameless vertical scroll area: the page scrolls instead of imposing its full minimum
    height on the window."""
    from PySide6.QtWidgets import QScrollArea

    class _Scroll(QScrollArea):
        def minimumSizeHint(self):
            return QSize(widget.minimumSizeHint().width(), min_height)

    sa = _Scroll()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sa.setWidget(widget)
    return sa


def toolbar_divider() -> QFrame:
    """Vertical 1 px divider for toolbars (a QFrame, styled by QFrame#toolbar-divider)."""
    line = QFrame()
    line.setObjectName("toolbar-divider")
    line.setFixedSize(1, 20)
    return line


def caption(text: str) -> QLabel:
    """Small muted label placed above an input."""
    lb = QLabel(text)
    lb.setProperty("role", "caption")
    return lb


def set_kind(button: QPushButton, kind: str) -> None:
    """kind: 'primary', 'danger', 'icon' or '' (secondary); re-polishes the style."""
    button.setProperty("kind", kind)
    button.style().unpolish(button)
    button.style().polish(button)
    button.update()


def add_glow(widget: QWidget, color: str | None = None, blur: int = 22) -> None:
    fx = QGraphicsDropShadowEffect(widget)
    fx.setBlurRadius(blur)
    fx.setOffset(0, 0)
    c = QColor(color or token("ACCENT"))
    c.setAlpha(120)
    fx.setColor(c)
    widget.setGraphicsEffect(fx)


# ---- copy from tables / trees ------------------------------------------------------------------------------
def view_text(view, whole_rows: bool = False) -> str:
    """Text of a table / tree view for the clipboard: the current cell, or (whole_rows, or several rows selected)
    the selected rows as tab-separated lines - pastes straight into a spreadsheet or an editor."""
    model, sel = view.model(), view.selectionModel()
    if model is None:
        return ""
    rows = sel.selectedRows(0) if sel is not None else []
    cur = view.currentIndex()
    if not whole_rows and len(rows) <= 1 and cur.isValid():
        v = cur.data()
        return "" if v is None else str(v)
    if not rows and cur.isValid():
        rows = [cur.sibling(cur.row(), 0)]
    hidden = getattr(view, "isColumnHidden", lambda _c: False)
    lines = []
    for r in sorted(rows, key=lambda i: (view.visualRect(i).top(), i.row())):
        cells = []
        for c in range(model.columnCount(r.parent())):
            if hidden(c):
                continue
            v = model.index(r.row(), c, r.parent()).data()
            cells.append("" if v is None else str(v))
        lines.append("\t".join(cells).rstrip("\t"))
    return "\n".join(lines)


def copy_view(view, whole_rows: bool = False) -> str:
    from PySide6.QtWidgets import QApplication
    text = view_text(view, whole_rows)
    if text:
        QApplication.clipboard().setText(text)
    return text


def install_copy(view, context_menu: bool = True) -> None:
    """Ctrl+C on the view copies the current cell (several selected rows: the rows); with `context_menu` the
    right-click menu offers Copy and Copy row(s). Views with their own menu add copy_actions() to it."""
    from PySide6.QtGui import QAction, QKeySequence
    act = QAction("Copy", view)
    act.setShortcut(QKeySequence.Copy)
    act.setShortcutContext(Qt.WidgetWithChildrenShortcut)
    act.triggered.connect(lambda: copy_view(view))
    view.addAction(act)
    if context_menu:
        rows = QAction("Copy row", view)
        rows.triggered.connect(lambda: copy_view(view, whole_rows=True))
        view.addAction(rows)
        view.setContextMenuPolicy(Qt.ActionsContextMenu)


def add_copy_actions(menu, view) -> None:
    """Copy / Copy row entries for a view's own context menu."""
    menu.addAction("Copy\tCtrl+C", lambda: copy_view(view))
    menu.addAction("Copy row", lambda: copy_view(view, whole_rows=True))


class _FillColumn(QObject):
    """Gives the free width of a view to one column (e.g. Data / Value) while every column stays resizable by
    dragging. Once the user resizes that column itself, its width is theirs and is no longer adjusted."""

    def __init__(self, view, col: int, minimum: int = 80) -> None:
        super().__init__(view)
        self.view, self.col, self.minimum = view, col, minimum
        self.header = view.header() if hasattr(view, "header") else view.horizontalHeader()
        self.manual = False
        self._busy = False
        self.header.setStretchLastSection(False)
        self.header.sectionResized.connect(self._resized)
        view.viewport().installEventFilter(self)
        QTimer.singleShot(0, self.refit)

    def _resized(self, idx: int, _old: int, _new: int) -> None:
        if self._busy:
            return
        if idx == self.col:
            self.manual = True                            # the user took this column over
        else:
            self.refit()                                  # another column changed: give / take the difference

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Resize:
            self.refit()
        return False

    def refit(self) -> None:
        if self.manual:
            return
        h = self.header
        others = sum(h.sectionSize(c) for c in range(h.count()) if c != self.col and not h.isSectionHidden(c))
        w = max(self.minimum, self.view.viewport().width() - others)
        if w != h.sectionSize(self.col):
            self._busy = True
            try:
                h.resizeSection(self.col, w)
            finally:
                self._busy = False


def fill_column(view, col: int, minimum: int = 80) -> "_FillColumn":
    """Interactive columns where `col` takes the free width (see _FillColumn)."""
    view._fill_column = _FillColumn(view, col, minimum)
    return view._fill_column
