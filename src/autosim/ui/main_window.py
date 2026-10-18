"""Main window shell.

    app bar     [sidebar] AutoSim Studio ......... status pill, Connect all, palette, View menu, theme, ⚙
    workspace   CAN dock workspace: collapsible sidebar (Channels, Database) | Signals + Signal plot over Bus trace
    bottom      Log panel (collapsible), status bar (bus load, frames/s, errors, trace size, recording, elapsed)

Dock layout, theme, last port and last databases persist in QSettings. The dock layout is saved with
LAYOUT_VERSION: a layout stored by another version is discarded and the default layout used instead.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QMainWindow, QMessageBox,
                               QToolButton, QVBoxLayout, QWidget)

from autosim.context import AppContext
from autosim.core.common.config import ProjectConfig
from autosim.ui.bridge import QtBridge
from autosim.ui.icons import make_icon
from autosim.ui.panels.connection_panel import CanConnectionPanel
from autosim.ui.panels.dbc_explorer import DbcExplorer
from autosim.ui.panels.log_panel import LogPanel
from autosim.ui.panels.plot_panel import PlotPanel
from autosim.ui.panels.signal_panel import SignalPanel
from autosim.ui.panels.trace_panel import TracePanel
from autosim.ui.settings_dialog import SettingsDialog
from autosim.ui.settings_store import app_settings
from autosim.ui.shell.app_bar import AppBar
from autosim.ui.shell.bus_load import BusLoadMeter
from autosim.ui.shell.commands import CommandPalette, CommandRegistry
from autosim.ui.shell.panels import Panel
from autosim.ui.shell.sidebar import Sidebar
from autosim.ui.shell.status_bar import AppStatusBar
from autosim.ui.styles.tokens import MOTION_BASE
from autosim.ui.theme import apply_theme, font_report, icon_color, is_dark

log = logging.getLogger(__name__)

LAYOUT_VERSION = 3                 # bump whenever panels are added / renamed / restructured (3: CAN only)
QWIDGETSIZE_MAX = (1 << 24) - 1


class DockWorkspace(QMainWindow):
    """A QMainWindow embedded as a page. All panels live in one dock area with nesting, so there is no fixed
    central widget: every panel (the trace included) can be moved, tabbed, resized or floated."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.Widget)
        self.setDockNestingEnabled(True)
        self.setDockOptions(QMainWindow.AnimatedDocks | QMainWindow.AllowNestedDocks | QMainWindow.AllowTabbedDocks)
        self.docks: dict[str, QDockWidget] = {}

    def add_panel(self, title: str, widget: QWidget, primary: bool = False, header: bool = True) -> Panel:
        p = Panel(title, widget, primary=primary, parent=self)
        if not header:                                   # the sidebar: fixed, no title bar
            p.setTitleBarWidget(QWidget())
            p.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.addDockWidget(Qt.LeftDockWidgetArea, p)
        self.docks[title] = p
        return p


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("AutoSim Studio")
        self.resize(1560, 980)
        self.bridge = QtBridge(ctx.events, self)
        self.current_theme = str(app_settings().value("theme", "dark"))
        self.commands = CommandRegistry(self)

        self.can_conn = CanConnectionPanel(ctx, self.bridge)
        self.dbc = DbcExplorer(ctx, self.bridge)
        self.signals_panel = SignalPanel(ctx)
        self.plot = PlotPanel(ctx, self.bridge)
        self.trace = TracePanel(ctx, self.bridge)
        self.log = LogPanel(self.bridge)

        self.can_tab = self._build_can_tab()
        self.center = QWidget()                          # app bar above the CAN dock workspace
        self.center_layout = QVBoxLayout(self.center)
        self.center_layout.setContentsMargins(0, 0, 0, 0)
        self.center_layout.setSpacing(0)
        self.center_layout.addWidget(self.can_tab, 1)
        self.setCentralWidget(self.center)

        self.log_dock = Panel("Log", self.log, parent=self)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)

        self._build_settings()
        self._build_app_bar()
        self.status = AppStatusBar()
        self.setStatusBar(self.status)
        self.bus_load = BusLoadMeter(self._channel_rates)
        self._node_states: dict[str, dict] = {}
        self._trace_t0: float | None = None
        self.palette_popup = CommandPalette(self.commands, self)
        self._register_commands()

        self.dbc.messageActivated.connect(self._add_message)
        self.signals_panel.openDbcRequested.connect(self.dbc.load_dialog)
        self.signals_panel.filesDropped.connect(lambda files: [self.dbc.load_file(f) for f in files])
        self.bridge.dbLoaded.connect(lambda _p: self.signals_panel.refresh_empty())
        self.bridge.canBatch.connect(self._on_batch)
        self.bridge.nodeState.connect(self._on_node_state)
        self.trace.clear_act.triggered.connect(self._trace_cleared)
        self.trace.pause.toggled.connect(lambda on: self.status.set_recording(not on))
        self.status.log_btn.toggled.connect(lambda on: self.log_dock.setVisible(on))
        self.log_dock.visibilityChanged.connect(lambda _v: self._sync_log_btn())
        QApplication.instance().focusChanged.connect(self._focus_changed)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._default_layout = False
        self._restore()
        ok, text = font_report()                         # which fonts were really resolved (support / review)
        self.ctx.events.publish("log", text)
        if ok:
            log.info(text)
        else:
            log.warning(text)

    # ---- CAN workspace --------------------------------------------------------------------------
    def _build_can_tab(self) -> DockWorkspace:
        self.signals_panel.setObjectName("signals")
        self.sidebar = Sidebar()
        self.sidebar.add_section("channels", "Channels", self.can_conn)
        self.sidebar.add_section("database", "Database", self.dbc, stretch=True)
        ws = DockWorkspace()
        self.can_ws = ws
        self.sidebar_dock = ws.add_panel("Sidebar", self.sidebar, header=False)
        self.signals_card = ws.add_panel("Signals", self.signals_panel)
        self.plot_card = ws.add_panel("Signal plot", self.plot)
        self.trace_card = ws.add_panel("Bus trace", self.trace, primary=True)
        self._arrange_can()
        return ws

    def _arrange_can(self) -> None:
        """Default layout: sidebar left; Signals | Signal plot on top (35 %), Bus trace below (65 %)."""
        ws, d = self.can_ws, self.can_ws.docks
        for p in d.values():
            p.setFloating(False)
            p.show()
        self.sidebar_dock.setMaximumWidth(QWIDGETSIZE_MAX)
        ws.addDockWidget(Qt.LeftDockWidgetArea, d["Sidebar"])
        ws.addDockWidget(Qt.LeftDockWidgetArea, d["Signals"])
        ws.splitDockWidget(d["Sidebar"], d["Signals"], Qt.Horizontal)
        ws.splitDockWidget(d["Signals"], d["Bus trace"], Qt.Vertical)
        ws.splitDockWidget(d["Signals"], d["Signal plot"], Qt.Horizontal)
        self._size_can_docks()

    def _size_can_docks(self) -> None:
        """Proportional default sizes (the workspace is tiny until the window is shown)."""
        ws, d = self.can_ws, self.can_ws.docks
        w = ws.width() if ws.width() > 900 else self.width() - 20
        h = ws.height() if ws.height() > 500 else self.height() - 140
        side = max(self.can_conn.minimumSizeHint().width() + 16, min(440, int(w * 0.27)))   # form never clipped
        rest = w - side
        sig = max(int(rest * 0.6), self.signals_panel.minimumWidth() + 90)       # grid + sliders when possible
        ws.resizeDocks([d["Sidebar"], d["Signals"], d["Signal plot"]],
                       [side, sig, max(200, rest - sig)], Qt.Horizontal)
        ws.resizeDocks([d["Signals"], d["Bus trace"]], [int(h * 0.35), int(h * 0.65)], Qt.Vertical)

    # ---- app bar / settings -------------------------------------------------------------------
    def _build_settings(self) -> None:
        self.btn_settings = QToolButton()
        self.btn_settings.setObjectName("settings-btn")
        self.btn_settings.setIconSize(QSize(18, 18))
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.setAccessibleName("Settings")
        self._refresh_gear()
        self.settings_dialog = SettingsDialog(self)
        self.btn_settings.clicked.connect(self.show_settings)

    def _build_app_bar(self) -> None:
        self.app_bar = bar = AppBar(self.btn_settings)
        self.center_layout.insertWidget(0, bar.widget)
        bar.sidebar_btn.clicked.connect(lambda: self.toggle_sidebar())
        bar.status.clicked.connect(self.show_channels)
        bar.connect_btn.clicked.connect(self.quick_connect)
        bar.palette_btn.clicked.connect(self.show_palette)
        bar.theme_btn.clicked.connect(self.toggle_theme)
        bar.layout_menu.aboutToShow.connect(self._fill_layout_menu)

    def _fill_layout_menu(self) -> None:
        m = self.app_bar.layout_menu
        m.clear()
        for title, dock in self.dock_registry().items():
            a = m.addAction(title)
            a.setCheckable(True)
            a.setChecked(not dock.isHidden())
            a.toggled.connect(lambda on, t=title: self.set_panel_visible(t, on))
        m.addSeparator()
        m.addAction("Show all panels", self.show_all_panels)
        m.addAction("Reset layout", self.reset_layout)

    def _refresh_gear(self) -> None:
        self.btn_settings.setIcon(make_icon("gear", icon_color(), active=icon_color()))

    def show_settings(self) -> None:
        from PySide6.QtCore import QPoint
        dlg = self.settings_dialog
        dlg.sync()
        dlg.adjustSize()
        dlg.move(self.btn_settings.mapToGlobal(QPoint(self.btn_settings.width() - dlg.width(),
                                                      self.btn_settings.height() + 6)))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def show_palette(self) -> None:
        self.palette_popup.open_centered()

    # ---- commands / shortcuts ----------------------------------------------------------------
    def _register_commands(self) -> None:
        r, t = self.commands, self.trace

        def on_can(fn):                                  # kept for readability: every command is a CAN command
            return fn
        r.add("palette", "Command palette", self.show_palette, "Ctrl+K", "View")
        r.add("view.sidebar", "Toggle sidebar", lambda: self.toggle_sidebar(), "Ctrl+B", "View")
        r.add("view.log", "Toggle log", lambda: self.set_panel_visible("Log", self.log_dock.isHidden()),
              "Ctrl+J", "View")
        r.add("view.reset", "Reset layout", self.reset_layout, "", "View")
        r.add("view.all", "Show all panels", self.show_all_panels, "", "View")
        r.add("view.theme", "Toggle light / dark theme", self.toggle_theme, "Ctrl+Shift+T", "View")
        for title in ("Signals", "Signal plot", "Bus trace", "Log"):
            r.add(f"show.{title}", f"Show panel: {title}", lambda t_=title: self.set_panel_visible(t_, True), "", "View")
        r.add("settings", "Settings", self.show_settings, "Ctrl+,", "App")
        r.add("trace.pause", "Pause / resume trace", t.pause.toggle, "Space", "Trace")
        r.add("trace.clear", "Clear trace", on_can(t.clear_act.trigger), "Ctrl+L", "Trace")
        r.add("trace.export", "Export trace…", on_can(t.export_act.trigger), "Ctrl+E", "Trace")
        r.add("trace.search", "Search trace", on_can(self._focus_trace_search), "Ctrl+F", "Trace")
        r.add("trace.mode", "Toggle fixed / chronological trace", on_can(t.fixed.toggle), "", "Trace")
        r.add("can.connect", "Connect all adapters", on_can(lambda: self.can_conn.connect_all()), "", "CAN",
              enabled=lambda: not self.can_conn._open)
        r.add("can.disconnect", "Disconnect all channels", self.disconnect_all, "", "CAN",
              enabled=lambda: bool(self.can_conn._open))
        r.add("can.dbc", "Load DBC / ARXML…", self.open_database, "Ctrl+O", "CAN")
        r.add("can.stop_tx", "Stop all cyclic TX", self.signals_panel.stop_all, "", "CAN")
        r.add("project.open", "Open project…", self.open_project, "", "Project")
        r.add("project.save", "Save project…", self.save_project, "Ctrl+S", "Project")
        # tooltips show the shortcut
        t.pause.setToolTip(r.tooltip("Pause / resume the trace display", "trace.pause"))
        t.clear_act.setToolTip(r.tooltip("Clear the trace", "trace.clear"))
        t.export_act.setToolTip(r.tooltip("Export every frame to CSV / ASC / BLF", "trace.export"))
        t.f_name.setToolTip(r.tooltip("Search message / signal names", "trace.search"))
        bar = self.app_bar
        bar.sidebar_btn.setToolTip(r.tooltip("Toggle sidebar", "view.sidebar"))
        bar.palette_btn.setToolTip(r.tooltip("Command palette", "palette"))
        bar.layout_btn.setToolTip("View: show / hide panels, reset layout")
        self.btn_settings.setToolTip(r.tooltip("Settings", "settings"))
        self._sync_theme_tip()

    def _focus_trace_search(self) -> None:
        self.set_panel_visible("Bus trace", True)
        self.trace.f_name.setFocus(Qt.ShortcutFocusReason)
        self.trace.f_name.selectAll()

    def _sync_theme_tip(self) -> None:
        self.app_bar.theme_btn.setToolTip(self.commands.tooltip(
            "Switch to light theme" if is_dark() else "Switch to dark theme", "view.theme"))

    # ---- panels ---------------------------------------------------------------------------------
    def dock_registry(self) -> dict[str, QDockWidget]:
        """Every panel by title (CAN workspace panels + the shared Log panel)."""
        return {**self.can_ws.docks, "Log": self.log_dock}

    def set_panel_visible(self, title: str, on: bool) -> None:
        if title == "Sidebar":
            if on != self.sidebar_visible():
                self.toggle_sidebar(animate=False)
            return
        d = self.dock_registry()[title]
        d.setVisible(on)
        if on:
            d.raise_()

    def show_all_panels(self) -> None:
        for title in self.dock_registry():
            self.set_panel_visible(title, True)

    def _validate_layout(self) -> None:
        """A restored layout must not hide a panel by accident: a docked panel squeezed to (almost) nothing or a
        floating one outside every screen means the saved state does not fit this screen - fix it."""
        from PySide6.QtGui import QGuiApplication
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        broken = []
        for title, d in self.dock_registry().items():
            if d.isHidden():
                continue
            if d.isFloating():
                if not any(s.intersects(d.frameGeometry()) for s in screens):
                    d.setFloating(False)
                    broken.append(title)
            elif d.isVisible() and (d.width() < 40 or d.height() < 40):
                broken.append(title)
        if broken and any(not self.dock_registry()[t].isFloating() for t in broken):
            self.reset_layout()
            QTimer.singleShot(0, lambda: (self._size_log(), self._size_can_docks()))
        if broken:
            self.ctx.events.publish("log", f"Layout: {', '.join(broken)} was not visible on this screen - restored")

    def pop_out(self, title: str) -> None:
        d = self.dock_registry()[title]
        if not d.features() & QDockWidget.DockWidgetFloatable:
            return
        d.show()
        d.setFloating(True)

    def sidebar_visible(self) -> bool:
        return self.app_bar.sidebar_btn.isChecked()

    def toggle_sidebar(self, animate: bool = True) -> None:
        """Collapse / expand the sidebar (160 ms width animation)."""
        d = self.sidebar_dock
        show = d.isHidden() or getattr(self, "_sidebar_closing", False)
        self.app_bar.sidebar_btn.setChecked(show)
        anim = getattr(self, "_sidebar_anim", None)
        if anim is not None:
            anim.stop()
        if not animate or not self.isVisible():
            self._sidebar_closing = False
            d.setMaximumWidth(QWIDGETSIZE_MAX)
            d.setVisible(show)
            return
        if show:
            self._sidebar_closing = False
            target = getattr(self, "_sidebar_w", 340)
            d.setMaximumWidth(0)
            d.show()
            start, end = 0, target
        else:
            self._sidebar_closing = True
            self._sidebar_w = d.width()
            start, end = d.width(), 0
        self._sidebar_anim = anim = QPropertyAnimation(d, b"maximumWidth", self)
        anim.setDuration(MOTION_BASE)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(start)
        anim.setEndValue(end)

        def done():
            if self._sidebar_closing:
                d.hide()
                self._sidebar_closing = False
            else:
                self.can_ws.resizeDocks([d], [end], Qt.Horizontal)
            d.setMaximumWidth(QWIDGETSIZE_MAX)
        anim.finished.connect(done)
        anim.start()

    def show_channels(self) -> None:
        if not self.sidebar_visible():
            self.toggle_sidebar()
        self.sidebar.sections["channels"].set_expanded(True)
        self.sidebar.ensureWidgetVisible(self.can_conn)

    def _sync_log_btn(self) -> None:
        b = self.status.log_btn
        b.blockSignals(True)
        b.setChecked(not self.log_dock.isHidden())
        b.blockSignals(False)
        b.setToolTip(self.commands.tooltip("Hide the log" if b.isChecked() else "Show the log", "view.log"))

    def _focus_changed(self, _old, new) -> None:
        """The panel holding the keyboard focus gets an accent-tinted header."""
        owner = None
        w = new
        while w is not None:
            if isinstance(w, Panel):
                owner = None if w is self.sidebar_dock else w      # the sidebar has no header
                break
            w = w.parentWidget()
        for p in self.dock_registry().values():
            p.header.set_focused(p is owner)

    # ---- theme --------------------------------------------------------------------------------------
    def set_theme(self, mode: str) -> None:
        """Switch theme live and remember it for the next start."""
        self.current_theme = mode
        dark = apply_theme(QApplication.instance(), mode)
        self.plot.apply_theme(dark)
        self.trace.refresh_icons()
        self._refresh_gear()
        self._sync_theme_tip()
        if hasattr(self, "settings_dialog"):
            self.settings_dialog.sync_theme()
        app_settings().setValue("theme", mode)

    def toggle_theme(self) -> None:
        self.set_theme("light" if is_dark() else "dark")

    # ---- connections ------------------------------------------------------------------------------
    def quick_connect(self) -> None:
        if self.can_conn._open:
            self.disconnect_all()
        else:
            self.can_conn.connect_all()
        self._update_status()

    def disconnect_all(self) -> bool:
        """Close every channel after a confirmation (which mentions running cyclic transmission)."""
        n = len(self.can_conn._open)
        running = [c for c in self.signals_panel.cards if c.job.enabled]
        text = f"Disconnect all {n} channel{'s' if n != 1 else ''}?"
        if running:
            text += f"\nCyclic TX is running for {len(running)} message(s) and will stop."
        ans = QMessageBox.question(self, "Disconnect all", text, QMessageBox.Yes | QMessageBox.Cancel,
                                   QMessageBox.Cancel)
        if ans != QMessageBox.Yes:
            return False
        self.can_conn.disconnect_all()
        self._update_status()
        return True

    def _channel_rates(self, ch: str):
        n = self.can_conn._open.get(ch)
        return (n.nominal_bitrate, n.data_bitrate) if n else None

    def _on_node_state(self, st: dict) -> None:
        self._node_states[st["channel"]] = st

    def _on_batch(self, batch) -> None:
        self.bus_load.add_rows(batch)
        if self._trace_t0 is None and batch and not self.trace.pause.isChecked():
            self._trace_t0 = time.monotonic()

    def _trace_cleared(self) -> None:
        self._trace_t0 = None

    def _tick(self) -> None:
        self._update_status()

    def _update_status(self) -> None:
        open_ch = self.ctx.can.bus.channels
        states = [self._node_states.get(ch, {}) for ch in open_ch]
        errors = sum(int(s.get("errors") or 0) for s in states)
        warn = sum(1 for s in states if int(s.get("errors") or 0) or s.get("state") == "error")
        bar = self.app_bar
        bar.set_connected(bool(open_ch))
        if not open_ch:
            bar.status.set_state("offline", "No channels")
        else:
            text = f"{len(open_ch)} channel{'s' if len(open_ch) != 1 else ''}"
            if warn:
                text += f" · {warn} warning{'s' if warn != 1 else ''}"
            state = "error" if any(s.get("state") == "error" for s in states) else ("connecting" if warn else "online")
            bar.status.set_state(state, text)
        self.status.set_channels(self.bus_load.snapshot(), open_ch)
        self.status.set_errors(errors)
        self.status.set_trace(self.trace.fixed_model.message_count(), self.trace.model.rowCount())
        self.status.set_elapsed(0 if self._trace_t0 is None else time.monotonic() - self._trace_t0)

    # ---- files -------------------------------------------------------------------------------------
    def open_database(self) -> None:
        self.dbc.load_dialog()

    # ---- layout persistence -------------------------------------------------------------------------
    def _restore(self) -> None:
        s = app_settings()
        for old in ("geometry", "state", "can_state", "can_split", "top_split", "someip_split",
                    "layout/someip_split"):
            s.remove(old)                                   # keys of the pre-dock layout (version 1)
        geo = s.value("layout/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        restored = False
        if s.value("layout/version") is not None:
            if int(s.value("layout/version")) == LAYOUT_VERSION:
                main, can = s.value("layout/main"), s.value("layout/can")
                restored = (main is not None and can is not None and self.restoreState(main, LAYOUT_VERSION)
                            and self.can_ws.restoreState(can, LAYOUT_VERSION))
            if not restored:
                self.ctx.events.publish("log", "Saved window layout is from another version: default layout used")
        if not restored:
            self._default_layout = True                    # sizes are applied once the window has its real size
            self.reset_layout()
        for key, sec in self.sidebar.sections.items():
            v = s.value(f"layout/section_{key}")
            if v is not None:
                sec.set_expanded(str(v).lower() == "true")
        self.app_bar.sidebar_btn.setChecked(not self.sidebar_dock.isHidden())
        self._sync_log_btn()
        port = s.value("last/port")
        if port and self.can_conn.port.findText(str(port)) >= 0:
            self.can_conn.port.setCurrentText(str(port))
        dbcs = s.value("last/dbc") or []
        if isinstance(dbcs, str):
            dbcs = [dbcs]
        existing = [p for p in dbcs if Path(p).exists() and p not in self.ctx.can.registry.paths]
        if existing:
            QTimer.singleShot(0, lambda: [self.dbc.load_file(p) for p in existing])

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not getattr(self, "_validated", False):
            self._validated = True
            QTimer.singleShot(50, self._validate_layout)
        if self._default_layout:
            self._default_layout = False
            QTimer.singleShot(0, lambda: (self._size_log(), self._size_can_docks()))

    def reset_layout(self) -> None:
        self._arrange_can()
        if hasattr(self, "app_bar"):
            self.app_bar.sidebar_btn.setChecked(True)
        self.log_dock.setFloating(False)
        self.log_dock.show()
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self._size_log()

    def _size_log(self) -> None:
        self.resizeDocks([self.log_dock], [140 if self.height() >= 900 else 110], Qt.Vertical)

    _reset_layout = reset_layout                          # older name, used by the settings dialog / tests

    def save_layout(self) -> None:
        s = app_settings()
        s.setValue("layout/version", LAYOUT_VERSION)
        s.setValue("layout/geometry", self.saveGeometry())
        s.setValue("layout/main", self.saveState(LAYOUT_VERSION))
        s.setValue("layout/can", self.can_ws.saveState(LAYOUT_VERSION))
        for key, sec in self.sidebar.sections.items():
            s.setValue(f"layout/section_{key}", sec.expanded)
        s.setValue("last/port", self.can_conn.port.currentText())
        s.setValue("last/dbc", self.ctx.can.registry.paths)

    def closeEvent(self, e) -> None:
        self.signals_panel.stop_all()
        self.save_layout()
        for d in self.dock_registry().values():          # floating panels must not outlive the app
            if d.isFloating():
                d.setFloating(False)
        self.commands.uninstall()
        self.ctx.shutdown()
        super().closeEvent(e)

    # ---- actions ------------------------------------------------------------------
    def _add_message(self, name: str) -> None:
        if self.signals_panel.add_message(name):
            self.set_panel_visible("Signals", True)

    def save_project(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save project", "autosim.project.json", "Project (*.json)")
        if not path:
            return
        cfg = self.ctx.config
        cfg.dbc_paths = self.ctx.can.registry.paths
        self.can_conn.collect(cfg)
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
        self.can_conn.apply(cfg)
        if missing:
            self.ctx.events.publish("log", "Project: could not load " + ", ".join(missing))
