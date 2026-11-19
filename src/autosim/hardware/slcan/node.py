"""CANable 2.0 / slcan USB-CAN FD adapter as an AutoSim CAN channel.

`SlcanNode` mirrors the attributes of the ESP32 `SerialNode` (state, info, status counters and the
on_rx / on_state / on_status / on_error callbacks) so the rest of the app - node table, status pills, trace,
TX scheduler, projects - treats both kinds of hardware the same.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from autosim.core.can_engine.bus_manager import CanBackend
from autosim.core.common import clock
from autosim.core.common.frame import CanChannelConfig, CanFrame
from autosim.hardware.esp32_gateway.protocol import NodeStatus
from autosim.hardware.esp32_gateway.serial_node import NodeError, NodeState
from autosim.hardware.esp32_gateway.transport import Transport
from autosim.hardware.slcan import protocol as S

SLCAN_BAUD = 115200                 # USB CDC: the value is ignored by the adapter


class _SerialTransport:
    def __init__(self, port: str) -> None:
        import serial
        self._s = serial.serial_for_url(port, baudrate=SLCAN_BAUD, timeout=0.02, write_timeout=1.0)

    def read_some(self) -> bytes:
        return self._s.read(self._s.in_waiting or 1)

    def write(self, data: bytes) -> None:
        self._s.write(data)

    def close(self) -> None:
        self._s.close()


class SlcanNode:
    kind = "slcan"

    def __init__(self, port: str, channel_name: str = "", serial_number: str = "",
                 transport_factory: Callable[[], Transport] | None = None, status_period_s: float = 1.0) -> None:
        self.port, self.channel_name = port, channel_name or port
        self._factory = transport_factory or (lambda: _SerialTransport(port))
        self.status_period_s = status_period_s
        self._t: Transport | None = None
        self._wlock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._version_evt = threading.Event()
        self._reg_evt = threading.Event()
        self._reg_seen = 0
        self._baseline = 0                  # error register at connect: the firmware latches flags until re-plug
        self._cmds: list[str] = []
        self._loopback = False
        self._last_reg = 0
        self.state = NodeState.DISCONNECTED
        self.info: dict = {"node_id": serial_number or port, "fw": "", "proto": "slcan", "caps": 1,
                           "device": "CANable 2.0 (slcan)"}
        self.status = NodeStatus()
        self.crc_errors = 0                 # malformed lines from the adapter
        self.tx_nacks = 0                   # frames the adapter refused / could not send
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

    def _write(self, line: str) -> None:
        if self._t is None:
            raise NodeError("not connected")
        with self._wlock:
            self._t.write((line + "\r").encode("ascii"))

    def _spawn(self, fn, name: str) -> None:
        t = threading.Thread(target=fn, name=f"{name}-{self.channel_name}", daemon=True)
        t.start()
        self._threads.append(t)

    # ---- lifecycle (same verbs as SerialNode) --------------------------------------------------
    def connect(self) -> dict:
        """Open the port, close any channel a previous session left open, read the firmware version."""
        self._stop.clear()
        try:
            self._t = self._factory()
        except Exception as e:
            raise NodeError(f"cannot open {self.port}: {e}") from e
        self._spawn(self._reader, "slcan-rx")
        self._set_state(NodeState.HELLO)
        self._write("C")
        for _ in range(3):
            self._version_evt.clear()
            self._write("V")
            if self._version_evt.wait(0.5):
                break
        else:
            self.close()
            raise NodeError(f"{self.port}: no slcan answer to 'V' - is this a CANable / slcan adapter?")
        self._reg_evt.clear()
        self._write("E")
        self._reg_evt.wait(0.5)
        self.info["latched_errors"] = self._baseline
        return self.info

    def configure(self, cfg: CanChannelConfig) -> None:
        try:
            self._cmds = S.config_commands(cfg)
        except S.SlcanError as e:
            raise NodeError(f"invalid CAN configuration: {e}") from e
        # The CANable has no internal loopback mode: 'Loop' echoes transmitted frames on the PC instead,
        # and nothing is put on the bus.
        self._loopback = bool(cfg.extras.get("loopback"))
        self._set_state(NodeState.CONFIGURED)

    def start(self) -> None:
        if not self._loopback:
            self._write("C")
            for c in self._cmds or ["S6", "Y2", "M0", "A1"]:
                self._write(c)
            self._write("O")
        self._last_reg = self._baseline
        if self._baseline:
            flags = ", ".join(t for b, t in S.ERROR_BITS.items() if self._baseline & (1 << b))
            self.on_error(-1, f"error flags already latched from an earlier session ({flags}); the adapter only "
                              f"clears them when re-plugged, so these cannot be detected again until then")
        self._spawn(self._status_loop, "slcan-status")
        self._set_state(NodeState.RUNNING)

    def stop(self) -> None:
        if not self._loopback:
            self._write("C")
        self._set_state(NodeState.CONFIGURED)

    def close(self) -> None:
        if self._t is not None and self.state == NodeState.RUNNING:
            try:
                self._write("C")
            except Exception:
                pass
        self._stop.set()
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(1)
        self._threads.clear()
        if self._t is not None:
            try:
                self._t.close()
            except Exception:
                pass
            self._t = None
        self._set_state(NodeState.DISCONNECTED)

    def ping(self, timeout: float = 0.5) -> bool:
        """True if the adapter answers a version request (it is alive and parsing commands)."""
        self._version_evt.clear()
        self._write("V")
        return self._version_evt.wait(timeout)

    def read_error_register(self, timeout: float = 0.5) -> int | None:
        """Current (latched) error register as answered by the adapter, or None if it did not answer."""
        self._reg_evt.clear()
        self._write("E")
        return self._reg_seen if self._reg_evt.wait(timeout) else None

    def set_name(self, name: str) -> None:
        raise NodeError("CANable adapters cannot store a name; they are identified by their USB serial number")

    # ---- traffic ----------------------------------------------------------------------------
    def send_frame(self, frame: CanFrame) -> None:
        if self.state != NodeState.RUNNING:
            raise NodeError("channel not started")
        try:
            line = S.encode_frame(frame)
        except S.SlcanError as e:
            self.tx_nacks += 1
            raise NodeError(str(e)) from e
        if self._loopback:
            echo = CanFrame(frame.arbitration_id, _padded(frame), frame.is_extended,
                            frame.is_fd, frame.bitrate_switch, clock.now(), self.channel_name, True)
            self.status.tx_count += 1
            self.status.rx_count += 1
            self.on_rx(echo)
            return
        with self._wlock:
            self._t.write(line)
        self.status.tx_count += 1

    def _reader(self) -> None:
        splitter = S.LineSplitter()
        while not self._stop.is_set():
            try:
                chunk = self._t.read_some()
            except Exception:
                if not self._stop.is_set():
                    self._set_state(NodeState.ERROR)
                    self.on_error(0, f"{self.port}: adapter disconnected")
                return
            for kind, value in splitter.feed(chunk, idle=not chunk):
                if kind == "frame":
                    value.channel, value.timestamp = self.channel_name, clock.now()
                    self.status.rx_count += 1
                    self.on_rx(value)
                elif kind == "error_reg":
                    self._reg_seen = value
                    if self.state == NodeState.RUNNING:
                        self._on_error_reg(value)
                    else:
                        self._baseline = value
                    self._reg_evt.set()
                elif kind == "text":
                    if "github.com" in value or "canable" in value.lower() or not self.info["fw"]                             or value.strip() == self.info["fw"]:
                        self.info["fw"] = value.strip()
                        self._version_evt.set()
                    else:
                        self.crc_errors += 1

    def _on_error_reg(self, reg: int) -> None:
        new = reg & ~self._last_reg
        self._last_reg = reg
        self.status.err_count = bin(reg).count("1")
        self.status.bus_flags = 0x01 if reg & ((1 << S.ERR_FULLBUF_CANTX) | (1 << S.ERR_CAN_TXFAIL)) else 0
        for bit, text in S.ERROR_BITS.items():
            if new & (1 << bit):
                if bit in (S.ERR_FULLBUF_CANTX, S.ERR_CAN_TXFAIL):
                    self.tx_nacks += 1
                    text += " - frames are not being acknowledged (another node on the bus? termination?)"
                self.last_error = (bit, text)
                self.on_error(bit, text)
        self.on_status(self.status)

    def _status_loop(self) -> None:
        while not self._stop.wait(self.status_period_s):
            if self.state != NodeState.RUNNING:
                return
            if self._loopback:
                self.on_status(self.status)
                continue
            try:
                self._write("E")
            except Exception:
                return


def _padded(frame: CanFrame) -> bytes:
    data = bytes(frame.data)
    return data + bytes(S.fd_length(len(data)) - len(data)) if frame.is_fd else data


class SlcanBackend(CanBackend):
    name = "slcan"

    def __init__(self, node: SlcanNode) -> None:
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


def wait_for(cond, timeout: float = 2.0) -> bool:          # small helper for tools / tests
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False
