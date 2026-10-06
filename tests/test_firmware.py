"""The app's SerialNode talking to the real firmware protocol + node logic (compiled with g++).

fw_sim = firmware/esp32_can_node/lib/autosim_proto behind a loopback fake CAN controller, so this is a
byte-level compatibility test of the Python host against the C++ firmware code.
"""
import time

import pytest

from autosim.core.can_engine.engine import CanEngine
from autosim.core.common.events import EventBus
from autosim.core.common.frame import CanChannelConfig, CanFrame
from autosim.hardware.esp32_gateway import protocol as P
from autosim.hardware.esp32_gateway.manager import NodeManager
from autosim.hardware.esp32_gateway.protocol import MsgType, Packet
from autosim.hardware.esp32_gateway.serial_node import NodeError, NodeState, SerialNode
from tests import fw_sim_helper as H

pytestmark = pytest.mark.skipif(not H.have_compiler(), reason="needs g++ to build the firmware simulator")


def wait_for(cond, t=3.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(scope="module")
def exe(tmp_path_factory):
    return H.build(tmp_path_factory.mktemp("fw"))


@pytest.fixture
def make(exe):
    created = []

    def factory(env=None, channel="ch"):
        n = SerialNode("fw", channel, transport_factory=lambda: H.ProcessTransport(exe, env), ping_s=0.2)
        created.append(n)
        return n

    yield factory
    for n in created:
        n.close()


def running(make, env=None, cfg=None):
    node = make(env)
    got = []
    node.on_rx = got.append
    node.connect()
    node.configure(cfg or CanChannelConfig(500_000, 2_000_000, True, True))
    node.start()
    return node, got


def raw_tx(node, flags, can_id, data=b"", length=None):
    """Send a TX_FRAME with arbitrary flags (the Python CanFrame cannot express RTR etc.)."""
    import struct
    n = len(data) if length is None else length
    payload = struct.pack("<IBBQ", can_id, flags, n, 0) + data
    node._write(Packet(MsgType.TX_FRAME, node._next_seq(), payload))


# ---------------------------------------------------------------- handshake / identity
def test_handshake_reports_firmware_identity(make):
    node = make({"FW_SIM_NAME": "Left-Door"})
    info = node.connect()
    assert info == {"proto": 1, "fw": "1.0", "caps": 3, "node_id": "Left-Door"}


def test_set_name_and_validation(make):
    node = make()
    node.connect()
    node.set_name("Rear-ECU_2")
    resp = node.request(MsgType.HELLO, bytes([1]))
    assert P.decode_hello_ack(resp.payload)["node_id"] == "Rear-ECU_2"
    for bad in (b"bad name!", b"", b"x" * 16, b"\xc3\xa9"):
        with pytest.raises(NodeError, match="bad configuration"):
            node.request(MsgType.SET_NAME, bad)
    with pytest.raises(NodeError, match="1-15"):
        node.set_name("way-too-long-name!")            # the host refuses before sending


def test_set_name_storage_failure_is_reported(make):
    node = make({"FW_SIM_FAIL_NVS": "1"})
    node.connect()
    with pytest.raises(NodeError, match="could not store"):
        node.set_name("abc")


# ---------------------------------------------------------------- configuration
def test_config_validation_matches_firmware_rules(make):
    node = make()
    node.connect()
    node.configure(CanChannelConfig(500_000, 5_000_000))              # factor 10: OK
    node.configure(CanChannelConfig(1_000_000, 8_000_000))            # factor 8: OK
    node.configure(CanChannelConfig(250_000, 2_000_000, fd_enabled=False))   # classic: data rate ignored
    # the host refuses an impossible ratio before it reaches the node ...
    with pytest.raises(NodeError, match="ratio"):
        node.configure(CanChannelConfig(500_000, 8_000_000))
    # ... and the firmware independently rejects it with the reason as text
    for cfg, why in ((CanChannelConfig(500_000, 8_000_000), "ratio"),
                     (CanChannelConfig(500_000, 2_700_000), "multiple"),
                     (CanChannelConfig(500_000, 250_000), ">= nominal"),
                     (CanChannelConfig(2_000_000, 2_000_000), "nominal bit rate")):
        with pytest.raises(NodeError, match=why):
            node.request(MsgType.CONFIG, P.encode_config(cfg))


def test_config_while_running_is_busy_and_stop_allows_it(make):
    node, _ = running(make)
    with pytest.raises(NodeError, match="busy"):
        node.configure(CanChannelConfig(250_000, 1_000_000))
    node.stop()
    node.configure(CanChannelConfig(250_000, 1_000_000))
    node.start()
    assert node.state == NodeState.RUNNING


def test_start_without_config_uses_defaults(make):
    node = make()
    got = []
    node.on_rx = got.append
    node.connect()
    node.start()
    node.send_frame(CanFrame(0x100, bytes(range(24)), is_fd=True, bitrate_switch=True))
    assert wait_for(lambda: got) and got[0].data == bytes(range(24))


def test_mcp_start_failure_is_reported_with_reason(make):
    node = make({"FW_SIM_FAIL_START": "1"})
    node.connect()
    node.configure(CanChannelConfig())
    with pytest.raises(NodeError, match="MCP2518FD fault.*SPI read-back"):
        node.start()


# ---------------------------------------------------------------- CAN traffic
def test_fd_frame_roundtrip_all_flags(make):
    node, got = running(make)
    node.send_frame(CanFrame(0x1ABCDE, bytes(range(64)), is_extended=True, is_fd=True, bitrate_switch=True))
    node.send_frame(CanFrame(0x123, b"\x01\x02\x03", is_fd=False))
    assert wait_for(lambda: len(got) == 2)
    a, b = got
    assert (a.arbitration_id, a.is_extended, a.is_fd, a.bitrate_switch, a.data) == \
        (0x1ABCDE, True, True, True, bytes(range(64)))
    assert (b.arbitration_id, b.is_extended, b.is_fd, b.data) == (0x123, False, False, b"\x01\x02\x03")
    assert a.channel == "ch" and 0 < a.timestamp


def test_fd_frames_are_padded_to_a_valid_dlc(make):
    node, got = running(make)
    for n in (9, 10, 13, 17, 21, 25, 33, 49):
        node.send_frame(CanFrame(0x200 + n, bytes([0xA5]) * n, is_fd=True))
    assert wait_for(lambda: len(got) == 8)
    assert [len(f.data) for f in got] == [12, 12, 16, 20, 24, 32, 48, 64]
    assert all(f.data[:n] == bytes([0xA5]) * n and not any(f.data[n:])
               for f, n in zip(got, (9, 10, 13, 17, 21, 25, 33, 49)))


def test_invalid_frames_are_nacked_not_sent(make):
    node, got = running(make)
    node.send_frame(CanFrame(0x100, bytes(9), is_fd=False))            # classic CAN carries <= 8 bytes
    assert wait_for(lambda: node.tx_nacks == 1) and node.last_nack == (0x10, P.ERR_BAD_LEN)
    node.send_frame(CanFrame(0x800, b"\x01"))                          # 11-bit id too large
    assert wait_for(lambda: node.tx_nacks == 2) and node.last_nack == (0x10, P.ERR_BAD_LEN)
    node.send_frame(CanFrame(0x100, bytes(65), is_fd=True))            # > 64
    assert wait_for(lambda: node.tx_nacks == 3)
    time.sleep(0.1)
    assert got == []                                                   # nothing reached the bus


def test_tx_requires_start_and_works_again_after_stop_start(make):
    node = make()
    got = []
    node.on_rx = got.append
    node.connect()
    node.configure(CanChannelConfig())
    node.send_frame(CanFrame(0x100, b"\x01"))                          # before START
    assert wait_for(lambda: node.tx_nacks == 1) and node.last_nack == (0x10, P.ERR_NOT_STARTED)
    node.start()
    node.send_frame(CanFrame(0x100, b"\x01"))
    assert wait_for(lambda: len(got) == 1)
    node.stop()
    node.send_frame(CanFrame(0x100, b"\x02"))
    assert wait_for(lambda: node.tx_nacks == 2) and node.last_nack == (0x10, P.ERR_NOT_STARTED)
    node.start()
    node.send_frame(CanFrame(0x100, b"\x03"))
    assert wait_for(lambda: len(got) == 2) and got[1].data == b"\x03"


def test_classic_mode_rejects_fd_frames(make):
    node, got = running(make, cfg=CanChannelConfig(500_000, 2_000_000, fd_enabled=False))
    node.send_frame(CanFrame(0x100, b"\x01", is_fd=True))
    assert wait_for(lambda: node.tx_nacks == 1) and node.last_nack == (0x10, P.ERR_UNSUPPORTED)
    node.send_frame(CanFrame(0x100, b"\x01", is_fd=False))
    assert wait_for(lambda: len(got) == 1)


def test_remote_frame_and_batch(make):
    node, got = running(make)
    raw_tx(node, 0x10, 0x321, b"", length=4)                           # RTR, requested DLC 4, no data bytes
    assert wait_for(lambda: len(got) == 1) and got[0].arbitration_id == 0x321 and got[0].data == b""
    import struct

    def one(i, data):
        body = struct.pack("<IBBQ", i, 0, len(data), 0) + data
        return bytes([len(body)]) + body
    node._write(Packet(MsgType.TX_BATCH, node._next_seq(), bytes([3]) + one(1, b"\xaa") + one(2, b"\xbb\xcc") + one(3, b"")))
    assert wait_for(lambda: len(got) == 4)
    assert [(f.arbitration_id, f.data) for f in got[1:]] == [(1, b"\xaa"), (2, b"\xbb\xcc"), (3, b"")]


def test_full_tx_queue_is_reported_as_busy(make):
    node, got = running(make, env={"FW_SIM_TX_LIMIT": "3"})
    for i in range(6):
        node.send_frame(CanFrame(0x100 + i, b"\x01"))
    assert wait_for(lambda: len(got) == 3 and node.tx_nacks == 3)
    assert node.last_nack == (0x10, P.ERR_BUSY)


def test_bad_bytes_on_the_wire_are_reported_and_survived(make):
    node, got = running(make)
    errors = []
    node.on_error = lambda code, text: errors.append((code, text))
    node._t.write(b"\x05garbage\x00")                                  # bad COBS / framing
    bad_crc = bytearray(Packet(MsgType.PING, 7, b"\x01\x00\x00\x00").encode())
    bad_crc[2] ^= 0xFF                                                 # corrupt a byte, keep the delimiter
    node._t.write(bytes(bad_crc))
    assert wait_for(lambda: len(errors) >= 2)
    assert {c for c, _ in errors} <= {P.ERR_BAD_CRC, P.ERR_BAD_LEN}
    node.send_frame(CanFrame(0x1, b"\x09"))                            # still fully functional
    assert wait_for(lambda: len(got) == 1)


def test_unknown_message_type_is_nacked_as_unsupported(make):
    node = make()
    node.connect()
    with pytest.raises(NodeError, match="unsupported"):
        node.request(MsgType.TX_CONFIRM, b"")


def test_heartbeat_keeps_the_session_alive(make):
    node, _ = running(make)
    time.sleep(1.0)                                                    # several PING/PONG rounds
    assert node.state == NodeState.RUNNING


# ---------------------------------------------------------------- multiple ESP32 nodes
def test_multiple_nodes_with_distinct_names_and_independent_channels(exe):
    eb = EventBus()
    eng = CanEngine(eb)
    mgr = NodeManager(eb)
    eng.start()
    try:
        for ch, name in (("CAN1", "NODE-A"), ("CAN2", "NODE-B"), ("CAN3", "NODE-C")):
            backend = mgr.create_backend(f"fw-{ch}", ch, lambda n=name: H.ProcessTransport(exe, {"FW_SIM_NAME": n}))
            eng.open_channel(ch, backend, CanChannelConfig(500_000, 2_000_000))
        assert {c: n.info["node_id"] for c, n in mgr.nodes.items()} == {"CAN1": "NODE-A", "CAN2": "NODE-B", "CAN3": "NODE-C"}
        assert mgr.duplicate_ids() == {}
        seen = []
        eng.rx.eventbus.subscribe("can.rx", lambda rows: seen.extend(rows))
        for i, ch in enumerate(("CAN1", "CAN2", "CAN3")):
            eng.bus.send(ch, CanFrame(0x100 + i, bytes([i]) * (i + 1), is_fd=True))
        assert wait_for(lambda: sum(1 for r in seen if r.frame.is_rx) == 3)
        rx = {r.frame.channel: r.frame for r in seen if r.frame.is_rx}
        assert {c: (f.arbitration_id, f.data) for c, f in rx.items()} == \
            {"CAN1": (0x100, b"\x00"), "CAN2": (0x101, b"\x01\x01"), "CAN3": (0x102, b"\x02\x02\x02")}

        mgr.rename("CAN2", "Gateway-2")
        assert mgr.nodes["CAN2"].info["node_id"] == "Gateway-2"
        mgr.rename("CAN3", "NODE-A")                                   # collides with CAN1
        assert mgr.duplicate_ids() == {"NODE-A": ["CAN1", "CAN3"]}
        for ch in ("CAN1", "CAN2", "CAN3"):
            eng.close_channel(ch)
            mgr.release(ch)
    finally:
        eng.shutdown()
