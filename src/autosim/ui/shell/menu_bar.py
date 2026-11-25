"""Classic menu bar, organised like CANoe / CANalyzer:

    File  Edit  View  Hardware  Database  Analysis  Simulation  Help

Each feature menu starts with the entry that opens its window (Hardware > Channels window, Database > Databases
window, Analysis > Trace window, Simulation > Signals / Auto response window); View switches
desktops, opens / closes any window and detaches windows into their own top-level windows.

Every entry runs a command of the CommandRegistry (the same actions as the command palette and the shortcuts),
so a menu item, its shortcut and its palette entry can never disagree. The shortcut is shown in the menu but bound
only once, by the registry (a QAction shortcut would register it a second time and make it ambiguous).
Checkable entries (panels, trace modes, auto response, theme) read their state each time the menu opens.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QMenuBar


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
    def _window(self, menu: QMenu, title: str, label: str) -> QAction:
        """'Open' entry of a feature window: shows it (switching to its desktop) and is ticked while it is open."""
        dock = self.win.dock_registry()[title]
        return self._action(menu, label, lambda on, t=title: self.win.set_panel_visible(t, on),
                            checked=lambda d=dock: not d.isHidden())

    def _build(self) -> None:
        from autosim.ui.main_window import DESKTOPS
        w = self.win
        t = w.trace

        m = self._menu("&File")
        self._cmd(m, "project.open", "Open project…")
        self._cmd(m, "project.save", "Save project…")
        m.addSeparator()
        self._cmd(m, "board.open", "Open signal board…")
        self._cmd(m, "board.save", "Save signal board…")
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
        self._cmd(m, "log.clear", "Clear log")
        m.addSeparator()
        self._cmd(m, "palette", "Command palette…")

        m = self._menu("&View")
        group = QActionGroup(m)
        for name, _titles in DESKTOPS:                     # desktops, one per feature group
            a = self._cmd(m, f"desk.{name}", f"{name} desktop", checked=lambda n=name: w.desktop() == n)
            group.addAction(a)
        m.addSeparator()
        self.panel_actions: dict[str, QAction] = {}
        for title in (*w.panels, "Log"):
            label = f"{title}\tCtrl+J" if title == "Log" else title
            self.panel_actions[title] = self._window(m, title, label)
        detach = m.addMenu("Detach window")
        detach.setToolTipsVisible(True)
        for title in (*w.panels, "Log"):
            self._action(detach, title, lambda t_=title: w.pop_out(t_))
        self.detach_menu = detach
        m.addSeparator()
        self._cmd(m, "view.all", "Show all windows")
        self._cmd(m, "view.reset", "Reset layout")
        m.addSeparator()
        theme = m.addMenu("Theme")
        theme.aboutToShow.connect(self._sync)
        tgroup = QActionGroup(theme)
        for mode, label in (("dark", "Dark"), ("light", "Light"), ("auto", "Follow system")):
            a = self._action(theme, label, lambda _on, md=mode: w.set_theme(md),
                             checked=lambda md=mode: w.current_theme == md)
            tgroup.addAction(a)
        self.theme_menu = theme

        m = self._menu("&Hardware")
        self._window(m, "Channels", "Channels window")
        m.addSeparator()
        self._action(m, "Scan adapters", w.can_conn.scan)
        self._cmd(m, "can.connect", "Connect all")
        self._cmd(m, "can.disconnect", "Disconnect all…")
        m.addSeparator()
        self._action(m, "Download PEAK driver (PCAN)…", self.open_peak_driver_page)

        m = self._menu("&Database")
        self._window(m, "Database", "Database window")
        m.addSeparator()
        self._cmd(m, "can.dbc", "Add database…")
        self.db_files = m.addMenu("Loaded databases")
        self.db_files.aboutToShow.connect(self._fill_databases)

        m = self._menu("&Analysis")
        self._window(m, "Bus trace", "Trace window")
        m.addSeparator()
        self._action(m, "Pause trace\tSpace", lambda on: t.pause.setChecked(on), checked=t.pause.isChecked)
        self._action(m, "Fixed view (one row per message)", lambda on: t.fixed.setChecked(on),
                     checked=t.fixed.isChecked)
        self._action(m, "Follow newest frames", lambda on: t.autoscroll.setChecked(on),
                     checked=t.autoscroll.isChecked)
        self._action(m, "Highlight changed data", lambda on: t.flash.setChecked(on), checked=t.flash.isChecked)
        m.addSeparator()
        self._cmd(m, "trace.clear", "Clear trace")
        self._cmd(m, "trace.export", "Export trace…")

        m = self._menu("&Simulation")
        self._cmd(m, "measurement.toggle", "Measurement running", checked=lambda: w.ctx.can.measuring)
        self._action(m, "Clear trace on Start", lambda on: w.measurement.set_clear_on_start(on),
                     checked=w.measurement.clear_on_start)
        m.addSeparator()
        self._window(m, "Signals", "Signals window (interactive generator)")
        self._cmd(m, "can.add_signals", "Add signals…")
        self._window(m, "Auto response", "Auto response window")
        m.addSeparator()
        self._action(m, "Auto response enabled\tCtrl+Shift+R",
                     lambda on: w.auto_response.set_enabled(on),
                     checked=lambda: w.ctx.can.responder.enabled)
        self._cmd(m, "can.auto_response.add", "Add auto response rule…")
        m.addSeparator()
        self._cmd(m, "can.stop_tx", "Stop all cyclic transmission")

        m = self._menu("&Help")
        self._cmd(m, "palette", "Commands and shortcuts…")
        self._action(m, "Documentation folder", self.open_docs)
        self._action(m, "Download PEAK driver (PCAN)…", self.open_peak_driver_page)
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

    def open_docs(self) -> None:
        from pathlib import Path

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        docs = Path(__file__).resolve().parents[4] / "docs"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(docs if docs.exists() else Path.cwd())))

    @staticmethod
    def open_peak_driver_page() -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from autosim.hardware.pcan import DRIVER_URL
        QDesktopServices.openUrl(QUrl(DRIVER_URL))

    def about(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.about(self.win, "About AutoSim Studio",
                          "AutoSim Studio\nCAN / CAN FD simulation and analysis\n\n"
                          "Adapters: ESP32 + MCP2518FD, CANable (slcan), PEAK PCAN, Android vcan bridge")
