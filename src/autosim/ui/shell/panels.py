"""Dock panels with a custom title bar that matches the panel-header component.

    Panel("Bus trace", widget, primary=True)    # primary: accent rule under the header (the main view)

The header shows the title, an optional accessory widget (e.g. a counter) and detach / close buttons. Mouse
events the header does not use are ignored, so QDockWidget keeps its normal behaviour: drag the header to move
or tab the panel, double-click it to float / re-dock.

detach() turns the panel into an ordinary top-level window (window frame with minimise / maximise, can go to a
second monitor) that stays visible whichever desktop is selected; attach() or closing that window docks it back
where it was, so a window cannot get lost.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import QDockWidget, QHBoxLayout, QLabel, QToolButton, QWidget

from autosim.ui.icons import make_icon
from autosim.ui.theme import ThemeManager, icon_color


DETACH_TIP = "Detach into its own window, e.g. onto a second monitor (double-click the header to toggle)"
WINDOW_FLAGS = (Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMinimizeButtonHint |
                Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint)


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
        self.lead = QHBoxLayout()                        # widgets right after the title (e.g. a status)
        self.lead.setSpacing(8)
        self.accessory = QHBoxLayout()                   # widgets at the right, before detach / close
        self.accessory.setSpacing(8)
        self.float_btn = QToolButton()
        self.float_btn.setObjectName("panel-btn")
        self.float_btn.setIconSize(QSize(14, 14))
        self.float_btn.setToolTip(DETACH_TIP)
        self.float_btn.setAccessibleName(f"Detach {title}")
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("panel-btn")
        self.close_btn.setIconSize(QSize(14, 14))
        self.close_btn.setToolTip("Close window (open it again from the View menu or Ctrl+K)")
        self.close_btn.setAccessibleName(f"Hide {title}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(4)
        lay.addWidget(self.title)
        lay.addSpacing(12)
        lay.addLayout(self.lead)
        lay.addStretch()
        lay.addLayout(self.accessory)
        lay.addWidget(self.float_btn)
        lay.addWidget(self.close_btn)
        self.setFixedHeight(self.HEIGHT)
        self.float_btn.clicked.connect(dock.toggle_detached_later)
        self.close_btn.clicked.connect(dock.close)
        ThemeManager.instance().changed.connect(self.refresh_icons)      # bound: auto-disconnects
        self.refresh_icons()

    HEIGHT = 36

    def sizeHint(self) -> QSize:              # QDockWidget sizes its title area from the hint, not the fixed height:
        return QSize(super().sizeHint().width(), self.HEIGHT)   # without this the header covers the content

    def minimumSizeHint(self) -> QSize:
        return QSize(super().minimumSizeHint().width(), self.HEIGHT)

    def mouseDoubleClickEvent(self, e) -> None:
        # QDockWidget would toggle floating synchronously, inside this event: re-docking then destroys the native
        # window that is still delivering the event (an intermittent crash on Windows). Toggle afterwards instead.
        if e.button() == Qt.LeftButton and self.dock.features() & QDockWidget.DockWidgetFloatable:
            e.accept()
            self.dock.toggle_detached_later()
            return
        super().mouseDoubleClickEvent(e)

    def refresh_icons(self, _dark: bool = True) -> None:
        c = icon_color()
        self.float_btn.setIcon(make_icon("popout", c))
        self.close_btn.setIcon(make_icon("close", c))

    def set_title(self, text: str) -> None:
        """Shown title (the panel keeps its key, e.g. "Signals" for menus, commands and the saved layout)."""
        self.title.setText(text)
        self.dock.setWindowTitle(text)

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
        self.header.float_btn.setToolTip("Attach back to its desktop" if floating else DETACH_TIP)
        if floating:
            self.resize(max(self.width(), 700), max(self.height(), 420))

    def detach(self) -> None:
        """Own top-level window with a normal frame (minimise / maximise), titled 'AutoSim - <panel>'."""
        if not self.isFloating():
            self.setFloating(True)
        self.setWindowTitle(f"AutoSim Studio - {self.windowTitle().split(' - ')[-1]}")
        self.setWindowFlags(WINDOW_FLAGS)
        self.resize(max(self.width(), 900), max(self.height(), 520))
        self.show()
        self.raise_()

    def attach(self) -> None:
        if self.isFloating():
            self.setFloating(False)
        self.setWindowTitle(self.windowTitle().split(" - ")[-1])

    def toggle_detached(self) -> None:
        self.attach() if self.isFloating() else self.detach()

    def toggle_detached_later(self) -> None:
        """Toggle once the current event is handled. Detaching / re-docking re-creates the panel's native window,
        which must not happen while that window is still delivering the click, double-click or close that asked
        for it (use-after-free: the crash seen now and then when detaching and docking back)."""
        QTimer.singleShot(0, self, self.toggle_detached)

    def closeEvent(self, e) -> None:
        if self.isFloating():                     # closing a detached window docks it back (after the close event)
            e.ignore()
            QTimer.singleShot(0, self, self.attach)
            return
        super().closeEvent(e)
