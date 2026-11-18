"""PEAK PCAN adapter as an AutoSim CAN channel.

`PcanNode` mirrors the attributes of the ESP32 `SerialNode` and the CANable `SlcanNode` (state, info, status
counters and the on_rx / on_state / on_status / on_error callbacks), so the node table, status bar, trace, TX
scheduler and projects treat every kind of hardware the same. The CAN work itself is python-can's `PcanBus`.
"""
from __future__ import annotations

import threading
from collections.abc import Callable

import can

from autosim.core.can_engine.bus_manager import CanBackend
from autosim.core.common import clock
from autosim.core.common.frame import CanChannelConfig, CanFrame
from autosim.hardware.esp32_gateway.protocol import NodeStatus
from autosim.hardware.esp32_gateway.serial_node import NodeError, NodeState
from autosim.hardware.pcan import _api, _const, driver_missing_text, driver_status, scan_channels
from autosim.hardware.pcan.timing import TimingError, classic_timing, describe, fd_timing
from autosim.hardware.slcan.protocol import fd_length

FLAG_PASSIVE, FLAG_BUSOFF = 0x01, 0x02


def _default_bus_factory(**kw):
    return _api().PcanBus(**kw)


class PcanNode:
    kind = "pcan"

    def __init__(self, port: str, channel_name: str = "", bus_factory: Callable[..., can.BusABC] | None = None,
                 status_period_s: float = 1.0) -> None:
        self.port, self.channel_name = port.upper(), channel_name or port
        self._factory = bus_factory or _default_bus_factory
        self.status_period_s = status_period_s
        self._bus: can.BusABC | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._timing = None
        self._fd = False
        self._loopback = False
        self._supports_fd = True
        self._last_status = 0
        self._reported: set[str] = set()
        self.state = NodeState.DISCONNECTED
        self.info: dict = {"node_id": self.port, "fw": "", "proto": "pcan", "caps": 1, "device": "PCAN"}
        self.status = NodeStatus()
        self.crc_errors = 0                 # error frames seen on the bus
        self.tx_nacks = 0                   # frames the driver refused / could not send
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

    def _error(self, code: int, text: str, once_key: str | None = None) -> None:
        if once_key is not None:
            if once_key in self._reported:
                return
            self._reported.add(once_key)
        self.last_error = (code, text)
        self.on_error(code, text)

    def _spawn(self, fn, name: str) -> None:
        t = threading.Thread(target=fn, name=f"{name}-{self.channel_name}", daemon=True)
        t.start()
        self._threads.append(t)

    # ---- lifecycle (same verbs as SerialNode / SlcanNode) ----------------------------------------
    def connect(self) -> dict:
        """Check the driver and that an adapter is plugged in on this channel; fills `info`."""
        ok, detail = driver_status()
        if not ok:
            raise NodeError(driver_missing_text(detail))
        found = next((c for c in scan_channels() if c["port"] == self.port), None)
        if found is None:
            raise NodeError(f"{self.port}: no PCAN adapter on this channel (plugged in? see PEAK's PCAN-View)")
        if found["occupied"]:
            raise NodeError(f"{self.port} is in use by another application (PCAN-View, CANoe, …) - close it first")
        self._supports_fd = found["supports_fd"]
        suffix = self.port.rsplit("BUS", 1)[-1]
        self.info.update({"node_id": f"{found['label']} {suffix}", "fw": f"PCAN-Basic {detail}".strip(),
                          "device": found["label"], "device_id": found["device_id"],
                          "supports_fd": self._supports_fd})
        self._set_state(NodeState.HELLO)
        return self.info

    def configure(self, cfg: CanChannelConfig) -> None:
        if cfg.fd_enabled and not self._supports_fd:
            raise NodeError(f"{self.info['device']} does not support CAN FD - untick FD (classic CAN up to 1 Mbit/s)")
        try:
            if cfg.fd_enabled:
                self._timing = fd_timing(cfg.nominal_bitrate, cfg.data_bitrate,
                                         cfg.sample_point_nominal, cfg.sample_point_data)
            else:
                self._timing = classic_timing(cfg.nominal_bitrate, cfg.sample_point_nominal)
        except TimingError as e:
            raise NodeError(f"invalid CAN configuration: {e}") from e
        self._fd = cfg.fd_enabled
        # Like the CANable: 'Loop' echoes transmitted frames on the PC and nothing is put on the bus.
        self._loopback = bool(cfg.extras.get("loopback"))
        self.info["timing"] = describe(self._timing)
        self._set_state(NodeState.CONFIGURED)

    def start(self) -> None:
        if not self._loopback:
            try:
                self._bus = self._factory(channel=self.port, timing=self._timing, auto_reset=True)
            except Exception as e:
                raise NodeError(f"{self.port}: cannot open the PCAN channel: {e}") from e
        self._stop.clear()
        if self._bus is not None:
            self._spawn(self._reader, "pcan-rx")
        self._last_status = 0
        self._spawn(self._status_loop, "pcan-status")
        self._set_state(NodeState.RUNNING)

    def stop(self) -> None:
        self._shutdown_bus()
        self._set_state(NodeState.CONFIGURED)

    def _shutdown_bus(self) -> None:
        self._stop.set()
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(1)
        self._threads.clear()
        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:
                pass
            self._bus = None

    def close(self) -> None:
        self._shutdown_bus()
        self._set_state(NodeState.DISCONNECTED)

    def ping(self, timeout: float = 0.5) -> bool:
        if self._loopback and self.state == NodeState.RUNNING:
            return True
        if self._bus is None:
            return False
        try:
            st = self._bus.status()
        except Exception:
            return False
        return not _unplugged(int(st), _const())

    def set_name(self, name: str) -> None:
        raise NodeError("PCAN adapters are identified by their channel (PCAN_USBBUS1, …); they cannot be renamed "
                        "from AutoSim")

    # ---- traffic ----------------------------------------------------------------------------
    def send_frame(self, frame: CanFrame) -> None:
        if self.state != NodeState.RUNNING:
            raise NodeError("channel not started")
        if frame.is_fd and not self._fd:
            self.tx_nacks += 1
            raise NodeError("CAN FD frame on a channel opened for classic CAN - Connect with FD ticked")
        data = bytes(frame.data)
        if frame.is_fd:
            data += bytes(fd_length(len(data)) - len(data))          # 13 bytes -> 16 (the next valid FD length)
        elif len(data) > 8:
            self.tx_nacks += 1
            raise NodeError(f"classic CAN frame with {len(data)} bytes (max 8)")
        if self._loopback:
            self.status.tx_count += 1
            self.status.rx_count += 1
            self.on_rx(CanFrame(frame.arbitration_id, data, frame.is_extended, frame.is_fd,
                                frame.is_fd and frame.bitrate_switch, clock.now(), self.channel_name, True))
            return
        msg = can.Message(arbitration_id=frame.arbitration_id, data=data, is_extended_id=frame.is_extended,
                          is_fd=frame.is_fd, bitrate_switch=frame.is_fd and frame.bitrate_switch, check=False)
        try:
            self._bus.send(msg)
        except Exception as e:
            self.tx_nacks += 1
            raise NodeError(f"{self.port}: send failed: {e}") from e
        self.status.tx_count += 1

    def _reader(self) -> None:
        while not self._stop.is_set():
            bus = self._bus
            if bus is None:
                return
            try:
                msg = bus.recv(0.05)
            except Exception as e:
                if not self._stop.is_set():
                    self._set_state(NodeState.ERROR)
                    self._error(0, f"{self.port}: adapter unplugged or driver error ({e}) - plug it in and "
                                   "Connect again", once_key="unplug")
                return
            if msg is None:
                continue
            if msg.is_error_frame:
                self.crc_errors += 1
                self.status.err_count += 1
                self._error(1, "error frames on the bus (bit / stuff / form / CRC errors) - check bit rates, "
                               "FD / BRS settings and the 120 Ω termination", once_key="error-frame")
                continue
            if not msg.is_rx:                                # our own echo
                continue
            self.status.rx_count += 1
            self.on_rx(CanFrame(msg.arbitration_id, bytes(msg.data), msg.is_extended_id, msg.is_fd,
                                msg.bitrate_switch, clock.now(), self.channel_name, True))

    def _status_loop(self) -> None:
        pc = _const()
        while not self._stop.wait(self.status_period_s):
            if self.state != NodeState.RUNNING:
                if self.state == NodeState.ERROR:
                    self.on_status(self.status)
                continue
            if self._bus is None:                            # loopback
                self.on_status(self.status)
                continue
            try:
                st = int(self._bus.status())
            except Exception as e:
                self._set_state(NodeState.ERROR)
                self._error(0, f"{self.port}: adapter not answering ({e})", once_key="status-fail")
                continue
            self._on_status_code(st, pc)
            self.on_status(self.status)

    def _on_status_code(self, st: int, pc) -> None:
        if _unplugged(st, pc):
            self._set_state(NodeState.ERROR)
            self._error(0, f"{self.port}: adapter unplugged - plug it in and Connect again", once_key="unplug")
            return
        flags = 0
        if st & pc.PCAN_ERROR_BUSPASSIVE:
            flags |= FLAG_PASSIVE
        if st & pc.PCAN_ERROR_BUSOFF:
            flags |= FLAG_BUSOFF
        new = st & ~self._last_status
        self._last_status = st
        self.status.bus_flags = flags
        if new & pc.PCAN_ERROR_BUSOFF:
            self.status.err_count += 1
            self._error(2, "bus-off: too many transmit errors (no other node acknowledging? wrong bit rate?) - "
                           "the driver restarts the controller automatically")
        elif new & pc.PCAN_ERROR_BUSPASSIVE:
            self.status.err_count += 1
            self._error(3, "error passive: frames are not being acknowledged (another node on the bus? "
                           "termination? bit rates?)")
        elif new & (pc.PCAN_ERROR_BUSHEAVY | pc.PCAN_ERROR_BUSLIGHT):
            self._error(4, "error warning: error counters rising (bit rates, termination, wiring?)",
                        once_key="warning")
        if st & pc.PCAN_ERROR_QXMTFULL:
            self._error(5, "transmit queue full: frames are not leaving (no ACK on the bus?)", once_key="qfull")


def _unplugged(st: int, pc) -> bool:
    """Hardware handle invalid (adapter unplugged) or driver gone. ILLHW (0x1400) shares bits with HWINUSE /
    ILLNET / ILLCLIENT, so the 0x1C00 field is compared as a whole."""
    return (st & 0x1C00) == int(pc.PCAN_ERROR_ILLHW) or bool(st & int(pc.PCAN_ERROR_NODRIVER))


class PcanBackend(CanBackend):
    name = "pcan"

    def __init__(self, node: PcanNode) -> None:
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
