"""Android bridge channel: AutoSim <-> ``autosim-bridge`` (SocketCAN vcan inside the Android VM) over TCP.

Port strings: ``bridge://HOST:PORT`` (``bridge://127.0.0.1:29536`` = through ``adb forward``; any other host =
the head unit's IP over Ethernet). ``BridgeNode`` mirrors the attributes of ``SlcanNode`` / ``SerialNode``
(state, info, status counters, on_rx / on_state / on_status / on_error) so the channel table, status pills,
trace, TX scheduler and auto response treat it like any other adapter.

The link survives restarts of the bridge or of adb: when the TCP connection drops the node goes to ERROR,
then reconnects every second and returns to RUNNING by itself.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable

from autosim.core.can_engine.bus_manager import CanBackend
from autosim.core.common import clock
from autosim.core.common.frame import CanChannelConfig, CanFrame
from autosim.hardware.bridge import protocol as P
from autosim.hardware.esp32_gateway.protocol import NodeStatus
from autosim.hardware.esp32_gateway.serial_node import NodeError, NodeState

PREFIX = "bridge://"
DEFAULT_URL = f"{PREFIX}127.0.0.1:{P.DEFAULT_PORT}"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def is_bridge_port(port: str) -> bool:
    return port.strip().lower().startswith(PREFIX)


def parse_url(port: str) -> tuple[str, int]:
    """'bridge://host[:port]' -> (host, port)."""
    rest = port.strip()[len(PREFIX):] if is_bridge_port(port) else port.strip()
    rest = rest.rstrip("/")
    if rest.startswith("["):                                  # [ipv6]:port
        host, _, tail = rest[1:].partition("]")
        p = tail.lstrip(":")
    elif rest.count(":") == 1:
        host, p = rest.split(":")
    else:
        host, p = rest, ""
    if not host:
        raise NodeError(f"{port!r}: no host (expected bridge://HOST:PORT)")
    try:
        num = int(p) if p else P.DEFAULT_PORT
    except ValueError:
        raise NodeError(f"{port!r}: port {p!r} is not a number") from None
    if not 0 < num < 65536:
        raise NodeError(f"{port!r}: port {num} out of range")
    return host, num


def adb_forward(port: int, adb: str | None = None, timeout: float = 5.0) -> tuple[bool, str]:
    """`adb forward tcp:PORT tcp:PORT` (PC port -> bridge port in Android). Returns (ok, message)."""
    exe = adb or shutil.which("adb")
    if not exe:
        return False, "adb not found in PATH"
    try:
        r = subprocess.run([exe, "forward", f"tcp:{port}", f"tcp:{port}"], capture_output=True, text=True,
                           check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"adb forward failed: {e}"
    msg = (r.stderr or r.stdout).strip()
    return r.returncode == 0, msg or f"adb forward tcp:{port} tcp:{port}"


class BridgeNode:
    kind = "bridge"

    def __init__(self, port: str, channel_name: str = "", connect_timeout: float = 2.0,
                 hello_timeout: float = 1.5, reconnect_s: float = 1.0, status_period_s: float = 1.0,
                 link_timeout_s: float = 5.0, use_adb: bool = True) -> None:
        self.port = port
        self.host, self.tcp_port = parse_url(port)
        self.channel_name = channel_name or port
        self.connect_timeout, self.hello_timeout = connect_timeout, hello_timeout
        self.reconnect_s, self.status_period_s, self.link_timeout_s = reconnect_s, status_period_s, link_timeout_s
        self.use_adb = use_adb and self.host in LOCAL_HOSTS
        self._sock: socket.socket | None = None
        self._wlock = threading.Lock()
        self._link_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._hello_evt = threading.Event()
        self._last_rx = 0.0
        self._started = False
        self._want_fd = True
        self._adb_tried = False
        self.state = NodeState.DISCONNECTED
        self.info: dict = {"node_id": f"{self.host}:{self.tcp_port}", "fw": "", "proto": "abp", "caps": 0,
                           "device": "Android bridge (autosim-bridge)", "iface": ""}
        self.status = NodeStatus()
        self.bridge_status = P.BridgeStatus()
        self.crc_errors = 0                     # protocol errors on the link
        self.reconnects = 0
        self.last_error: tuple[int, str] | None = None
        self.on_rx: Callable[[CanFrame], None] = lambda f: None
        self.on_status: Callable[[NodeStatus], None] = lambda s: None
        self.on_state: Callable[[NodeState], None] = lambda s: None
        self.on_error: Callable[[int, str], None] = lambda code, text: None

    # ---- state -------------------------------------------------------------------------------
    def _set_state(self, st: NodeState) -> None:
        if st != self.state:
            self.state = st
            self.on_state(st)

    def _spawn(self, fn, name: str) -> None:
        t = threading.Thread(target=fn, name=f"{name}-{self.channel_name}", daemon=True)
        t.start()
        self._threads.append(t)

    def _report(self, code: int, text: str) -> None:
        self.last_error = (code, text)
        self.on_error(code, text)

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # ---- TCP link ------------------------------------------------------------------------------
    def _open_socket(self) -> socket.socket:
        try:
            return socket.create_connection((self.host, self.tcp_port), timeout=self.connect_timeout)
        except OSError as first:
            if not self.use_adb or self._adb_tried:
                raise
            self._adb_tried = True                  # localhost refused: maybe the adb forward is missing
            ok, msg = adb_forward(self.tcp_port)
            if not ok:
                raise OSError(f"{first}; {msg}") from first
            self._report(0, f"{msg} (adb forward set up)")
            return socket.create_connection((self.host, self.tcp_port), timeout=self.connect_timeout)

    def _handshake(self, s: socket.socket) -> dict:
        """Wait for the bridge's HELLO, answer with ours. Returns the decoded HELLO."""
        s.settimeout(self.hello_timeout)
        parser = P.PacketParser()
        deadline = time.monotonic() + self.hello_timeout
        pending: list[tuple[int, bytes]] = []
        hello = None
        while hello is None:
            if time.monotonic() > deadline:
                raise NodeError("no HELLO from the bridge - is autosim-bridge running on that port?")
            try:
                chunk = s.recv(4096)
            except TimeoutError:
                continue
            if not chunk:
                raise NodeError("the bridge closed the connection (another AutoSim connected?)")
            for ptype, payload in parser.feed(chunk):
                if hello is None and ptype == P.T_HELLO:
                    hello = P.decode_hello(payload)
                elif hello is not None:
                    pending.append((ptype, payload))
        if hello["version"] != P.VERSION:
            raise NodeError(f"bridge speaks protocol v{hello['version']}, AutoSim v{P.VERSION}")
        s.sendall(P.encode_hello("AutoSim Studio", "autosim", 0))
        s.settimeout(None)
        self._parser = parser
        for ptype, payload in pending:
            self._dispatch(ptype, payload)
        return hello

    def _attach(self) -> None:
        """Open + handshake; on success the socket becomes the live link."""
        s = self._open_socket()
        try:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            hello = self._handshake(s)
        except Exception:
            s.close()
            raise
        self.info.update(fw=hello["software"], caps=hello["caps"], iface=hello["name"],
                         node_id=f"android:{hello['name'] or '?'}")
        self._last_rx = time.monotonic()
        self._sock = s

    def _detach(self) -> None:
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()

    def _write(self, data: bytes) -> None:
        s = self._sock
        if s is None:
            raise NodeError(f"{self.port}: bridge not connected")
        try:
            with self._wlock:
                s.sendall(data)
        except OSError as e:
            raise NodeError(f"{self.port}: {e}") from e

    # ---- lifecycle (same verbs as SerialNode / SlcanNode) ------------------------------------------
    def connect(self) -> dict:
        self._stop.clear()
        self._set_state(NodeState.HELLO)
        try:
            self._attach()
        except NodeError as e:
            self._set_state(NodeState.DISCONNECTED)
            raise NodeError(f"{self.port}: {e}") from e
        except OSError as e:
            self._set_state(NodeState.DISCONNECTED)
            hint = (f" - start autosim-bridge on the head unit and check `adb forward tcp:{self.tcp_port} tcp:{self.tcp_port}`"
                     if self.host in LOCAL_HOSTS else
                    " - is the head unit reachable and autosim-bridge listening on that address?")
            raise NodeError(f"cannot connect to {self.host}:{self.tcp_port}: {e}{hint}") from e
        self._spawn(self._reader, "bridge-rx")
        return self.info

    def configure(self, cfg: CanChannelConfig) -> None:
        self._want_fd = cfg.fd_enabled
        if cfg.fd_enabled and not self.info["caps"] & P.CAP_FD:
            self._report(-1, f"{self.info['iface'] or 'the interface'} in Android is not CAN FD capable "
                             f"(MTU 16): FD frames will be rejected - `ip link set <if> mtu 72`")
        self._set_state(NodeState.CONFIGURED)

    def start(self) -> None:
        self._started = True
        self._spawn(self._status_loop, "bridge-status")
        self._set_state(NodeState.RUNNING)

    def stop(self) -> None:
        self._started = False
        self._set_state(NodeState.CONFIGURED)

    def close(self) -> None:
        self._started = False
        self._stop.set()
        self._detach()
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(2)
        self._threads.clear()
        self._set_state(NodeState.DISCONNECTED)

    def ping(self, timeout: float = 0.5) -> bool:
        self._pong = threading.Event()
        self._write(P.packet(P.T_PING, b"ping"))
        return self._pong.wait(timeout)

    def set_name(self, name: str) -> None:
        raise NodeError("the Android bridge is identified by its CAN interface; it has no stored name")

    # ---- traffic ----------------------------------------------------------------------------
    def send_frame(self, frame: CanFrame) -> None:
        if self.state == NodeState.ERROR:
            raise NodeError(f"{self.port}: link to the bridge lost - reconnecting")
        if self.state != NodeState.RUNNING:
            raise NodeError("channel not started")
        try:
            pkt = P.encode_frame(frame, int(time.time() * 1e6))
        except P.ProtocolError as e:
            raise NodeError(str(e)) from e
        self._write(pkt)
        self.status.tx_count += 1

    def _dispatch(self, ptype: int, payload: bytes) -> None:
        if ptype == P.T_FRAME:
            frame, _flags, _ts = P.decode_frame(payload)
            frame.channel, frame.timestamp = self.channel_name, clock.now()
            self.status.rx_count += 1
            self.on_rx(frame)
        elif ptype == P.T_STATUS:
            self.bridge_status = st = P.decode_status(payload)
            self.status.err_count = st.tx_errors
            self.status.dropped = st.dropped
            self.status.bus_flags = 0 if st.can_up else 0x02
            self.on_status(self.status)
        elif ptype == P.T_ERROR:
            code, text = P.decode_error(payload)
            self._report(code, f"bridge: {text}")
        elif ptype == P.T_PING:
            self._write(P.packet(P.T_PONG, payload))
        elif ptype == P.T_PONG:
            ev = getattr(self, "_pong", None)
            if ev is not None:
                ev.set()
        # T_HELLO after the handshake / unknown types: ignored (forward compatibility)

    def _reader(self) -> None:
        while not self._stop.is_set():
            s = self._sock
            if s is None:
                if not self._reconnect():
                    return
                continue
            try:
                chunk = s.recv(65536)
                if not chunk:
                    raise ConnectionError("connection closed by the bridge")
                self._last_rx = time.monotonic()
                for ptype, payload in self._parser.feed(chunk):
                    self._dispatch(ptype, payload)
            except P.ProtocolError as e:
                self.crc_errors += 1
                self._lost(f"protocol error ({e})", s)
            except (OSError, ConnectionError) as e:
                self._lost(str(e), s)

    def _lost(self, why: str, sock: socket.socket) -> None:
        """Drop `sock` if it is still the live link (reader and keep-alive may both notice the same loss)."""
        with self._link_lock:
            if self._stop.is_set() or self._sock is not sock:
                return
            self._detach()
        self._set_state(NodeState.ERROR)
        self._report(0, f"{self.port}: link to the bridge lost: {why}; reconnecting")

    def _reconnect(self) -> bool:
        """Retry until connected (True) or closed (False)."""
        while not self._stop.wait(self.reconnect_s):
            try:
                self._attach()
            except (OSError, NodeError):
                continue
            if self._stop.is_set():                 # closed while connecting: do not keep the new link
                self._detach()
                return False
            self.reconnects += 1
            self._report(0, f"{self.port}: reconnected to the bridge ({self.info['iface']})")
            self._set_state(NodeState.RUNNING if self._started else NodeState.CONFIGURED)
            return True
        return False

    def _status_loop(self) -> None:
        """Keep-alive: a PING every period; no traffic at all for link_timeout_s = half-open link -> reconnect."""
        while not self._stop.wait(self.status_period_s):
            s = self._sock
            if s is None:
                continue
            if time.monotonic() - self._last_rx > self.link_timeout_s:
                self._lost(f"no data for {self.link_timeout_s:g} s", s)
                continue
            try:
                self._write(P.packet(P.T_PING, b""))
            except NodeError:
                pass
            self.on_status(self.status)


class BridgeBackend(CanBackend):
    name = "bridge"

    def __init__(self, node: BridgeNode) -> None:
        self.node = node

    def set_rx_callback(self, cb) -> None:
        self.node.on_rx = cb

    def open(self, cfg: CanChannelConfig) -> None:
        if self.node.state == NodeState.DISCONNECTED:
            self.node.connect()
        self.node.configure(cfg)
        self.node.start()

    def close(self) -> None:
        self.node.close()

    def send(self, frame: CanFrame) -> None:
        self.node.send_frame(frame)
