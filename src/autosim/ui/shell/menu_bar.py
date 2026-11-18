"""Classic menu bar, organised like CANoe / CANalyzer:

    File  Edit  View  Hardware  Database  Trace  Simulation  Help

Every entry runs a command of the CommandRegistry (the same actions as the command palette and the shortcuts),
so a menu item, its shortcut and its palette entry can never disagree. The shortcut is shown in the menu but bound
only once, by the registry (a QAction shortcut would register it a second time and make it ambiguous).
Checkable entries (panels, trace modes, auto response, theme) read their state each time the menu opens.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QMenuBar

PANELS = ("Signals", "Signal plot", "Auto response", "Bus trace", "Log")


class AppMenuBar(QMenuBar):
    def __init__(self, win) -> None:
        super().__init__(win)
        self.win = win
        self.setObjectName("app-menubar")
        self.setNativeMenuBar(False)              # same look on every OS, inside the window
        self.menus: dict[str, QMenu] = {}
        self._checks: list[tuple[QAction, Callable[[], bool]]] = []
        self._enables: list[tuple[QAction, Callable[[], bool]]] = []
        self._build()

    # ---- helpers -------------------------------------------------------------------------------
    def _menu(self, title: str) -> QMenu:
        m = self.addMenu(title)
        m.aboutToShow.connect(self._sync)
        self.menus[title.replace("&", "")] = m
        return m

    def _cmd(self, menu: QMenu, cid: str, text: str | None = None, checked: Callable[[], bool] | None = None) -> QAction:
        """Menu entry for registry command `cid` (title, shortcut text, enabled state from the registry)."""
        cmd = self.win.commands.commands[cid]
        label = text or cmd.title
        a = menu.addAction(f"{label}\t{cmd.shortcut}" if cmd.shortcut else label)
        a.setData(cid)
        a.triggered.connect(lambda _c=False, c=cid: self.win.commands.run(c))
        if cmd.enabled is not None:
            self._enables.append((a, cmd.is_enabled))
        if checked is not None:
            a.setCheckable(True)
            self._checks.append((a, checked))
        return a

    def _action(self, menu: QMenu, text: str, fn: Callable[[], None], enabled: Callable[[], bool] | None = None,
                checked: Callable[[], bool] | None = None) -> QAction:
        a = menu.addAction(text)
        if checked is not None:
            a.setCheckable(True)
            self._checks.append((a, checked))
            a.triggered.connect(lambda on: fn(on))
        else:
            a.triggered.connect(lambda _c=False: fn())
        if enabled is not None:
            self._enables.append((a, enabled))
        return a

    def _sync(self) -> None:
        for a, fn in self._checks:
            a.blockSignals(True)
            a.setChecked(bool(fn()))
            a.blockSignals(False)
        for a, fn in self._enables:
            a.setEnabled(bool(fn()))

    # ---- menus ---------------------------------------------------------------------------------
    def _build(self) -> None:
        w = self.win
        t = w.trace
        has_channels = lambda: bool(w.can_conn._open)  # noqa: E731

        m = self._menu("&File")
        self._cmd(m, "project.open", "Open project…")
        self._cmd(m, "project.save", "Save project…")
        m.addSeparator()
        self._cmd(m, "can.dbc", "Load database (DBC / ARXML)…")
        self._cmd(m, "trace.export", "Export trace (CSV / ASC / BLF)…")
        m.addSeparator()
        self._cmd(m, "settings", "Settings…")
        m.addSeparator()
        self._cmd(m, "app.quit", "Exit")

        m = self._menu("&Edit")
        self._cmd(m, "trace.search", "Find in trace")
        self._cmd(m, "trace.clear", "Clear trace")
        m.addSeparator()
        self._cmd(m, "palette", "Command palette…")

        m = self._menu("&View")
        self.panel_actions: dict[str, QAction] = {}
        self.panel_actions["Sidebar"] = self._action(m, "Sidebar (Channels, Database)\tCtrl+B",
                                                     lambda on: w.set_panel_visible("Sidebar", on),
                                                     checked=w.sidebar_visible)
        for title in PANELS:
            dock = w.dock_registry()[title]
            label = f"{title}\tCtrl+J" if title == "Log" else title
            self.panel_actions[title] = self._action(m, label, lambda on, t_=title: w.set_panel_visible(t_, on),
                                                     checked=lambda d=dock: not d.isHidden())
        m.addSeparator()
        self._cmd(m, "view.all", "Show all panels")
        self._cmd(m, "view.reset", "Reset layout")
        m.addSeparator()
        theme = m.addMenu("Theme")
        theme.aboutToShow.connect(self._sync)
        group = QActionGroup(theme)
        for mode, label in (("dark", "Dark"), ("light", "Light"), ("auto", "Follow system")):
            a = self._action(theme, label, lambda _on, md=mode: w.set_theme(md),
                             checked=lambda md=mode: w.current_theme == md)
            group.addAction(a)
        self.theme_menu = theme

        m = self._menu("&Hardware")
        self._action(m, "Channel configuration…", w.show_channels)
        self._action(m, "Scan adapters", w.can_conn.scan)
        m.addSeparator()
        self._cmd(m, "can.connect", "Connect all")
        self._cmd(m, "can.disconnect", "Disconnect all…")

        m = self._menu("&Database")
        self._cmd(m, "can.dbc", "Load database…")
        self.db_files = m.addMenu("Loaded databases")
        self.db_files.aboutToShow.connect(self._fill_databases)
        m.addSeparator()
        self._action(m, "Database browser", self.show_database)

        m = self._menu("&Trace")
        self._action(m, "Pause\tSpace", lambda on: t.pause.setChecked(on), checked=t.pause.isChecked)
        self._action(m, "Fixed view (one row per message)", lambda on: t.fixed.setChecked(on),
                     checked=t.fixed.isChecked)
        self._action(m, "Follow newest frames", lambda on: t.autoscroll.setChecked(on),
                     checked=t.autoscroll.isChecked)
        self._action(m, "Highlight changed data", lambda on: t.flash.setChecked(on), checked=t.flash.isChecked)
        m.addSeparator()
        self._cmd(m, "trace.clear", "Clear")
        self._cmd(m, "trace.export", "Export…")

        m = self._menu("&Simulation")
        self._action(m, "Auto response enabled\tCtrl+Shift+R",
                     lambda on: w.auto_response.set_enabled(on),
                     checked=lambda: w.ctx.can.responder.enabled)
        self._cmd(m, "can.auto_response.add", "Add auto response rule…")
        m.addSeparator()
        self._cmd(m, "can.stop_tx", "Stop all cyclic transmission")
        self._action(m, "Signals panel", lambda: w.set_panel_visible("Signals", True), enabled=has_channels)

        m = self._menu("&Help")
        self._cmd(m, "palette", "Commands and shortcuts…")
        self._action(m, "Documentation folder", self.open_docs)
        m.addSeparator()
        self._action(m, "About AutoSim Studio", self.about)

    # ---- entries with their own logic -------------------------------------------------------------
    def _fill_databases(self) -> None:
        m = self.db_files
        m.clear()
        paths = self.win.ctx.can.registry.paths
        if not paths:
            a = m.addAction("No database loaded")
            a.setEnabled(False)
            return
        from pathlib import Path
        for p in paths:
            sub = m.addMenu(Path(p).name)
            sub.setToolTipsVisible(True)
            sub.addAction("Reload", lambda p_=p: self.win.dbc.load_file(p_))
            sub.addAction("Remove…", lambda p_=p: self.win.dbc.remove_file(p_))

    def show_database(self) -> None:
        w = self.win
        if not w.sidebar_visible():
            w.toggle_sidebar()
        w.sidebar.sections["database"].set_expanded(True)

    def open_docs(self) -> None:
        from pathlib import Path
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        docs = Path(__file__).resolve().parents[4] / "docs"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(docs if docs.exists() else Path.cwd())))

    def about(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.about(self.win, "About AutoSim Studio",
                          "AutoSim Studio\nCAN / CAN FD simulation and analysis\n\n"
                          "Adapters: ESP32 + MCP2518FD, CANable (slcan), Android vcan bridge")
