"""Custom title bar: the menus share the row with the window title and the window buttons, like VS Code / modern
Windows apps, and it follows the app theme (the native bar stays light on a dark app).

    [icon] File Edit View Hardware Database Analysis Simulation Help ................................ [—] [☐] [✕]

(no title text: the window title still names the app in the taskbar and Alt+Tab)

The window is frameless; the platform still moves, snaps and resizes it: dragging the empty part of the bar calls
QWindow.startSystemMove (Windows Aero snap included), a double-click maximises / restores, and EdgeResizer starts
QWindow.startSystemResize from the outer 6 px of the window. AUTOSIM_NATIVE_TITLEBAR=1 keeps the OS title bar.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QToolButton, QWidget

from autosim.ui.icons import make_icon
from autosim.ui.theme import ThemeManager, app_icon, icon_color

HEIGHT = 36
EDGE = 6                                     # px of the window border that resize it


def use_custom_title_bar() -> bool:
    return os.environ.get("AUTOSIM_NATIVE_TITLEBAR", "") not in ("1", "true", "yes")


class TitleBar(QWidget):
    def __init__(self, window, menu_bar) -> None:
        super().__init__(window)
        self.win = window
        self.setObjectName("title-bar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(HEIGHT)
        self.icon = QLabel()
        self.icon.setPixmap(app_icon().pixmap(18, 18))
        self.icon.setFixedSize(QSize(28, HEIGHT))
        self.icon.setAlignment(Qt.AlignCenter)
        self.min_btn = self._button("win-min", "Minimize")
        self.max_btn = self._button("win-max", "Maximize")
        self.close_btn = self._button("close", "Close")
        self.close_btn.setObjectName("win-close")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.icon)
        lay.addWidget(menu_bar, 0, Qt.AlignVCenter)
        lay.addStretch(1)
        for b in (self.min_btn, self.max_btn, self.close_btn):
            lay.addWidget(b)
        self.min_btn.clicked.connect(window.showMinimized)
        self.max_btn.clicked.connect(self.toggle_maximized)
        self.close_btn.clicked.connect(window.close)
        window.installEventFilter(self)                                  # follow maximise / restore
        ThemeManager.instance().changed.connect(self.refresh_icons)
        self.refresh_icons()

    def _button(self, icon: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setObjectName("win-btn")
        b.setProperty("icon_name", icon)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setFocusPolicy(Qt.NoFocus)
        b.setIconSize(QSize(16, 16))
        b.setFixedSize(46, HEIGHT)
        return b

    def refresh_icons(self, _dark: bool = True) -> None:
        c = icon_color()
        self.max_btn.setProperty("icon_name", "win-restore" if self.win.isMaximized() else "win-max")
        self.max_btn.setToolTip("Restore" if self.win.isMaximized() else "Maximize")
        for b in (self.min_btn, self.max_btn, self.close_btn):
            b.setIcon(make_icon(b.property("icon_name"), c))

    def toggle_maximized(self) -> None:
        if self.win.isMaximized():
            self.win.showNormal()
        else:
            self.win.showMaximized()

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.win and ev.type() == QEvent.WindowStateChange:
            self.refresh_icons()
        return False

    # ---- move / maximise from the empty part of the bar ----------------------------------------------------
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self.win.windowHandle() is not None:
            self.win.windowHandle().startSystemMove()                    # native move: Aero snap works
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.toggle_maximized()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


class EdgeResizer(QObject):
    """Resizing a frameless window from its border: an application event filter that shows the resize cursor in the
    outer EDGE px and starts the platform's resize there (children cover the edges, so the window itself would not
    see these mouse events)."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.win = window
        self._cursor = False

    def _edges(self, gpos: QPoint):
        w = self.win
        if w.isMaximized() or w.isFullScreen() or not w.isVisible():
            return Qt.Edge(0)
        r = w.frameGeometry()
        edges = Qt.Edge(0)
        if gpos.x() - r.left() < EDGE:
            edges |= Qt.LeftEdge
        elif r.right() - gpos.x() < EDGE:
            edges |= Qt.RightEdge
        if gpos.y() - r.top() < EDGE:
            edges |= Qt.TopEdge
        elif r.bottom() - gpos.y() < EDGE:
            edges |= Qt.BottomEdge
        if not r.adjusted(-1, -1, 1, 1).contains(gpos):
            return Qt.Edge(0)
        return edges

    @staticmethod
    def _shape(edges):
        both = (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge)
        if edges in both:
            return Qt.SizeFDiagCursor
        if edges in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
            return Qt.SizeBDiagCursor
        if edges & (Qt.LeftEdge | Qt.RightEdge):
            return Qt.SizeHorCursor
        return Qt.SizeVerCursor

    def _set_cursor(self, edges) -> None:
        if edges and not self._cursor:
            QApplication.setOverrideCursor(QCursor(self._shape(edges)))
            self._cursor = True
        elif edges and self._cursor:
            QApplication.changeOverrideCursor(QCursor(self._shape(edges)))
        elif not edges and self._cursor:
            QApplication.restoreOverrideCursor()
            self._cursor = False

    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if t not in (QEvent.MouseMove, QEvent.MouseButtonPress) or not isinstance(obj, QWidget):
            return False
        if obj.window() is not self.win:
            return False
        gpos = ev.globalPosition().toPoint()
        edges = self._edges(gpos)
        if t == QEvent.MouseMove and not ev.buttons():
            self._set_cursor(edges)
        elif t == QEvent.MouseButtonPress and edges and ev.button() == Qt.LeftButton:
            handle = self.win.windowHandle()
            if handle is not None and handle.startSystemResize(edges):
                self._set_cursor(Qt.Edge(0))
                return True
        return False
