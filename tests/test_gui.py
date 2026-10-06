import re
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from autosim.context import AppContext
from autosim.ui.main_window import MainWindow
from autosim.ui.qt_loop import qt_thread_factory

DATA = Path(__file__).parent / "data"


@pytest.fixture
def win(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path))
    boxes = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, n=name: boxes.append((n, a[2:]))))
    ctx = AppContext(qt_thread_factory)
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
    speed.spin.setValue(88.0)                           # user edit reaches the TX state
    assert card.job.state.get_signal("Speed") == pytest.approx(88.0)
    speed.gen.setCurrentText("sine")                    # generator takes over
    assert "Speed" in card.job.bindings and not speed.spin.isEnabled()
    gear = next(r for r in card.rows if r.sig.name == "Gear")
    gear.choice.setCurrentIndex(3)
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


def test_someip_flow_through_panel(win, qtbot):
    p, c = win.someip, win.eth_conn
    assert p.load_config_file(str(DATA / "vsomeip.json"))
    assert p.load_proto_file(str(DATA / "sample.proto"))
    svc = win.ctx.services[0]
    svc.methods.append(__import__("autosim.core.someip_engine.config_loader", fromlist=["x"]).MethodDef(
        "GetState", 1, "", "proto:demo.VehicleState"))
    p.refresh_services()
    c.nic.setCurrentIndex(0)                            # loopback entry
    c.sd_port.setValue(30497)
    c.toggle_someip()
    assert win.ctx.someip.running
    p.select_service(0)
    p.toggle_offer()
    p.toggle_consume()
    qtbot.waitUntil(lambda: win.ctx.someip.consumers[0].available, timeout=5000)
    qtbot.waitUntil(lambda: p.cons_badge.text == "Available", timeout=3000)
    assert p.prov_badge.state == "online" and p.cons_badge.state == "online"
    assert "offered" in p.svc_tree.topLevelItem(0).text(2) and "available" in p.svc_tree.topLevelItem(0).text(2)

    # provider-side fixed protobuf response, then call the method from the caller tab
    p.s_svc.setCurrentIndex(0)
    p.s_method.setCurrentIndex(0)
    p.s_payload.mode.setCurrentIndex(2)
    p.s_payload.msg.setCurrentText("demo.VehicleState")
    p.s_payload.form.set_values({"speed": 77, "mode": "ECO"})
    p._set_response(True)
    p.m_svc.setCurrentIndex(0)
    p.m_method.setCurrentIndex(0)
    p.m_resp.set_spec("proto:demo.VehicleState")
    p.m_req.mode.setCurrentIndex(0)
    p.m_req.raw.setText("01 02")
    p.send_request()
    qtbot.waitUntil(lambda: p.m_status.text().startswith("OK"), timeout=4000)
    assert p.m_resp.form.values()["speed"] == 77 and "ECO" in p.m_status.text()
    assert p.s_table.rowCount() == 1

    # event: subscribe then publish through the widgets
    p.sub_svc.setCurrentIndex(0)
    p._subscribe(True)
    qtbot.waitUntil(lambda: bool(win.ctx.someip.providers[0].subscribers), timeout=5000)
    p.e_svc.setCurrentIndex(0)
    p.e_event.setCurrentIndex(0)
    p.e_payload.mode.setCurrentIndex(1)
    p.e_payload.types.setText("u16")
    p.e_payload.values.setText("513")
    p.publish_once()
    qtbot.waitUntil(lambda: p.ev_table.rowCount() == 1, timeout=3000)
    assert p.ev_table.item(0, 3).text() == "02 01"
    assert p.sd_table.rowCount() > 0

    # bad input is reported, not raised
    p.e_payload.values.setText("not-a-number")
    p.publish_once()
    assert win.boxes and win.boxes[-1][0] == "warning"

    c.toggle_someip()
    assert not win.ctx.someip.running


def test_project_save_and_load_roundtrip(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    win.dbc.load_file(str(DATA / "sample.dbc"))
    win.someip.load_proto_file(str(DATA / "sample.proto"))
    win.can_conn.channel.setText("MYCAN")
    out = tmp_path / "p.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    win.save_project()
    assert out.exists()
    win.ctx.can.registry.clear()
    win.can_conn.channel.setText("other")
    win.load_project(str(out))
    assert win.ctx.can.registry.paths and win.can_conn.channel.text() == "MYCAN"


def test_two_separate_top_level_tabs(win):
    tabs = [win.center.tabText(i) for i in range(win.center.count())]
    assert tabs == ["CAN", "SOME/IP"]
    assert win.can_tab.isAncestorOf(win.can_conn) and win.can_tab.isAncestorOf(win.trace)
    assert win.can_tab.isAncestorOf(win.dbc) and win.can_tab.isAncestorOf(win.signals_panel)
    assert win.someip_tab.isAncestorOf(win.eth_conn) and win.someip_tab.isAncestorOf(win.someip)
    assert not win.someip_tab.isAncestorOf(win.can_conn) and not win.can_tab.isAncestorOf(win.someip)


def test_theme_switch_light_dark_and_persist(win):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from autosim.ui.theme import ThemeManager, is_dark
    app = QApplication.instance()
    seen = []
    ThemeManager.instance().changed.connect(seen.append)
    win.set_theme("light")
    assert not is_dark(app) and "#F8FAFC" in app.styleSheet() and "#2563EB" in app.styleSheet()
    assert app.palette().window().color().name().upper() == "#F8FAFC"           # native palette follows too
    assert str(QSettings("AutoSim", "AutoSimStudio").value("theme")) == "light"
    assert win.settings_dialog.theme_buttons["light"].isChecked()
    win.set_theme("dark")
    assert is_dark(app) and "#09090B" in app.styleSheet() and "#4F46E5" in app.styleSheet()
    assert app.palette().window().color().name().upper() == "#09090B"
    assert str(QSettings("AutoSim", "AutoSimStudio").value("theme")) == "dark"
    assert seen[-2:] == [False, True]                                           # widgets are notified


def test_dark_and_light_qss_follow_the_design_system():
    from autosim.ui.icons import build_qss_assets
    from autosim.ui.styles.themes import DARK, DARK_QSS, LIGHT, LIGHT_QSS, build_qss
    dark, light = (build_qss(pal, build_qss_assets(pal["MUTED"])) for pal in (DARK, LIGHT))
    for qss in (dark, light):
        assert "@" not in qss
        for needle in ("border-radius: 6px", "min-height: 30px", "min-height: 34px", "QPushButton:hover",
                       "QPushButton:pressed", "QPushButton:disabled", "border-bottom: 3px solid", "QFrame#card"):
            assert needle in qss, needle
    for hexv in ("#09090B", "#141518", "#1A1B20", "#27272B", "#F4F4F5", "#8F92A1", "#4F46E5", "#6366F1",
                 "#1F2026", "#2E3038", "#E4E4E7", "#18181B", "#EF4444", "#F87171"):
        assert hexv in dark, hexv
    for hexv in ("#F8FAFC", "#FFFFFF", "#F1F5F9", "#E2E8F0", "#0F172A", "#64748B", "#2563EB", "#1D4ED8",
                 "#CBD5E1", "#334155", "#FEF2F2", "#FCA5A5", "#DC2626"):
        assert hexv in light, hexv
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
        qss = build_qss(pal, build_qss_assets(pal["MUTED"]))
        assert not _blue_borders(qss, exempt=("QTabBar", 'kind="primary"', ":checked", "QSlider")), pal["BASE"]
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


def test_someip_services_empty_state_and_action_buttons(win):
    p = win.someip
    assert p.svc_stack.currentIndex() == 0 and p.empty.title.text() == "No Services Configured"
    assert not p.offer_btn.isEnabled() and not p.cons_btn.isEnabled()
    assert p.load_config_file(str(DATA / "vsomeip.json"))
    assert p.svc_stack.currentIndex() == 1
    assert p.svc_tree.currentItem() is p.svc_tree.topLevelItem(0)          # first service auto-selected
    assert p.offer_btn.isEnabled() and p.offer_btn.text() == "Offer Service" and p.offer_btn.property("kind") == "primary"
    assert p.cons_btn.text() == "Consume Service" and p.cons_btn.property("kind") in ("", None)


def test_eth_card_layout_has_no_duplicate_status_text(win):
    e = win.eth_conn
    assert e.objectName() == "card" and not hasattr(e, "eth_state")
    assert e.badge.text == "Stopped" and e.eth_btn.property("kind") == "primary"
    e.nic.setCurrentIndex(0)
    e.sd_port.setValue(30496)
    e.toggle_someip()
    assert e.badge.state == "online" and e.badge.text.startswith("Running")
    assert e.eth_btn.text() == "Stop SOME/IP" and e.eth_btn.property("kind") == "danger"
    e.toggle_someip()
    assert e.badge.state == "offline" and e.eth_btn.text() == "Start SOME/IP"


def test_controls_share_32px_height_and_trace_header_34px(win, qtbot):
    from PySide6.QtWidgets import QApplication
    from autosim.ui.theme import apply_theme
    apply_theme(QApplication.instance(), "dark")
    c, e = win.can_conn, win.eth_conn
    win.center.setCurrentWidget(win.someip_tab)
    qtbot.wait(100)
    for w in (e.eth_btn, e.refresh_btn, e.mc_btn, e.ip, e.mcast, e.sd_port, e.nic):  # compact sidebar card: 30 px
        assert w.height() == 30, ("SOME/IP", type(w).__name__, w.height())
    for w in (e.ip, e.mcast, e.sd_port, e.nic):
        assert w.width() <= 200 and w.maximumWidth() == 200
    win.center.setCurrentWidget(win.can_tab)
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
    assert set(ws.docks) == {"CAN connection", "DBC explorer", "Signal plot", "Bus trace"}
    win.pop_out("Bus trace")
    d = ws.docks["Bus trace"]
    assert d.isFloating() and d.isWindow()                      # separate top-level window
    win.pop_out("Signal plot")
    assert ws.docks["Signal plot"].isFloating()
    win._reset_layout()
    assert not d.isFloating() and not ws.docks["Signal plot"].isFloating()
    assert win.can_tab.isAncestorOf(win.trace)


def test_trace_id_and_data_columns_use_soft_cyan_and_orange(win):
    from PySide6.QtCore import Qt
    from autosim.core.can_engine.rx_dispatcher import TraceRow
    from autosim.core.common.frame import CanFrame
    from autosim.ui.theme import apply_theme
    from PySide6.QtWidgets import QApplication
    apply_theme(QApplication.instance(), "dark")
    m = win.trace.model
    m.add_batch([TraceRow(CanFrame(0x100, b"\x01\x02", channel="A", is_rx=True), "Rx"),
                 TraceRow(CanFrame(0x101, b"\x03", channel="A", is_rx=False), "Tx")])
    fg = lambda r, c: m.data(m.index(r, c), Qt.ForegroundRole)  # noqa: E731
    assert fg(0, 3).name().lower() == "#7dd3fc" and fg(1, 3).name().lower() == "#7dd3fc"      # ID
    assert fg(0, 6).name().lower() == "#fdba74" and fg(1, 6).name().lower() == "#fdba74"      # data bytes
    assert fg(0, 2) is None and fg(1, 2).name().lower() == "#38bdf8"                        # Tx marker only
    assert fg(0, 4) is None                                                                  # name: default text


def test_status_pills_follow_the_spec_in_both_themes():
    from autosim.ui.theme import badge_style
    for dark in (True, False):
        text, bg, border, dot = badge_style("offline", dark)
        assert dot == "#EF4444" and bg[3] == 255 and text in ("#8F92A1", "#64748B")    # neutral pill, red dot
        text, bg, border, dot = badge_style("online", dark)
        assert dot == "#10B981" and bg[:3] == (16, 185, 129) and bg[3] < 60 and border[:3] == (16, 185, 129)
    assert badge_style("online", True)[0] == "#34D399"                                  # dark: spec text colour
    assert badge_style("online", False)[0] == "#047857"                                 # light: readable on white


def test_can_card_buttons_are_compact_fixed_size_and_aligned(win, qtbot):
    from PySide6.QtWidgets import QApplication
    from autosim.ui.theme import apply_theme
    apply_theme(QApplication.instance(), "dark")
    win.center.setCurrentWidget(win.can_tab)
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
    win.center.setCurrentWidget(win.someip_tab)                # SOME/IP sidebar: compact 130 x 30 start button
    qtbot.wait(150)
    e = win.eth_conn
    assert (e.eth_btn.width(), e.eth_btn.height()) == (130, 30)
    assert e.eth_btn.width() < e.width() / 2                                     # not stretched


def test_button_styles_secondary_primary_and_outlined_destructive():
    from autosim.ui.icons import build_qss_assets
    from autosim.ui.styles.themes import DARK, LIGHT, build_qss

    def rule(qss, selector):
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", qss)
        assert m, selector
        return m.group(1)

    dark = build_qss(DARK, build_qss_assets(DARK["MUTED"]))
    light = build_qss(LIGHT, build_qss_assets(LIGHT["MUTED"]))
    sec = rule(dark, "QPushButton")
    assert "#1F2026" in sec and "1px solid #2E3038" in sec and "#E4E4E7" in sec
    pri, pri_h = rule(dark, 'QPushButton[kind="primary"]'), rule(dark, 'QPushButton[kind="primary"]:hover')
    assert "#4F46E5" in pri and "font-weight: 600" in pri and "color: #FFFFFF" in pri and "#6366F1" in pri_h
    dang, dang_h = rule(dark, 'QPushButton[kind="danger"]'), rule(dark, 'QPushButton[kind="danger"]:hover')
    assert "background: #18181B" in dang and "1px solid #EF4444" in dang and "color: #F87171" in dang
    assert "#7F1D1D" in dang_h and "#FFFFFF" in dang_h
    sec = rule(light, "QPushButton")
    assert "#F8FAFC" in sec and "1px solid #CBD5E1" in sec and "#334155" in sec
    pri, pri_h = rule(light, 'QPushButton[kind="primary"]'), rule(light, 'QPushButton[kind="primary"]:hover')
    assert "#2563EB" in pri and "color: #FFFFFF" in pri and "#1D4ED8" in pri_h
    dang = rule(light, 'QPushButton[kind="danger"]')
    assert "background: #FEF2F2" in dang and "1px solid #FCA5A5" in dang and "color: #DC2626" in dang


def test_menu_bar_removed_and_gear_button_in_tab_corner(win, qtbot):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QMenuBar, QToolButton
    assert win.menuWidget() is None and not win.findChildren(QMenuBar)          # no File / View / Tools bar
    gear = win.center.cornerWidget(Qt.TopRightCorner)
    assert gear is win.btn_settings and isinstance(gear, QToolButton)
    assert not gear.icon().isNull() and gear.toolTip().startswith("Settings")
    qtbot.wait(100)
    # the tab bar starts at the very top of the window (menu bar space reclaimed)
    assert win.center.tabBar().mapTo(win, QPoint(0, 0)).y() <= 2
    # gear sits at the top-right corner, level with the tabs
    g = gear.mapTo(win, QPoint(0, 0))
    assert g.y() <= 12 and win.width() - (g.x() + gear.width()) <= 24
    assert (gear.width(), gear.height()) == (32, 32) or gear.height() <= 40


def test_gear_hover_style_in_stylesheet():
    from autosim.ui.icons import build_qss_assets
    from autosim.ui.styles.themes import DARK, build_qss
    qss = build_qss(DARK, build_qss_assets(DARK["MUTED"]))
    m = re.search(r"QToolButton#settings-btn:hover\s*\{([^}]*)\}", qss)
    assert m and "#27272A" in m.group(1)
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
    monkeypatch.setattr(win.someip, "load_config", lambda: called.append("svc"))
    monkeypatch.setattr(win.someip, "load_proto", lambda: called.append("proto"))
    monkeypatch.setattr(type(win), "open_project", lambda self: called.append("open"))
    monkeypatch.setattr(type(win), "save_project", lambda self: called.append("save"))
    d2 = type(d)(win)                                        # fresh dialog binds the patched callbacks
    for key in ("database", "service", "proto", "open_project", "save_project"):
        d2.file_buttons[key].click()
    assert called == ["db", "svc", "proto", "open", "save"]
    assert win.center.currentWidget() is win.someip_tab      # service/proto pages switch tab first
    # View menu: panel visibility, pop out, reset layout.
    # (the SOME/IP tab is showing now, so CAN panels are off screen -- they must still read as "shown")
    d.sync()
    assert all(cb.isChecked() for cb in d.dock_checks.values())
    win.center.setCurrentWidget(win.can_tab)
    qtbot.wait(100)
    assert set(d.dock_checks) == {"CAN connection", "DBC explorer", "Signal plot", "Bus trace", "Log"}
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
    assert c.table.item(0, 1).foreground().color().name().lower() == "#fbbf24"
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
    assert "flash firmware/esp32_can_node" in args[0]
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


def test_someip_master_detail_layout(win, qtbot):
    from PySide6.QtCore import Qt
    p = win.someip
    assert p.splitter.orientation() == Qt.Horizontal and p.splitter.count() == 2
    assert p.splitter.widget(0) is p.sidebar and p.sidebar.isAncestorOf(win.eth_conn)
    assert p.sidebar.isAncestorOf(p.svc_tree) and p.splitter.widget(1) is p.inspector
    assert [p.tabs.tabText(i) for i in range(p.tabs.count())] == \
        ["Method Caller", "Event Publisher", "Provider Responses", "SD Log Trace"]
    win.center.setCurrentWidget(win.someip_tab)
    qtbot.wait(100)
    assert 280 <= p.sidebar.width() <= 340 and p.inspector.width() > p.sidebar.width()

    from autosim.core.someip_engine.config_loader import EventDef, MethodDef, ServiceDef
    win.ctx.services.extend([
        ServiceDef("Door", 0x1111, 1, methods=[MethodDef("Open", 1), MethodDef("Close", 2)],
                   events=[EventDef("Opened", 0x8001, 0x10)]),
        ServiceDef("Seat", 0x2222, 2, methods=[MethodDef("Move", 5)], events=[]),
    ])
    p.refresh_services()
    t = p.svc_tree
    assert t.topLevelItemCount() == 2 and t.topLevelItem(0).text(0) == "Door"
    door = t.topLevelItem(0)
    groups = {door.child(i).text(0): door.child(i) for i in range(door.childCount())}
    assert set(groups) == {"Methods", "Events"} and groups["Methods"].childCount() == 2
    # selecting a method opens the Method Caller on it; an event opens the Event Publisher
    t.setCurrentItem(groups["Methods"].child(1))
    assert p.tabs.currentIndex() == 0 and p.m_method.currentData().name == "Close"
    t.setCurrentItem(groups["Events"].child(0))
    assert p.tabs.currentIndex() == 1 and p.e_event.currentData().event_id == 0x8001
    assert p.svc_title.text() == "Door" and "0x1111" in p.svc_sub.text()
    # selecting the other service retargets every tab (the per-tab selectors are hidden)
    p.select_service(1)
    assert p.svc_title.text() == "Seat" and all(c.currentIndex() == 1 and c.isHidden() for c in p._svc_combos)
    assert p.m_method.currentData().name == "Move"


def test_can_tab_vertical_splitter_35_65_and_panel_pop_out(win, qtbot):
    from PySide6.QtCore import Qt
    sp = win.can_split
    assert sp.orientation() == Qt.Vertical and sp.count() == 2
    assert sp.widget(0) is win.top_split and sp.widget(1) is win.trace_card
    assert win.top_split.widget(0) is win.signals_card and win.top_split.widget(1) is win.plot_card
    win.center.setCurrentWidget(win.can_tab)
    qtbot.wait(150)
    win._size_can_docks()
    qtbot.wait(50)
    top, bottom = sp.sizes()
    assert abs(bottom / (top + bottom) - 0.65) < 0.05                           # trace gets ~65 %
    # pop the trace out to its own window and back
    win.pop_out("Bus trace")
    assert win.trace_card.isFloating() and win.trace_card.isWindow() and sp.count() == 1
    win.trace_card.close()                                                     # closing the window re-docks
    assert not win.trace_card.isFloating() and sp.widget(1) is win.trace_card


def test_dbc_explorer_headers_never_cut_numeric_columns(win):
    from PySide6.QtWidgets import QHeaderView
    hh = win.dbc.tree.header()
    assert hh.sectionResizeMode(0) == QHeaderView.Stretch
    assert all(hh.sectionResizeMode(i) == QHeaderView.ResizeToContents for i in range(1, hh.count()))
