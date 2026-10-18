"""Scan COM ports and manage the CAN nodes: ESP32 + MCP2518FD (AutoSim protocol), CANable 2.0 USB-CAN FD
adapters (slcan) and the built-in simulated nodes."""
from __future__ import annotations

from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.hardware.esp32_gateway.bus_adapter import Esp32Backend
from autosim.hardware.esp32_gateway.serial_node import SerialNode
from autosim.hardware.esp32_gateway.sim_node import SimNode
from autosim.hardware.slcan import SLCAN_USB_IDS, device_kind
from autosim.hardware.slcan.node import SlcanBackend, SlcanNode

SIM_PREFIX = "SIM"


class NodeManager:
    def __init__(self, eventbus: EventBus | None = None) -> None:
        self.eventbus = eventbus
        self.nodes: dict[str, SerialNode] = {}
        self._sims: dict[str, SimNode] = {}

    def scan(self) -> list[dict]:
        """List serial ports with the hardware kind recognised from the USB VID:PID:
        'slcan' (CANable 2.0 USB-CAN FD adapter) or 'autosim' (anything else: ESP32 + AutoSim firmware)."""
        from serial.tools import list_ports
        out = []
        for p in sorted(list_ports.comports(), key=lambda p: p.device):
            kind = device_kind(p.vid, p.pid)
            label = SLCAN_USB_IDS.get((p.vid, p.pid), p.description)
            out.append({"port": p.device, "description": p.description, "hwid": p.hwid, "kind": kind,
                        "label": label, "serial": p.serial_number or ""})
        return out

    def kind_of(self, port: str) -> tuple[str, str]:
        """(kind, usb serial number) of a port; ('sim', '') for the built-in simulated nodes."""
        if port.upper().startswith(SIM_PREFIX):
            return "sim", ""
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
        serial_no = ""
        if kind is None:
            kind, serial_no = ("autosim", "") if transport_factory is not None else self.kind_of(port)
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
        node.on_state = lambda st, ch=channel: self._publish(ch, node)
        if self.eventbus:                         # firmware ERROR messages (bad config, MCP fault ...) go to the log
            node.on_error = lambda code, text, ch=channel: self.eventbus.publish(
                events.LOG, f"[{ch}] node error {code}: {text}")
        node.on_status = lambda st, ch=channel: self._publish(ch, node)
        self.nodes[channel] = node
        return backend if kind == "slcan" else Esp32Backend(node)

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
