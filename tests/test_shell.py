"""App shell (step 2): minimum size, docks + versioned layout, sidebar, app bar, status bar, command palette and
keyboard shortcuts."""
from pathlib import Path

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from autosim.context import AppContext
from autosim.ui.main_window import LAYOUT_VERSION, MainWindow
from autosim.ui.settings_store import app_settings
from autosim.ui.shell.bus_load import BusLoadMeter, frame_seconds

DATA = Path(__file__).parent / "data"


@pytest.fixture
def make_win(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    boxes = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, n=name: boxes.append((n, a[2:]))))
    from autosim.hardware.esp32_gateway.manager import NodeManager
    monkeypatch.setattr(NodeManager, "scan", lambda self: [])
    wins = []

    def make():
        from PySide6.QtWidgets import QApplication
        from autosim.ui.theme import apply_theme
        apply_theme(QApplication.instance(), "dark")           # as app.main(): theme + font before widgets
        ctx = AppContext()
        ctx.start()
        w = MainWindow(ctx)
        w.boxes = boxes
        qtbot.addWidget(w)
        w.show()
        wins.append(w)
        return w
    yield make
    for w in wins:
        if w.isVisible():
            w.close()


@pytest.fixture
def win(make_win):
    return make_win()


def test_window_fits_1366x768(win, qtbot):
    hint = win.minimumSizeHint()
    assert hint.width() <= 1366 and hint.height() <= 768, hint
    win.resize(1366, 768)
    qtbot.wait(50)
    assert win.size() == QSize(1366, 768)
    assert win.trace_card.height() > 250                       # the trace still gets real space


def test_space_pauses_trace_except_while_typing(win, qtbot):
    from PySide6.QtTest import QTest
    t = win.trace
    view = t.tree if t.fixed.isChecked() else t.view
    view.setFocus()
    QTest.keyClick(view, Qt.Key_Space)
    assert t.pause.isChecked()                                  # Space on the trace: pause
    QTest.keyClick(view, Qt.Key_Space)
    assert not t.pause.isChecked()
    for w in (t.f_name, t.f_id, win.can_conn.port, win.can_conn.nominal):     # text input, combo: no shortcut
        w.setFocus()
        QTest.keyClick(w, Qt.Key_Space)
        assert not t.pause.isChecked(), type(w).__name__
    assert " " in t.f_name.text()                               # the space was typed
    QTest.keyClick(win.can_conn.fd, Qt.Key_Space)               # a focused checkbox keeps its Space
    assert not t.pause.isChecked()
    win.pop_out("Bus trace")                                     # also in the popped-out trace window
    QTest.keyClick(view, Qt.Key_Space)
    assert t.pause.isChecked()
    QTest.keyClick(view, Qt.Key_Space)
    win.trace_card.setFloating(False)
    from PySide6.QtWidgets import QDialog, QLineEdit, QVBoxLayout
    dlg = QDialog(win)                                           # never in another dialog's widgets
    dlg.setLayout(QVBoxLayout())
    other = QLineEdit()
    dlg.layout().addWidget(other)
    from PySide6.QtWidgets import QTreeWidget
    tree = QTreeWidget()
    dlg.layout().addWidget(tree)
    QTest.keyClick(tree, Qt.Key_Space)
    assert not t.pause.isChecked()


def test_modifier_shortcuts_and_tooltips(win, qtbot):
    r = win.commands
    for cid, seq in (("palette", "Ctrl+K"), ("trace.clear", "Ctrl+L"), ("trace.export", "Ctrl+E"),
                     ("trace.search", "Ctrl+F"), ("view.sidebar", "Ctrl+B"), ("trace.pause", "Space")):
        assert r.commands[cid].shortcut == seq
    assert "(Space)" in win.trace.pause.toolTip() and "(Ctrl+L)" in win.trace.clear_act.toolTip()
    assert "(Ctrl+K)" in win.app_bar.palette_btn.toolTip()
    win.trace_card.hide()
    r.run("trace.search")                                        # shows the trace and focuses the search box
    assert not win.trace_card.isHidden()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is win.trace.f_name or win.trace.f_name.hasFocus(), timeout=1000) \
        if win.isActiveWindow() else None


def test_command_palette_filters_and_runs(win, qtbot):
    from autosim.core.can_engine.rx_dispatcher import TraceRow
    from autosim.core.common.frame import CanFrame
    win.trace.on_batch([TraceRow(CanFrame(0x100, b"\x01", channel="A"), "X")])
    assert win.trace.model.rowCount() == 1
    pal = win.palette_popup
    win.show_palette()
    qtbot.waitUntil(pal.isVisible, timeout=1000)
    pal.search.setText("clear tr")
    titles = [pal.list.item(i).text() for i in range(pal.list.count())]
    assert titles == ["Clear trace"]
    pal.run_current()
    assert not pal.isVisible() and win.trace.model.rowCount() == 0
    win.show_palette()
    pal.search.setText("disconnect")                             # nothing open: listed but disabled
    it = pal.list.item(0)
    assert it.text() == "Disconnect all channels" and not (it.flags() & Qt.ItemIsEnabled)
    pal.close()


def test_layout_is_versioned_and_restored(make_win, qtbot):
    w1 = make_win()
    w1.trace_card.hide()                                         # a user change that must survive a restart
    w1.close()
    s = app_settings()
    assert int(s.value("layout/version")) == LAYOUT_VERSION and s.value("layout/can") is not None
    w2 = make_win()
    assert w2.trace_card.isHidden()                              # restored
    w2.close()
    s = app_settings()
    s.setValue("layout/version", LAYOUT_VERSION - 1)             # stored by another version
    logs = []
    w3 = make_win()
    assert not w3.trace_card.isHidden()                          # default layout, not the stale one
    assert any("another version" in w3.log.text.toPlainText() for _ in [0])


def test_reset_layout_from_menu_and_palette(win, qtbot):
    win.plot_card.hide()
    win.app_bar.layout_menu.aboutToShow.emit()
    acts = {a.text(): a for a in win.app_bar.layout_menu.actions()}
    assert set(acts) >= {"Sidebar", "Signals", "Signal plot", "Bus trace", "Log", "Reset layout"}
    assert not acts["Signal plot"].isChecked()
    acts["Reset layout"].trigger()
    assert not win.plot_card.isHidden()
    win.trace_card.hide()
    win.commands.run("view.reset")
    assert not win.trace_card.isHidden()


def test_sidebar_collapses_and_sections_fold(win, qtbot):
    win.toggle_sidebar(animate=False)
    assert win.sidebar_dock.isHidden() and not win.app_bar.sidebar_btn.isChecked()
    win.toggle_sidebar()                                         # animated expand
    qtbot.waitUntil(lambda: win.sidebar_dock.isVisible() and win.sidebar_dock.width() > 200, timeout=2000)
    assert win.app_bar.sidebar_btn.isChecked()
    win.toggle_sidebar()                                         # animated collapse
    qtbot.waitUntil(win.sidebar_dock.isHidden, timeout=2000)
    win.show_channels()                                          # status pill click brings it back
    qtbot.waitUntil(win.sidebar_dock.isVisible, timeout=2000)
    sec = win.sidebar.sections["channels"]
    sec.set_expanded(False)
    assert win.can_conn.isHidden()
    sec.set_expanded(True)
    assert not win.can_conn.isHidden()


def test_app_bar_status_pill_quick_connect_and_status_bar(win, qtbot, monkeypatch):
    bar = win.app_bar
    win._update_status()
    assert bar.status.text == "No channels" and bar.connect_btn.text() == "Connect all"
    c = win.can_conn
    c.port.setCurrentText("SIM1")
    assert c.connect_node(interactive=False)
    assert win.dbc.load_file(str(DATA / "sample.dbc"))
    card = win.signals_panel.add_message("EngineData")
    card.cycle.setValue(10)
    card.enable.setChecked(True)
    qtbot.wait(1100)
    win._update_status()
    assert bar.status.text == "1 channel" and bar.status.state == "online"
    assert bar.connect_btn.text() == "Disconnect all"
    lb = win.status.channel_labels["CAN1"]
    load = float(lb.text().split()[1].rstrip("%"))
    assert 0 < load < 100 and "fr/s" in lb.text()
    assert win.status.rec.text().endswith("Recording") and win.status.elapsed.text() != "00:00:00"
    win.trace.pause.setChecked(True)
    assert win.status.rec.text().endswith("Paused")
    win.trace.pause.setChecked(False)
    # quick disconnect asks while cyclic TX is running; Cancel keeps the channel
    answers = [QMessageBox.Cancel, QMessageBox.Yes]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: answers.pop(0)))
    bar.connect_btn.click()
    assert win.ctx.can.bus.channels == ["CAN1"]
    bar.connect_btn.click()
    assert win.ctx.can.bus.channels == [] and bar.connect_btn.text() == "Connect all"


def test_status_pill_warns_about_errors(win):
    win.can_conn.port.setCurrentText("SIM1")
    assert win.can_conn.connect_node(interactive=False)
    win._on_node_state({"channel": "CAN1", "state": "running", "errors": 3})
    win._update_status()
    assert win.app_bar.status.text == "1 channel · 1 warning" and win.app_bar.status.state == "connecting"
    assert win.status.errors.text() == "Err 3"


def test_last_port_and_database_are_remembered(make_win, qtbot):
    w1 = make_win()
    assert w1.dbc.load_file(str(DATA / "sample.dbc"))
    w1.can_conn.port.setCurrentText("SIM2")
    w1.close()
    w2 = make_win()
    qtbot.waitUntil(lambda: w2.ctx.can.registry.paths != [], timeout=2000)
    assert Path(w2.ctx.can.registry.paths[0]).name == "sample.dbc"
    assert w2.can_conn.port.currentText() == "SIM2"


def test_focused_panel_header_is_highlighted(win, qtbot):
    win._focus_changed(None, win.trace.f_name)
    assert win.trace_card.header.property("focused") is True
    assert not win.signals_card.header.property("focused")
    win._focus_changed(None, win.can_conn.port)                  # sidebar: no panel header highlighted
    assert not any(p.header.property("focused") for p in win.dock_registry().values())


# ---- bus load --------------------------------------------------------------------------------------------
def test_frame_time_matches_the_frame_layout():
    # classic 8-byte, 11-bit id at 500 kbit/s: 111 bits + ~10 % stuffing ~ 0.24 ms
    assert frame_seconds(8, False, False, False, 500_000, 2_000_000) == pytest.approx(242e-6, rel=0.02)
    # 64-byte FD with BRS at 500 k / 2 M: ~0.44 ms; without BRS the data phase runs at 500 k
    brs = frame_seconds(64, True, True, False, 500_000, 2_000_000)
    assert brs == pytest.approx(441e-6, rel=0.03)
    assert frame_seconds(64, True, False, False, 500_000, 2_000_000) > 2.5 * brs
    assert frame_seconds(8, False, False, True, 500_000, 0) > frame_seconds(8, False, False, False, 500_000, 0)


def test_bus_load_meter_per_channel():
    from autosim.core.common.frame import CanFrame
    t = [0.0]
    m = BusLoadMeter(lambda ch: (500_000, 2_000_000) if ch == "CAN1" else None, clock=lambda: t[0])
    for _ in range(100):                                         # 100 classic frames in one second
        m.add(CanFrame(0x100, bytes(8), channel="CAN1"))
    m.add(CanFrame(0x100, bytes(8), channel="unknown"))          # no bit rate known: ignored
    t[0] = 1.0
    snap = m.snapshot()
    assert set(snap) == {"CAN1"}
    load, fps = snap["CAN1"]
    assert load == pytest.approx(100 * 100 * 242e-6, rel=0.03) and fps == 100
    t[0] = 2.0
    assert m.snapshot() == {}


def test_trace_toolbar_wording_and_labels(win):
    t = win.trace
    assert t.f_id.placeholderText() == "ID (hex)" and t.f_chan.itemText(0) == "All channels"
    from PySide6.QtCore import Qt
    for act, label in ((t.autoscroll, "Follow"), (t.flash, "Highlight")):
        btn = t.toolbar.widgetForAction(act)
        assert act.text() == label and act.toolTip() and btn.toolButtonStyle() != Qt.ToolButtonIconOnly
    hh = t.tree.header()
    fm = hh.fontMetrics()
    assert hh.sectionSize(7) >= fm.horizontalAdvance("Cycle [ms]") + 20             # header text fits


def test_disconnect_all_is_ghost_and_always_confirms(win, monkeypatch):
    win.can_conn.port.setCurrentText("SIM1")
    assert win.can_conn.connect_node(interactive=False)
    win._update_status()
    assert win.app_bar.connect_btn.property("kind") == "ghost-danger"            # red only on hover
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.Cancel))
    win.app_bar.connect_btn.click()                                              # no cyclic TX running: still asks
    assert asked and win.ctx.can.bus.channels == ["CAN1"]


def test_font_report_is_logged_at_startup(win):
    text = win.log.text.toPlainText()
    assert "Fonts: UI text resolves to 'Segoe UI'" in text and "WARNING" not in text


def test_panel_header_never_covers_the_panel_content(win, qtbot):
    """QDockWidget sizes its title area from the header's size hint: the content starts below the full header,
    so toolbars (e.g. the trace buttons) are not clipped at the top."""
    from PySide6.QtCore import QPoint
    qtbot.wait(50)
    for title, d in win.dock_registry().items():
        if d is win.sidebar_dock:
            continue
        assert d.widget().mapTo(d, QPoint(0, 0)).y() >= d.header.height() == 36, title
    tb = win.trace.toolbar
    b = tb.widgetForAction(win.trace.fixed)
    top, bottom = b.y(), tb.height() - b.y() - b.height()
    assert abs(top - bottom) <= 1 and b.height() == 32                           # buttons centred, 32 px


def test_app_icon_in_every_size(win):
    from autosim.ui.theme import app_icon
    sizes = {s.width() for s in app_icon().availableSizes()}
    assert {16, 24, 32, 48, 256} <= sizes and not win.windowIcon().isNull()
    from pathlib import Path
    ico = Path(__file__).parents[1] / "packaging" / "autosim.ico"
    data = ico.read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00" and int.from_bytes(data[4:6], "little") >= 6     # multi-size ICO


def test_message_added_before_connecting_sends_after_connect(win, qtbot):
    """Add a message with no channel open, connect afterwards: the section picks the new channel and sends.
    Disconnect / reconnect keeps it working; a closed channel is replaced by another open one."""
    assert win.dbc.load_file(str(DATA / "sample.dbc"))
    card = win.signals_panel.add_message("EngineData")
    assert card.job.channel == "" and card.channel.count() == 0
    c = win.can_conn
    c.port.setCurrentText("SIM1")
    c.channel.setText("CAN1")
    assert c.connect_node(interactive=False)
    assert card.job.channel == "CAN1" and card.channel.currentText() == "CAN1"      # picked up immediately
    card.cycle.setValue(10)
    card.enable.setChecked(True)
    qtbot.waitUntil(lambda: card.job.count > 5, timeout=3000)
    assert not win.boxes                                                          # no "No channel" warning
    card.enable.setChecked(False)
    c._close_channel("CAN1")
    assert card.job.channel == "" and not card.enable.isChecked()
    c.port.setCurrentText("SIM2")
    c.channel.setText("CAN7")
    assert c.connect_node(interactive=False)
    assert card.job.channel == "CAN7"                                            # reconnect: works again
    n = card.job.count
    card._send_once()
    qtbot.waitUntil(lambda: card.job.count > n, timeout=2000)


def test_messages_collapse_to_one_line(win, qtbot):
    """Each message folds to its header (with a channel · cycle · TX · sent summary); the Signals header folds
    every message at once, so many messages fit in view."""
    assert win.dbc.load_file(str(DATA / "sample.dbc"))
    a = win.signals_panel.add_message("EngineData")
    names = [m.name for m in win.ctx.can.registry.messages() if m.name != "EngineData"]
    b = win.signals_panel.add_message(names[0])
    qtbot.wait(50)
    assert a.is_open and not a.body.isHidden() and a.state.isHidden()           # new messages open
    tall = a.sizeHint().height()
    a.fold.click()
    assert not a.is_open and a.body.isHidden() and not a.state.isHidden()
    assert "TX off" in a.state.text() and "sent" in a.state.text()
    assert a.sizeHint().height() < tall / 2
    win.fold_all_btn.click()                                                     # b still open -> fold all
    assert not a.is_open and not b.is_open and win.fold_all_btn.text() == "Expand messages"
    win.fold_all_btn.click()
    assert a.is_open and b.is_open and win.fold_all_btn.text() == "Collapse messages"


def test_theme_switch_is_fast(win, qtbot):
    """Clear-then-apply stylesheet, lazy signal details and paused app filters keep a switch well under 0.3 s."""
    import time
    assert win.dbc.load_file(str(DATA / "sample.dbc"))
    for m in win.ctx.can.registry.messages():
        win.signals_panel.add_message(m.name)
    qtbot.wait(50)
    times = []
    for mode in ("light", "dark", "light", "dark"):
        t0 = time.perf_counter()
        win.set_theme(mode)
        QApplication.processEvents()
        times.append(time.perf_counter() - t0)
    assert min(times) < 0.3, times
