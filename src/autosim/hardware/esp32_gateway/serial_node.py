"""One ESP32 node: reader/heartbeat threads, handshake, config, ack/retry."""
from __future__ import annotations

import struct
import threading
import time
from enum import Enum
from typing import Callable

from autosim.core.common import clock
from autosim.core.common.frame import CanChannelConfig, CanFrame
from autosim.hardware.esp32_gateway import protocol as P
from autosim.hardware.esp32_gateway.protocol import MsgType, Packet, ProtocolError
from autosim.hardware.esp32_gateway.transport import DEFAULT_BAUD, SerialTransport, Transport


class NodeState(Enum):
    DISCONNECTED = "disconnected"
    HELLO = "hello"
    CONFIGURED = "configured"
    RUNNING = "running"
    ERROR = "error"


class NodeError(Exception):
    """Node rejected a request (NACK) or did not answer."""


class _Pending:
    __slots__ = ("event", "resp")

    def __init__(self) -> None:
        self.event, self.resp = threading.Event(), None


class SerialNode:
    """Lifecycle: DISCONNECTED -> HELLO -> CONFIGURED -> RUNNING (-> ERROR on lost heartbeat).

    - reader thread splits the byte stream on 0x00, decodes packets and dispatches them
      (RX_FRAME -> on_rx, ACK/NACK -> waiting request, STATUS -> on_status, PONG -> liveness)
    - request() sends a control packet and waits for ACK (retries on timeout)
    - TX_FRAME is fire-and-forget
    - heartbeat: PING every `ping_s`; no traffic for `dead_after_s` => ERROR
    """

    def __init__(self, port: str, channel_name: str = "", baud: int = DEFAULT_BAUD,
                 transport_factory: Callable[[], Transport] | None = None,
                 ping_s: float = 1.0, dead_after_s: float = 3.5) -> None:
        self.port, self.channel_name, self.baud = port, channel_name or port, baud
        self._factory = transport_factory or (lambda: SerialTransport(port, baud))
        self.ping_s, self.dead_after_s = ping_s, dead_after_s
        self._t: Transport | None = None
        self._wlock = threading.Lock()
        self._seq = 0
        self._pending: dict[int, _Pending] = {}
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._last_rx = 0.0
        self.state = NodeState.DISCONNECTED
        self.info: dict = {}
        self.status = P.NodeStatus()
        self.crc_errors = 0
        self.tx_nacks = 0                      # TX_FRAMEs the node rejected (fire-and-forget, so counted here)
        self.last_nack: tuple[int, int] | None = None   # (acked type, error code) of the last unsolicited NACK
        self.last_error: tuple[int, str] | None = None  # last ERROR message from the node (code, text)
        self.on_error: Callable[[int, str], None] = lambda code, text: None
        self.on_rx: Callable[[CanFrame], None] = lambda f: None
        self.on_status: Callable[[P.NodeStatus], None] = lambda s: None
        self.on_state: Callable[[NodeState], None] = lambda s: None

    # ---- state ---------------------------------------------------------
    def _set_state(self, st: NodeState) -> None:
        if st != self.state:
            self.state = st
            self.on_state(st)

    # ---- lifecycle -----------------------------------------------------
    def connect(self) -> dict:
        """Open the port and perform the HELLO handshake; returns the node info."""
        self._stop.clear()
        self._t = self._factory()
        self._last_rx = time.monotonic()
        self._spawn(self._reader, "node-rx")
        self._set_state(NodeState.HELLO)
        try:
            # opening the port resets most dev boards (DTR/RTS) and the firmware needs ~0.5 s to boot: keep asking for ~1.8 s
            resp = self.request(MsgType.HELLO, bytes([P.PROTO_VERSION]), timeout=0.3, retries=6)
            self.info = P.decode_hello_ack(resp.payload)
        except Exception as e:
            self.close()
            if isinstance(e, NodeError) and "HELLO" in str(e):
                raise NodeError(f"{e}. No AutoSim firmware answered at {self.baud} baud. This port is not a "
                                f"CANable (slcan) adapter: pick a CANable port, or see docs/CANABLE_FIRMWARE.md "
                                f"if a CANable is not recognised.") from e
            raise
        return self.info

    def configure(self, cfg: CanChannelConfig) -> None:
        try:
            P.validate_config(cfg)                       # fail early with the same rules as the firmware
        except ValueError as e:
            raise NodeError(f"invalid CAN configuration: {e}") from e
        self.request(MsgType.CONFIG, P.encode_config(cfg))
        self._set_state(NodeState.CONFIGURED)

    def start(self) -> None:
        self.request(MsgType.START)
        self._last_rx = time.monotonic()
        self._spawn(self._heartbeat, "node-hb")
        self._set_state(NodeState.RUNNING)

    def set_name(self, name: str) -> None:
        """Give the node a persistent name (1..15 chars of A-Z a-z 0-9 _ . -), stored in its flash."""
        if not P.valid_node_name(name):
            raise NodeError("node name must be 1-15 characters of A-Z a-z 0-9 _ . -")
        self.request(MsgType.SET_NAME, name.encode("ascii"))
        self.info["node_id"] = name

    def stop(self) -> None:
        self.request(MsgType.STOP)
        self._set_state(NodeState.CONFIGURED)

    def close(self) -> None:
        was_running = self.state == NodeState.RUNNING
        if was_running:
            try:
                self.request(MsgType.STOP, timeout=0.1, retries=1)
            except Exception:
                pass
        self._stop.set()
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(1)
        self._threads.clear()
        if self._t:
            try:
                self._t.close()
            except Exception:
                pass
            self._t = None
        self._set_state(NodeState.DISCONNECTED)

    def _spawn(self, fn, name: str) -> None:
        t = threading.Thread(target=fn, name=f"{name}-{self.channel_name}", daemon=True)
        t.start()
        self._threads.append(t)

    # ---- TX --------------------------------------------------------------
    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFF
        return self._seq

    def _write(self, pkt: Packet) -> None:
        if self._t is None:
            raise NodeError("not connected")
        with self._wlock:
            self._t.write(pkt.encode())

    def send_frame(self, frame: CanFrame) -> None:
        self._write(Packet(MsgType.TX_FRAME, self._next_seq(), P.encode_can_payload(frame)))

    def request(self, mtype: MsgType, payload: bytes = b"", timeout: float = 0.1,
                retries: int = 3) -> Packet:
        for _ in range(retries):
            seq = self._next_seq()
            pend = self._pending[seq] = _Pending()
            self._write(Packet(mtype, seq, payload))
            ok = pend.event.wait(timeout)
            self._pending.pop(seq, None)
            if ok:
                if pend.resp.type == MsgType.NACK:
                    code = pend.resp.payload[1] if len(pend.resp.payload) > 1 else 0
                    detail = self.last_error[1] if self.last_error and self.last_error[0] == code else ""
                    why = P.ERROR_NAMES.get(code, f"error {code}")
                    raise NodeError(f"{mtype.name} rejected by node: {why}" + (f" - {detail}" if detail else ""))
                return pend.resp
        raise NodeError(f"{mtype.name}: no answer from {self.port}")

    # ---- RX ------------------------------------------------------------
    def _reader(self) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            try:
                chunk = self._t.read_some()
            except Exception:
                if not self._stop.is_set():
                    self._set_state(NodeState.ERROR)
                return
            if not chunk:
                continue
            buf += chunk
            while True:
                i = buf.find(0)
                if i < 0:
                    break
                block = bytes(buf[:i])
                del buf[: i + 1]
                if block:
                    self._handle(block)
            if len(buf) > 65536:
                buf.clear()

    def _handle(self, block: bytes) -> None:
        try:
            pkt = Packet.decode(block)
        except (ProtocolError, ValueError):
            self.crc_errors += 1
            return
        self._last_rx = time.monotonic()
        t = pkt.type
        if t == MsgType.RX_FRAME:
            try:
                f = P.decode_can_payload(pkt.payload, self.channel_name)
            except struct.error:
                self.crc_errors += 1
                return
            f.timestamp = clock.now()
            self.on_rx(f)
        elif t in (MsgType.ACK, MsgType.NACK, MsgType.HELLO_ACK):
            pend = self._pending.get(pkt.seq)
            if pend:
                pend.resp = pkt
                pend.event.set()
            elif t == MsgType.NACK and len(pkt.payload) >= 2:      # a rejected TX_FRAME
                self.tx_nacks += 1
                self.last_nack = (pkt.payload[0], pkt.payload[1])
        elif t == MsgType.ERROR and pkt.payload:
            self.last_error = (pkt.payload[0], pkt.payload[1:].decode("ascii", "replace"))
            self.on_error(*self.last_error)
        elif t == MsgType.STATUS:
            self.status = P.NodeStatus.decode(pkt.payload)
            self.on_status(self.status)
        # PONG / TX_CONFIRM / ERROR only refresh liveness here

    def _heartbeat(self) -> None:
        token = 0
        while not self._stop.wait(self.ping_s):
            token += 1
            try:
                self._write(Packet(MsgType.PING, self._next_seq(), struct.pack("<I", token)))
            except Exception:
                self._set_state(NodeState.ERROR)
                return
            if time.monotonic() - self._last_rx > self.dead_after_s:
                self._set_state(NodeState.ERROR)
                return
