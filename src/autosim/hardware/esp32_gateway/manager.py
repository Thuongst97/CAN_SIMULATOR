"""Scan ports and manage the CAN nodes: ESP32 + MCP2518FD (AutoSim protocol), CANable 2.0 USB-CAN FD adapters
(slcan), PEAK PCAN adapters (PCAN-Basic), the Android bridge and the built-in simulated nodes."""
from __future__ import annotations

from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.hardware import pcan
from autosim.hardware.bridge.node import BridgeBackend, BridgeNode, is_bridge_port
from autosim.hardware.esp32_gateway.bus_adapter import Esp32Backend
from autosim.hardware.esp32_gateway.protocol import bus_error_hint
from autosim.hardware.esp32_gateway.serial_node import SerialNode
from autosim.hardware.esp32_gateway.sim_node import SimNode
from autosim.hardware.pcan.node import PcanBackend, PcanNode
from autosim.hardware.slcan import SLCAN_USB_IDS, device_kind
from autosim.hardware.slcan.node import SlcanBackend, SlcanNode

SIM_PREFIX = "SIM"


class NodeManager:
    def __init__(self, eventbus: EventBus | None = None) -> None:
        self.eventbus = eventbus
        self.nodes: dict[str, SerialNode] = {}
        self._sims: dict[str, SimNode] = {}
        self.pcan_driver: tuple[bool, str] = (True, "")     # (installed, version or reason), updated by scan()

    def scan(self) -> list[dict]:
        """List the adapters: serial ports with the hardware kind recognised from the USB VID:PID ('slcan' for a
        CANable 2.0, 'autosim' for anything else: ESP32 + AutoSim firmware), then the PCAN channels ('pcan')
        when the PEAK driver is installed."""
        from serial.tools import list_ports
        out = []
        for p in sorted(list_ports.comports(), key=lambda p: p.device):
            kind = device_kind(p.vid, p.pid)
            label = SLCAN_USB_IDS.get((p.vid, p.pid), p.description)
            out.append({"port": p.device, "description": p.description, "hwid": p.hwid, "kind": kind,
                        "label": label, "serial": p.serial_number or ""})
        return out + self.scan_pcan()

    def scan_pcan(self) -> list[dict]:
        """PCAN channels in the same form as scan(); also refreshes `pcan_driver`."""
        self.pcan_driver = pcan.driver_status()
        if not self.pcan_driver[0]:
            return []
        return [{"port": c["port"], "description": c["label"], "hwid": c["hwid"], "kind": "pcan",
                 "label": c["label"], "serial": "", "supports_fd": c["supports_fd"], "occupied": c["occupied"]}
                for c in pcan.scan_channels()]

    def kind_of(self, port: str) -> tuple[str, str]:
        """(kind, usb serial number) of a port; ('sim', '') for the built-in simulated nodes."""
        if port.upper().startswith(SIM_PREFIX):
            return "sim", ""
        if is_bridge_port(port):
            return "bridge", ""
        if pcan.is_pcan_port(port):
            return "pcan", ""
        try:
            for p in self.scan():
                if p["port"].upper() == port.upper():
                    return p["kind"], p["serial"]
        except Exception:
            pass
        return "autosim", ""

    def probe(self, port: str) -> dict | None:
        """Open the port briefly and send HELLO; returns node info or None if it does not answer."""
        node = SerialNode(port)
        try:
            return node.connect()
        except Exception:
            return None
        finally:
            node.close()

    def create_backend(self, port: str, channel: str, transport_factory=None, kind: str | None = None):
        """Build (but do not open) a backend. Ports named 'SIM…' use the built-in simulated node;
        `transport_factory` lets tests plug in any byte transport (e.g. the firmware simulator)."""
        if channel in self.nodes:
            raise ValueError(f"channel {channel!r} already in use")
        if not port.upper().startswith(SIM_PREFIX):          # one channel per physical adapter
            owner = next((ch for ch, n in self.nodes.items() if n.port.upper() == port.upper()), None)
            if owner:
                raise ValueError(f"{port} is already open as channel {owner}")
        if is_bridge_port(port):                  # Android VHAL bridge (autosim-bridge over TCP)
            node = BridgeNode(port, channel)
            self._hook(channel, node)
            return BridgeBackend(node)
        serial_no = ""
        if kind is None:
            kind, serial_no = ("autosim", "") if transport_factory is not None else self.kind_of(port)
        if kind == "pcan":
            node = PcanNode(port, channel)        # PEAK PCAN adapter through the PCAN-Basic driver
            self._hook(channel, node)
            return PcanBackend(node)
        if kind == "slcan":                       # CANable 2.0 USB-CAN FD adapter
            node = SlcanNode(port, channel, serial_number=serial_no, transport_factory=transport_factory)
            backend = SlcanBackend(node)
        elif transport_factory is not None:
            node = SerialNode(port, channel, transport_factory=transport_factory)
        elif port.upper().startswith(SIM_PREFIX):
            host, sim = SimNode.create_pair(node_id=channel)
            self._sims[channel] = sim
            node = SerialNode(port, channel, transport_factory=lambda: host)
        else:
            node = SerialNode(port, channel)
        self._hook(channel, node)
        return backend if kind == "slcan" else Esp32Backend(node)

    def _hook(self, channel: str, node) -> None:
        node.on_state = lambda st, ch=channel: self._publish(ch, node)
        if self.eventbus:                         # firmware ERROR messages (bad config, MCP fault ...) go to the log
            if getattr(node, "kind", "") in ("bridge", "pcan"):
                node.on_error = lambda code, text, ch=channel: self.eventbus.publish(events.LOG, f"[{ch}] {text}")
            else:
                node.on_error = lambda code, text, ch=channel: self._log_node_error(ch, code, text)
        node.on_status = lambda st, ch=channel: self._publish(ch, node)
        self.nodes[channel] = node

    def _log_node_error(self, channel: str, code: int, text: str) -> None:
        msg = f"[{channel}] node error {code}: {text}"
        hint = bus_error_hint(code, text)
        self.eventbus.publish(events.LOG, f"{msg} \u2192 {hint}" if hint else msg)

    def rename(self, channel: str, name: str) -> None:
        """Store a new persistent name on the node behind `channel` (raises NodeError)."""
        self.nodes[channel].set_name(name)
        self._publish(channel, self.nodes[channel])

    def duplicate_ids(self) -> dict[str, list[str]]:
        """{node_id: [channels]} for node ids reported by more than one connected node. Two boards
        flashed with the same name cannot be told apart in the UI, so the app warns about them."""
        by_id: dict[str, list[str]] = {}
        for ch, node in self.nodes.items():
            nid = node.info.get("node_id")
            if nid:
                by_id.setdefault(nid, []).append(ch)
        return {k: v for k, v in by_id.items() if len(v) > 1}

    def release(self, channel: str) -> None:
        node = self.nodes.pop(channel, None)
        if node:
            node.close()
        sim = self._sims.pop(channel, None)
        if sim:
            sim.shutdown()

    def _publish(self, channel: str, node: SerialNode) -> None:
        if self.eventbus:
            s = node.status
            self.eventbus.publish(events.NODE_STATE, {
                "channel": channel, "port": node.port, "state": node.state.value,
                "rx": s.rx_count, "tx": s.tx_count, "errors": s.err_count + node.crc_errors,
                "info": node.info})
