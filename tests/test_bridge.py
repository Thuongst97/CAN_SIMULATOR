"""Android bridge: ABP codec, BridgeNode against the Python reference bridge (MemoryCanPort plays Android/VHAL),
reconnect after a lost link, FD handling, errors, NodeManager integration, and the full path
VHAL -> bridge -> AutoSim auto response -> bridge -> VHAL."""
import socket
import threading
import time
from pathlib import Path

import pytest

from autosim.core.can_engine.auto_response import ResponseRule
from autosim.core.can_engine.engine import CanEngine
from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.core.common.frame import CanChannelConfig, CanFrame
from autosim.hardware.bridge import protocol as P
from autosim.hardware.bridge.node import BridgeBackend, BridgeNode, is_bridge_port, parse_url
from autosim.hardware.bridge.sim_bridge import BridgeServer, MemoryCanPort
from autosim.hardware.esp32_gateway.manager import NodeManager
from autosim.hardware.esp32_gateway.serial_node import NodeError, NodeState

DBC = Path(__file__).parent / "data" / "vhal_demo.dbc"


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


# ---- protocol --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("frame", [
    CanFrame(0x21, bytes([10, 0, 0, 0, 0, 0, 0, 0])),
    CanFrame(0x7FF, b""),
    CanFrame(0x18FF0027, bytes(range(8)), is_extended=True),
    CanFrame(0x123, bytes(range(64)), is_fd=True, bitrate_switch=True),
    CanFrame(0x1ABCDEF0, bytes(range(12)), is_extended=True, is_fd=True),
])
def test_frame_roundtrip(frame):
    pkts = P.PacketParser().feed(P.encode_frame(frame, 123456))
    assert len(pkts) == 1 and pkts[0][0] == P.T_FRAME
    back, _flags, ts = P.decode_frame(pkts[0][1])
    assert ts == 123456
    assert (back.arbitration_id, back.data, back.is_extended, back.is_fd, back.bitrate_switch) == \
        (frame.arbitration_id, frame.data, frame.is_extended, frame.is_fd, frame.bitrate_switch)


def test_fd_payload_is_padded_and_classic_limits_checked():
    pkt = P.encode_frame(CanFrame(1, bytes(10), is_fd=True))
    f, _, _ = P.decode_frame(P.PacketParser().feed(pkt)[0][1])
    assert len(f.data) == 12
    with pytest.raises(P.ProtocolError, match="max 8"):
        P.encode_frame(CanFrame(1, bytes(9)))
    with pytest.raises(P.ProtocolError, match="11"):
        P.encode_frame(CanFrame(0x800, b""))
    with pytest.raises(P.ProtocolError, match="29"):
        P.encode_frame(CanFrame(0x20000000, b"", is_extended=True))


def test_parser_handles_fragmentation_and_rejects_garbage():
    stream = b"".join(P.encode_frame(CanFrame(i, bytes([i]))) for i in range(50)) + P.encode_hello("vcan0", "x")
    parser = P.PacketParser()
    out = []
    for i in range(0, len(stream), 7):                       # 7-byte chunks split headers and payloads
        out += parser.feed(stream[i:i + 7])
    assert len(out) == 51 and P.decode_hello(out[-1][1])["name"] == "vcan0"
    assert [P.decode_frame(p)[0].arbitration_id for t, p in out[:-1]] == list(range(50))
    with pytest.raises(P.ProtocolError, match="magic"):
        P.PacketParser().feed(b"\x00\x02\x00\x00")
    with pytest.raises(P.ProtocolError, match="too long"):
        P.PacketParser().feed(bytes([P.MAGIC, 2, 0xFF, 0xFF]))


def test_status_error_hello_codecs():
    st = P.BridgeStatus(1, 2, 3, 4, True)
    assert P.decode_status(P.PacketParser().feed(P.encode_status(st))[0][1]) == st
    assert P.decode_error(P.PacketParser().feed(P.encode_error(2, "can0 down"))[0][1]) == (2, "can0 down")
    h = P.decode_hello(P.PacketParser().feed(P.encode_hello("vcan0", "autosim-bridge 1.0", P.CAP_FD))[0][1])
    assert h == {"version": 1, "caps": 1, "name": "vcan0", "software": "autosim-bridge 1.0"}


def test_urls():
    assert parse_url("bridge://127.0.0.1:29536") == ("127.0.0.1", 29536)
    assert parse_url("bridge://192.168.1.20") == ("192.168.1.20", P.DEFAULT_PORT)
    assert parse_url("bridge://[::1]:1234") == ("::1", 1234)
    assert is_bridge_port("BRIDGE://x") and not is_bridge_port("COM3")
    with pytest.raises(NodeError):
        parse_url("bridge://host:abc")
    with pytest.raises(NodeError):
        parse_url("bridge://:1")


# ---- node against the reference bridge -------------------------------------------------------------------
@pytest.fixture
def bridge():
    port = MemoryCanPort("vcan0")
    srv = BridgeServer(port, "127.0.0.1", 0, status_period_s=0.1, log=lambda s: None).start()
    srv.port = port
    yield srv
    srv.stop()


def url(srv):
    return f"bridge://127.0.0.1:{srv.address[1]}"


def open_node(srv, **kw):
    node = BridgeNode(url(srv), "AND1", use_adb=False, **kw)
    backend = BridgeBackend(node)
    got = []
    backend.set_rx_callback(got.append)
    backend.open(CanChannelConfig())
    node.got = got
    return node, backend


def test_node_exchanges_frames_both_ways(bridge):
    node, backend = open_node(bridge)
    try:
        assert node.state == NodeState.RUNNING
        assert node.info["iface"] == "vcan0" and node.info["caps"] & P.CAP_FD
        assert node.info["node_id"] == "android:vcan0"
        # Android (VHAL) -> AutoSim
        bridge.port.inject(CanFrame(0x21, bytes([10]) + bytes(7)))
        bridge.port.inject(CanFrame(0x123, bytes(range(64)), is_fd=True, bitrate_switch=True))
        assert wait_for(lambda: len(node.got) == 2)
        a, b = node.got
        assert a.arbitration_id == 0x21 and a.data[0] == 10 and a.channel == "AND1" and a.is_rx
        assert b.is_fd and b.bitrate_switch and b.data == bytes(range(64))
        # AutoSim -> Android
        backend.send(CanFrame(0x27, bytes([100]) + bytes(7)))
        backend.send(CanFrame(0x28, bytes(16), is_fd=True, bitrate_switch=True))
        assert bridge.port.wait_written(2)
        w = bridge.port.written
        assert w[0].arbitration_id == 0x27 and w[0].data[0] == 100 and not w[0].is_fd
        assert w[1].is_fd and w[1].bitrate_switch and len(w[1].data) == 16
        assert node.status.rx_count == 2 and node.status.tx_count == 2
        assert node.ping()
        assert wait_for(lambda: node.bridge_status.tx == 2 and node.bridge_status.rx == 2)
    finally:
        backend.close()
    assert node.state == NodeState.DISCONNECTED
    assert wait_for(lambda: bridge.clients == 0)


def test_burst_keeps_order_and_nothing_is_lost(bridge):
    node, backend = open_node(bridge)
    try:
        for i in range(2000):
            bridge.port.inject(CanFrame(i & 0x7FF, i.to_bytes(4, "little"), is_fd=bool(i & 1)))
        assert wait_for(lambda: len(node.got) == 2000, 5)
        assert [int.from_bytes(f.data[:4], "little") for f in node.got] == list(range(2000))
        for i in range(2000):
            backend.send(CanFrame(0x100, i.to_bytes(2, "little")))
        assert bridge.port.wait_written(2000, 5)
        assert [int.from_bytes(f.data, "little") for f in bridge.port.written] == list(range(2000))
    finally:
        backend.close()


def test_fd_frame_on_classic_interface_reports_error():
    port = MemoryCanPort("can0", fd_capable=False)
    srv = BridgeServer(port, "127.0.0.1", 0, log=lambda s: None).start()
    errors = []
    try:
        node = BridgeNode(f"bridge://127.0.0.1:{srv.address[1]}", "AND1", use_adb=False)
        node.on_error = lambda code, text: errors.append(text)
        backend = BridgeBackend(node)
        backend.open(CanChannelConfig(fd_enabled=True))
        assert any("not CAN FD capable" in e for e in errors)          # warned at configure
        backend.send(CanFrame(0x28, bytes(16), is_fd=True))
        assert wait_for(lambda: any("classic CAN interface" in e for e in errors))
        backend.send(CanFrame(0x27, bytes(8)))                            # classic still works
        assert port.wait_written(1)
        backend.close()
    finally:
        srv.stop()


def test_connect_errors_are_clear():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    node = BridgeNode(f"bridge://127.0.0.1:{free}", "AND1", use_adb=False, connect_timeout=0.5)
    with pytest.raises(NodeError, match="autosim-bridge"):
        node.connect()
    assert node.state == NodeState.DISCONNECTED
    # something listening that is not the bridge: no HELLO
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        node = BridgeNode(f"bridge://127.0.0.1:{srv.getsockname()[1]}", "AND1", use_adb=False, hello_timeout=0.3)
        with pytest.raises(NodeError, match="no HELLO"):
            node.connect()
    finally:
        srv.close()


def test_send_before_start_and_after_close_raises(bridge):
    node = BridgeNode(url(bridge), "AND1", use_adb=False)
    node.connect()
    with pytest.raises(NodeError, match="not started"):
        node.send_frame(CanFrame(1, b""))
    node.close()
    with pytest.raises(NodeError):
        node.send_frame(CanFrame(1, b""))


def test_reconnects_after_the_bridge_drops_the_link(bridge):
    node, backend = open_node(bridge, reconnect_s=0.1)
    states, errors = [], []
    node.on_state = states.append
    node.on_error = lambda c, t: errors.append(t)
    try:
        bridge.kick_all()                                                  # bridge restart / adb glitch
        assert wait_for(lambda: NodeState.ERROR in states)
        assert wait_for(lambda: node.state == NodeState.RUNNING and node.reconnects == 1)
        assert any("link to the bridge lost" in e for e in errors) and any("reconnected" in e for e in errors)
        bridge.port.inject(CanFrame(0x21, b"\x14"))                       # traffic flows again
        assert wait_for(lambda: any(f.data == b"\x14" for f in node.got))
        backend.send(CanFrame(0x27, b"\x64"))
        assert bridge.port.wait_written(1)
    finally:
        backend.close()


def test_reconnects_after_the_bridge_process_restarts():
    port = MemoryCanPort("vcan0")
    srv = BridgeServer(port, "127.0.0.1", 0, log=lambda s: None).start()
    addr = srv.address
    node = BridgeNode(f"bridge://127.0.0.1:{addr[1]}", "AND1", use_adb=False, reconnect_s=0.1)
    backend = BridgeBackend(node)
    backend.open(CanChannelConfig())
    try:
        srv.stop()
        assert wait_for(lambda: node.state == NodeState.ERROR)
        with pytest.raises(NodeError, match="reconnecting"):
            backend.send(CanFrame(1, b""))
        time.sleep(0.3)                                                    # bridge down for a while
        port2 = MemoryCanPort("vcan0")
        srv = BridgeServer(port2, "127.0.0.1", addr[1], log=lambda s: None).start()
        assert wait_for(lambda: node.state == NodeState.RUNNING, 5)
        backend.send(CanFrame(0x27, b"\x0a"))
        assert port2.wait_written(1)
    finally:
        backend.close()
        srv.stop()


def test_half_open_link_is_detected_by_keepalive():
    """The bridge stops answering without closing TCP (VM paused, cable pulled): the node notices through
    the keep-alive and reconnects."""
    port = MemoryCanPort("vcan0")
    srv = BridgeServer(port, "127.0.0.1", 0, status_period_s=60, log=lambda s: None).start()
    node = BridgeNode(f"bridge://127.0.0.1:{srv.address[1]}", "AND1", use_adb=False, status_period_s=0.05,
                      link_timeout_s=0.3, reconnect_s=0.05)
    backend = BridgeBackend(node)
    backend.open(CanChannelConfig())
    try:
        orig = srv._handle
        srv._handle = lambda c, t, p: None if t == P.T_PING else orig(c, t, p)   # PONGs stop
        assert wait_for(lambda: node.state == NodeState.ERROR, 3)
        srv._handle = orig
        assert wait_for(lambda: node.state == NodeState.RUNNING, 3)
    finally:
        backend.close()
        srv.stop()


def test_two_autosim_clients_share_one_bridge(bridge):
    n1, b1 = open_node(bridge)
    n2, b2 = open_node(bridge)
    try:
        assert wait_for(lambda: bridge.clients == 2)
        bridge.port.inject(CanFrame(0x21, b"\x0a"))
        assert wait_for(lambda: len(n1.got) == 1 and len(n2.got) == 1)
        b1.send(CanFrame(1, b"\x01"))
        b2.send(CanFrame(2, b"\x02"))
        assert bridge.port.wait_written(2)
    finally:
        b1.close()
        b2.close()


def test_garbage_from_the_bridge_triggers_reconnect():
    """A peer that sends a bad packet: the node counts a protocol error and reconnects instead of crashing."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(2)
    conns = []

    def serve():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            conns.append(c)
            c.sendall(P.encode_hello("vcan0", "fake"))
            if len(conns) == 1:
                time.sleep(0.1)
                c.sendall(b"\x00garbage")
    threading.Thread(target=serve, daemon=True).start()
    node = BridgeNode(f"bridge://127.0.0.1:{srv.getsockname()[1]}", "AND1", use_adb=False, reconnect_s=0.05)
    backend = BridgeBackend(node)
    try:
        backend.open(CanChannelConfig())
        assert wait_for(lambda: node.crc_errors == 1 and node.reconnects == 1)
        assert node.state == NodeState.RUNNING
    finally:
        backend.close()
        srv.close()
        for c in conns:
            c.close()


# ---- NodeManager + engine ----------------------------------------------------------------------------------
def test_node_manager_creates_bridge_channels(bridge):
    eb = EventBus()
    states = []
    eb.subscribe(events.NODE_STATE, states.append)
    nm = NodeManager(eb)
    assert nm.kind_of(url(bridge)) == ("bridge", "")
    backend = nm.create_backend(url(bridge), "AND1")
    assert isinstance(backend, BridgeBackend)
    with pytest.raises(ValueError, match="already open"):
        nm.create_backend(url(bridge), "AND2")                         # one channel per bridge address
    backend.open(CanChannelConfig())
    try:
        assert wait_for(lambda: any(s["state"] == "running" and s["channel"] == "AND1" for s in states))
        assert states[-1]["info"]["node_id"] == "android:vcan0"
        with pytest.raises(NodeError):
            nm.rename("AND1", "x")
    finally:
        nm.release("AND1")
    assert "AND1" not in nm.nodes


def test_vhal_to_autosim_auto_response_full_path(bridge):
    """The user's scenario end to end: Android writes 0x21 = 10 / 20 on vcan0, AutoSim (rules) answers 0x27 =
    10 / 100, and the answers arrive on vcan0 where the VHAL reads them. Measures the round trip."""
    eb = EventBus()
    eng = CanEngine(eb)
    eng.load_database(DBC)
    eng.start()
    nm = NodeManager(eb)
    try:
        eng.open_channel("AND1", nm.create_backend(url(bridge), "AND1"), CanChannelConfig())
        eng.responder.set_rules([
            ResponseRule(rx_id=0x21, match="raw", op="==", value="10", tx_id=0x27, tx_value="10"),
            ResponseRule(rx_id=0x21, match="raw", op="==", value="20", tx_id=0x27, tx_value="100"),
        ])
        eng.responder.enabled = True
        rtts = []
        for i, v in enumerate((10, 20, 10, 20, 30, 10)):
            n = len(bridge.port.written)
            t0 = time.perf_counter()
            bridge.port.inject(CanFrame(0x21, bytes([v]) + bytes(7)))
            if v == 30:
                time.sleep(0.1)
                assert len(bridge.port.written) == n                    # no rule for 30: no answer
                continue
            assert bridge.port.wait_written(n + 1)
            rtts.append(time.perf_counter() - t0)
            f = bridge.port.written[-1]
            assert f.arbitration_id == 0x27 and f.data[0] == (10 if v == 10 else 100) and len(f.data) == 8
        assert max(rtts) < 0.1, rtts                                       # localhost round trip
    finally:
        eng.shutdown()
        nm.release("AND1")
