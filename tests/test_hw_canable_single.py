"""Real-device tests that need only ONE CANable 2.0 adapter (USB 16D0:117E).

    $env:AUTOSIM_HW = "1"; uv run pytest tests/test_hw_canable_single.py -v -s

A lone CAN node cannot receive (the firmware does not echo its own frames) and nobody acknowledges what it
sends, so these tests verify everything up to and including the adapter:
  * every bit-rate configuration is accepted and the adapter stays responsive
  * real production DBCs (opendbc: Hyundai CAN FD, Tesla, VW, Toyota): every signal of every message is set
    to a random valid value, sent through the app, and the exact bytes written to the adapter are decoded
    back with the DBC and compared
  * the USB link keeps up (no new 'USB RX buffer full' flag) and the adapter survives connect cycles
  * the GUI drives the adapter with an FD DBC loaded
"""
import os
import random
import time
from pathlib import Path

import pytest

from autosim.core.can_engine.database import DatabaseRegistry
from autosim.core.can_engine.engine import CanEngine
from autosim.core.can_engine.signal_codec import MessageState
from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.core.common.frame import CanChannelConfig, CanFrame
from autosim.hardware.esp32_gateway.manager import NodeManager
from autosim.hardware.slcan import protocol as S
from autosim.hardware.slcan.node import SlcanNode, _SerialTransport

HW = os.environ.get("AUTOSIM_HW") == "1"
PORTS = [p for p in NodeManager().scan() if p["kind"] == "slcan"] if HW else []
pytestmark = pytest.mark.skipif(not (HW and PORTS), reason="set AUTOSIM_HW=1 and connect a CANable adapter")
REAL_DBC = Path(__file__).parent / "data" / "real_dbc"
USB_BITS = (1 << 5) | (1 << 6)                    # USB RX / TX buffer full: must never appear


class RecordingTransport:
    """The real serial port, plus a copy of every byte written to the adapter."""

    def __init__(self, port: str) -> None:
        self.inner = _SerialTransport(port)
        self.written = bytearray()

    def read_some(self) -> bytes:
        return self.inner.read_some()

    def write(self, data: bytes) -> None:
        self.written += data
        self.inner.write(data)

    def close(self) -> None:
        self.inner.close()

    def lines(self) -> list[str]:
        return [ln for ln in self.written.decode("ascii").split("\r") if ln]


def open_node(cfg: CanChannelConfig, record: bool = False):
    rec = RecordingTransport(PORTS[0]["port"]) if record else None
    node = SlcanNode(PORTS[0]["port"], "CAN1", serial_number=PORTS[0]["serial"],
                     transport_factory=(lambda: rec) if record else None, status_period_s=0.3)
    node.connect()
    node.configure(cfg)
    node.start()
    return node, rec


def random_values(msg, rng: random.Random) -> dict[str, float]:
    """A random, exactly representable physical value for every signal (respecting min / max)."""
    out = {}
    for sig in msg.signals:
        if sig.is_float:
            out[sig.name] = rng.uniform(-1000, 1000) if sig.length == 64 else float(rng.randint(-1000, 1000))
            continue
        lo_raw = -(1 << (sig.length - 1)) if sig.is_signed else 0
        hi_raw = (1 << (sig.length - 1)) - 1 if sig.is_signed else (1 << sig.length) - 1
        candidates = []
        for _ in range(20):
            raw = rng.randint(lo_raw, hi_raw)
            phys = raw * sig.scale + sig.offset
            if (sig.minimum is None or phys >= sig.minimum - 1e-9) and (sig.maximum is None or phys <= sig.maximum + 1e-9):
                candidates.append(phys)
                break
        out[sig.name] = candidates[0] if candidates else (sig.minimum if sig.minimum is not None else sig.offset)
    return out


# ------------------------------------------------------------------------------------------------
def test_every_bit_rate_configuration_is_accepted():
    combos = [(n, d, fd) for n in sorted(S.NOMINAL_CODES) for d, fd in ((2_000_000, False), (2_000_000, True),
                                                                        (5_000_000, True))]
    for nominal, data, fd in combos:
        node, _ = open_node(CanChannelConfig(nominal, data, fd, fd))
        try:
            assert node.ping(), f"no answer after configuring {nominal}/{data}/{fd}"
            reg = node.read_error_register()
            assert reg is not None and not (reg & USB_BITS), f"{nominal}/{data}: error register {reg:#x}"
        finally:
            node.close()
    print(f"\n{len(combos)} configurations accepted (nominal {sorted(S.NOMINAL_CODES)}, data 2M / 5M, classic)")


@pytest.mark.parametrize("dbc, cfg", [
    ("hyundai_canfd.dbc", CanChannelConfig(500_000, 2_000_000, True, True)),
    ("tesla_model3_vehicle.dbc", CanChannelConfig(500_000, 2_000_000, False, False)),
    ("vw_mqb.dbc", CanChannelConfig(500_000, 2_000_000, False, False)),
    ("toyota_2017_ref_pt.dbc", CanChannelConfig(500_000, 2_000_000, False, False)),
])
def test_real_dbc_signals_reach_the_adapter_bit_exact(dbc, cfg):
    reg = DatabaseRegistry()
    db = reg.load(REAL_DBC / dbc)
    rng = random.Random(1234)
    node, rec = open_node(cfg, record=True)
    sent = []
    try:
        for msg in db.messages:
            state = MessageState(msg)
            values = random_values(msg, rng)
            for name, v in values.items():
                state.values[name] = v                      # exact values (set_signal would clamp)
            data = state.encode()
            fd = msg.length > 8
            frame = CanFrame(msg.frame_id, data, is_extended=msg.is_extended_frame, is_fd=fd, bitrate_switch=fd)
            node.send_frame(frame)
            sent.append((msg, state, data))
            if len(sent) % 20 == 0:
                time.sleep(0.02)                            # let USB drain; the adapter has no flow control
        time.sleep(0.5)
        assert node.ping(), "adapter stopped answering"
        reg_now = node.read_error_register()
        assert reg_now is not None and not (reg_now & USB_BITS), f"USB overflow flagged: {reg_now:#x}"
    finally:
        node.close()

    lines = [ln for ln in rec.lines() if ln[0] in "tTdDbB"]
    assert len(lines) == len(sent), (len(lines), len(sent))
    mismatches = 0
    for (msg, state, data), line in zip(sent, lines):
        f = S.decode_frame(line)                            # what actually went to the adapter
        assert f is not None and f.arbitration_id == msg.frame_id and f.is_extended == msg.is_extended_frame
        assert f.data[:len(data)] == data and f.is_fd == (msg.length > 8)
        decoded = msg.decode(f.data[:msg.length], decode_choices=False, allow_truncated=True)
        for name, v in decoded.items():
            if abs(v - state.values[name]) > max(1e-6, abs(next(s for s in msg.signals if s.name == name).scale)):
                mismatches += 1
    assert mismatches == 0
    n_sig = sum(len(m.signals) for m, _, _ in sent)
    print(f"\n{dbc}: {len(sent)} messages / {n_sig} signals sent to the real adapter, all decoded back exactly")


def test_connect_disconnect_cycles_through_the_app():
    eb = EventBus()
    eng = CanEngine(eb)
    mgr = NodeManager(eb)
    eng.start()
    try:
        for i in range(20):
            backend = mgr.create_backend(PORTS[0]["port"], "CAN1")
            eng.open_channel("CAN1", backend, CanChannelConfig(500_000, 5_000_000 if i % 2 else 2_000_000, True, True))
            node = mgr.nodes["CAN1"]
            assert node.state.value == "running" and node.ping()
            eng.bus.send("CAN1", CanFrame(0x100 + i, bytes(range(16)), is_fd=True, bitrate_switch=True))
            eng.close_channel("CAN1")
            mgr.release("CAN1")
    finally:
        eng.shutdown()


def test_gui_drives_the_adapter_with_a_can_fd_dbc(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QMessageBox
    from autosim.context import AppContext
    from autosim.ui.main_window import MainWindow
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
    logs = []
    ctx.events.subscribe(events.LOG, logs.append)
    try:
        c = win.can_conn
        assert c.port.currentText() == PORTS[0]["port"] and c.device_kind() == "slcan"   # picked automatically
        assert win.dbc.load_file(str(REAL_DBC / "hyundai_canfd.dbc"))
        c.nominal.setCurrentIndex(c.nominal.findData(500_000))
        c.data.setCurrentIndex(c.data.findData(2_000_000))
        assert c.connect_node(), boxes
        qtbot.waitUntil(lambda: c.badge.state == "online", timeout=3000)
        msg = next(m for m in ctx.can.registry.messages() if m.length == 32)
        card = win.signals_panel.add_message(msg.name)
        card.fd.setChecked(True)
        card.brs.setChecked(True)
        card.cycle.setValue(20)
        card.enable.setChecked(True)
        qtbot.wait(1500)
        card.enable.setChecked(False)
        tx = [r for r in win.trace.model.rows() if not r.frame.is_rx and r.name == msg.name]
        assert len(tx) >= 40 and all(len(r.frame.data) == 32 and r.frame.is_fd and r.frame.bitrate_switch for r in tx)
        assert ctx.nodes.nodes["CAN1"].status.tx_count >= len(tx)
        assert ctx.nodes.nodes["CAN1"].ping()
        print(f"\nGUI: {msg.name} (32-byte CAN FD, BRS) sent {len(tx)} times at 20 ms through the real CANable")
        c.port.setCurrentText(PORTS[0]["port"])             # with a 2nd adapter the form moved on to it
        assert c.toggle_btn.text() == "Disconnect"
        c.toggle_btn.click()
        assert c.badge.state == "offline" and not boxes
    finally:
        win.close()
