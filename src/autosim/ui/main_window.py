"""Main window shell, organised like CANoe: one window per feature, grouped on desktops.

    menu bar    File Edit View Hardware Database Analysis Simulation Help ....... status pill, Connect all,
                palette, theme, ⚙       (a real QMenuBar under the OS title bar; menus: shell/menu_bar.py)
    rail        left icon rail: Setup / Simulation / Analysis desktops (Ctrl+1 / 2 / 3)
                  Setup       Channels | Databases
                  Simulation  Signals (interactive generator) | Auto response
                  Analysis    Bus trace over Signal plot
    bottom      Log on demand (Ctrl+J; the status-bar button counts unseen lines), status bar (bus load,
                frames/s, errors, elapsed)

Every window can be detached into its own window (header button or View > Detach window), e.g. the trace on a
second monitor while the Signals desktop is showing; a detached window stays visible whatever desktop is
selected, and closing it docks it back on its desktop.

Dock layout, theme, last port and last databases persist in QSettings. The dock layout is saved with
LAYOUT_VERSION: a layout stored by another version is discarded and the default layout used instead.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QFrame, QHBoxLayout, QMainWindow, QMessageBox,
                               QPushButton, QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from autosim.context import AppContext
from autosim.core.common.config import ProjectConfig
from autosim.ui.bridge import QtBridge
from autosim.ui.icons import make_icon
from autosim.ui.panels.auto_response_panel import AutoResponsePanel
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
from autosim.ui.shell.status_bar import AppStatusBar
from autosim.ui.theme import app_icon, apply_theme, font_report, icon_color, is_dark

log = logging.getLogger(__name__)

LAYOUT_VERSION = 5                 # bump whenever panels are added / renamed / restructured (5: desktops)
# desktop -> its windows, in workflow order (CANoe desktops)
DESKTOPS = (("Setup", ("Channels", "Databases")),
            ("Simulation", ("Signals", "Auto response")),
            ("Analysis", ("Bus trace", "Signal plot")))
DESKTOP_ICONS = {"Setup": "plug", "Simulation": "pulse", "Analysis": "chart"}
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

    def add_panel(self, title: str, widget: QWidget, primary: bool = False) -> Panel:
        p = Panel(title, widget, primary=primary, parent=self)
        self.addDockWidget(Qt.LeftDockWidgetArea, p)
        self.docks[title] = p
        return p


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("AutoSim Studio")
        self.setWindowIcon(app_icon())
        self.resize(1560, 980)
        self.bridge = QtBridge(ctx.events, self)
        self.current_theme = str(app_settings().value("theme", "dark"))
        self.commands = CommandRegistry(self)
        self._log_unread = 0                             # log lines not seen while the log is hidden

        self.can_conn = CanConnectionPanel(ctx, self.bridge)
        self.dbc = DbcExplorer(ctx, self.bridge)
        self.signals_panel = SignalPanel(ctx)
        self.plot = PlotPanel(ctx, self.bridge)
        self.trace = TracePanel(ctx, self.bridge)
        self.auto_response = AutoResponsePanel(ctx, self.bridge)
        self.log = LogPanel(self.bridge)

        self.can_tab = self._build_desktops()
        self.center = QWidget()                          # app bar above the CAN dock workspace
        self.center_layout = QVBoxLayout(self.center)
        self.center_layout.setContentsMargins(0, 0, 0, 0)
        self.center_layout.setSpacing(0)
        self.center_layout.addWidget(self.can_tab, 1)
        self.setCentralWidget(self.center)

        self.log_dock = Panel("Log", self.log, parent=self)
        self.log_clear_btn = QPushButton("Clear")
        self.log_clear_btn.setObjectName("panel-text-btn")
        self.log_clear_btn.clicked.connect(self.log.clear)
        self.log_dock.header.accessory.addWidget(self.log_clear_btn)
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
        from autosim.ui.shell.menu_bar import AppMenuBar
        self.menu = AppMenuBar(self)
        self.setMenuBar(self.menu)                       # a real menu bar, right under the OS title bar
        self.menu.setCornerWidget(self.app_bar.widget, Qt.TopRightCorner)

        self.dbc.messageActivated.connect(self._add_message)
        self.can_conn.channelsChanged.connect(self.signals_panel.channels_changed)
        self.signals_panel.openDbcRequested.connect(self.dbc.load_dialog)
        self.signals_panel.filesDropped.connect(lambda files: [self.dbc.load_file(f) for f in files])
        self.bridge.dbLoaded.connect(lambda _p: self.signals_panel.refresh_empty())
        self.bridge.dbUnloaded.connect(lambda _p: self.signals_panel.drop_removed_jobs())
        self.bridge.canBatch.connect(self._on_batch)
        self.bridge.nodeState.connect(self._on_node_state)
        self.trace.clear_act.triggered.connect(self._trace_cleared)
        self.trace.pause.toggled.connect(lambda on: self.status.set_recording(not on))
        self.status.log_btn.toggled.connect(lambda on: self.log_dock.setVisible(on))
        self.log_dock.visibilityChanged.connect(lambda _v: self._sync_log_btn())
        self.bridge.log.connect(self._log_line)
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

    # ---- desktops ---------------------------------------------------------------------------------
    def _build_desktops(self) -> QWidget:
        self.signals_panel.setObjectName("signals")
        channels = QScrollArea()                         # the channel form never forces the window taller
        channels.setObjectName("channels-scroll")
        channels.setWidgetResizable(True)
        channels.setFrameShape(QFrame.NoFrame)
        channels.setWidget(self.can_conn)
        widgets = {"Channels": channels, "Databases": self.dbc, "Signals": self.signals_panel,
                   "Auto response": self.auto_response, "Bus trace": self.trace, "Signal plot": self.plot}
        self.desktops: dict[str, DockWorkspace] = {}
        self.panels: dict[str, Panel] = {}
        self.panel_desktop: dict[str, str] = {}
        self.desk_stack = QStackedWidget()
        for name, titles in DESKTOPS:
            ws = DockWorkspace()
            ws.setObjectName(f"desktop-{name}")
            for t in titles:
                self.panels[t] = ws.add_panel(t, widgets[t], primary=(t == "Bus trace"))
                self.panel_desktop[t] = name
            self.desktops[name] = ws
            self.desk_stack.addWidget(ws)
        from autosim.ui.shell.app_bar import DesktopRail
        self.rail = DesktopRail([(n, DESKTOP_ICONS[n], f"{n}: {', '.join(t)}  (Ctrl+{i})")
                                 for i, (n, t) in enumerate(DESKTOPS, 1)])
        self.rail.changed.connect(self._desktop_changed)
        self._sized: set[str] = set()
        p = self.panels
        self.chan_card, self.db_card = p["Channels"], p["Databases"]
        self.signals_card, self.auto_card = p["Signals"], p["Auto response"]
        self.trace_card, self.plot_card = p["Bus trace"], p["Signal plot"]
        self.fold_all_btn = QPushButton("Collapse messages")    # Signals header: fold every message to one line
        self.fold_all_btn.setObjectName("panel-text-btn")
        self.fold_all_btn.setToolTip("Collapse / expand every message, to see many messages at once")
        self.fold_all_btn.clicked.connect(self._fold_all_messages)
        self.signals_card.header.accessory.addWidget(self.fold_all_btn)
        self.plot_card.header.accessory.addWidget(self.plot.controls)  # plot controls in the header: no toolbar

        self._arrange_desktops()
        box = QWidget()                                  # rail | desktop
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.rail)
        lay.addWidget(self.desk_stack, 1)
        self.rail.set_current(0)
        return box

    def desktop(self) -> str:
        return DESKTOPS[self.desk_stack.currentIndex()][0]

    def show_desktop(self, name: str) -> None:
        self.rail.set_current([n for n, _ in DESKTOPS].index(name))

    def _desktop_changed(self, i: int) -> None:
        self.desk_stack.setCurrentIndex(i)
        self.rail.refresh_icons()
        name = DESKTOPS[i][0]
        if name not in self._sized and self.isVisible():     # proportions once the desktop has its real size
            QTimer.singleShot(0, lambda n=name: self._size_desktop(n))

    def _arrange_desktops(self) -> None:
        """Default window arrangement of every desktop (detached windows are docked back)."""
        for name, titles in DESKTOPS:
            ws = self.desktops[name]
            for t in titles:
                p = self.panels[t]
                p.attach()
                ws.removeDockWidget(p)
            first = self.panels[titles[0]]
            ws.addDockWidget(Qt.LeftDockWidgetArea, first)
            for t in titles[1:]:
                ws.addDockWidget(Qt.LeftDockWidgetArea, self.panels[t])
                # Analysis: trace over the plot; Setup / Simulation: side by side
                ws.splitDockWidget(first, self.panels[t], Qt.Vertical if name == "Analysis" else Qt.Horizontal)
            for t in titles:
                self.panels[t].show()
        self._sized.clear()

    def _size_desktop(self, name: str) -> None:
        """Proportional sizes for one desktop (a hidden desktop is tiny until it is first shown)."""
        ws = self.desktops[name]
        w = ws.width() if ws.width() > 900 else self.width() - 20
        h = ws.height() if ws.height() > 400 else self.height() - 200
        a, b = (self.panels[t] for t in dict(DESKTOPS)[name])
        if name == "Analysis":
            ws.resizeDocks([a, b], [int(h * 0.62), int(h * 0.38)], Qt.Vertical)
        else:
            left = 0.42 if name == "Setup" else 0.56
            first = max(int(w * left), a.widget().minimumSizeHint().width() + 24)
            ws.resizeDocks([a, b], [first, max(240, w - first)], Qt.Horizontal)
        self._sized.add(name)

    def _size_can_docks(self) -> None:
        for name, _ in DESKTOPS:
            if self.desktops[name].isVisible():
                self._size_desktop(name)

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
        bar.status.clicked.connect(self.show_channels)
        bar.connect_btn.clicked.connect(self.quick_connect)
        bar.palette_btn.clicked.connect(self.show_palette)
        bar.theme_btn.clicked.connect(self.toggle_theme)

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
        for i, (name, titles) in enumerate(DESKTOPS, 1):
            r.add(f"desk.{name}", f"Desktop: {name} ({', '.join(titles)})", lambda n=name: self.show_desktop(n),
                  f"Ctrl+{i}", "View")
        r.add("view.log", "Toggle log", lambda: self.set_panel_visible("Log", self.log_dock.isHidden()),
              "Ctrl+J", "View")
        r.add("view.reset", "Reset layout", self.reset_layout, "", "View")
        r.add("view.all", "Show all panels", self.show_all_panels, "", "View")
        r.add("view.theme", "Toggle light / dark theme", self.toggle_theme, "Ctrl+Shift+T", "View")
        for title in (*self.panels, "Log"):
            r.add(f"show.{title}", f"Show window: {title}", lambda t_=title: self.set_panel_visible(t_, True), "",
                  "View")
            r.add(f"detach.{title}", f"Detach window: {title}", lambda t_=title: self.pop_out(t_), "", "View")
        r.add("log.clear", "Clear log", self.log.clear, "", "View")
        r.add("settings", "Settings", self.show_settings, "Ctrl+,", "App")
        r.add("app.quit", "Exit", self.close, "Ctrl+Q", "App")
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
        r.add("can.auto_response", "Toggle auto response", self.toggle_auto_response, "Ctrl+Shift+R", "CAN")
        r.add("can.auto_response.add", "Add auto response rule…", self.add_auto_response_rule, "", "CAN")
        r.add("project.open", "Open project…", self.open_project, "", "Project")
        r.add("project.save", "Save project…", self.save_project, "Ctrl+S", "Project")
        # tooltips show the shortcut
        t.pause.setToolTip(r.tooltip("Pause / resume the trace display", "trace.pause"))
        t.clear_act.setToolTip(r.tooltip("Clear the trace", "trace.clear"))
        t.export_act.setToolTip(r.tooltip("Export every frame to CSV / ASC / BLF", "trace.export"))
        t.f_name.setToolTip(r.tooltip("Search message / signal names", "trace.search"))
        bar = self.app_bar
        bar.palette_btn.setToolTip(r.tooltip("Command palette", "palette"))
        self.btn_settings.setToolTip(r.tooltip("Settings", "settings"))
        self._sync_theme_tip()

    def toggle_auto_response(self) -> None:
        self.auto_response.set_enabled(not self.ctx.can.responder.enabled)
        self.set_panel_visible("Auto response", True)

    def add_auto_response_rule(self) -> None:
        self.set_panel_visible("Auto response", True)
        self.auto_response.add_rule()

    def _fold_all_messages(self) -> None:
        cards = self.signals_panel.cards
        open_now = any(c.is_open for c in cards)
        self.signals_panel.set_all_open(not open_now)
        self.fold_all_btn.setText("Expand messages" if open_now and cards else "Collapse messages")

    def _focus_trace_search(self) -> None:
        self.set_panel_visible("Bus trace", True)

        def focus():                                     # after the desktop switch has shown the field
            self.trace.f_name.setFocus(Qt.ShortcutFocusReason)
            self.trace.f_name.selectAll()
        focus()
        QTimer.singleShot(0, focus)

    def _sync_theme_tip(self) -> None:
        self.app_bar.theme_btn.setToolTip(self.commands.tooltip(
            "Switch to light theme" if is_dark() else "Switch to dark theme", "view.theme"))

    # ---- windows -------------------------------------------------------------------------------
    def dock_registry(self) -> dict[str, QDockWidget]:
        """Every window by title (desktop windows + the shared Log)."""
        return {**self.panels, "Log": self.log_dock}

    def set_panel_visible(self, title: str, on: bool) -> None:
        """Show (and bring forward: switch to its desktop unless it is detached) or hide a window."""
        d = self.dock_registry()[title]
        if on and title in self.panel_desktop and not d.isFloating():
            self.show_desktop(self.panel_desktop[title])
        d.setVisible(on)
        if on:
            d.raise_()
            if d.isFloating():
                d.activateWindow()

    def show_all_panels(self) -> None:
        for d in self.dock_registry().values():
            d.show()

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
        """Detach a window into its own top-level window (another monitor, side by side with any desktop)."""
        d = self.dock_registry()[title]
        if not d.features() & QDockWidget.DockWidgetFloatable:
            return
        d.show()
        d.detach()

    def show_channels(self) -> None:
        self.set_panel_visible("Channels", True)

    def _log_line(self, _text: str) -> None:
        """While the log is hidden the status-bar button counts the new lines ("Log · 3")."""
        if self.log_dock.isHidden():
            self._log_unread += 1
            self._sync_log_btn()

    def _sync_log_btn(self) -> None:
        b = self.status.log_btn
        b.blockSignals(True)
        b.setChecked(not self.log_dock.isHidden())
        b.blockSignals(False)
        if b.isChecked():
            self._log_unread = 0
        b.setText(f"Log · {self._log_unread}" if self._log_unread else "Log")
        b.setToolTip(self.commands.tooltip("Hide the log" if b.isChecked() else "Show the log", "view.log"))

    def _focus_changed(self, _old, new) -> None:
        """The panel holding the keyboard focus gets an accent-tinted header."""
        owner = None
        w = new
        while w is not None:
            if isinstance(w, Panel):
                owner = w
                break
            w = w.parentWidget()
        for p in self.dock_registry().values():
            p.header.set_focused(p is owner)

    # ---- theme --------------------------------------------------------------------------------------
    def set_theme(self, mode: str) -> None:
        """Switch theme live and remember it for the next start."""
        self.current_theme = mode
        self.setUpdatesEnabled(False)                     # one repaint with the new theme, no half-styled frames
        try:
            dark = apply_theme(QApplication.instance(), mode)
            self.plot.apply_theme(dark)
            self.trace.refresh_icons()
            self._refresh_gear()
        finally:
            self.setUpdatesEnabled(True)
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
                main = s.value("layout/main")
                desks = {n: s.value(f"layout/desk_{n}") for n, _ in DESKTOPS}
                restored = (main is not None and all(v is not None for v in desks.values())
                            and self.restoreState(main, LAYOUT_VERSION)
                            and all(self.desktops[n].restoreState(v, LAYOUT_VERSION) for n, v in desks.items()))
                if restored:
                    self._sized.update(n for n, _ in DESKTOPS)      # saved sizes win over the defaults
                    for d in self.dock_registry().values():         # detached windows come back as windows
                        if d.isFloating():
                            d.detach()
            if not restored:
                self.ctx.events.publish("log", "Saved window layout is from another version: default layout used")
        if not restored:
            self._default_layout = True                    # sizes are applied once the window has its real size
            self.reset_layout()
        names = [n for n, _ in DESKTOPS]
        desk = str(s.value("layout/desktop", "Setup"))
        self.show_desktop(desk if desk in names else "Setup")
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
        self._arrange_desktops()
        self.log_dock.attach()
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self._size_log()
        self.log_dock.hide()                              # on demand (Ctrl+J / status bar): the windows get the room

    def _size_log(self) -> None:
        self.resizeDocks([self.log_dock], [140 if self.height() >= 900 else 110], Qt.Vertical)

    _reset_layout = reset_layout                          # older name, used by the settings dialog / tests

    def save_layout(self) -> None:
        s = app_settings()
        s.setValue("layout/version", LAYOUT_VERSION)
        s.setValue("layout/geometry", self.saveGeometry())
        s.setValue("layout/main", self.saveState(LAYOUT_VERSION))
        for n, ws in self.desktops.items():
            s.setValue(f"layout/desk_{n}", ws.saveState(LAYOUT_VERSION))
        s.setValue("layout/desktop", self.desktop())
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
        cfg.auto_response = self.auto_response.to_config()
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
        if cfg.auto_response:                              # older projects have none: keep the current rules
            self.auto_response.load_config(cfg.auto_response)
        if missing:
            self.ctx.events.publish("log", "Project: could not load " + ", ".join(missing))
