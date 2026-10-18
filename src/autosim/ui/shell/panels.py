"""Dock panels with a custom title bar that matches the panel-header component.

    Panel("Bus trace", widget, primary=True)    # primary: accent rule under the header (the main view)

The header shows the title, an optional accessory widget (e.g. a counter) and float / close buttons. Mouse
events the header does not use are ignored, so QDockWidget keeps its normal behaviour: drag the header to move
or tab the panel, double-click it to float / re-dock. A floating panel's close button re-docks it instead of
hiding it, so a panel cannot get lost on a second monitor.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDockWidget, QHBoxLayout, QLabel, QToolButton, QWidget

from autosim.ui.icons import make_icon
from autosim.ui.theme import ThemeManager, icon_color


def _repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


class PanelHeader(QWidget):
    def __init__(self, dock: "Panel", title: str, primary: bool = False) -> None:
        super().__init__()
        self.dock = dock
        self.setObjectName("panel-header")
        self.setProperty("primary", primary)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.title = QLabel(title)
        self.title.setObjectName("panel-title")
        self.accessory = QHBoxLayout()
        self.accessory.setSpacing(8)
        self.float_btn = QToolButton()
        self.float_btn.setObjectName("panel-btn")
        self.float_btn.setIconSize(QSize(14, 14))
        self.float_btn.setToolTip("Pop out into its own window (double-click the header to toggle)")
        self.float_btn.setAccessibleName(f"Pop out {title}")
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("panel-btn")
        self.close_btn.setIconSize(QSize(14, 14))
        self.close_btn.setToolTip("Hide panel (bring it back from the Layout menu or Ctrl+K)")
        self.close_btn.setAccessibleName(f"Hide {title}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(4)
        lay.addWidget(self.title)
        lay.addStretch()
        lay.addLayout(self.accessory)
        lay.addWidget(self.float_btn)
        lay.addWidget(self.close_btn)
        self.setFixedHeight(self.HEIGHT)
        self.float_btn.clicked.connect(lambda: dock.setFloating(not dock.isFloating()))
        self.close_btn.clicked.connect(dock.close)
        ThemeManager.instance().changed.connect(self.refresh_icons)      # bound: auto-disconnects
        self.refresh_icons()

    HEIGHT = 36

    def sizeHint(self) -> QSize:              # QDockWidget sizes its title area from the hint, not the fixed height:
        return QSize(super().sizeHint().width(), self.HEIGHT)   # without this the header covers the content

    def minimumSizeHint(self) -> QSize:
        return QSize(super().minimumSizeHint().width(), self.HEIGHT)

    def refresh_icons(self, _dark: bool = True) -> None:
        c = icon_color()
        self.float_btn.setIcon(make_icon("popout", c))
        self.close_btn.setIcon(make_icon("close", c))

    def set_focused(self, on: bool) -> None:
        if self.property("focused") != on:
            self.setProperty("focused", on)
            _repolish(self)
            _repolish(self.title)


class Panel(QDockWidget):
    """A dockable panel of the CAN workspace (or the log)."""

    def __init__(self, title: str, widget: QWidget, primary: bool = False, parent=None) -> None:
        super().__init__(title, parent)
        self.setObjectName("dock-" + title.replace(" ", "-"))
        self.setWidget(widget)
        self.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable |
                         QDockWidget.DockWidgetFloatable)
        self.header = PanelHeader(self, title, primary)
        self.setTitleBarWidget(self.header)
        self.topLevelChanged.connect(self._floating_changed)

    def _floating_changed(self, floating: bool) -> None:
        self.header.float_btn.setToolTip("Dock back into the window" if floating else
                                         "Pop out into its own window (double-click the header to toggle)")
        if floating:
            self.resize(max(self.width(), 700), max(self.height(), 420))

    def closeEvent(self, e) -> None:
        if self.isFloating():                     # closing a popped-out window re-docks the panel
            e.ignore()
            self.setFloating(False)
            return
        super().closeEvent(e)
