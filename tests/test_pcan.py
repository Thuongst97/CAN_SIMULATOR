"""PEAK PCAN adapters through python-can's PcanBus, against a model of the PCAN-Basic library (tests/fake_pcan.py).

Covers bit timing, driver detection, scan, classic / CAN FD traffic between two adapters, bit-rate mismatch, error
passive / bus-off, unplugging, channels used by another application, loopback, and the engine end to end.
"""
from __future__ import annotations

import time

import can
import pytest

from autosim.core.common.frame import CanChannelConfig, CanFrame
from autosim.hardware.esp32_gateway.serial_node import NodeError, NodeState
from autosim.hardware.pcan import DRIVER_URL, driver_status, is_pcan_port, scan_channels
from autosim.hardware.pcan.node import FLAG_BUSOFF, FLAG_PASSIVE, PcanBackend, PcanNode
from autosim.hardware.pcan.timing import (
    CLASSIC,
    FD_DATA,
    FD_NOMINAL,
    TimingError,
    classic_timing,
    describe,
    fd_timing,
)
from tests.fake_pcan import FakePeak, classic_rate, parse_fd


def wait_for(cond, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def peak(monkeypatch):
    p = FakePeak().install(monkeypatch)
    p.add("PCAN_USBBUS1", "PCAN-USB FD", fd=True, device_id=1)
    p.add("PCAN_USBBUS2", "PCAN-USB FD", fd=True, device_id=2)
    return p


def adapter(peak, port):
    return next(a for a in peak.adapters.values() if a.channel == port)


class Node:
    """A PcanNode with recorded callbacks."""

    def __init__(self, port: str, channel: str, cfg: CanChannelConfig, status_period_s: float = 0.05) -> None:
        self.rx: list[CanFrame] = []
        self.errors: list[tuple[int, str]] = []
        self.states: list[NodeState] = []
        self.n = PcanNode(port, channel, status_period_s=status_period_s)
        self.n.on_rx = self.rx.append
        self.n.on_error = lambda code, text: self.errors.append((code, text))
        self.n.on_state = self.states.append
        self.cfg = cfg

    def open(self) -> Node:
        b = PcanBackend(self.n)
        b.set_rx_callback(self.rx.append)
        b.open(self.cfg)
        return self


FD_500_2M = CanChannelConfig(500_000, 2_000_000, True, True)
CLASSIC_500 = CanChannelConfig(500_000, 2_000_000, False, False)


# ---- bit timing --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("rate", [125_000, 250_000, 500_000, 1_000_000])
def test_classic_timing_hits_the_rate_within_controller_limits(rate):
    t = classic_timing(rate)
    assert t.f_clock == 8_000_000 and t.bitrate == rate
    assert 1 <= t.tseg1 <= CLASSIC.tseg1 and 1 <= t.tseg2 <= CLASSIC.tseg2 and 1 <= t.sjw <= CLASSIC.sjw
    assert abs(t.sample_point - 80.0) <= 5.0
    assert classic_rate(t.btr0 << 8 | t.btr1) == rate               # the register value PCAN-Basic receives


@pytest.mark.parametrize("nominal,data", [(n, d) for n in (125_000, 250_000, 500_000, 1_000_000)
                                          for d in (1_000_000, 2_000_000, 4_000_000, 5_000_000, 8_000_000)
                                          if d >= n])
def test_fd_timing_hits_both_rates_with_sensible_sample_points(nominal, data):
    t = fd_timing(nominal, data)
    assert t.f_clock == 80_000_000 and t.nom_bitrate == nominal and t.data_bitrate == data
    assert abs(t.nom_sample_point - 80.0) <= 2.5
    assert abs(t.data_sample_point - 75.0) <= 5.0
    assert 1 <= t.nom_tseg1 <= FD_NOMINAL.tseg1 and 1 <= t.nom_tseg2 <= FD_NOMINAL.tseg2
    assert 1 <= t.data_tseg1 <= FD_DATA.tseg1 and 1 <= t.data_tseg2 <= FD_DATA.tseg2
    assert t.nom_sjw == min(t.nom_tseg2, FD_NOMINAL.sjw) and t.data_sjw == min(t.data_tseg2, FD_DATA.sjw)
    if nominal >= 250_000:                                       # same prescaler in both phases (TDC friendly)
        assert t.nom_brp == t.data_brp


def test_impossible_rates_are_refused_with_a_reason():
    with pytest.raises(TimingError, match="33333"):
        classic_timing(33_333)
    with pytest.raises(TimingError, match="data rate 3000000"):
        fd_timing(500_000, 3_000_000)


def test_timing_description_for_the_log():
    assert describe(fd_timing(500_000, 2_000_000)) == "500 kbit/s @ 80.0 % / 2 Mbit/s @ 75.0 %"
    assert describe(classic_timing(1_000_000)) == "1 Mbit/s @ 75.0 %"


# ---- driver and scan ---------------------------------------------------------------------------------------
def test_missing_driver_is_reported_with_the_download_link(monkeypatch):
    FakePeak(driver=False).install(monkeypatch)
    ok, detail = driver_status()
    assert not ok and "could not be loaded" in detail
    assert scan_channels() == []
    from autosim.hardware.esp32_gateway.manager import NodeManager
    mgr = NodeManager()
    assert mgr.scan_pcan() == [] and mgr.pcan_driver[0] is False
    with pytest.raises(NodeError) as e:
        PcanNode("PCAN_USBBUS1", "CAN1").connect()
    assert "PEAK driver" in str(e.value) and DRIVER_URL in str(e.value)


def test_scan_lists_plugged_channels_with_fd_capability_in_natural_order(monkeypatch):
    p = FakePeak().install(monkeypatch)
    p.add("PCAN_USBBUS10", "PCAN-USB Pro FD", fd=True, device_id=0x10)
    p.add("PCAN_USBBUS2", "PCAN-USB", fd=False, device_id=2)
    p.add("PCAN_USBBUS1", "PCAN-USB FD", fd=True, device_id=1)
    gone = p.add("PCAN_USBBUS3", "PCAN-USB FD")
    gone.unplug()
    busy = p.add("PCAN_USBBUS4", "PCAN-USB FD")
    busy.occupied = True
    assert driver_status() == (True, "4.8.0.123")
    chans = scan_channels()
    assert [c["port"] for c in chans] == ["PCAN_USBBUS1", "PCAN_USBBUS2", "PCAN_USBBUS4", "PCAN_USBBUS10"]
    by = {c["port"]: c for c in chans}
    assert by["PCAN_USBBUS1"]["supports_fd"] and not by["PCAN_USBBUS2"]["supports_fd"]
    assert by["PCAN_USBBUS2"]["label"] == "PCAN-USB" and "device id 0x2" in by["PCAN_USBBUS2"]["hwid"]
    assert by["PCAN_USBBUS4"]["occupied"] and "in use" in by["PCAN_USBBUS4"]["hwid"]
    from autosim.hardware.esp32_gateway.manager import NodeManager
    mgr = NodeManager()
    entries = [e for e in mgr.scan() if e["kind"] == "pcan"]
    assert [e["port"] for e in entries] == [c["port"] for c in chans] and mgr.pcan_driver == (True, "4.8.0.123")
    assert mgr.kind_of("pcan_usbbus1") == ("pcan", "") and is_pcan_port("PCAN_USBBUS1")


def test_connect_checks_the_channel_is_present_and_free(peak):
    with pytest.raises(NodeError, match="no PCAN adapter"):
        PcanNode("PCAN_USBBUS7", "CAN1").connect()
    adapter(peak, "PCAN_USBBUS2").occupied = True
    with pytest.raises(NodeError, match="in use by another application"):
        PcanNode("PCAN_USBBUS2", "CAN1").connect()
    info = PcanNode("PCAN_USBBUS1", "CAN1").connect()
    assert info["node_id"] == "PCAN-USB FD 1" and info["fw"] == "PCAN-Basic 4.8.0.123" and info["supports_fd"]


# ---- traffic -------------------------------------------------------------------------------------------------
def test_two_adapters_exchange_classic_and_fd_frames(peak):
    a = Node("PCAN_USBBUS1", "CAN1", FD_500_2M).open()
    b = Node("PCAN_USBBUS2", "CAN2", FD_500_2M).open()
    assert a.n.state == b.n.state == NodeState.RUNNING
    assert a.n.info["timing"] == "500 kbit/s @ 80.0 % / 2 Mbit/s @ 75.0 %"
    p = parse_fd(adapter(peak, "PCAN_USBBUS1").init_calls[-1][1])
    assert (p["nominal"], p["data"], p["clock"]) == (500_000, 2_000_000, 80_000_000)
    frames = [CanFrame(0x123, bytes([1, 2, 3]), False, False, False),
              CanFrame(0x18DAF110, bytes(range(8)), True, False, False),
              CanFrame(0x456, bytes(range(64)), False, True, True),
              CanFrame(0x457, bytes(range(12)), False, True, False)]
    for f in frames:
        a.n.send_frame(f)
    b.n.send_frame(CanFrame(0x7FF, b"\xAA" * 21, False, True, True))
    assert wait_for(lambda: len(b.rx) == 4 and len(a.rx) == 1)
    got = [(f.arbitration_id, bytes(f.data), f.is_extended, f.is_fd, f.bitrate_switch) for f in b.rx]
    assert got == [(f.arbitration_id, bytes(f.data), f.is_extended, f.is_fd, f.bitrate_switch) for f in frames]
    assert all(f.is_rx and f.channel == "CAN2" for f in b.rx)
    assert bytes(a.rx[0].data) == b"\xAA" * 21 + bytes(3)          # 21 bytes -> 24 (next valid FD length)
    assert a.n.status.tx_count == 4 and b.n.status.rx_count == 4 and not a.errors and not b.errors
    a.n.close()
    b.n.close()
    assert not adapter(peak, "PCAN_USBBUS1").open and a.n.state == NodeState.DISCONNECTED


def test_classic_channel_refuses_fd_frames_and_oversized_payloads(peak):
    a = Node("PCAN_USBBUS1", "CAN1", CLASSIC_500).open()
    assert adapter(peak, "PCAN_USBBUS1").init_calls[-1][0] == "classic"
    with pytest.raises(NodeError, match="classic CAN"):
        a.n.send_frame(CanFrame(0x100, bytes(16), False, True, True))
    with pytest.raises(NodeError, match="max 8"):
        a.n.send_frame(CanFrame(0x100, bytes(9), False, False, False))
    assert a.n.tx_nacks == 2
    a.n.close()


def test_adapter_without_fd_cannot_be_configured_for_fd(monkeypatch):
    p = FakePeak().install(monkeypatch)
    p.add("PCAN_USBBUS1", "PCAN-USB", fd=False)
    n = Node("PCAN_USBBUS1", "CAN1", FD_500_2M)
    with pytest.raises(NodeError, match="does not support CAN FD"):
        n.open()
    Node("PCAN_USBBUS1", "CAN1", CLASSIC_500).open().n.close()


def test_bit_rate_mismatch_gives_error_frames_then_error_passive_and_bus_off(peak):
    a = Node("PCAN_USBBUS1", "CAN1", CanChannelConfig(500_000, 2_000_000, False, False)).open()
    b = Node("PCAN_USBBUS2", "CAN2", CanChannelConfig(250_000, 2_000_000, False, False)).open()
    for i in range(8):
        a.n.send_frame(CanFrame(0x100, bytes([i]), False, False, False))
    assert wait_for(lambda: any("error passive" in t for _c, t in a.errors))
    assert a.n.status.bus_flags & FLAG_PASSIVE
    assert wait_for(lambda: any("error frames" in t for _c, t in b.errors)) and not b.rx
    assert sum("error frames" in t for _c, t in b.errors) == 1         # reported once, counted every time
    assert b.n.crc_errors == 8
    for i in range(24):
        a.n.send_frame(CanFrame(0x100, bytes([i]), False, False, False))
    assert wait_for(lambda: any("bus-off" in t for _c, t in a.errors))
    assert a.n.status.bus_flags & FLAG_BUSOFF
    with pytest.raises(NodeError, match="send failed"):
        a.n.send_frame(CanFrame(0x100, b"\x00", False, False, False))
    assert a.n.tx_nacks == 1
    a.n.close()
    b.n.close()


def test_fd_frame_to_a_classic_only_listener_is_an_error_frame(peak):
    a = Node("PCAN_USBBUS1", "CAN1", FD_500_2M).open()
    b = Node("PCAN_USBBUS2", "CAN2", CLASSIC_500).open()
    a.n.send_frame(CanFrame(0x100, bytes(8), False, False, False))       # classic: both understand it
    a.n.send_frame(CanFrame(0x101, bytes(16), False, True, True))        # FD: the classic node flags it
    assert wait_for(lambda: len(b.rx) == 1 and b.n.crc_errors == 1)
    assert b.rx[0].arbitration_id == 0x100
    a.n.close()
    b.n.close()


def test_unplugging_the_adapter_puts_the_channel_in_error(peak):
    a = Node("PCAN_USBBUS1", "CAN1", FD_500_2M).open()
    adapter(peak, "PCAN_USBBUS1").unplug()
    assert wait_for(lambda: a.n.state == NodeState.ERROR)
    assert wait_for(lambda: any("unplugged" in t for _c, t in a.errors))
    time.sleep(0.2)
    assert sum("unplugged" in t for _c, t in a.errors) == 1             # reader and status loop: one message
    assert not a.n.ping()
    with pytest.raises(NodeError):
        a.n.send_frame(CanFrame(0x100, b"\x01", False, False, False))
    a.n.close()


def test_channel_opened_by_another_application_in_the_meantime(peak):
    n = Node("PCAN_USBBUS1", "CAN1", FD_500_2M)
    n.n.connect()
    adapter(peak, "PCAN_USBBUS1").occupied = True                       # e.g. PCAN-View opened it meanwhile
    with pytest.raises(NodeError, match="cannot open the PCAN channel"):
        n.n.configure(FD_500_2M)
        n.n.start()


def test_loopback_echoes_on_the_pc_without_touching_the_adapter(peak):
    cfg = CanChannelConfig(500_000, 2_000_000, True, True, extras={"loopback": True})
    a = Node("PCAN_USBBUS1", "CAN1", cfg).open()
    a.n.send_frame(CanFrame(0x321, bytes(range(10)), False, True, True))
    assert len(a.rx) == 1 and bytes(a.rx[0].data) == bytes(range(10)) + bytes(2) and a.rx[0].is_rx
    assert adapter(peak, "PCAN_USBBUS1").init_calls == [] and a.n.ping()
    a.n.close()


def test_rename_is_refused_and_reconnect_works(peak):
    a = Node("PCAN_USBBUS1", "CAN1", FD_500_2M).open()
    with pytest.raises(NodeError, match="cannot be renamed"):
        a.n.set_name("ECU")
    a.n.close()
    b = Node("PCAN_USBBUS2", "CAN2", FD_500_2M).open()
    a2 = Node("PCAN_USBBUS1", "CAN1", FD_500_2M).open()
    a2.n.send_frame(CanFrame(0x55, b"\x01\x02", False, False, False))
    assert wait_for(lambda: len(b.rx) == 1)
    a2.n.close()
    b.n.close()


# ---- engine end to end -------------------------------------------------------------------------------------
def test_engine_and_node_manager_drive_two_pcan_adapters(peak, tmp_path):
    from autosim.context import AppContext
    from autosim.core.common import events
    ctx = AppContext()
    ctx.start()
    logs: list[str] = []
    ctx.events.subscribe(events.LOG, logs.append)
    try:
        assert {e["port"] for e in ctx.nodes.scan() if e["kind"] == "pcan"} == {"PCAN_USBBUS1", "PCAN_USBBUS2"}
        for port, ch in (("PCAN_USBBUS1", "CAN1"), ("PCAN_USBBUS2", "CAN2")):
            ctx.can.open_channel(ch, ctx.nodes.create_backend(port, ch), FD_500_2M)
        with pytest.raises(ValueError, match="already open"):
            ctx.nodes.create_backend("PCAN_USBBUS1", "CAN9")
        assert ctx.can.load_database("tests/data/sample.dbc")
        job = ctx.can.add_tx_message("CAN1", "EngineData", 10)
        ctx.can.set_signal(job, "Speed", 88.0)
        ctx.can.tx.start(job)
        seen: list[CanFrame] = []                                     # what the trace receives
        ctx.events.subscribe(events.CAN_RX, lambda rows: seen.extend(r.frame for r in rows))
        assert wait_for(lambda: any(f.is_rx and f.channel == "CAN2" and f.arbitration_id == 0x100
                                    for f in list(seen)))
        ctx.can.tx.stop(job)
        rx = next(f for f in list(seen) if f.is_rx and f.channel == "CAN2")
        assert ctx.can.registry.decode(rx)[1]["Speed"] == pytest.approx(88.0)
        # bus problems reach the log with the channel name
        adapter(peak, "PCAN_USBBUS2").unplug()
        assert wait_for(lambda: any(line.startswith("[CAN2]") and "unplugged" in line for line in logs))
        assert ctx.nodes.nodes["CAN2"].state == NodeState.ERROR
    finally:
        ctx.shutdown()
    assert not any(a.open for a in peak.adapters.values())


def test_python_can_message_flags_reach_the_driver(peak):
    """The frame type bits PCAN-Basic receives (extended, FD, BRS) come from the AutoSim frame."""
    a = Node("PCAN_USBBUS1", "CAN1", FD_500_2M).open()
    Node("PCAN_USBBUS2", "CAN2", FD_500_2M).open()
    a.n.send_frame(CanFrame(0x1ABCDE, bytes(32), True, True, False))
    sent = adapter(peak, "PCAN_USBBUS1").sent[-1]
    assert sent == (0x1ABCDE, bytes(32), True, False)
    assert isinstance(a.n._bus, can.BusABC)
