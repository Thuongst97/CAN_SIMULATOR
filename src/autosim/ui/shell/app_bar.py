"""Window chrome besides the menus:

    menu bar   File Edit View Hardware Database Analysis Simulation Help      (shell/menu_bar.py, nothing else:
                                                                              a normal menu bar, normal height)
    rail       [⌕ search]                                                    (command palette, Ctrl+K)
               Setup / Database / Simulation / Analysis                      (desktops, Ctrl+1…4)
               ...
               [☀ theme] [⚙ settings]                                        (bottom of the rail)

Channel state lives in the status bar and the Channels window; Connect all / Disconnect all in the Hardware menu.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QToolButton, QVBoxLayout, QWidget

from autosim.ui.icons import make_icon
from autosim.ui.theme import ThemeManager, accent, icon_color, is_dark


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
    """The quick-action buttons (search, theme, settings); the main window places them on the rail and wires them
    (MainWindow._build_app_bar). A QObject child of the search button, so its theme connection ends with it."""

    def __init__(self, settings_btn: QToolButton) -> None:
        self.palette_btn = _icon_button("command", "Command palette", "Command palette")
        super().__init__(self.palette_btn)
        self.theme_btn = _icon_button("sun", "Switch to light theme", "Toggle theme")
        self.settings_btn = settings_btn
        self.palette_btn.setObjectName("rail-tool")      # search: icon only, above the desktops
        self.palette_btn.setIconSize(QSize(20, 20))
        self.palette_btn.setFixedSize(40, 36)
        for b, text in ((self.theme_btn, "Theme"), (settings_btn, "Settings")):   # like the desktop buttons
            b.setObjectName("rail-btn")
            b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(22, 22))
            b.setFixedSize(74, 58)
        ThemeManager.instance().changed.connect(self.refresh_icons)
        self.refresh_icons()

    def refresh_icons(self, _dark: bool = True) -> None:
        c = icon_color()
        self.palette_btn.setIcon(make_icon(self.palette_btn.property("icon_name"), c))
        self.theme_btn.setIcon(make_icon("sun" if is_dark() else "moon", c))   # tooltip: set by the main window


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
        self._lay = lay
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

    def add_top(self, w: QWidget) -> None:
        """A tool above the desktops (search)."""
        self._lay.insertWidget(0, w, 0, Qt.AlignHCenter)
        self._lay.insertSpacing(1, 6)

    def add_after_desktops(self, w: QWidget) -> None:
        """A tool right under the desktop buttons (Log)."""
        self._lay.insertWidget(self._lay.indexOf(self.buttons[-1]) + 1, w, 0, Qt.AlignHCenter)

    def add_bottom(self, w: QWidget) -> None:
        """A tool at the bottom of the rail (theme, settings)."""
        self._lay.addWidget(w, 0, Qt.AlignHCenter)

    def set_current(self, i: int) -> None:
        self.buttons[i].setChecked(True)
        self.refresh_icons()

    def current(self) -> int:
        return max(0, self.group.checkedId())

    def text(self, i: int) -> str:
        return self.buttons[i].text()

    def count(self) -> int:
        return len(self.buttons)
