"""PCAN in the GUI: Scan lists PEAK adapters, the form adapts to the device, a missing PEAK driver is announced
with a download link, and Connect all drives two PCAN adapters end to end (model of PCAN-Basic: fake_pcan)."""
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from autosim.context import AppContext
from autosim.hardware.pcan import DRIVER_URL
from autosim.ui.main_window import MainWindow
from tests.fake_pcan import FakePeak

DATA = Path(__file__).parent / "data"


@pytest.fixture
def peak(monkeypatch):
    p = FakePeak().install(monkeypatch)
    p.add("PCAN_USBBUS1", "PCAN-USB FD", fd=True, device_id=1)
    p.add("PCAN_USBBUS2", "PCAN-USB FD", fd=True, device_id=2)
    return p


@pytest.fixture
def make_win(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    boxes = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, n=name: boxes.append((n, a[2:]))))
    from autosim.hardware.esp32_gateway.manager import NodeManager
    # no serial ports (what is plugged into the test machine must not matter): only the PCAN part of the scan
    monkeypatch.setattr(NodeManager, "scan", lambda self: self.scan_pcan())
    from autosim.hardware import pcan
    monkeypatch.setattr(pcan, "peak_usb_present", lambda: False)
    import autosim.ui.panels.connection_panel as cp
    monkeypatch.setattr(cp, "peak_usb_present", lambda: False)
    wins = []

    def make():
        from PySide6.QtWidgets import QApplication

        from autosim.ui.theme import apply_theme
        apply_theme(QApplication.instance(), "dark")
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
        w.close()


def test_scan_lists_pcan_and_the_form_follows_the_device(peak, make_win):
    peak.add("PCAN_USBBUS3", "PCAN-USB", fd=False, device_id=3)
    win = make_win()
    c = win.can_conn
    ports = [c.port.itemText(i) for i in range(c.port.count())]
    assert ports[:3] == ["PCAN_USBBUS1", "PCAN_USBBUS2", "PCAN_USBBUS3"]
    assert c.port.currentText() == "PCAN_USBBUS1"                     # a real adapter is preselected
    assert c.device_kind() == "pcan" and c.port_hint.text() == "PCAN-USB FD · PEAK"
    assert c.iso.isChecked() and not c.iso.isEnabled()
    assert c.fd.isEnabled() and c.fd.isChecked() and c.data.isEnabled()
    c.nominal.setCurrentIndex(c.nominal.findData(500_000))
    assert [c.data.itemData(i) for i in range(c.data.count())] == [500_000, 1_000_000, 2_000_000, 4_000_000,
                                                                  5_000_000, 8_000_000]
    c.nominal.setCurrentIndex(c.nominal.findData(1_000_000))
    assert [c.data.itemData(i) for i in range(c.data.count())] == [1_000_000, 2_000_000, 4_000_000, 5_000_000,
                                                                  8_000_000]
    # PCAN-USB without FD: classic CAN only
    c.port.setCurrentText("PCAN_USBBUS3")
    assert c.port_hint.text() == "PCAN-USB · PEAK"
    assert not c.fd.isChecked() and not c.fd.isEnabled() and not c.data.isEnabled() and not c.brs.isEnabled()
    assert "classic CAN only" in c.fd.toolTip()
    # back to a simulated node: everything available again
    c.port.setCurrentText("SIM1")
    assert c.fd.isEnabled() and c.iso.isEnabled()
    c.fd.setChecked(True)
    assert c.data.isEnabled()
    # free adapters for Connect all: the PCAN channels, never the simulated nodes
    assert c.free_ports() == ["PCAN_USBBUS1", "PCAN_USBBUS2", "PCAN_USBBUS3"]


def test_missing_driver_is_only_reported_when_it_matters(monkeypatch, make_win):
    """No notice in the Channels window; a Log line when a PEAK adapter is plugged in without its driver, and a
    clear Connect error with the download link."""
    FakePeak(driver=False).install(monkeypatch)
    win = make_win()
    c = win.can_conn
    win.show_desktop("Setup")
    assert not hasattr(c, "pcan_note")
    assert next(c.port.itemText(i) for i in range(c.port.count())) == "SIM1"   # nothing PCAN to offer
    # the Log gets a line only when a PEAK adapter has been plugged into this PC, and only once
    logs = []
    win.ctx.events.subscribe("log", logs.append)
    c.scan()
    assert not [m for m in logs if "PEAK driver" in m]
    import autosim.ui.panels.connection_panel as cp
    monkeypatch.setattr(cp, "peak_usb_present", lambda: True)
    c.scan()
    c.scan()
    hits = [m for m in logs if "PEAK driver is not installed" in m]
    assert len(hits) == 1 and DRIVER_URL in hits[0]
    # typing a PCAN port anyway: Connect fails with the same advice instead of a cryptic error
    c.port.setCurrentText("PCAN_USBBUS1")
    assert c.device_kind() == "pcan"
    assert c.connect_node() is False
    kind, text = win.boxes[-1][0], str(win.boxes[-1][1])
    assert kind == "critical" and "PEAK driver" in text and DRIVER_URL in text
    assert win.ctx.can.bus.channels == []


def test_driver_installed_after_start_is_picked_up_by_scan(monkeypatch, make_win):
    p = FakePeak(driver=False).install(monkeypatch)
    win = make_win()
    c = win.can_conn
    win.show_desktop("Setup")
    assert win.ctx.nodes.pcan_driver[0] is False
    p.driver = True                                                    # the user installed the driver
    p.add("PCAN_USBBUS1", "PCAN-USB FD")
    c.scan()
    assert win.ctx.nodes.pcan_driver[0] is True and c.port.currentText() == "PCAN_USBBUS1"


def test_connect_all_drives_two_pcan_adapters_end_to_end(peak, make_win, qtbot):
    win = make_win()
    c = win.can_conn
    assert win.dbc.load_file(str(DATA / "real_dbc" / "hyundai_canfd.dbc"))
    c.nominal.setCurrentIndex(c.nominal.findData(500_000))
    c.data.setCurrentIndex(c.data.findData(2_000_000))
    assert c.connect_all() == ["CAN1", "CAN2"] and not win.boxes
    qtbot.waitUntil(lambda: c.badge.text == "2 connected", timeout=3000)
    nodes = {c.table.item(r, 0).text(): c.table.item(r, 1).text() for r in range(c.table.rowCount())}
    assert nodes == {"CAN1": "PCAN-USB FD 1", "CAN2": "PCAN-USB FD 2"}
    qtbot.waitUntil(lambda: all(c.table.item(r, 3).text() == "running" for r in range(2)), timeout=3000)
    fd = [m for m in win.ctx.can.registry.messages() if m.length >= 32 and m.signals][:2]
    cards = []
    for msg, ch in zip(fd, ("CAN1", "CAN2")):
        card = win.signals_panel.add_message(msg.name, ch)
        card.fd.setChecked(True)
        card.brs.setChecked(True)
        card.cycle.setValue(20)
        card.enable.setChecked(True)
        cards.append(card)
    rows = lambda: win.trace.model.rows()
    qtbot.waitUntil(lambda: {(r.frame.channel, r.frame.is_rx, r.name) for r in rows()} >= {
        ("CAN1", False, fd[0].name), ("CAN2", True, fd[0].name),
        ("CAN2", False, fd[1].name), ("CAN1", True, fd[1].name)}, timeout=4000)
    for card in cards:
        card.enable.setChecked(False)
    rx = [r for r in rows() if r.frame.is_rx]
    assert all(r.frame.is_fd and r.frame.bitrate_switch and len(r.frame.data) >= 32 for r in rx)
    # the counters of the node table follow the traffic
    qtbot.waitUntil(lambda: int(c.table.item(0, 5).text() or 0) > 0 and int(c.table.item(1, 4).text() or 0) > 0,
                    timeout=3000)
    # renaming is not possible on PCAN: a clear message, nothing changes
    assert c.rename_node_to("CAN1", "ECU") is False
    assert any("cannot be renamed" in str(b[1]) for b in win.boxes)
    # unplugging an adapter: its row and the status bar show the error, the Log says why
    logs = []
    win.ctx.events.subscribe("log", logs.append)
    next(a for a in peak.adapters.values() if a.channel == "PCAN_USBBUS2").unplug()
    qtbot.waitUntil(lambda: c.table.item(1, 3).text() == "error", timeout=3000)
    qtbot.waitUntil(lambda: any(m.startswith("[CAN2]") and "unplugged" in m for m in logs), timeout=3000)
    assert c.disconnect_all() == 2 and win.ctx.can.bus.channels == []
    assert not any(a.open for a in peak.adapters.values())


def test_project_restores_a_pcan_channel(peak, make_win, tmp_path):
    win = make_win()
    c = win.can_conn
    c.port.setCurrentText("PCAN_USBBUS2")
    c.channel.setText("CAN3")
    c.nominal.setCurrentIndex(c.nominal.findData(1_000_000))
    c.data.setCurrentIndex(c.data.findData(8_000_000))
    assert c.connect_node()
    from autosim.core.common.config import ProjectConfig
    cfg = ProjectConfig()
    c.collect(cfg)
    c.disconnect_all()
    c.apply(cfg)
    assert win.ctx.can.bus.channels == ["CAN3"]
    a = next(x for x in peak.adapters.values() if x.channel == "PCAN_USBBUS2")
    assert a.open and (a.nominal, a.data) == (1_000_000, 8_000_000)


def test_menus_offer_the_peak_driver_download(peak, make_win, monkeypatch):
    win = make_win()
    opened = []
    from PySide6.QtGui import QDesktopServices
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString())))
    menus = {a.text().replace("&", ""): a.menu() for a in win.menu.actions() if a.menu()}
    for name in ("Hardware", "Help"):
        act = next(a for a in menus[name].actions() if a.text().startswith("Download PEAK driver"))
        act.trigger()
    assert opened == [DRIVER_URL, DRIVER_URL]
