"""Real-hardware tests with CANable 2.0 USB-CAN FD adapters (USB 16D0:117E, slcan firmware).

    $env:AUTOSIM_HW = "1"; uv run pytest tests/test_hw_canable.py -v

* one adapter plugged in   -> detection, configuration, real CAN FD transmission, "not acknowledged" report
* two adapters on one bus  -> frames in both directions, classic / FD / FD+BRS at 2 and 5 Mbit/s, all 16
                              FD lengths, 11/29-bit ids, a 1000-frame burst (order + loss), throughput
Wire CANH-CANH and CANL-CANL and terminate the bus with 120 ohm at each end (many adapters have a jumper).
"""
import os
import time

import pytest

from autosim.core.can_engine.engine import CanEngine
from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.core.common.frame import CanChannelConfig, CanFrame
from autosim.hardware.esp32_gateway.manager import NodeManager
from autosim.hardware.slcan import protocol as S


def canables() -> list[dict]:
    try:
        return [p for p in NodeManager().scan() if p["kind"] == "slcan"]
    except Exception:
        return []


HW = os.environ.get("AUTOSIM_HW") == "1"
PORTS = canables() if HW else []
pytestmark = pytest.mark.skipif(not HW, reason="set AUTOSIM_HW=1 to run tests on real CANable adapters")
needs_one = pytest.mark.skipif(len(PORTS) < 1, reason="no CANable adapter connected")
needs_two = pytest.mark.skipif(len(PORTS) < 2, reason="needs two CANable adapters on one bus")


def wait_for(cond, t=3.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def rig():
    eb = EventBus()
    eng = CanEngine(eb)
    mgr = NodeManager(eb)
    rx: dict[str, list[CanFrame]] = {}
    logs: list[str] = []

    def on_rows(rows):
        for r in rows:
            if r.frame.is_rx:
                rx.setdefault(r.frame.channel, []).append(r.frame)
    eb.subscribe(events.CAN_RX, on_rows)
    eb.subscribe(events.LOG, logs.append)
    eng.start()

    def open_ch(ch, port, cfg):
        backend = mgr.create_backend(port, ch)               # kind detected from the real USB VID:PID
        eng.open_channel(ch, backend, cfg)
        return mgr.nodes[ch]

    yield type("Rig", (), {"eng": eng, "mgr": mgr, "rx": rx, "logs": logs, "open": staticmethod(open_ch)})
    for ch in list(mgr.nodes):
        try:
            eng.close_channel(ch)
        finally:
            mgr.release(ch)
    eng.shutdown()


@needs_one
def test_adapter_is_detected_and_identifies_itself(rig):
    port = PORTS[0]
    node = rig.open("CAN1", port["port"], CanChannelConfig(500_000, 2_000_000, True, True))
    assert type(node).__name__ == "SlcanNode"
    assert "canable2" in node.info["fw"] and node.info["node_id"] == port["serial"]
    assert node.state.value == "running"


@needs_one
@pytest.mark.skipif(len(PORTS) != 1, reason="only meaningful when the adapter is alone on the bus")
def test_lone_adapter_transmits_and_reports_missing_acknowledge(rig):
    node = rig.open("CAN1", PORTS[0]["port"], CanChannelConfig(500_000, 2_000_000, True, True))
    if node.info["latched_errors"] & (1 << S.ERR_FULLBUF_CANTX):
        pytest.skip("TX-full flag latched since an earlier run: re-plug the adapter to re-test this")
    errors = []
    prev = node.on_error
    node.on_error = lambda c, t: (errors.append((c, t)), prev(c, t))
    for i in range(80):
        rig.eng.bus.send("CAN1", CanFrame(0x100 + i, bytes(range(16)), is_fd=True, bitrate_switch=True))
    assert wait_for(lambda: errors, 4)
    assert any(c in (S.ERR_FULLBUF_CANTX, S.ERR_CAN_TXFAIL) and "acknowledged" in t for c, t in errors)


def _pair(rig, cfg):
    a = rig.open("A", PORTS[0]["port"], cfg)
    b = rig.open("B", PORTS[1]["port"], cfg)
    time.sleep(0.2)
    return a, b


@needs_two
@pytest.mark.parametrize("nominal, data", [(500_000, 2_000_000), (500_000, 5_000_000), (1_000_000, 5_000_000),
                                           (250_000, 2_000_000)])
def test_frames_cross_the_bus_in_both_directions(rig, nominal, data):
    _pair(rig, CanChannelConfig(nominal, data, True, True))
    frames = [CanFrame(0x123, b"\x01\x02\x03"), CanFrame(0x1ABCDEF, bytes(range(8)), is_extended=True),
              CanFrame(0x200, bytes(range(12)), is_fd=True), CanFrame(0x7FF, bytes(range(64)), is_fd=True,
                                                                   bitrate_switch=True),
              CanFrame(0x18DAF110, bytes(range(32)), is_extended=True, is_fd=True, bitrate_switch=True)]
    for f in frames:
        rig.eng.bus.send("A", f)
    assert wait_for(lambda: len(rig.rx.get("B", [])) == len(frames)), rig.logs
    got = [(f.arbitration_id, f.data, f.is_fd, f.bitrate_switch, f.is_extended) for f in rig.rx["B"]]
    assert got == [(f.arbitration_id, f.data, f.is_fd, f.bitrate_switch, f.is_extended) for f in frames]
    rig.eng.bus.send("B", CanFrame(0x55, b"\xAA\xBB", is_fd=True, bitrate_switch=True))
    assert wait_for(lambda: rig.rx.get("A")) and rig.rx["A"][0].data == b"\xAA\xBB"


@needs_two
def test_every_fd_length_and_padding(rig):
    _pair(rig, CanChannelConfig(500_000, 2_000_000, True, True))
    lengths = list(range(0, 9)) + [10, 12, 15, 16, 20, 24, 30, 32, 40, 48, 64]
    for n in lengths:
        rig.eng.bus.send("A", CanFrame(0x300 + n, bytes((i * 7) & 0xFF for i in range(n)), is_fd=True,
                                       bitrate_switch=True))
    assert wait_for(lambda: len(rig.rx.get("B", [])) == len(lengths))
    for n, f in zip(lengths, rig.rx["B"]):
        padded = S.fd_length(n)
        assert f.arbitration_id == 0x300 + n and len(f.data) == padded
        assert f.data[:n] == bytes((i * 7) & 0xFF for i in range(n)) and not any(f.data[n:])


@needs_two
def test_burst_of_1000_fd_frames_arrives_complete_and_in_order(rig):
    _pair(rig, CanChannelConfig(500_000, 5_000_000, True, True))
    t0 = time.perf_counter()
    for i in range(1000):
        rig.eng.bus.send("A", CanFrame(0x100, i.to_bytes(2, "big") + bytes(14), is_fd=True, bitrate_switch=True))
        if i % 50 == 49:
            time.sleep(0.01)                                  # stay below the adapter's 64-frame TX queue
    assert wait_for(lambda: len(rig.rx.get("B", [])) == 1000, 15), f"got {len(rig.rx.get('B', []))}"
    seq = [int.from_bytes(f.data[:2], "big") for f in rig.rx["B"]]
    assert seq == list(range(1000))
    dt = time.perf_counter() - t0
    print(f"\n1000 FD frames (16 B, 500k/5M BRS) in {dt:.2f} s -> {1000 / dt:.0f} frames/s")


@needs_two
def test_bit_rate_mismatch_is_detected(rig):
    rig.open("A", PORTS[0]["port"], CanChannelConfig(500_000, 2_000_000, True, True))
    rig.open("B", PORTS[1]["port"], CanChannelConfig(250_000, 2_000_000, True, True))
    for i in range(80):
        rig.eng.bus.send("A", CanFrame(0x100, b"\x01"))
    time.sleep(1.5)
    assert not rig.rx.get("B") or all(f.arbitration_id != 0x100 for f in rig.rx["B"])


@needs_one
def test_latched_error_flags_are_read_at_connect_not_reported_as_new(rig):
    node = rig.open("CAN1", PORTS[0]["port"], CanChannelConfig(500_000, 2_000_000, True, True))
    base = node.info["latched_errors"]
    assert isinstance(base, int)
    if base:
        assert any("already latched" in m for m in rig.logs)
    seen = []
    node.on_error = lambda c, t: seen.append(c)
    time.sleep(1.5)                                            # several 'E' polls with nothing new happening
    assert seen == []


@needs_two
def test_gui_two_adapters_send_and_receive_dbc_can_fd_in_parallel(qtbot, monkeypatch, tmp_path):
    """The full app on the real bus: Hyundai CAN FD DBC loaded, Connect all opens both adapters (CAN1, CAN2),
    each sends its own 32/24-byte FD+BRS message every 10 ms while receiving the other's; received signals are
    decoded bit-exact and the fixed trace shows one row per message with the latest signal values."""
    from pathlib import Path
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QMessageBox
    from autosim.context import AppContext
    from autosim.ui.main_window import MainWindow
    from autosim.ui.models.fixed_trace_model import COL_VALUE
    QSettings.setDefaultFormat(QSettings.IniFormat)          # never the real registry
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    boxes = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, n=name: boxes.append((n, a[2:]))))
    ctx = AppContext()
    ctx.start()
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.show()
    try:
        assert win.dbc.load_file(str(Path(__file__).parent / "data" / "real_dbc" / "hyundai_canfd.dbc"))
        c = win.can_conn
        c.nominal.setCurrentIndex(c.nominal.findData(500_000))
        c.data.setCurrentIndex(c.data.findData(2_000_000))
        assert c.connect_all() == ["CAN1", "CAN2"], boxes
        qtbot.waitUntil(lambda: c.badge.text == "2 connected", timeout=3000)
        msgs = [m for m in ctx.can.registry.messages() if m.length == 32 and len(m.signals) > 4][:1] + \
               [m for m in ctx.can.registry.messages() if m.length == 24 and len(m.signals) > 4][:1]
        cards = []
        for msg, ch in zip(msgs, ("CAN1", "CAN2")):
            card = win.signals_panel.add_message(msg.name, ch)
            card.fd.setChecked(True)
            card.brs.setChecked(True)
            card.cycle.setValue(10)
            row = next(r for r in card.rows if r.sig.length >= 4 and not r.sig.choices)
            row.set_generator("ramp")
            card.enable.setChecked(True)
            cards.append(card)
        qtbot.wait(2000)
        for card in cards:
            card.enable.setChecked(False)
        qtbot.wait(300)
        rows = win.trace.model.rows()
        for msg, tx_ch, rx_ch in ((msgs[0], "CAN1", "CAN2"), (msgs[1], "CAN2", "CAN1")):
            tx = [r for r in rows if r.name == msg.name and r.frame.channel == tx_ch and not r.frame.is_rx]
            rx = [r for r in rows if r.name == msg.name and r.frame.channel == rx_ch and r.frame.is_rx]
            assert len(tx) >= 150 and len(rx) >= len(tx) - 2, (msg.name, len(tx), len(rx))
            assert all(r.frame.is_fd and r.frame.bitrate_switch and len(r.frame.data) == msg.length for r in rx)
            sent = {bytes(r.frame.data) for r in tx}
            assert all(bytes(r.frame.data) in sent for r in rx)          # every received payload was sent
            print(f"\n{msg.name} ({msg.length} B FD+BRS): {tx_ch} sent {len(tx)}, {rx_ch} received {len(rx)}")
        for ch in ("CAN1", "CAN2"):
            st = ctx.nodes.nodes[ch].status
            assert st.rx_count > 0 and st.tx_count > 0, (ch, st)
        fm = win.trace.fixed_model
        assert fm.message_count() == 4
        for i in range(fm.rowCount()):
            e = fm.entry(i)
            if e.row.frame.is_rx:
                decoded = ctx.can.registry.decode(e.row.frame)[1]
                top = fm.index(i, 0)
                s0 = e.signals[0]
                assert fm.data(fm.index(0, COL_VALUE, top)).startswith(f"{decoded[s0.name]:g}")
        c.port.setCurrentText(ctx.nodes.nodes["CAN1"].port)
        c.toggle_btn.click()
        c.port.setCurrentText(ctx.nodes.nodes["CAN2"].port)
        c.toggle_btn.click()
        assert ctx.can.bus.channels == [] and not boxes
    finally:
        win.close()
