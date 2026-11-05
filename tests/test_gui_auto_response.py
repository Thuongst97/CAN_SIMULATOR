# ruff: noqa: F811  (the imported `win` fixture is requested by name in the test arguments)
"""Auto response UI: rule dialog (DBC and raw), panel table / switch / persistence, the Android bridge channel in
the Channels panel, and the user's scenario end to end through the GUI (0x21 = 10 -> 0x27 = 10, 20 -> 100)."""
import json
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox

from autosim.core.can_engine.auto_response import ResponseRule
from autosim.core.common.frame import CanFrame
from autosim.hardware.bridge.sim_bridge import BridgeServer, MemoryCanPort
from autosim.ui.panels.auto_response_panel import C_HITS, C_ON, AutoResponsePanel, RuleDialog
from tests.test_gui import win  # noqa: F401  (the `win` fixture, used as a test argument)

DATA = Path(__file__).parent / "data"
DBC = DATA / "vhal_demo.dbc"


@pytest.fixture
def bridge_srv():
    port = MemoryCanPort("vcan0")
    srv = BridgeServer(port, "127.0.0.1", 0, log=lambda s: None).start()
    srv.can_port = port
    yield srv
    srv.stop()


def pick(picker, text):
    i = picker.combo.findText(text, Qt.MatchStartsWith)
    assert i >= 0, text
    picker.combo.setCurrentIndex(i)


def set_signal_value(dlg, name, value):
    for r in range(dlg.sig_table.rowCount()):
        if dlg.sig_table.item(r, 0).text() == name:
            dlg.sig_table.item(r, 1).setText(value)
            return
    raise AssertionError(name)


def dbc_rule(win, req, resp, name=""):
    dlg = RuleDialog(win.ctx, None, ["AND1"], win)
    dlg.name.setText(name)
    pick(dlg.rx_msg, "HU_Request")
    assert dlg.match.currentData() == "signal"                 # a DBC message: condition on a signal
    dlg.signal.setCurrentText("ReqValue")
    dlg.value.setText(str(req))
    pick(dlg.tx_msg, "GW_Response")
    assert dlg.tx_mode.currentData() == "signal" and dlg.tx_dlc.value() == 8 and not dlg.tx_fd.isChecked()
    set_signal_value(dlg, "RespValue", str(resp))
    rule, text = dlg.check()
    assert rule is not None, text
    return dlg, rule, text


def test_panel_is_a_dock_tabbed_with_the_plot(win):
    assert "Auto response" in win.can_ws.docks
    tabbed = win.can_ws.tabifiedDockWidgets(win.can_ws.docks["Signal plot"])
    assert win.can_ws.docks["Auto response"] in tabbed
    p = win.auto_response
    assert not p.master.isChecked() and not win.ctx.can.responder.enabled
    assert p.table.rowCount() == 0 and p.empty.isVisible()
    assert "0/0" in p.state.text()


def test_dialog_builds_the_user_rules_from_the_dbc(win):
    assert win.dbc.load_file(str(DBC))
    dlg, r10, text = dbc_rule(win, 10, 10)
    # unused bits are padded with 1 like every DBC message AutoSim sends; RespStatus (bits 8-9) keeps its 0
    assert "0x027 [0A FC FF FF FF FF FF FF]" in text and "ReqValue == 10" in text
    assert dlg.buttons.button(QDialogButtonBox.Ok).isEnabled()
    assert (r10.rx_id, r10.match, r10.signal, r10.value, r10.tx_id, r10.tx_mode, r10.tx_signals) == \
        (0x21, "signal", "ReqValue", "10", 0x27, "signal", {"RespValue": "10"})
    _dlg, r20, text = dbc_rule(win, 20, 100)
    assert "[64 FC FF FF FF FF FF FF]" in text
    dlg2 = RuleDialog(win.ctx, r20.copy(), [], win)            # editing restores every field
    assert dlg2.rule().to_dict() == r20.to_dict()


def test_dialog_raw_mode_without_database(win):
    dlg = RuleDialog(win.ctx, None, [], win)
    dlg.rx_msg.combo.setEditText("0x21")
    dlg.match.setCurrentIndex(dlg.match.findData("raw"))
    dlg.value.setText("20")
    dlg.tx_msg.combo.setEditText("0x27")
    dlg.tx_mode.setCurrentIndex(dlg.tx_mode.findData("raw"))
    dlg.tx_value.setText("100")
    rule, text = dlg.check()
    assert rule is not None, text
    assert (rule.rx_id, rule.match, rule.value, rule.tx_id, rule.tx_value) == (0x21, "raw", "20", 0x27, "100")
    assert "[64 00 00 00 00 00 00 00]" in text and "byte 0 == 20" in text
    dlg.tx_value.setText("rx * 2")                              # expression of the received value
    assert "[28 00" in dlg.check()[1]
    dlg.tx_fd.setChecked(True)
    dlg.tx_dlc.setValue(10)                                     # FD pads to 12
    assert "FD 0x027 [28 00 00 00 00 00 00 00 00 00 00 00]" in dlg.check()[1]
    dlg.tx_msg.combo.setEditText("hello")                       # problems disable OK and are shown
    assert dlg.check()[0] is None
    assert not dlg.buttons.button(QDialogButtonBox.Ok).isEnabled()
    assert dlg.preview.text().startswith("Problem:")


def test_table_switch_toggle_and_persistence(win, tmp_path):
    p = win.auto_response
    p.add_rule(ResponseRule("ten", rx_id=0x21, match="raw", value="10", tx_id=0x27, tx_value="10"))
    p.add_rule(ResponseRule("twenty", rx_id=0x21, match="raw", value="20", tx_id=0x27, tx_value="100"))
    assert p.table.rowCount() == 2 and not p.empty.isVisible()
    assert p.table.item(0, 1).text() == "ten" and "byte 0 == 10" in p.table.item(0, 3).text()
    p.master.setChecked(True)
    assert win.ctx.can.responder.enabled and p.state.text().startswith("ON")
    p.table.item(1, C_ON).setCheckState(Qt.Unchecked)           # the checkbox switches one rule
    assert not p.rules[1].enabled and "1/2" in p.state.text()
    # restored by a new panel (QSettings) ...
    p2 = AutoResponsePanel(win.ctx, win.bridge)
    assert [r.name for r in p2.rules] == ["ten", "twenty"] and p2.master.isChecked()
    # ... exported / imported ...
    f = tmp_path / "rules.json"
    p.export_file(str(f))
    assert json.loads(f.read_text())["rules"][1]["enabled"] is False
    p.table.selectRow(0)
    p.remove_selected()
    assert [r.name for r in p.rules] == ["twenty"]
    assert p.import_file(str(f)) == [] and [r.name for r in p.rules] == ["ten", "twenty"]
    p.table.selectRow(0)
    p.duplicate_selected()
    assert [r.name for r in p.rules] == ["ten", "ten (copy)", "twenty"]


def test_project_file_keeps_rules(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    p = win.auto_response
    p.add_rule(ResponseRule("ten", rx_id=0x21, match="raw", value="10", tx_id=0x27, tx_value="10"))
    p.set_enabled(True)
    path = tmp_path / "x.project.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    win.save_project()
    assert json.loads(path.read_text())["auto_response"]["rules"][0]["name"] == "ten"
    p.set_rules([])
    p.set_enabled(False)
    win.load_project(str(path))
    assert [r.name for r in p.rules] == ["ten"] and p.master.isChecked()
    p2 = AutoResponsePanel(win.ctx, win.bridge)                 # imports straight from a project file too
    p2.set_rules([])
    assert p2.import_file(str(path)) == [] and [r.name for r in p2.rules] == ["ten"]


def test_invalid_rule_is_shown_but_not_active(win):
    p = win.auto_response
    p.add_rule(ResponseRule("bad", rx_id=0x21, tx_id=0x27, tx_value="rx"))     # rx without a read value
    assert "Not active" in p.table.item(0, 2).toolTip()
    assert win.ctx.can.responder._index == {}


def test_connection_panel_offers_the_android_bridge(win):
    c = win.can_conn
    i = c.port.findText("bridge://127.0.0.1:29536")
    assert i >= 0
    c.port.setCurrentIndex(i)
    assert c.device_kind() == "bridge" and "Android bridge" in c.port_hint.text()
    assert not c.nominal.isEnabled() and not c.data.isEnabled() and not c.loop.isEnabled()
    c.fd.setChecked(False)
    c.fd.setChecked(True)
    assert not c.data.isEnabled()                               # FD toggle does not re-enable the rate
    c.port.setEditText("bridge://10.0.0.5:29536")              # a typed head-unit address
    assert c.device_kind() == "bridge"
    c.port.setCurrentText("SIM1")
    assert c.nominal.isEnabled() and c.loop.isEnabled()


def test_user_scenario_end_to_end_through_the_gui(win, qtbot, bridge_srv):
    """DBC loaded, two rules made in the dialog, Android bridge channel connected from the Channels panel,
    auto response switched on: the 'VHAL' sends 0x21 = 10 / 20 and reads back 0x27 = 10 / 100."""
    assert win.dbc.load_file(str(DBC))
    c = win.can_conn
    c.port.setEditText(f"bridge://127.0.0.1:{bridge_srv.address[1]}")
    c.channel.setText("AND1")
    assert c.connect_node(interactive=False), win.boxes
    qtbot.waitUntil(lambda: c.table.rowCount() == 1 and c.table.item(0, 3).text() == "running", timeout=3000)
    assert c.table.item(0, 1).text() == "android:vcan0"
    p = win.auto_response
    for req, resp in ((10, 10), (20, 100)):
        _dlg, rule, _t = dbc_rule(win, req, resp, f"req {req}")
        p.add_rule(rule)
    p.master.setChecked(True)
    port = bridge_srv.can_port
    req = win.ctx.can.registry.message_by_name("HU_Request")
    resp = win.ctx.can.registry.message_by_name("GW_Response")
    for i, (v, want) in enumerate(((10, 10), (20, 100), (10, 10))):
        port.inject(CanFrame(0x21, req.encode({"ReqValue": v, "ReqCounter": i})))
        assert port.wait_written(i + 1)
        f = port.written[-1]
        assert f.arbitration_id == 0x27 and resp.decode(f.data)["RespValue"] == want
    qtbot.waitUntil(lambda: p.table.item(0, C_HITS).text() == "2" and p.table.item(1, C_HITS).text() == "1",
                    timeout=2000)
    win.trace.fixed.setChecked(False)                            # chronological trace: requests and answers
    qtbot.waitUntil(lambda: sum(1 for r in win.trace.model.rows() if r.name == "GW_Response"
                                and not r.frame.is_rx) == 3, timeout=2000)
    assert sum(1 for r in win.trace.model.rows() if r.name == "HU_Request" and r.frame.is_rx) == 3
    p.master.setChecked(False)                                   # off: no more answers
    port.inject(CanFrame(0x21, req.encode({"ReqValue": 10, "ReqCounter": 9})))
    time.sleep(0.1)
    assert len(port.written) == 3


def test_loop_guard_unticks_the_rule_in_the_table(win, qtbot):
    """A rule answering its own id on a loopback channel would flood the bus: the engine switches it off and
    the table follows."""
    c = win.can_conn
    c.port.setCurrentText("SIM1")
    c.loop.setChecked(True)
    assert c.connect_node(interactive=False)
    qtbot.waitUntil(lambda: c.table.rowCount() == 1 and c.table.item(0, 3).text() == "running", timeout=3000)
    win.ctx.can.responder.max_rate = 50
    p = win.auto_response
    p.add_rule(ResponseRule("echo", rx_id=0x27, tx_id=0x27, tx_value="1"))
    p.master.setChecked(True)
    win.ctx.can.bus.send("CAN1", CanFrame(0x27, b"\x01"))         # starts the ping-pong
    qtbot.waitUntil(lambda: not p.rules[0].enabled, timeout=5000)
    qtbot.waitUntil(lambda: p.table.item(0, C_ON).checkState() == Qt.Unchecked, timeout=2000)
    assert "switched off" in p.table.item(0, C_HITS).toolTip()
