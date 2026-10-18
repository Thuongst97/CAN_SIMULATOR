"""Collapsible left sidebar of the CAN workspace: titled sections (Channels, Database) in a scroll area, so it
never forces the window taller than the screen (1366 x 768 minimum)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from autosim.ui.icons import make_icon
from autosim.ui.theme import ThemeManager, icon_color


class SidebarSection(QFrame):
    """Header (chevron + title + optional accessory) over a body that can be collapsed."""

    toggled = Signal(bool)

    def __init__(self, title: str, body: QWidget, stretch: bool = False) -> None:
        super().__init__()
        self.setObjectName("sidebar-section")
        self.body = body
        self.stretch = stretch
        self.chevron = QToolButton()
        self.chevron.setObjectName("panel-btn")
        self.chevron.setIconSize(QSize(14, 14))
        self.chevron.setAccessibleName(f"Collapse {title}")
        self.title = QLabel(title)
        self.title.setObjectName("section-title")
        self.accessory = QHBoxLayout()
        self.accessory.setSpacing(6)
        head = QWidget()
        head.setObjectName("section-header")
        head.setAttribute(Qt.WA_StyledBackground, True)
        head.setFixedHeight(32)
        head.setCursor(Qt.PointingHandCursor)
        head.mousePressEvent = lambda e: self.set_expanded(not self.expanded)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(4, 0, 8, 0)
        hl.setSpacing(4)
        hl.addWidget(self.chevron)
        hl.addWidget(self.title)
        hl.addStretch()
        hl.addLayout(self.accessory)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(head)
        lay.addWidget(body, 1 if stretch else 0)
        self.header = head
        self.expanded = True
        self.chevron.clicked.connect(lambda: self.set_expanded(not self.expanded))
        ThemeManager.instance().changed.connect(self._icon)             # bound: auto-disconnects
        self._icon()

    def _icon(self, _dark: bool = True) -> None:
        self.chevron.setIcon(make_icon("chevron-down" if self.expanded else "chevron-right", icon_color()))

    def set_expanded(self, on: bool) -> None:
        self.expanded = on
        self.body.setVisible(on)
        self.chevron.setToolTip("Collapse" if on else "Expand")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding if (on and self.stretch) else QSizePolicy.Fixed)
        self._icon()
        self.toggled.emit(on)


class Sidebar(QScrollArea):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sidebar")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.NoFrame)
        self.host = QWidget()
        self.host.setObjectName("sidebar-host")
        self.box = QVBoxLayout(self.host)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(0)
        self.setWidget(self.host)
        self.sections: dict[str, SidebarSection] = {}
        self.box.addStretch(0)                         # takes the space when no stretching section is open

    def add_section(self, key: str, title: str, body: QWidget, stretch: bool = False) -> SidebarSection:
        s = SidebarSection(title, body, stretch)
        self.box.insertWidget(self.box.count() - 1, s)
        self.sections[key] = s
        s.toggled.connect(lambda _on: self._relayout())
        self._relayout()
        return s

    def _relayout(self) -> None:
        open_stretch = False
        for s in self.sections.values():
            grow = s.stretch and s.expanded
            open_stretch |= grow
            self.box.setStretchFactor(s, 1 if grow else 0)
        self.box.setStretch(self.box.count() - 1, 0 if open_stretch else 1)

    def minimumSizeHint(self) -> QSize:                 # scrolls instead of imposing its content's minimum
        return QSize(260, 120)
