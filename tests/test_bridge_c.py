"""The C daemon (bridge/autosim-bridge) against AutoSim's BridgeNode.

Built here as `autosim-bridge-fake`: identical code except that the "CAN interface" is a unix SOCK_SEQPACKET
socket carrying struct can_frame / struct canfd_frame records exactly like a raw CAN socket. The test plays the
Android/VHAL side on that socket. Skipped where no C compiler is available (e.g. Windows)."""
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from autosim.core.can_engine.auto_response import ResponseRule
from autosim.core.can_engine.engine import CanEngine
from autosim.core.common.events import EventBus
from autosim.core.common.frame import CanChannelConfig, CanFrame
from autosim.hardware.bridge import protocol as P
from autosim.hardware.bridge.node import BridgeBackend, BridgeNode
from autosim.hardware.esp32_gateway.manager import NodeManager
from autosim.hardware.esp32_gateway.serial_node import NodeState

SRC = Path(__file__).resolve().parents[1] / "bridge" / "autosim-bridge"
DBC = Path(__file__).parent / "data" / "vhal_demo.dbc"
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")

pytestmark = pytest.mark.skipif(sys.platform != "linux" or CC is None, reason="needs Linux and a C compiler")

CAN_EFF_FLAG, CAN_RTR_FLAG = 0x80000000, 0x40000000
CANFD_BRS, CANFD_ESI, CANFD_FDF = 0x01, 0x02, 0x04


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp("bridge") / "autosim-bridge-fake"
    extra = os.environ.get("ABRIDGE_TEST_CFLAGS", "").split()      # e.g. -fsanitize=address,undefined
    r = subprocess.run([CC, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-DABRIDGE_FAKE_CAN", *extra,
                        "-o", str(out), str(SRC / "autosim_bridge.c")], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    return out


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeCan:
    """Android side of the fake interface: a SOCK_SEQPACKET server the bridge connects to."""

    def __init__(self, path: Path):
        self.path = path
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.srv.bind(str(path))
        self.srv.listen(1)
        self.conn = None

    def accept(self, timeout=3.0):
        self.srv.settimeout(timeout)
        self.conn, _ = self.srv.accept()
        self.conn.settimeout(2.0)

    # VHAL writes a frame
    def write(self, can_id, data, ext=False, fd=False, brs=False, rtr=False):
        cid = can_id | (CAN_EFF_FLAG if ext else 0) | (CAN_RTR_FLAG if rtr else 0)
        if fd:
            rec = struct.pack("<IBBBB", cid, len(data), CANFD_FDF | (CANFD_BRS if brs else 0), 0, 0) \
                + bytes(data).ljust(64, b"\0")
        else:
            rec = struct.pack("<IBBBB", cid, len(data), 0, 0, 0) + bytes(data).ljust(8, b"\0")
        self.conn.send(rec)

    # VHAL reads a frame
    def read(self):
        rec = self.conn.recv(128)
        cid, n, flags = struct.unpack_from("<IBB", rec)
        return {"id": cid & (0x1FFFFFFF if cid & CAN_EFF_FLAG else 0x7FF), "ext": bool(cid & CAN_EFF_FLAG),
                "fd": len(rec) == 72, "brs": bool(flags & CANFD_BRS) and len(rec) == 72,
                "data": rec[8:8 + n], "size": len(rec)}

    def close(self):
        if self.conn:
            self.conn.close()
        self.srv.close()


class Bridge:
    def __init__(self, binary, ifpath, port, env=None, verbose=False):
        e = dict(os.environ)
        e.update(env or {})
        args = [str(binary), "-i", str(ifpath), "-b", "127.0.0.1", "-p", str(port)] + (["-v"] if verbose else [])
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=e, text=True)
        self.port = port
        assert wait_for(self._listening, 3), self.output()

    def _listening(self):
        try:
            socket.create_connection(("127.0.0.1", self.port), 0.2).close()
            return True
        except OSError:
            return self.proc.poll() is not None

    def output(self):
        if self.proc.poll() is None:
            self.stop()
        return self.proc.stdout.read()

    def stop(self, sig=signal.SIGTERM):
        if self.proc.poll() is None:
            self.proc.send_signal(sig)
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        return self.proc.returncode


@pytest.fixture
def env(binary, tmp_path):
    can = FakeCan(tmp_path / "vcan0")
    port = free_port()
    br = Bridge(binary, can.path, port)
    can.accept()
    yield can, br
    br.stop()
    can.close()


def connect(port, **kw):
    node = BridgeNode(f"bridge://127.0.0.1:{port}", "AND1", use_adb=False, **kw)
    be = BridgeBackend(node)
    node.got, node.errors = [], []
    be.set_rx_callback(node.got.append)
    node.on_error = lambda c, t: node.errors.append(t)
    be.open(CanChannelConfig())
    return node, be


def test_c_bridge_frames_both_directions(env):
    can, br = env
    node, be = connect(br.port)
    try:
        assert node.info["iface"].endswith("vcan0") and node.info["caps"] & P.CAP_FD
        assert node.info["fw"].startswith("autosim-bridge")
        can.write(0x21, [10, 0, 0, 0, 0, 0, 0, 0])
        can.write(0x18FF0021, [1, 2, 3], ext=True)
        can.write(0x123, bytes(range(64)), fd=True, brs=True)
        can.write(0x55, [], rtr=True)
        assert wait_for(lambda: len(node.got) == 4)
        a, b, c, d = node.got
        assert (a.arbitration_id, a.data, a.is_extended, a.is_fd) == (0x21, bytes([10] + [0] * 7), False, False)
        assert b.is_extended and b.arbitration_id == 0x18FF0021 and b.data == b"\x01\x02\x03"
        assert c.is_fd and c.bitrate_switch and c.data == bytes(range(64))
        assert d.arbitration_id == 0x55
        be.send(CanFrame(0x27, bytes([100]) + bytes(7)))
        be.send(CanFrame(0x18FF0027, b"\xAA", is_extended=True))
        be.send(CanFrame(0x28, bytes(range(16)), is_fd=True, bitrate_switch=True))
        r1, r2, r3 = can.read(), can.read(), can.read()
        assert (r1["id"], r1["data"], r1["fd"], r1["size"]) == (0x27, bytes([100]) + bytes(7), False, 16)
        assert r2["ext"] and r2["id"] == 0x18FF0027 and r2["data"] == b"\xAA"
        assert r3["fd"] and r3["brs"] and r3["data"] == bytes(range(16)) and r3["size"] == 72
        assert wait_for(lambda: node.bridge_status.rx == 4 and node.bridge_status.tx == 3, 3)
        assert node.ping()
    finally:
        be.close()


def test_c_bridge_vhal_auto_response_full_path(env):
    """The user's scenario through the real daemon: VHAL writes 0x21 = 10 / 20, AutoSim answers 0x27 = 10 / 100."""
    can, br = env
    eb = EventBus()
    eng = CanEngine(eb)
    eng.load_database(DBC)
    eng.start()
    nm = NodeManager(eb)
    try:
        eng.open_channel("AND1", nm.create_backend(f"bridge://127.0.0.1:{br.port}", "AND1"), CanChannelConfig())
        eng.responder.set_rules([
            ResponseRule(rx_id=0x21, match="signal", signal="ReqValue", op="==", value="10", tx_id=0x27,
                         tx_mode="signal", tx_signals={"RespValue": "10"}),
            ResponseRule(rx_id=0x21, match="signal", signal="ReqValue", op="==", value="20", tx_id=0x27,
                         tx_mode="signal", tx_signals={"RespValue": "100"}),
        ])
        eng.responder.enabled = True
        rtts = []
        for v in (10, 20) * 10:
            t0 = time.perf_counter()
            can.write(0x21, [v, 0, 0, 0, 0, 0, 0, 0])
            r = can.read()
            rtts.append(time.perf_counter() - t0)
            assert r["id"] == 0x27 and r["data"][0] == (10 if v == 10 else 100) and len(r["data"]) == 8
        can.write(0x21, [30] + [0] * 7)                                        # no rule
        can.conn.settimeout(0.2)
        with pytest.raises(socket.timeout):
            can.read()
        assert sorted(rtts)[len(rtts) // 2] < 0.02, rtts                       # median round trip (localhost)
    finally:
        eng.shutdown()
        nm.release("AND1")


def test_c_bridge_throughput_and_order(env):
    can, br = env
    node, be = connect(br.port)
    try:
        n = 3000
        for i in range(n):
            can.write(i & 0x7FF, i.to_bytes(4, "little"), fd=bool(i & 1))
        assert wait_for(lambda: len(node.got) == n, 10), len(node.got)
        assert [int.from_bytes(f.data[:4], "little") for f in node.got] == list(range(n))
        for i in range(n):
            be.send(CanFrame(0x100, i.to_bytes(2, "little")))
        got = [int.from_bytes(can.read()["data"], "little") for _ in range(n)]
        assert got == list(range(n))
    finally:
        be.close()


def test_c_bridge_rejects_fd_on_classic_interface(binary, tmp_path):
    can = FakeCan(tmp_path / "can0")
    br = Bridge(binary, can.path, free_port(), env={"ABRIDGE_FAKE_CLASSIC": "1"})
    try:
        can.accept()
        node, be = connect(br.port)
        assert not node.info["caps"] & P.CAP_FD
        assert any("not CAN FD capable" in e for e in node.errors)
        be.send(CanFrame(0x28, bytes(16), is_fd=True))
        assert wait_for(lambda: any("classic CAN interface" in e for e in node.errors))
        be.send(CanFrame(0x27, bytes(8)))
        assert can.read()["id"] == 0x27
        assert wait_for(lambda: node.bridge_status.tx_errors == 1)
        be.close()
    finally:
        br.stop()
        can.close()


def test_c_bridge_waits_for_the_interface_and_survives_its_loss(binary, tmp_path):
    path = tmp_path / "vcan0"
    br = Bridge(binary, path, free_port())                         # interface does not exist yet
    can = None
    try:
        node, be = connect(br.port)
        assert wait_for(lambda: any("not available" in e for e in node.errors))
        be.send(CanFrame(0x27, b"\x01"))                           # nowhere to send
        assert wait_for(lambda: any("0x27 not sent" in e for e in node.errors))
        can = FakeCan(path)                                         # interface appears: bridge retries every 1 s
        can.accept(5)
        can.write(0x21, [10])
        assert wait_for(lambda: len(node.got) == 1, 3)
        assert wait_for(lambda: node.bridge_status.can_up, 3)
        can.conn.close()                                            # interface vanishes
        can.srv.close()
        path.unlink()
        assert wait_for(lambda: any("reopening" in e for e in node.errors), 3)
        assert wait_for(lambda: not node.bridge_status.can_up, 3)
        can = FakeCan(path)                                         # and comes back
        can.accept(5)
        can.write(0x21, [20])
        assert wait_for(lambda: len(node.got) == 2, 3) and node.got[-1].data == b"\x14"
        assert node.state == NodeState.RUNNING                     # the TCP link was never affected
        be.close()
    finally:
        br.stop()
        if can:
            can.close()


def test_autosim_reconnects_after_the_daemon_restarts(binary, tmp_path):
    can = FakeCan(tmp_path / "vcan0")
    port = free_port()
    br = Bridge(binary, can.path, port)
    try:
        can.accept()
        node, be = connect(port, reconnect_s=0.1)
        assert br.stop() == 0                                       # SIGTERM: clean exit
        assert wait_for(lambda: node.state == NodeState.ERROR)
        can.conn.close()
        br = Bridge(binary, can.path, port)
        can.accept()
        assert wait_for(lambda: node.state == NodeState.RUNNING, 5)
        can.write(0x21, [10])
        assert wait_for(lambda: len(node.got) == 1)
        be.send(CanFrame(0x27, b"\x0a"))
        assert can.read()["data"] == b"\x0a"
        be.close()
    finally:
        br.stop()
        can.close()


def test_c_bridge_drops_a_client_that_sends_garbage_and_serves_others(env):
    can, br = env
    node, be = connect(br.port)
    raw = socket.create_connection(("127.0.0.1", br.port), 2)
    try:
        hdr = raw.recv(4)
        assert hdr[0] == P.MAGIC and hdr[1] == P.T_HELLO
        raw.sendall(b"\x00\x01garbage")
        data = b""
        raw.settimeout(2)
        while True:
            chunk = raw.recv(4096)
            if not chunk:
                break                                               # closed by the bridge
            data += chunk
        assert b"bad packet header" in data
        # invalid frame (classic with 9 bytes) on the good client: error, connection kept
        be.node._write(P.packet(P.T_FRAME, struct.pack("<IBBHQ", 0x27, 0, 9, 0, 0) + bytes(9)))
        assert wait_for(lambda: any("invalid classic data length" in e for e in node.errors))
        can.write(0x21, [1])
        assert wait_for(lambda: len(node.got) == 1)
    finally:
        raw.close()
        be.close()


def test_c_bridge_limits_clients(env):
    can, br = env
    nodes = [connect(br.port) for _ in range(4)]
    extra = socket.create_connection(("127.0.0.1", br.port), 2)
    try:
        extra.settimeout(2)
        assert extra.recv(16) == b""                                # 5th client refused
        can.write(0x21, [5])
        assert wait_for(lambda: all(len(n.got) == 1 for n, _ in nodes))
    finally:
        extra.close()
        for _, be in nodes:
            be.close()


def test_c_bridge_usage_and_version(binary):
    r = subprocess.run([str(binary), "-V"], capture_output=True, text=True, check=False)
    assert r.returncode == 0 and "protocol v1" in r.stdout
    r = subprocess.run([str(binary), "-p", "0"], capture_output=True, text=True, check=False)
    assert r.returncode == 2
