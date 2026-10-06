"""Main window: CAN / SOME-IP tabs, dockable CAN workspace, menus, project save/restore."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QLabel, QMainWindow, QMessageBox, QSplitter, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)

from autosim.context import AppContext
from autosim.core.common.config import ProjectConfig
from autosim.ui.bridge import QtBridge
from autosim.ui.panels.connection_panel import CanConnectionPanel, EthConnectionPanel
from autosim.ui.panels.dbc_explorer import DbcExplorer
from autosim.ui.panels.log_panel import LogPanel
from autosim.ui.panels.plot_panel import PlotPanel
from autosim.ui.panels.signal_panel import SignalPanel
from autosim.ui.panels.someip_panel import SomeIpPanel
from autosim.ui.panels.trace_panel import TracePanel
from autosim.ui.icons import make_icon
from autosim.ui.settings_dialog import SettingsDialog
from autosim.ui.widgets import PanelCard
from autosim.ui.theme import apply_theme, icon_color

SETTINGS = ("AutoSim", "AutoSimStudio")


class DockWorkspace(QMainWindow):
    """A QMainWindow embedded as a page: its QDockWidgets can be rearranged, tabbed or popped out
    into their own windows (e.g. onto a second monitor)."""

    def __init__(self, central: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.Widget)
        self.setDockNestingEnabled(True)
        self.setCorner(Qt.BottomLeftCorner, Qt.LeftDockWidgetArea)     # left docks run full height
        self.setCentralWidget(central)
        self.docks: dict[str, QDockWidget] = {}

    def add_dock(self, title: str, widget: QWidget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName("dock-" + title.replace(" ", "-"))
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(area, d)
        self.docks[title] = d
        return d


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("AutoSim Studio")
        self.resize(1560, 980)
        self.bridge = QtBridge(ctx.events, self)
        self.current_theme = str(QSettings(*SETTINGS).value("theme", "dark"))

        self.can_conn = CanConnectionPanel(ctx, self.bridge)
        self.eth_conn = EthConnectionPanel(ctx)
        self.dbc = DbcExplorer(ctx, self.bridge)
        self.signals_panel = SignalPanel(ctx)
        self.someip = SomeIpPanel(ctx, self.bridge, eth_panel=self.eth_conn)   # Ethernet card lives in its sidebar
        self.plot = PlotPanel(ctx, self.bridge)
        self.trace = TracePanel(ctx, self.bridge)
        self.log = LogPanel(self.bridge)

        # Two independent top-level tabs: CAN (dockable workspace) and SOME/IP
        self.center = QTabWidget()
        self.center.setDocumentMode(True)
        self.can_tab = self._build_can_tab()
        self.someip_tab = self._build_someip_tab()
        self.center.addTab(self.can_tab, "CAN")
        self.center.addTab(self.someip_tab, "SOME/IP")
        self.setCentralWidget(self.center)

        self.log_dock = QDockWidget("Log", self)
        self.log_dock.setObjectName("dock-Log")
        self.log_dock.setWidget(self.log)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)

        self._build_settings()
        self.rate = QLabel("")
        self.statusBar().addPermanentWidget(self.rate)
        self._last_count, self._timer = 0, QTimer(self)
        self._timer.timeout.connect(self._update_rate)
        self._timer.start(1000)

        self.dbc.messageActivated.connect(self._add_message)
        self.signals_panel.openDbcRequested.connect(self.dbc.load_dialog)
        self.signals_panel.filesDropped.connect(lambda files: [self.dbc.load_file(f) for f in files])
        self.bridge.dbLoaded.connect(lambda _p: self.signals_panel.refresh_empty())
        self.someip.set_engine_running(False)
        self._default_layout = False
        self._restore()

    # ---- tabs ---------------------------------------------------------------------
    def _build_can_tab(self) -> DockWorkspace:
        """CAN tab: left docks (connection, DBC explorer) + centre vertical QSplitter:
        top 35 % = [Signals | Signal plot] side by side, bottom 65 % = Bus trace. Plot and trace are
        PanelCards, so they can still pop out into their own windows."""
        self.signals_panel.setObjectName("signals")            # the card frame comes from PanelCard
        self.signals_card = PanelCard("Signals", self.signals_panel)
        self.plot_card = PanelCard("Signal plot", self.plot)
        self.trace_card = PanelCard("Bus trace", self.trace)
        self.top_split = QSplitter(Qt.Horizontal)
        self.top_split.addWidget(self.signals_card)
        self.top_split.addWidget(self.plot_card)
        self.top_split.setChildrenCollapsible(False)
        self.can_split = QSplitter(Qt.Vertical)
        self.can_split.addWidget(self.top_split)
        self.can_split.addWidget(self.trace_card)
        self.can_split.setStretchFactor(0, 35)                 # plot / signals 35 %
        self.can_split.setStretchFactor(1, 65)                 # bus trace 65 %
        self.can_split.setChildrenCollapsible(False)
        host = QWidget()
        hl = QVBoxLayout(host)
        hl.setContentsMargins(6, 6, 6, 6)
        hl.addWidget(self.can_split)

        ws = DockWorkspace(host)
        ws.add_dock("CAN connection", self.can_conn, Qt.LeftDockWidgetArea)
        ws.add_dock("DBC explorer", self.dbc, Qt.LeftDockWidgetArea)
        ws.docks["Signal plot"] = self.plot_card
        ws.docks["Bus trace"] = self.trace_card
        self.can_ws = ws
        self._arrange_can()
        return ws

    def _arrange_can(self) -> None:
        """Default CAN layout: connection over explorer (left), signals + plot (top), trace (bottom)."""
        ws, d = self.can_ws, self.can_ws.docks
        for name in ("CAN connection", "DBC explorer"):
            d[name].setFloating(False)
            d[name].show()
        for card in (self.signals_card, self.plot_card, self.trace_card):
            card.setFloating(False)
            card.show()
        ws.addDockWidget(Qt.LeftDockWidgetArea, d["CAN connection"])
        ws.addDockWidget(Qt.LeftDockWidgetArea, d["DBC explorer"])
        ws.splitDockWidget(d["CAN connection"], d["DBC explorer"], Qt.Vertical)
        self._size_can_docks()

    def _size_can_docks(self) -> None:
        """Proportional default sizes (the workspace is tiny until the window is shown)."""
        ws, d = self.can_ws, self.can_ws.docks
        w = ws.width() if ws.width() > 900 else self.width() - 20
        h = ws.height() if ws.height() > 500 else self.height() - 140
        ws.resizeDocks([d["CAN connection"]], [int(w * 0.27)], Qt.Horizontal)
        ws.resizeDocks([d["CAN connection"], d["DBC explorer"]], [int(h * 0.50), int(h * 0.50)], Qt.Vertical)
        total = self.can_split.height() if self.can_split.height() > 300 else h
        self.can_split.setSizes([int(total * 0.35), int(total * 0.65)])
        tw = self.top_split.width() if self.top_split.width() > 400 else int(w * 0.72)
        self.top_split.setSizes([int(tw * 0.55), int(tw * 0.45)])

    def _build_someip_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(self.someip)
        self.eth_conn.someipStateChanged.connect(self.someip.set_engine_running)
        return w

    # ---- settings (replaces the old File / View / Tools menu bar) --------------------
    def _build_settings(self) -> None:
        self.btn_settings = QToolButton()
        self.btn_settings.setObjectName("settings-btn")
        self.btn_settings.setIconSize(QSize(18, 18))
        self.btn_settings.setToolTip("Settings  (Ctrl+,)")
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self._refresh_gear()
        self.center.setCornerWidget(self.btn_settings, Qt.TopRightCorner)
        self.settings_dialog = SettingsDialog(self)
        self.btn_settings.clicked.connect(self.show_settings)
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self.show_settings)

    def _refresh_gear(self) -> None:
        self.btn_settings.setIcon(make_icon("gear", icon_color(), active=icon_color()))

    def show_settings(self) -> None:
        dlg = self.settings_dialog
        dlg.sync()
        dlg.adjustSize()
        dlg.move(self.btn_settings.mapToGlobal(QPoint(self.btn_settings.width() - dlg.width(),
                                                      self.btn_settings.height() + 6)))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def dock_registry(self) -> dict[str, QDockWidget]:
        """Every dock panel by title (CAN workspace docks + the shared Log dock)."""
        return {**self.can_ws.docks, "Log": self.log_dock}

    def open_database(self) -> None:
        self.center.setCurrentWidget(self.can_tab)
        self.dbc.load_dialog()

    def open_service_config(self) -> None:
        self.center.setCurrentWidget(self.someip_tab)
        self.someip.load_config()

    def open_proto(self) -> None:
        self.center.setCurrentWidget(self.someip_tab)
        self.someip.load_proto()

    def pop_out(self, title: str) -> None:
        d = self.dock_registry()[title]
        d.show()
        d.setFloating(True)
        d.resize(max(d.width(), 700), max(d.height(), 400))

    def set_theme(self, mode: str) -> None:
        """Switch theme live and remember it for the next start."""
        self.current_theme = mode
        dark = apply_theme(QApplication.instance(), mode)
        self.plot.apply_theme(dark)
        self.trace.refresh_icons()
        self._refresh_gear()
        if hasattr(self, "settings_dialog"):
            self.settings_dialog.sync_theme()
        QSettings(*SETTINGS).setValue("theme", mode)

    # ---- layout persistence -----------------------------------------------------------
    def _restore(self) -> None:
        s = QSettings(*SETTINGS)
        if s.value("geometry") is not None:
            self.restoreGeometry(s.value("geometry"))
            if s.value("state") is not None:
                self.restoreState(s.value("state"))
            if s.value("can_state") is not None:
                self.can_ws.restoreState(s.value("can_state"))
            for key, split in (("can_split", self.can_split), ("top_split", self.top_split),
                               ("someip_split", self.someip.splitter)):
                if s.value(key) is not None:
                    split.restoreState(s.value(key))
        else:
            self._default_layout = True        # sizes are applied once the window has its real size
            self._reset_layout()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self._default_layout:
            self._default_layout = False
            QTimer.singleShot(0, self._size_can_docks)

    def _reset_layout(self) -> None:
        self._arrange_can()
        self.log_dock.setFloating(False)
        self.log_dock.show()
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.resizeDocks([self.log_dock], [140], Qt.Vertical)

    def closeEvent(self, e) -> None:
        self.signals_panel.stop_all()
        s = QSettings(*SETTINGS)
        s.setValue("geometry", self.saveGeometry())
        s.setValue("state", self.saveState())
        s.setValue("can_state", self.can_ws.saveState())
        s.setValue("can_split", self.can_split.saveState())
        s.setValue("top_split", self.top_split.saveState())
        s.setValue("someip_split", self.someip.splitter.saveState())
        for d in self.can_ws.docks.values():          # floating panels must not outlive the app
            if d.isFloating():
                d.close()
        self.ctx.shutdown()
        super().closeEvent(e)

    # ---- actions ------------------------------------------------------------------
    def _add_message(self, name: str) -> None:
        if self.signals_panel.add_message(name):
            self.center.setCurrentWidget(self.can_tab)

    def _update_rate(self) -> None:
        n = self.ctx.can.rx.rx_count
        self.rate.setText(f"{n - self._last_count} frames/s   total {n}")
        self._last_count = n

    def save_project(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save project", "autosim.project.json", "Project (*.json)")
        if not path:
            return
        cfg = self.ctx.config
        cfg.dbc_paths = self.ctx.can.registry.paths
        self.can_conn.collect(cfg)
        self.eth_conn.collect(cfg)
        try:
            cfg.save(Path(path))
        except OSError as e:
            QMessageBox.critical(self, "Save project", str(e))

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Project (*.json)")
        if path:
            self.load_project(path)

    def load_project(self, path: str) -> None:
        try:
            cfg = ProjectConfig.load(Path(path))
        except (OSError, ValueError, TypeError) as e:
            QMessageBox.critical(self, "Open project", f"{path}: {e}")
            return
        missing = []
        for p in cfg.dbc_paths:
            if not Path(p).exists() or not self.dbc.load_file(p):
                missing.append(p)
        for p in cfg.someip_config_paths:
            if not Path(p).exists() or not self.someip.load_config_file(p):
                missing.append(p)
        for p in cfg.proto_paths:
            if not Path(p).exists() or not self.someip.load_proto_file(p):
                missing.append(p)
        self.can_conn.apply(cfg)
        self.eth_conn.apply(cfg)
        if missing:
            self.ctx.events.publish("log", "Project: could not load " + ", ".join(missing))
