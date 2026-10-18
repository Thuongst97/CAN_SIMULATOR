"""Top app bar:

    [sidebar] AutoSim Studio  ..........  (● 2 channels · 1 warning) [Connect all] [⌕] [View] [☀] [⚙]
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QWidget

from autosim.ui.icons import make_icon
from autosim.ui.theme import ThemeManager, icon_color, is_dark
from autosim.ui.widgets import StatusBadge, set_kind


class ClickableBadge(StatusBadge):
    clicked = Signal()

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


def _icon_button(name: str, tip: str, accessible: str) -> QToolButton:
    b = QToolButton()
    b.setObjectName("appbar-btn")
    b.setIconSize(QSize(18, 18))
    b.setToolTip(tip)
    b.setAccessibleName(accessible)
    b.setProperty("icon_name", name)
    b.setCursor(Qt.PointingHandCursor)
    return b


class AppBar(QObject):
    """Holds the app-bar widgets; the main window wires their actions (see MainWindow._build_app_bar).
    A QObject child of the bar widget, so its theme connection ends with the window."""

    def __init__(self, settings_btn: QToolButton) -> None:
        bar = QWidget()
        super().__init__(bar)
        self.widget = bar
        bar.setObjectName("app-bar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(48)
        left = QWidget()
        self.left = left
        self.left.setObjectName("appbar-left")
        self.sidebar_btn = _icon_button("sidebar", "Toggle sidebar", "Toggle sidebar")
        self.sidebar_btn.setCheckable(True)
        self.sidebar_btn.setChecked(True)
        self.title = QLabel("AutoSim Studio")
        self.title.setObjectName("app-title")
        ll = QHBoxLayout(self.left)
        ll.setContentsMargins(8, 0, 16, 0)
        ll.setSpacing(8)
        ll.addWidget(self.sidebar_btn)
        ll.addWidget(self.title)

        # ---- right corner ----
        self.right = QWidget()
        self.right.setObjectName("appbar-right")
        self.status = ClickableBadge("No channels", "offline")
        self.status.setToolTip("Channel status - click to show the channels")
        self.connect_btn = QPushButton("Connect all")
        self.connect_btn.setObjectName("appbar-connect")
        self.connect_btn.setFixedHeight(28)
        self.palette_btn = _icon_button("command", "Command palette", "Command palette")
        self.layout_btn = _icon_button("layout", "Layout: panels and reset", "Layout menu")
        self.layout_btn.setPopupMode(QToolButton.InstantPopup)
        self.layout_menu = QMenu(self.layout_btn)
        self.layout_btn.setMenu(self.layout_menu)
        self.theme_btn = _icon_button("sun", "Switch to light theme", "Toggle theme")
        self.settings_btn = settings_btn
        rl = QHBoxLayout(self.right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        rl.addWidget(self.status)
        rl.addSpacing(4)
        rl.addWidget(self.connect_btn)
        rl.addSpacing(4)
        for b in (self.palette_btn, self.layout_btn, self.theme_btn):
            rl.addWidget(b)
        rl.addWidget(settings_btn)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 8, 0)
        bl.setSpacing(0)
        bl.addWidget(self.left)
        bl.addStretch()
        bl.addWidget(self.right)
        ThemeManager.instance().changed.connect(self.refresh_icons)
        self.refresh_icons()

    def refresh_icons(self, _dark: bool = True) -> None:
        c = icon_color()
        for b in (self.sidebar_btn, self.palette_btn, self.layout_btn):
            b.setIcon(make_icon(b.property("icon_name"), c))
        self.theme_btn.setIcon(make_icon("sun" if is_dark() else "moon", c))   # tooltip: set by the main window

    def set_connected(self, any_open: bool) -> None:
        if any_open:
            self.connect_btn.setText("Disconnect all")
            set_kind(self.connect_btn, "ghost-danger")          # red only on hover; always confirms
        else:
            self.connect_btn.setText("Connect all")
            set_kind(self.connect_btn, "primary")
