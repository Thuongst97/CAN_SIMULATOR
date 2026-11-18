"""Top of the window, a standard menu bar directly under the OS title bar:

    File Edit View Hardware Database Analysis Simulation Help ........ (● 2 channels) [Connect all] [⌕] [☀] [⚙]

The menus are the window's QMenuBar (shell/menu_bar.py); this module builds its right corner (status pill and
quick actions). The app icon and name are the OS title bar's, so the window does not repeat them. Desktops are
switched from the rail on the left (DesktopRail).
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QToolButton, QVBoxLayout, QWidget

from autosim.ui.icons import make_icon
from autosim.ui.theme import ThemeManager, accent, icon_color, is_dark
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
    """The menu bar's right corner; the main window wires the actions (MainWindow._build_app_bar).
    A QObject child of the corner widget, so its theme connection ends with the window."""

    def __init__(self, settings_btn: QToolButton) -> None:
        corner = QWidget()
        super().__init__(corner)
        self.widget = corner
        corner.setObjectName("appbar-right")
        self.status = ClickableBadge("No channels", "offline")
        self.status.setToolTip("Channel status - click to show the channels")
        self.connect_btn = QPushButton("Connect all")
        self.connect_btn.setObjectName("appbar-connect")
        self.connect_btn.setFixedHeight(28)
        self.palette_btn = _icon_button("command", "Command palette", "Command palette")
        self.theme_btn = _icon_button("sun", "Switch to light theme", "Toggle theme")
        self.settings_btn = settings_btn
        rl = QHBoxLayout(corner)
        rl.setContentsMargins(0, 2, 8, 2)
        rl.setSpacing(6)
        rl.addWidget(self.status)
        rl.addSpacing(4)
        rl.addWidget(self.connect_btn)
        rl.addSpacing(4)
        for b in (self.palette_btn, self.theme_btn):
            rl.addWidget(b)
        rl.addWidget(settings_btn)
        ThemeManager.instance().changed.connect(self.refresh_icons)
        self.refresh_icons()

    def refresh_icons(self, _dark: bool = True) -> None:
        c = icon_color()
        self.palette_btn.setIcon(make_icon(self.palette_btn.property("icon_name"), c))
        self.theme_btn.setIcon(make_icon("sun" if is_dark() else "moon", c))   # tooltip: set by the main window

    def set_connected(self, any_open: bool) -> None:
        if any_open:
            self.connect_btn.setText("Disconnect all")
            set_kind(self.connect_btn, "ghost-danger")          # red only on hover; always confirms
        else:
            self.connect_btn.setText("Connect all")
            set_kind(self.connect_btn, "primary")


class DesktopRail(QWidget):
    """Narrow navigation rail on the left: one icon + label button per desktop (Setup, Simulation, Analysis)."""

    changed = Signal(int)

    def __init__(self, desktops: list[tuple[str, str, str]]) -> None:
        """desktops: (name, icon, tooltip)."""
        super().__init__()
        self.setObjectName("desktop-rail")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(84)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QToolButton] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 8)
        lay.setSpacing(4)
        for i, (name, icon, tip) in enumerate(desktops):
            b = QToolButton()
            b.setObjectName("rail-btn")
            b.setText(name)
            b.setToolTip(tip)
            b.setAccessibleName(f"{name} desktop")
            b.setProperty("icon_name", icon)
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(22, 22))
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)               # Ctrl+1/2/3 switch desktops from the keyboard
            b.setFixedSize(74, 58)
            self.group.addButton(b, i)
            self.buttons.append(b)
            lay.addWidget(b, 0, Qt.AlignHCenter)
        lay.addStretch()
        self.group.idToggled.connect(lambda i, on: on and self.changed.emit(i))
        ThemeManager.instance().changed.connect(self.refresh_icons)
        self.refresh_icons()

    def refresh_icons(self, _dark: bool = True) -> None:
        for b in self.buttons:
            b.setIcon(make_icon(b.property("icon_name"), accent() if b.isChecked() else icon_color()))

    def set_current(self, i: int) -> None:
        self.buttons[i].setChecked(True)
        self.refresh_icons()

    def current(self) -> int:
        return max(0, self.group.checkedId())

    def text(self, i: int) -> str:
        return self.buttons[i].text()

    def count(self) -> int:
        return len(self.buttons)
