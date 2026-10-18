"""slcan / CANable 2.0 support: codec, stream splitting, configuration, and two adapters on one (fake) bus
driven through the real SlcanNode + CanEngine code."""
import pytest

from autosim.core.can_engine.engine import CanEngine
from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.core.common.frame import CanChannelConfig, CanFrame
from autosim.hardware.esp32_gateway.manager import NodeManager
from autosim.hardware.esp32_gateway.serial_node import NodeError, NodeState
from autosim.hardware.slcan import device_kind
from autosim.hardware.slcan import protocol as S
from autosim.hardware.slcan.node import SlcanNode, wait_for
from tests.fake_canable import FakeBus


# ------------------------------------------------------------------------------ codec
@pytest.mark.parametrize("frame, line", [
    (CanFrame(0x123, b"\x11\x22\x33"), "t1233112233"),
    (CanFrame(0x1ABCDEF, b"", is_extended=True), "T01ABCDEF0"),
    (CanFrame(0x7FF, bytes(range(8))), "t7FF80001020304050607"),
    (CanFrame(0x100, bytes([0xAA]) * 12, is_fd=True), "d1009" + "AA" * 12),
    (CanFrame(0x100, bytes([1]) * 64, is_fd=True, bitrate_switch=True), "b100F" + "01" * 64),
    (CanFrame(0x18FF00F1, b"\x01", is_extended=True, is_fd=True, bitrate_switch=True), "B18FF00F1101"),
])
def test_encode_matches_firmware_format(frame, line):
    assert S.encode_frame(frame) == (line + "\r").encode()


def test_encode_pads_fd_and_rejects_impossible_frames():
    assert S.encode_frame(CanFrame(0x1, bytes(10), is_fd=True)) == ("d0019" + "00" * 12 + "\r").encode()
    assert S.encode_frame(CanFrame(0x1, bytes(33), is_fd=True)).startswith(b"d001E")      # 33 -> 48 bytes
    for bad in (CanFrame(0x1, bytes(9)), CanFrame(0x800, b""), CanFrame(0x1, bytes(65), is_fd=True),
                CanFrame(0x20000000, b"", is_extended=True)):
        with pytest.raises(S.SlcanError):
            S.encode_frame(bad)


def test_decode_all_frame_types_and_strict_lengths():
    f = S.decode_frame("t1233112233")
    assert (f.arbitration_id, f.data, f.is_fd, f.is_extended) == (0x123, b"\x11\x22\x33", False, False)
    f = S.decode_frame("B18FF00F1F" + "AB" * 64)
    assert (f.arbitration_id, len(f.data), f.is_fd, f.bitrate_switch, f.is_extended) == (0x18FF00F1, 64, True, True, True)
    f = S.decode_frame("d1009" + "00" * 12)
    assert f.is_fd and not f.bitrate_switch and len(f.data) == 12
    f = S.decode_frame("r1234")                                    # remote frame: DLC but no data
    assert f.arbitration_id == 0x123 and f.data == b""
    for bad in ("", "x123", "t12", "t1233AABB", "t123311223344", "t1239" + "00" * 9, "d100F00",
                "16e7497-dirty github.com/normaldotcom/canable2.git", "tZZZ0"):
        assert S.decode_frame(bad) is None, bad


def test_line_splitter_handles_error_register_without_terminator():
    sp = S.LineSplitter()
    out = sp.feed(b"t1231AA\rCANable Error Register: 1")             # reply still open ...
    assert out == [("frame", S.decode_frame("t1231AA"))]
    assert sp.feed(b"", idle=True) == [("error_reg", 1)]            # ... complete once the stream is quiet
    # glued to the next frame, and frames can start with hex letters (b/d/B/D): the length decides
    out = sp.feed(b"CANable Error Register: 4b1231AA\r")
    assert out == [("error_reg", 4), ("frame", S.decode_frame("b1231AA"))]
    out = sp.feed(b"CANable Error Register: 1Cd1231AA\r")
    assert out == [("error_reg", 0x1C), ("frame", S.decode_frame("d1231AA"))]
    out = sp.feed(b"16e7497-dirty github.com/normaldotcom/canable2.git\r\x07\r")
    assert out == [("text", "16e7497-dirty github.com/normaldotcom/canable2.git")]


def test_config_commands_follow_the_firmware_tables():
    assert S.config_commands(CanChannelConfig(500_000, 2_000_000, True, True)) == ["S6", "Y2", "M0", "A1"]
    assert S.config_commands(CanChannelConfig(1_000_000, 5_000_000, True)) == ["S8", "Y5", "M0", "A1"]
    assert S.config_commands(CanChannelConfig(250_000, 4_000_000, fd_enabled=False)) == ["S5", "M0", "A1"]
    assert S.config_commands(CanChannelConfig(125_000, 2_000_000, listen_only=True))[-2] == "M1"
    for cfg, why in ((CanChannelConfig(500_000, 4_000_000), "2 Mbit/s and 5 Mbit/s"),
                     (CanChannelConfig(400_000, 2_000_000), "not supported"),
                     (CanChannelConfig(500_000, 2_000_000, iso_mode=False), "ISO")):
        with pytest.raises(S.SlcanError, match=why):
            S.config_commands(cfg)


def test_usb_ids_select_the_backend():
    assert device_kind(0x16D0, 0x117E) == "slcan"
    assert device_kind(0x1A86, 0x55D3) == "autosim" and device_kind(None, None) == "autosim"


# ------------------------------------------------------------------------------ node on a fake bus
def make_node(bus, name="CAN1", **kw):
    host, dev = bus.adapter()
    return SlcanNode(f"FAKE-{name}", name, serial_number=f"SN-{name}", transport_factory=lambda: host,
                     status_period_s=0.1, **kw), dev


def test_two_adapters_exchange_classic_and_fd_frames():
    bus = FakeBus()
    (a, _), (b, _) = make_node(bus, "A"), make_node(bus, "B")
    rx_a, rx_b = [], []
    a.on_rx, b.on_rx = rx_a.append, rx_b.append
    for n in (a, b):
        assert n.connect()["fw"].endswith("canable2.git")
        n.configure(CanChannelConfig(500_000, 2_000_000, True, True))
        n.start()
    frames = [CanFrame(0x123, b"\x01\x02"), CanFrame(0x1ABCDE, bytes(range(8)), is_extended=True),
              CanFrame(0x200, bytes(range(20)), is_fd=True), CanFrame(0x201, bytes(range(64)), is_fd=True,
                                                                   bitrate_switch=True)]
    for f in frames:
        a.send_frame(f)
    assert wait_for(lambda: len(rx_b) == 4)
    got = [(f.arbitration_id, f.data, f.is_fd, f.bitrate_switch, f.is_extended) for f in rx_b]
    assert got == [(0x123, b"\x01\x02", False, False, False), (0x1ABCDE, bytes(range(8)), False, False, True),
                   (0x200, bytes(range(20)), True, False, False),                 # 20 is a valid FD length
                   (0x201, bytes(range(64)), True, True, False)]
    b.send_frame(CanFrame(0x7FF, b"\xFF"))
    assert wait_for(lambda: len(rx_a) == 1) and rx_a[0].arbitration_id == 0x7FF
    assert rx_b[0].channel == "B" and rx_b[0].timestamp > 0 and a.status.tx_count == 4
    a.close(), b.close()


def test_lone_adapter_reports_unacknowledged_frames():
    bus = FakeBus()
    a, dev = make_node(bus, "A")
    errors = []
    a.on_error = lambda code, text: errors.append((code, text))
    a.connect()
    a.configure(CanChannelConfig(500_000, 2_000_000))
    a.start()
    for i in range(70):
        a.send_frame(CanFrame(0x100 + i, b"\x01"))
    assert wait_for(lambda: errors, 3)
    assert errors[0][0] == S.ERR_FULLBUF_CANTX and "not being acknowledged" in errors[0][1]
    assert a.tx_nacks >= 1 and a.status.bus_flags & 1
    a.close()


def test_mismatched_bit_rates_do_not_talk():
    bus = FakeBus()
    (a, _), (b, _) = make_node(bus, "A"), make_node(bus, "B")
    rx_b = []
    b.on_rx = rx_b.append
    for n, nominal in ((a, 500_000), (b, 250_000)):
        n.connect()
        n.configure(CanChannelConfig(nominal, 2_000_000))
        n.start()
    a.send_frame(CanFrame(0x1, b"\x01"))
    assert not wait_for(lambda: rx_b, 0.3)
    a.close(), b.close()


def test_configuration_errors_and_name_are_reported():
    bus = FakeBus()
    a, _ = make_node(bus, "A")
    a.connect()
    with pytest.raises(NodeError, match="2 Mbit/s and 5 Mbit/s"):
        a.configure(CanChannelConfig(500_000, 4_000_000))
    with pytest.raises(NodeError, match="cannot store a name"):
        a.set_name("X")
    with pytest.raises(NodeError, match="not started"):
        a.send_frame(CanFrame(0x1, b""))
    a.close()


def test_software_loopback_echoes_without_touching_the_bus():
    bus = FakeBus()
    a, dev = make_node(bus, "A")
    rx = []
    a.on_rx = rx.append
    a.connect()
    a.configure(CanChannelConfig(500_000, 2_000_000, extras={"loopback": True}))
    a.start()
    a.send_frame(CanFrame(0x55, bytes(10), is_fd=True))
    assert len(rx) == 1 and len(rx[0].data) == 12 and dev.sent == [] and not dev.open
    a.close()


def test_not_a_canable_is_rejected():
    from autosim.hardware.esp32_gateway.transport import make_pipe
    host, _silent = make_pipe()
    n = SlcanNode("COMX", "CAN1", transport_factory=lambda: host)
    with pytest.raises(NodeError, match="no slcan answer"):
        n.connect()
    assert n.state == NodeState.DISCONNECTED


def test_engine_and_node_manager_drive_two_canables_end_to_end():
    """Full app path: NodeManager picks the slcan backend, CanEngine sends, the trace sees both directions."""
    bus = FakeBus()
    eb = EventBus()
    eng = CanEngine(eb)
    mgr = NodeManager(eb)
    rows = []
    eb.subscribe(events.CAN_RX, rows.extend)
    eng.start()
    try:
        for ch in ("CAN1", "CAN2"):
            host, _ = bus.adapter()
            backend = mgr.create_backend(f"FAKE-{ch}", ch, transport_factory=lambda h=host: h, kind="slcan")
            eng.open_channel(ch, backend, CanChannelConfig(500_000, 5_000_000, True, True))
        assert type(mgr.nodes["CAN1"]).__name__ == "SlcanNode"
        eng.bus.send("CAN1", CanFrame(0x321, bytes(range(48)), is_fd=True, bitrate_switch=True))
        assert wait_for(lambda: any(r.frame.is_rx and r.frame.channel == "CAN2" for r in rows))
        rx = next(r.frame for r in rows if r.frame.is_rx)
        assert (rx.arbitration_id, len(rx.data), rx.bitrate_switch) == (0x321, 48, True)
        assert any(not r.frame.is_rx and r.frame.channel == "CAN1" for r in rows)
        for ch in ("CAN1", "CAN2"):
            eng.close_channel(ch)
            mgr.release(ch)
    finally:
        eng.shutdown()


def test_non_standard_fd_length_is_padded_on_the_wire():
    bus = FakeBus()
    (a, _), (b, _) = make_node(bus, "A"), make_node(bus, "B")
    rx = []
    b.on_rx = rx.append
    for n in (a, b):
        n.connect()
        n.configure(CanChannelConfig(500_000, 2_000_000))
        n.start()
    a.send_frame(CanFrame(0x10, bytes([7]) * 21, is_fd=True))            # 21 -> 24 bytes
    assert wait_for(lambda: rx) and rx[0].data == bytes([7]) * 21 + bytes(3)
    a.close(), b.close()


def test_flags_latched_before_connect_are_a_baseline_not_new_errors():
    bus = FakeBus()
    a, dev = make_node(bus, "A")
    dev.err = 1 << S.ERR_FULLBUF_CANTX                      # left over from an earlier session
    errors = []
    a.on_error = lambda code, text: errors.append((code, text))
    a.connect()
    assert a.info["latched_errors"] == dev.err
    a.configure(CanChannelConfig(500_000, 2_000_000))
    a.start()
    assert errors and errors[0][0] == -1 and "already latched" in errors[0][1]
    dev.err |= 1 << S.ERR_RX_OVERFLOW                       # a genuinely new problem
    assert wait_for(lambda: any(c == S.ERR_RX_OVERFLOW for c, _ in errors))
    assert not any(c == S.ERR_FULLBUF_CANTX for c, _ in errors)
    a.close()
