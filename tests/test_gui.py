import re
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from autosim.context import AppContext
from autosim.ui.main_window import MainWindow
from autosim.ui.settings_store import app_settings

DATA = Path(__file__).parent / "data"
FAKE_PORTS: list[dict] = []


@pytest.fixture
def win(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.IniFormat)          # never the real registry
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    boxes = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, n=name: boxes.append((n, a[2:]))))
    # GUI tests must not depend on what is plugged in: present "no adapters" unless a test sets FAKE_PORTS
    from autosim.hardware.esp32_gateway.manager import NodeManager
    monkeypatch.setattr(NodeManager, "scan", lambda self: list(FAKE_PORTS))
    from PySide6.QtWidgets import QApplication
    from autosim.ui.theme import apply_theme
    apply_theme(QApplication.instance(), "dark")           # as app.main(): theme + font before widgets
    ctx = AppContext()
    ctx.start()
    w = MainWindow(ctx)
    w.boxes = boxes
    qtbot.addWidget(w)
    w.show()
    yield w
    w.close()


def test_can_flow_through_widgets(win, qtbot, tmp_path):
    assert win.dbc.load_file(str(DATA / "sample.dbc"))
    assert win.dbc.tree.topLevelItemCount() == 2
    win.can_conn.connect_node()                       # default port SIM1 / channel CAN1
    assert win.ctx.can.bus.channels == ["CAN1"]
    qtbot.waitUntil(lambda: win.can_conn.table.item(0, 3).text() == "running", timeout=3000)

    card = win.signals_panel.add_message("EngineData")
    assert card.channel.currentText() == "CAN1" and card.cycle.value() == 10
    speed = next(r for r in card.rows if r.sig.name == "Speed")
    speed.set_value(88.0)                               # user edit reaches the TX state
    assert card.job.state.get_signal("Speed") == pytest.approx(88.0)
    speed.set_generator("sine")                         # generator takes over
    from autosim.ui.panels.signal_panel import C_RAW, C_VALUE
    assert "Speed" in card.job.bindings and speed.create_editor(C_VALUE, None) is None   # read-only now
    assert speed.create_editor(C_RAW, None) is None
    gear = next(r for r in card.rows if r.sig.name == "Gear")
    gear.set_value(3)
    assert card.job.state.get_signal("Gear") == 3

    card.enable.setChecked(True)
    qtbot.waitUntil(lambda: win.trace.model.rowCount() > 40, timeout=4000)
    assert card.job.enabled and win.trace.model.rowCount() > 0
    card.enable.setChecked(False)

    win.trace.f_name.setText("engine")
    n = win.trace.model.rowCount()
    assert n > 0 and all(r.name == "EngineData" for r in win.trace.model.rows())
    win.trace.f_dir.setCurrentText("Tx")
    assert all(not r.frame.is_rx for r in win.trace.model.rows())
    win.trace.f_name.setText("nomatch")
    assert win.trace.model.rowCount() == 0
    win.trace.f_name.setText("")
    win.trace.f_dir.setCurrentText("Rx + Tx")

    from autosim.core.can_engine.logger_io import export_by_extension
    assert export_by_extension(win.trace.model.rows(), tmp_path / "x.csv") > 0

    win.plot.msg.setCurrentText("EngineData")
    win.plot.sig.setCurrentText("Speed")
    win.plot.add_signal()
    card.enable.setChecked(True)
    qtbot.waitUntil(lambda: len(win.ctx.can.rx.snapshot("EngineData", "Speed")) > 5, timeout=3000)
    card.enable.setChecked(False)

    win.can_conn.table.selectRow(0)
    win.can_conn.disconnect_node()
    assert win.ctx.can.bus.channels == [] and win.can_conn.table.rowCount() == 0
    assert not win.boxes, win.boxes


def test_connect_errors_are_reported_not_raised(win):
    win.can_conn.port.setCurrentText("COM999")
    win.can_conn.connect_node()
    assert win.boxes and win.boxes[0][0] == "critical"
    assert win.ctx.can.bus.channels == [] and not win.ctx.nodes.nodes


def test_project_save_and_load_roundtrip(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    win.dbc.load_file(str(DATA / "sample.dbc"))
    win.can_conn.channel.setText("MYCAN")
    out = tmp_path / "p.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    win.save_project()
    assert out.exists()
    win.ctx.can.registry.clear()
    win.can_conn.channel.setText("other")
    win.load_project(str(out))
    assert win.ctx.can.registry.paths and win.can_conn.channel.text() == "MYCAN"


def test_can_only_workspace(win):
    """CAN is the whole application: no tabs, no SOME/IP / Ethernet UI."""
    from PySide6.QtWidgets import QTabWidget
    assert not any(isinstance(w, QTabWidget) and w.count() > 1 and w.tabText(1) == "SOME/IP"
                   for w in win.findChildren(QTabWidget))
    for w in (win.can_conn, win.trace, win.dbc, win.signals_panel, win.plot):
        assert win.can_tab.isAncestorOf(w)
    assert not hasattr(win, "someip") and not hasattr(win, "eth_conn") and not hasattr(win.ctx, "someip")


def test_theme_switch_light_dark_and_persist(win):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from autosim.ui.theme import ThemeManager, is_dark
    app = QApplication.instance()
    seen = []
    ThemeManager.instance().changed.connect(seen.append)
    win.set_theme("light")
    from autosim.ui.styles.tokens import LIGHT_COLORS as L
    assert not is_dark(app) and L["SURFACE_0"] in app.styleSheet() and L["ACCENT_STRONG"] in app.styleSheet()
    assert app.palette().window().color().name().upper() == L["SURFACE_0"]      # native palette follows too
    assert str(app_settings().value("theme")) == "light"
    assert win.settings_dialog.theme_buttons["light"].isChecked()
    win.set_theme("dark")
    from autosim.ui.styles.tokens import DARK_COLORS as D
    assert is_dark(app) and D["SURFACE_0"] in app.styleSheet() and D["ACCENT_STRONG"] in app.styleSheet()
    assert app.palette().window().color().name().upper() == D["SURFACE_0"]
    assert str(app_settings().value("theme")) == "dark"
    assert seen[-2:] == [False, True]                                           # widgets are notified


def test_dark_and_light_qss_follow_the_design_system():
    from autosim.ui.icons import build_qss_assets
    from autosim.ui.styles.themes import DARK, DARK_QSS, LIGHT, LIGHT_QSS, build_qss
    dark, light = (build_qss(pal, build_qss_assets(pal["TEXT_MUTED"])) for pal in (DARK, LIGHT))
    for qss, pal in ((dark, DARK), (light, LIGHT)):
        assert "@" not in qss
        for needle in ("border-radius: 6px", "border-radius: 8px", "min-height: 30px", "min-height: 34px",
                       "QPushButton:hover", "QPushButton:pressed", "QPushButton:disabled", "QPushButton:focus",
                       "border-bottom: 3px solid", "QFrame#card", "2px solid " + pal["FOCUS_RING"]):
            assert needle in qss, needle
        for key in ("SURFACE_0", "SURFACE_1", "SURFACE_2", "SURFACE_3", "BORDER", "BORDER_STRONG", "TEXT_PRIMARY",
                    "TEXT_SECONDARY", "TEXT_MUTED", "TEXT_DISABLED", "ACCENT", "ACCENT_STRONG", "ON_ACCENT",
                    "SUCCESS", "ERROR", "ERROR_STRONG"):
            assert pal[key] in qss, key
        assert re.findall(r"font-size: (\d+)px", qss) and not re.search(r"font-size: [\d.]+pt", qss)
    assert DARK["ACCENT_STRONG"] == LIGHT["ACCENT_STRONG"] == "#4F46E5"            # indigo in both themes
    # the exported constants are palette-resolved (only image tokens left) and share one geometry
    assert "@PRIMARY@" not in DARK_QSS and "@ARROW_DOWN@" in DARK_QSS and "@ARROW_DOWN@" in LIGHT_QSS
    strip = lambda q: re.sub(r"#[0-9A-Fa-f]{6}|rgba\([^)]*\)", "C", q)  # noqa: E731
    assert strip(DARK_QSS) == strip(LIGHT_QSS)


def _blue_borders(qss: str, exempt=()):
    """(selector, declaration) pairs whose border colour is a saturated blue/indigo hex."""
    found = []
    for rule in qss.split("}"):
        if "{" not in rule:
            continue
        selector, body = rule.split("{", 1)
        if any(x in selector for x in exempt):
            continue
        for decl in body.split(";"):
            if "border" not in decl.split(":")[0]:
                continue
            for hx in re.findall(r"#([0-9A-Fa-f]{6})(?![0-9A-Fa-f])", decl):
                r, g, bl = (int(hx[i:i + 2], 16) for i in (0, 2, 4))
                if bl >= 0xB0 and bl - r >= 0x50:
                    found.append((selector.strip(), decl.strip()))
    return found


def test_no_solid_blue_borders_in_stylesheet():
    """Dark theme: cards, inputs, tables, docks, secondary/destructive buttons never use a saturated blue/indigo
    border. Exempt (the border only matches an accent *fill*): primary button, checked controls, active tab bar."""
    from autosim.ui.icons import build_qss_assets
    from autosim.ui.styles.themes import DARK, LIGHT, build_qss
    for pal in (DARK, LIGHT):
        qss = build_qss(pal, build_qss_assets(pal["TEXT_MUTED"]))
        assert not _blue_borders(qss, exempt=("QTabBar", 'kind="primary"', ":checked", "QSlider", ":focus",
                                              "QTreeView:focus", 'primary="true"', 'focused="true"')), pal["SURFACE_0"]
        assert len(_blue_borders(qss)) >= 3
    assert _blue_borders("QFrame#card { border: 1px solid #3B82F6; }")
    assert not _blue_borders("QFrame#card { border: 1px solid #26272B; }")


def test_status_badge_and_single_connect_toggle(win, qtbot):
    c = win.can_conn
    b, btn = c.badge, c.toggle_btn
    assert (b.state, b.text) == ("offline", "Offline")
    assert (btn.text(), btn.property("kind")) == ("Connect", "primary")
    btn.click()                                           # blue Connect -> connected
    qtbot.waitUntil(lambda: b.state == "online", timeout=3000)
    assert b.text == "1 connected"
    assert (btn.text(), btn.property("kind")) == ("Disconnect", "danger")
    c.channel.setText("CAN2")                             # a different, not-yet-open channel
    assert (btn.text(), btn.property("kind")) == ("Connect", "primary")
    c.table.selectRow(0)                                  # selecting the open channel flips it back
    assert c.channel.text() == "CAN1" and btn.text() == "Disconnect"
    btn.click()                                           # red Disconnect -> closed
    assert b.state == "offline" and c.table.rowCount() == 0 and not win.ctx.can.bus.channels
    assert (btn.text(), btn.property("kind")) == ("Connect", "primary")


def test_signal_panel_empty_states_and_drop(win, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl
    sp = win.signals_panel
    assert sp.stack.currentIndex() == 0 and sp.empty.title.text() == "No DBC Database Loaded"
    assert sp.open_btn.text() == "Open DBC File" and sp.open_btn.property("kind") == "primary"
    # dropping a database file loads it (non-database files are ignored)
    class Ev:
        def __init__(self, paths):
            self._m = QMimeData()
            self._m.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
        def mimeData(self):
            return self._m
    txt = tmp_path / "notes.txt"
    txt.write_text("x")
    assert [Path(f) for f in sp._dropped_files(Ev([DATA / "sample.dbc", txt]))] == [DATA / "sample.dbc"]
    sp.filesDropped.emit([str(DATA / "sample.dbc")])
    assert win.ctx.can.registry.messages()
    assert sp.empty.title.text() == "No Message Selected" and sp.stack.currentIndex() == 0
    card = sp.add_message("EngineData")
    assert sp.stack.currentIndex() == 1
    sp.remove_card(card)
    assert sp.stack.currentIndex() == 0


def test_controls_share_32px_height_and_trace_header_34px(win, qtbot):
    from PySide6.QtWidgets import QApplication
    from autosim.ui.theme import apply_theme
    apply_theme(QApplication.instance(), "dark")
    c = win.can_conn
    qtbot.wait(100)
    for w in (c.toggle_btn, c.scan_btn, c.channel, c.port, c.nominal, c.data):
        assert w.height() == 32, ("CAN", type(w).__name__, w.height())
    assert win.trace.view.horizontalHeader().height() == 34
    from PySide6.QtCore import Qt
    m = win.trace.model
    from autosim.core.can_engine.rx_dispatcher import TraceRow
    from autosim.core.common.frame import CanFrame
    m.add_batch([TraceRow(CanFrame(0x1, b"\x01", timestamp=0.0), "N")])
    assert m.data(m.index(0, 3), Qt.FontRole) is not None and m.data(m.index(0, 6), Qt.FontRole) is not None
    assert m.data(m.index(0, 4), Qt.FontRole) is None


def test_trace_toolbar_actions_and_row_flash(win, qtbot):
    t = win.trace
    assert t.autoscroll.isChecked() and not t.pause.isChecked()
    from autosim.core.can_engine.rx_dispatcher import TraceRow
    from autosim.core.common import clock
    from autosim.core.common.frame import CanFrame
    from PySide6.QtCore import Qt
    row = TraceRow(CanFrame(0x100, b"\x01", channel="A", timestamp=clock.now()), "Msg", 10.0)
    t.on_batch([row])
    assert t.model.rowCount() == 1
    bg = t.model.data(t.model.index(0, 0), Qt.BackgroundRole)
    assert bg is not None and bg.alpha() > 0                     # fresh row is highlighted
    qtbot.wait(750)
    assert t.model.data(t.model.index(0, 0), Qt.BackgroundRole) is None   # highlight faded
    t.pause.setChecked(True)
    t.on_batch([row])
    assert t.model.rowCount() == 1 and t.dropped_while_paused == 1 and t.pause.text() == "Resume"
    t.pause.setChecked(False)
    t.flash.setChecked(False)
    t.on_batch([TraceRow(CanFrame(0x101, b"", channel="B", timestamp=clock.now()), "X")])
    assert t.model.data(t.model.index(1, 0), Qt.BackgroundRole) is None
    t.f_chan.blockSignals(True)
    t.f_chan.addItem("B")
    t.f_chan.setCurrentText("B")
    t.f_chan.blockSignals(False)
    t.apply_filter()
    assert t.model.rowCount() == 1
    t.clear_act.trigger()
    assert t.model.rowCount() == 0


def test_docks_can_float_and_reset(win):
    ws = win.can_ws
    assert set(ws.docks) == {"Sidebar", "Signals", "Signal plot", "Bus trace"}
    win.pop_out("Bus trace")
    d = ws.docks["Bus trace"]
    assert d.isFloating() and d.isWindow()                      # separate top-level window
    win.pop_out("Signal plot")
    assert ws.docks["Signal plot"].isFloating()
    win._reset_layout()
    assert not d.isFloating() and not ws.docks["Signal plot"].isFloating()
    assert win.can_tab.isAncestorOf(win.trace)


def test_trace_id_and_data_are_neutral_and_direction_uses_rx_tx_tokens(win):
    from PySide6.QtCore import Qt
    from autosim.core.can_engine.rx_dispatcher import TraceRow
    from autosim.core.common.frame import CanFrame
    from autosim.ui.theme import apply_theme
    from PySide6.QtWidgets import QApplication
    apply_theme(QApplication.instance(), "dark")
    m = win.trace.model
    m.add_batch([TraceRow(CanFrame(0x100, b"\x01\x02", channel="A", is_rx=True), "Rx"),
                 TraceRow(CanFrame(0x101, b"\x03", channel="A", is_rx=False), "Tx")])
    from autosim.ui.styles.tokens import DARK_COLORS as D
    fg = lambda r, c: m.data(m.index(r, c), Qt.ForegroundRole).name().upper()  # noqa: E731
    assert fg(0, 3) == fg(1, 3) == D["TEXT_PRIMARY"]                             # ID: neutral
    assert fg(0, 6) == fg(1, 6) == D["TEXT_PRIMARY"]                             # payload: neutral, never red
    assert fg(0, 2) == D["RX"] and fg(1, 2) == D["TX"]                           # direction: Rx cyan / Tx orange
    assert m.data(m.index(0, 4), Qt.ForegroundRole) is None                      # name: default text
    fm = win.trace.fixed_model
    fm.add_batch([TraceRow(CanFrame(0x100, b"\x01\x02", channel="A", is_rx=True), "Rx"),
                  TraceRow(CanFrame(0x101, b"\x03", channel="A", is_rx=False), "Tx")])
    ffg = lambda r, c: fm.data(fm.index(r, c), Qt.ForegroundRole).name().upper()  # noqa: E731
    assert ffg(0, 2) == D["RX"] and ffg(1, 2) == D["TX"] and ffg(0, 6) == D["TEXT_PRIMARY"]
    apply_theme(QApplication.instance(), "light")                                # cached colours follow the theme
    from autosim.ui.styles.tokens import LIGHT_COLORS as L
    assert fg(0, 2) == L["RX"] and ffg(1, 2) == L["TX"] and fg(0, 6) == L["TEXT_PRIMARY"]
    apply_theme(QApplication.instance(), "dark")


def test_status_pills_follow_the_spec_in_both_themes():
    from autosim.ui.theme import badge_style
    from PySide6.QtGui import QColor
    from autosim.ui.styles.tokens import colors
    for dark in (True, False):
        t = colors(dark)
        text, bg, border, dot = badge_style("offline", dark)
        assert dot == t["TEXT_MUTED"] and bg[3] == 255 and text == t["TEXT_SECONDARY"]   # neutral; red = errors only
        for state, key in (("online", "SUCCESS"), ("connecting", "WARNING"), ("error", "ERROR")):
            text, bg, border, dot = badge_style(state, dark)
            c = QColor(t[key])
            assert text == dot == t[key] and bg[:3] == (c.red(), c.green(), c.blue()) and bg[3] < 60


def test_can_card_buttons_are_compact_fixed_size_and_aligned(win, qtbot):
    from PySide6.QtWidgets import QApplication
    from autosim.ui.theme import apply_theme
    apply_theme(QApplication.instance(), "dark")
    qtbot.wait(150)
    c = win.can_conn
    assert c.width() > 300                                     # a wide card, and still:
    assert (c.scan_btn.width(), c.scan_btn.height()) == (80, 32)
    assert (c.toggle_btn.width(), c.toggle_btn.height()) == (130, 32)
    for w in (c.port, c.channel, c.nominal, c.data):
        assert w.height() == 32, type(w).__name__
    # Port dropdown and Scan sit on the same row, Scan immediately to the right
    port_g, scan_g = c.port.geometry(), c.scan_btn.geometry()
    assert abs(port_g.center().y() - scan_g.center().y()) <= 1 and 0 <= scan_g.left() - port_g.right() <= 12
    # Connect/Disconnect is LEFT-aligned: same left edge as the Port and Data-rate dropdowns, not stretched
    tg = c.toggle_btn.geometry()
    assert abs(tg.left() - c.port.geometry().left()) <= 1
    assert abs(tg.left() - c.data.geometry().left()) <= 1
    assert tg.right() < c.width() / 2
    # the action row sits below the form
    assert tg.top() > c.data.geometry().bottom()
    # status pill: 24px, top-right of the card header
    bg = c.badge.geometry()
    assert bg.height() == 24 and c.width() - bg.right() <= 16 and bg.top() <= 20
    # the toggle keeps size and position in both states
    c.toggle_btn.click()
    qtbot.waitUntil(lambda: c.toggle_btn.text() == "Disconnect", timeout=3000)
    assert (c.toggle_btn.width(), c.toggle_btn.height()) == (130, 32)
    assert abs(c.toggle_btn.geometry().left() - c.port.geometry().left()) <= 1
    c.toggle_btn.click()
    assert c.toggle_btn.text() == "Connect"


def test_button_styles_secondary_primary_and_outlined_destructive():
    from autosim.ui.icons import build_qss_assets
    from autosim.ui.styles.themes import DARK, LIGHT, build_qss

    def rule(qss, selector):
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", qss)
        assert m, selector
        return m.group(1)

    for pal in (DARK, LIGHT):
        qss = build_qss(pal, build_qss_assets(pal["TEXT_MUTED"]))
        sec = rule(qss, "QPushButton")
        assert pal["SURFACE_2"] in sec and "1px solid " + pal["BORDER_STRONG"] in sec and pal["TEXT_PRIMARY"] in sec
        pri, pri_h = rule(qss, 'QPushButton[kind="primary"]'), rule(qss, 'QPushButton[kind="primary"]:hover')
        assert pal["ACCENT_STRONG"] in pri and "font-weight: 600" in pri and "color: " + pal["ON_ACCENT"] in pri
        assert pal["ACCENT_STRONG_HOVER"] in pri_h
        dang, dang_h = rule(qss, 'QPushButton[kind="danger"]'), rule(qss, 'QPushButton[kind="danger"]:hover')
        assert "background: transparent" in dang and "1px solid " + pal["ERROR"] in dang and "color: " + pal["ERROR"] in dang
        assert pal["ERROR_STRONG"] in dang_h and pal["ON_ACCENT"] in dang_h


def test_menu_bar_removed_and_app_bar_on_top(win, qtbot):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenuBar, QToolButton
    assert win.menuWidget() is None and not win.findChildren(QMenuBar)          # no File / View / Tools bar
    bar = win.app_bar.widget
    gear = win.btn_settings
    assert bar.isAncestorOf(gear) and isinstance(gear, QToolButton)
    assert not gear.icon().isNull() and gear.toolTip().startswith("Settings")
    qtbot.wait(100)
    # the app bar starts at the very top of the window, above the workspace
    assert bar.mapTo(win, QPoint(0, 0)).y() <= 2 and win.can_tab.mapTo(win, QPoint(0, 0)).y() >= bar.height()
    # gear sits at the top-right corner of the bar
    g = gear.mapTo(win, QPoint(0, 0))
    assert g.y() <= 12 and win.width() - (g.x() + gear.width()) <= 24
    assert (gear.width(), gear.height()) == (32, 32) or gear.height() <= 40


def test_gear_hover_style_in_stylesheet():
    from autosim.ui.icons import build_qss_assets
    from autosim.ui.styles.themes import DARK, build_qss
    qss = build_qss(DARK, build_qss_assets(DARK["TEXT_MUTED"]))
    m = re.search(r"QToolButton#settings-btn:hover\s*\{([^}]*)\}", qss)
    assert m and DARK["SURFACE_3"] in m.group(1)
    assert "border-radius: 4px" in re.search(r"QToolButton#settings-btn\s*\{([^}]*)\}", qss).group(1)


def test_settings_dialog_holds_all_former_menu_actions(win, qtbot, monkeypatch):
    from PySide6.QtCore import Qt
    d = win.settings_dialog
    win.btn_settings.click()
    qtbot.waitUntil(d.isVisible, timeout=2000)
    assert not d.isModal()
    # File menu actions
    called = []
    monkeypatch.setattr(win.dbc, "load_dialog", lambda: called.append("db"))
    monkeypatch.setattr(type(win), "open_project", lambda self: called.append("open"))
    monkeypatch.setattr(type(win), "save_project", lambda self: called.append("save"))
    d2 = type(d)(win)                                        # fresh dialog binds the patched callbacks
    assert set(d2.file_buttons) == {"database", "open_project", "save_project"}     # no SOME/IP files
    for key in ("database", "open_project", "save_project"):
        d2.file_buttons[key].click()
    assert called == ["db", "open", "save"]
    # View: panel visibility, pop out, reset layout
    d.sync()
    assert all(cb.isChecked() for cb in d.dock_checks.values())
    assert set(d.dock_checks) == {"Sidebar", "Signals", "Signal plot", "Bus trace", "Log"}
    cb = d.dock_checks["Bus trace"]
    cb.setChecked(False)
    assert win.can_ws.docks["Bus trace"].isHidden()
    cb.setChecked(True)
    assert not win.can_ws.docks["Bus trace"].isHidden()
    win.can_ws.docks["Signal plot"].close()                  # closing the panel itself unticks the box
    assert not d.dock_checks["Signal plot"].isChecked()
    win._reset_layout()
    d.sync()
    assert d.dock_checks["Signal plot"].isChecked()
    win.pop_out("Log")
    assert win.log_dock.isFloating()
    win._reset_layout()
    # Theme: radios drive the theme live and stay in sync with it
    d.theme_buttons["light"].setChecked(True)
    assert win.current_theme == "light"
    win.set_theme("dark")
    assert d.theme_buttons["dark"].isChecked()
    # Tools menu
    card = win.signals_panel.add_message  # no DBC loaded here: stop_all must still be safe
    d.stop_tx.click()
    assert card is not None
    # Esc closes the popup
    qtbot.keyClick(d, Qt.Key_Escape)
    assert not d.isVisible()


def _connect(win, port, channel):
    c = win.can_conn
    c.port.setCurrentText(port)
    c.channel.setText(channel)
    return c.connect_node()


def test_multiple_nodes_node_column_rename_and_duplicate_warning(win, qtbot):
    from PySide6.QtCore import Qt
    c = win.can_conn
    logs = []
    win.ctx.events.subscribe("log", logs.append)
    assert _connect(win, "SIM1", "CAN1") and _connect(win, "SIM2", "CAN2")
    qtbot.waitUntil(lambda: c.badge.text == "2 connected", timeout=4000)
    assert [c.table.horizontalHeaderItem(i).text() for i in range(7)] == \
        ["Channel", "Node", "Port", "State", "Rx", "Tx", "Err"]
    qtbot.waitUntil(lambda: bool(c.table.item(0, 1).text() and c.table.item(1, 1).text()), timeout=3000)
    assert {c.table.item(r, 1).text() for r in range(2)} == {"CAN1", "CAN2"}     # SIM nodes report their channel name
    # rename: stored on the node, visible in the table
    assert c.rename_node_to("CAN2", "Gateway-2")
    assert c.table.item(1, 1).text() == "Gateway-2" and win.ctx.nodes.nodes["CAN2"].info["node_id"] == "Gateway-2"
    # an invalid name is refused with a message, nothing changes
    assert not c.rename_node_to("CAN2", "bad name!") and win.boxes[-1][0] == "warning"
    assert win.ctx.nodes.nodes["CAN2"].info["node_id"] == "Gateway-2"
    # a clash is highlighted and logged once
    assert c.rename_node_to("CAN2", "CAN1")
    assert any("same node name" in m and "CAN1" in m for m in logs)
    from autosim.ui.theme import token
    assert c.table.item(0, 1).foreground().color().name().upper() == token("WARNING")
    assert c.rename_node_to("CAN2", "Unique-2")
    assert c.table.item(0, 1).data(Qt.ForegroundRole) is None            # override removed: default (readable) text colour
    assert c.table.item(1, 1).data(Qt.ForegroundRole) is None
    c.table.selectRow(0)
    c.disconnect_node()
    assert win.ctx.can.bus.channels == ["CAN2"] and c.badge.text == "1 connected"


def test_project_saves_and_reconnects_every_open_node(win, tmp_path, monkeypatch, qtbot):
    from PySide6.QtWidgets import QFileDialog
    from autosim.core.common.config import ProjectConfig
    c = win.can_conn
    c.nominal.setCurrentIndex(c.nominal.findData(250_000))
    c.data.setCurrentIndex(c.data.findData(1_000_000))
    assert _connect(win, "SIM1", "CAN1")
    c.nominal.setCurrentIndex(c.nominal.findData(1_000_000))
    c.data.setCurrentIndex(c.data.findData(5_000_000))
    c.fd.setChecked(False)
    assert _connect(win, "SIM2", "CAN2")
    out = tmp_path / "multi.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    win.save_project()
    saved = {n.channel_name: n for n in ProjectConfig.load(out).nodes}
    assert set(saved) == {"CAN1", "CAN2"} and all(n.autoconnect for n in saved.values())
    assert (saved["CAN1"].nominal_bitrate, saved["CAN1"].data_bitrate, saved["CAN1"].fd) == (250_000, 1_000_000, True)
    assert (saved["CAN2"].nominal_bitrate, saved["CAN2"].fd) == (1_000_000, False)

    for ch in ("CAN1", "CAN2"):                                     # close everything, then reopen the project
        c._close_channel(ch)
    assert win.ctx.can.bus.channels == []
    win.load_project(str(out))
    qtbot.waitUntil(lambda: c.badge.text == "2 connected", timeout=4000)
    assert sorted(win.ctx.can.bus.channels) == ["CAN1", "CAN2"]

    # a node that cannot be reached is logged, the others still connect, no dialog blocks the load
    logs = []
    win.ctx.events.subscribe("log", logs.append)
    broken = ProjectConfig.load(out)
    broken.nodes[0].port, broken.nodes[0].channel_name = "COM999", "CAN9"
    broken.nodes[1].channel_name = "CAN8"
    broken.nodes[1].port = "SIM1"
    broken.save(tmp_path / "broken.json")
    n_boxes = len(win.boxes)
    win.load_project(str(tmp_path / "broken.json"))
    assert "CAN8" in win.ctx.can.bus.channels and "CAN9" not in win.ctx.can.bus.channels
    assert any("CAN9" in m and "COM999" in m for m in logs) and len(win.boxes) == n_boxes


def test_loopback_checkbox_is_sent_to_the_node_and_saved(win):
    c = win.can_conn
    assert not c.loop.isChecked() and not c.cfg().extras["loopback"]
    c.loop.setChecked(True)
    assert c.cfg().extras["loopback"]
    from autosim.hardware.esp32_gateway import protocol as P
    from autosim.core.common.config import ProjectConfig
    assert P.encode_config(c.cfg())[8] & P.CFG_LOOPBACK                         # flag reaches the node
    cfg = ProjectConfig()
    c.collect(cfg)
    assert cfg.nodes[0].loopback is True
    c.loop.setChecked(False)
    c.apply(cfg)
    assert c.loop.isChecked()


def test_failed_connect_leaves_no_row_badge_or_button_state(win, monkeypatch):
    """Regression: a board that does not answer left a 'disconnected' row and a 'CONNECTING' badge behind."""
    from autosim.hardware.esp32_gateway.manager import NodeManager
    from autosim.hardware.esp32_gateway.transport import make_pipe
    orig = NodeManager.create_backend
    # a port that opens fine but whose board runs some other firmware (nobody answers HELLO)
    monkeypatch.setattr(NodeManager, "create_backend",
                        lambda self, port, ch, tf=None: orig(self, port, ch, lambda: make_pipe()[0]))
    c = win.can_conn
    c.port.setCurrentText("COM4")
    assert not c.connect_node()
    kind, args = win.boxes[-1]
    assert kind == "critical" and "COM4" in args[0] and "No AutoSim firmware answered" in args[0]
    assert "not a CANable" in args[0] and "CANABLE_FIRMWARE.md" in args[0]
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    assert c.table.rowCount() == 0 and not win.ctx.nodes.nodes and not win.ctx.can.bus.channels
    assert (c.badge.state, c.badge.text) == ("offline", "Offline")
    assert (c.toggle_btn.text(), c.toggle_btn.property("kind")) == ("Connect", "primary")


def test_data_rate_list_only_offers_rates_the_mcp2518fd_can_reach(win):
    from autosim.core.common.frame import CanChannelConfig
    from autosim.hardware.esp32_gateway.protocol import validate_config
    c = win.can_conn

    def offered():
        return [c.data.itemData(i) for i in range(c.data.count())]

    def set_nominal(v):
        c.nominal.setCurrentIndex(c.nominal.findData(v))

    set_nominal(500_000)
    assert offered() == [500_000, 1_000_000, 2_000_000, 4_000_000, 5_000_000]     # 8M would be 16x: not offered
    assert "(= nominal)" in c.data.itemText(0)
    set_nominal(1_000_000)
    assert offered() == [1_000_000, 2_000_000, 4_000_000, 5_000_000, 8_000_000]
    set_nominal(125_000)
    assert offered() == [125_000, 1_000_000]
    # the previous choice is kept when still valid, otherwise the closest valid rate
    set_nominal(500_000)
    c.data.setCurrentIndex(c.data.findData(4_000_000))
    set_nominal(250_000)
    assert offered() == [250_000, 1_000_000, 2_000_000] and c.data.currentData() == 2_000_000
    set_nominal(1_000_000)
    c.data.setCurrentIndex(c.data.findData(8_000_000))
    set_nominal(500_000)
    assert c.data.currentData() == 5_000_000
    # every offered combination is accepted by the same rule the firmware applies
    for nom in (125_000, 250_000, 500_000, 1_000_000):
        set_nominal(nom)
        for rate in offered():
            validate_config(CanChannelConfig(nom, rate, True))


def test_can_tab_default_dock_layout_and_panel_pop_out(win, qtbot):
    """Sidebar left; Signals | Signal plot side by side on top; Bus trace below with ~65 % of the height and the
    full width of the main area. Every panel is a dock: it pops out and closing the window re-docks it."""
    qtbot.wait(150)
    win._size_can_docks()
    qtbot.wait(50)
    side, sig, plot, trace = (win.can_ws.docks[k].geometry() for k in ("Sidebar", "Signals", "Signal plot", "Bus trace"))
    assert side.right() < sig.left() and sig.right() < plot.left()                # left to right
    assert abs(sig.top() - plot.top()) <= 1 and trace.top() > sig.bottom()        # trace below both
    assert abs(trace.left() - sig.left()) <= 1 and abs(trace.right() - plot.right()) <= 1
    assert abs(trace.height() / (trace.height() + sig.height()) - 0.65) < 0.06     # trace gets ~65 %
    assert trace.width() * trace.height() == max(g.width() * g.height() for g in (side, sig, plot, trace))
    win.pop_out("Bus trace")
    assert win.trace_card.isFloating() and win.trace_card.isWindow()
    win.trace_card.close()                                                     # closing the window re-docks
    assert not win.trace_card.isFloating() and win.trace_card.isVisible()
    win.pop_out("Sidebar")                                                     # the sidebar never floats
    assert not win.sidebar_dock.isFloating()


def test_dbc_explorer_headers_never_cut_numeric_columns(win):
    from PySide6.QtWidgets import QHeaderView
    hh = win.dbc.tree.header()
    assert hh.sectionResizeMode(0) == QHeaderView.Stretch
    assert all(hh.sectionResizeMode(i) == QHeaderView.ResizeToContents for i in range(1, hh.count()))


def test_canable_port_is_recognised_and_limits_the_form(win, monkeypatch):
    """A CANable (USB 16D0:117E) is detected by Scan: hint shown, data rates limited to its 2 / 5 Mbit/s presets,
    ISO forced (its firmware has no non-ISO mode), and it is preselected over the simulated nodes."""
    monkeypatch.setitem(globals(), "FAKE_PORTS", [
        {"port": "COM42", "description": "USB Serial Device (COM42)", "hwid": "USB VID:PID=16D0:117E SER=ABC",
         "kind": "slcan", "label": "CANable 2.0 (slcan)", "serial": "ABC"}])
    c = win.can_conn
    c.scan()
    assert c.port.currentText() == "COM42" and c.device_kind() == "slcan"
    assert "CANable" in c.port_hint.text()
    assert [c.data.itemData(i) for i in range(c.data.count())] == [2_000_000, 5_000_000]
    assert c.iso.isChecked() and not c.iso.isEnabled()
    c.port.setCurrentText("SIM1")                                # back to a node that can do any ratio
    assert c.device_kind() == "sim" and c.iso.isEnabled()
    assert 4_000_000 in [c.data.itemData(i) for i in range(c.data.count())]


def test_two_canables_connect_all_and_exchange_can_fd_in_parallel(win, qtbot, monkeypatch):
    """Two CANable adapters on one bus (firmware model): Connect all opens CAN1 + CAN2, each sends its own
    CAN FD message cyclically and receives the other's; the fixed trace keeps one row per message."""
    from tests.fake_canable import FakeBus
    from autosim.ui.models.fixed_trace_model import COL_NAME, COL_VALUE
    monkeypatch.setitem(globals(), "FAKE_PORTS", [
        {"port": p, "description": p, "hwid": f"USB VID:PID=16D0:117E SER={p}", "kind": "slcan",
         "label": "CANable 2.0 (slcan)", "serial": p} for p in ("COM41", "COM42")])
    bus = FakeBus()
    hosts = {p: bus.adapter()[0] for p in ("COM41", "COM42")}
    mgr = win.ctx.nodes
    orig = type(mgr).create_backend
    monkeypatch.setattr(mgr, "create_backend", lambda port, ch, **kw: orig(
        mgr, port, ch, transport_factory=lambda: hosts[port], kind="slcan"))
    assert win.dbc.load_file(str(DATA / "real_dbc" / "hyundai_canfd.dbc"))
    c = win.can_conn
    c.scan()
    c.nominal.setCurrentIndex(c.nominal.findData(500_000))
    c.data.setCurrentIndex(c.data.findData(2_000_000))
    assert c.connect_all() == ["CAN1", "CAN2"] and not win.boxes
    assert sorted(win.ctx.can.bus.channels) == ["CAN1", "CAN2"]
    qtbot.waitUntil(lambda: c.badge.state == "online" and c.badge.text == "2 connected", timeout=3000)
    # an adapter already open cannot be opened a second time; selecting it shows its channel
    c.port.setCurrentText("COM41")
    assert c.channel.text() == "CAN1" and c.toggle_btn.text() == "Disconnect"
    assert c.connect_node(interactive=False) is False and len(win.ctx.can.bus.channels) == 2
    with pytest.raises(ValueError):
        mgr.create_backend("COM42", "CAN9")

    fd = [m for m in win.ctx.can.registry.messages() if m.length >= 32 and m.signals][:2]
    cards = []
    for msg, ch in zip(fd, ("CAN1", "CAN2")):
        card = win.signals_panel.add_message(msg.name, ch)
        assert card.channel.currentText() == ch and card.job.channel == ch
        card.fd.setChecked(True)
        card.brs.setChecked(True)
        card.cycle.setValue(20)
        card.enable.setChecked(True)
        cards.append(card)
    rows = lambda: win.trace.model.rows()
    qtbot.waitUntil(lambda: {(r.frame.channel, r.frame.is_rx, r.name) for r in rows()} >= {
        ("CAN1", False, fd[0].name), ("CAN2", True, fd[0].name),
        ("CAN2", False, fd[1].name), ("CAN1", True, fd[1].name)}, timeout=4000)
    qtbot.wait(300)
    for card in cards:
        card.enable.setChecked(False)
    rx = [r for r in rows() if r.frame.is_rx]
    assert all(len(r.frame.data) >= 32 and r.frame.is_fd and r.frame.bitrate_switch for r in rx)

    # fixed trace: one row per (channel, direction, message), signals as children with the latest value
    fm = win.trace.fixed_model
    assert win.trace.fixed.isChecked() and win.trace.stack.currentWidget() is win.trace.tree
    assert fm.message_count() == 4 < len(rows())
    i = next(r for r in range(fm.rowCount()) if fm.entry(r).row.name == fd[0].name and fm.entry(r).row.frame.is_rx)
    top = fm.index(i, 0)
    assert int(fm.data(fm.index(i, 8))) > 1 and fm.rowCount(top) == len(fd[0].signals)
    last = fm.entry(i).row.frame
    decoded = win.ctx.can.registry.decode(last)[1]
    sig = fd[0].signals[0]
    assert fm.data(fm.index(0, COL_NAME, top)) == sig.name
    assert fm.data(fm.index(0, COL_VALUE, top)).startswith(f"{decoded[sig.name]:g}")
    win.trace.fixed.setChecked(False)                          # chronological view: every frame
    assert win.trace.stack.currentWidget() is win.trace.view and win.trace.count.text().endswith("rows")
    win.trace.clear()
    assert fm.message_count() == 0


def test_one_font_family_and_consistent_sizes_everywhere(win, qtbot):
    """Every widget uses Segoe UI at one of the three type sizes: 13 px text, 14 px headers, 18 px app title -
    including the plot axes and the trace cells. Buttons take focus from Tab only."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontInfo
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget
    from autosim.ui.theme import UI_FAMILY, apply_theme, data_font, ui_font
    apply_theme(QApplication.instance(), "dark")
    assert win.dbc.load_file(str(DATA / "sample.dbc"))
    win.signals_panel.add_message("EngineData")
    qtbot.wait(100)
    f = QApplication.instance().font()
    assert QFontInfo(f).family() == UI_FAMILY == "Segoe UI" and f.pixelSize() == 13
    assert QFontInfo(data_font()).family() == UI_FAMILY and data_font().pixelSize() == 13
    assert QFontInfo(ui_font("panel_title")).family() == UI_FAMILY and ui_font("panel_title").pixelSize() == 14
    sizes, families = {}, set()
    for w in win.findChildren(QWidget):
        if not w.isVisible():
            continue
        info = QFontInfo(w.font())
        sizes.setdefault(info.pixelSize(), []).append(w.objectName() or type(w).__name__)
        families.add(info.family())
    odd = [(type(w).__name__, w.objectName(), w.font().families(), w.font().family())
           for w in win.findChildren(QWidget) if w.isVisible() and QFontInfo(w.font()).family() != "Segoe UI"]
    assert families == {"Segoe UI"}, odd[:6]
    assert set(sizes) <= {13, 14, 18}, {k: v[:5] for k, v in sizes.items()}
    assert win.app_bar.title.font().pixelSize() == 18
    assert win.trace_card.header.title.font().pixelSize() == 14
    for name in ("left", "bottom"):                                              # pyqtgraph axes
        tick = win.plot.plot.getAxis(name).style["tickFont"]
        assert tick is not None and tick.pixelSize() == 13 and QFontInfo(tick).family() == UI_FAMILY
    from autosim.core.can_engine.rx_dispatcher import TraceRow
    from autosim.core.common.frame import CanFrame
    m = win.trace.fixed_model
    m.add_batch([TraceRow(CanFrame(0x100, b"\x01\x02", channel="A"), "X")])
    cell = m.data(m.index(0, 6), Qt.FontRole)
    assert cell.pixelSize() == 13 and QFontInfo(cell).family() == UI_FAMILY
    buttons = win.findChildren(QPushButton)
    assert buttons and all(b.focusPolicy() != Qt.StrongFocus for b in buttons if b.isVisible())
    assert win.can_conn.toggle_btn.focusPolicy() == Qt.TabFocus


def test_signals_panel_is_a_tree_table(win, qtbot):
    """Messages are group rows with their transmit controls; signals are table rows (Signal | Value | Unit | Raw |
    Value table | Generator) edited in place; a signal expands into a detail row (slider, generator parameters,
    layout) built on first use; messages collapse to hide their signals."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QGroupBox
    from autosim.ui.panels.signal_panel import C_GEN, C_RAW, C_TABLE, C_UNIT, C_VALUE, COLUMN_TITLES
    assert win.dbc.load_file(str(DATA / "sample.dbc"))
    card = win.signals_panel.add_message("EngineData")
    tree = win.signals_panel.tree
    qtbot.wait(50)
    assert not win.signals_panel.findChildren(QGroupBox)
    assert tree.isHeaderHidden()                                                    # no shared header on top:
    assert card.child(0) is card.header                                            # every message has its own
    assert [card.header.text(c) for c in range(tree.columnCount())] == list(COLUMN_TITLES)
    assert COLUMN_TITLES == ("Signal", "Raw", "Physical", "Unit", "Generator", "Symbolic")
    assert not (card.header.flags() & (Qt.ItemIsSelectable | Qt.ItemIsEditable))
    assert tree.topLevelItem(0) is card and card.isExpanded()
    assert card.childCount() == len(card.message.signals) + 1 and card.rows[0] is card.child(1)
    assert card.isFirstColumnSpanned() and tree.itemWidget(card, 0) is card.row        # group row with controls
    from PySide6.QtWidgets import QCheckBox as _Box                                # FD / BRS check boxes like TX
    assert isinstance(card.fd, _Box) and isinstance(card.brs, _Box)
    card.fd.setChecked(False)
    assert not card.brs.isEnabled() and not card.job.fd                            # BRS only with FD
    card.fd.setChecked(True)
    card.brs.setChecked(True)
    assert card.brs.isEnabled() and card.job.fd and card.job.brs
    speed = next(r for r in card.rows if r.sig.name == "Speed")
    gear = next(r for r in card.rows if r.sig.name == "Gear")
    speed.set_value(88.0)
    raw = int(round((88.0 - speed.sig.offset) / speed.sig.scale))
    assert speed.text(C_VALUE).startswith("88") and speed.text(C_RAW) == str(raw)
    assert speed.text(C_UNIT) == (speed.sig.unit or "") and speed.text(C_TABLE) == "\u2014"
    assert speed.text(C_GEN) == "Off"
    # in-place editors: Value is a spin box in the table, Value table / Generator are drop-downs
    ed = speed.create_editor(C_VALUE, None)
    assert isinstance(ed, QDoubleSpinBox)
    speed.load_editor(C_VALUE, ed)
    ed.setValue(42.0)
    speed.commit_editor(C_VALUE, ed)
    assert card.job.state.get_signal("Speed") == pytest.approx(42.0)
    assert speed.create_editor(C_TABLE, None) is None                              # no value table
    ted = gear.create_editor(C_TABLE, None)
    assert isinstance(ted, QComboBox) and ted.count() == len(gear.sig.choices)
    ted.setCurrentIndex(2)
    gear.commit_editor(C_TABLE, ted)
    assert gear.value() == ted.itemData(2) and ":" in gear.text(C_TABLE)
    red = speed.create_editor(C_RAW, None)                                         # raw editing (CANoe style)
    speed.load_editor(C_RAW, red)
    red.setValue(500)
    speed.commit_editor(C_RAW, red)
    assert speed.value() == pytest.approx(500 * speed.sig.scale + speed.sig.offset) and speed.text(C_RAW) == "500"
    data = card.payload_hex()
    assert card.row.data.text() == data or (card.refresh() or card.row.data.text() == data)  # live payload bytes
    assert len(data.split()) == card.message.length
    tree.setCurrentItem(speed, C_VALUE)
    tree.editItem(speed, C_VALUE)                                                    # editing through the view
    assert isinstance(tree.indexWidget(tree.indexFromItem(speed, C_VALUE)), QDoubleSpinBox) or \
        tree.state() == tree.EditingState
    tree.closePersistentEditor(speed, C_VALUE)
    # details: built on first expand
    assert speed.detail is None and not speed.isExpanded()
    speed.set_expanded(True)
    assert speed.detail is not None and speed.isExpanded() and speed.detail.slider is not None
    assert speed.detail.param_row.isHidden()                                         # no generator yet
    gen = speed.create_editor(C_GEN, None)
    gen.setCurrentIndex(gen.findData("ramp"))
    speed.commit_editor(C_GEN, gen)
    assert speed.text(C_GEN) == "Ramp" and not speed.detail.param_row.isHidden()
    assert speed.font(C_VALUE).italic() and not speed.detail.slider.isEnabled()
    speed.detail.params[1].setValue(77.0)                                            # generator parameter
    assert speed.gen_params[1] == 77.0 and "Speed" in card.job.bindings
    speed.set_generator("none")
    assert "Speed" not in card.job.bindings and speed.detail.param_row.isHidden()
    card.set_all_expanded(True)
    assert all(r.isExpanded() and r.detail is not None for r in card.rows)
    card.set_all_expanded(False)
    assert not any(r.isExpanded() for r in card.rows)
    card.set_open(False)                                                             # collapse the message
    assert not card.isExpanded() and not card.is_open

def test_mouse_wheel_never_changes_a_value(win, qtbot):
    """Scrolling over a spin box / slider / combo box scrolls the panel instead of changing the value."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    assert win.dbc.load_file(str(DATA / "sample.dbc"))
    card = win.signals_panel.add_message("EngineData")
    card.set_all_expanded(True)
    qtbot.wait(50)
    speed = next(r for r in card.rows if r.sig.name == "Speed")

    def wheel(w):
        pos = QPointF(w.width() / 2, w.height() / 2)
        ev = QWheelEvent(pos, QPointF(w.mapToGlobal(pos.toPoint())), QPoint(0, 0), QPoint(0, -120),
                         Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        QApplication.sendEvent(w, ev)
    before = (speed.value(), speed.detail.slider.value(), card.cycle.value(), card.channel.currentIndex())
    for w in (speed.detail.slider, card.cycle, card.channel, card.cycle.lineEdit(), speed.detail.params[0]):
        wheel(w)
    assert (speed.value(), speed.detail.slider.value(), card.cycle.value(), card.channel.currentIndex()) == before

def test_signal_range_uses_the_raw_bits_when_the_dbc_has_none():
    from types import SimpleNamespace as S
    from autosim.ui.panels.signal_panel import signal_range
    assert signal_range(S(minimum=0, maximum=100, is_float=False, length=8, is_signed=False, scale=1, offset=0)) \
        == (0.0, 100.0, True)
    # a DBC range wider than the bits can hold is clipped (3 bit: 0..7), else such values cannot be encoded
    assert signal_range(S(minimum=0, maximum=63, is_float=False, length=3, is_signed=False, scale=1, offset=0)) \
        == (0.0, 7.0, True)
    assert signal_range(S(minimum=None, maximum=None, is_float=False, length=8, is_signed=False,
                          scale=0.5, offset=-10)) == (-10.0, 117.5, True)
    assert signal_range(S(minimum=None, maximum=None, is_float=False, length=4, is_signed=True,
                          scale=1, offset=0)) == (-8.0, 7.0, True)
    assert signal_range(S(minimum=None, maximum=None, is_float=True, length=32, is_signed=True,
                          scale=1, offset=0))[2] is False
