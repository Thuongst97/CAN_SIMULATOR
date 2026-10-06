"""Scan COM ports and manage multiple SerialNode instances."""
from __future__ import annotations

from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.hardware.esp32_gateway.bus_adapter import Esp32Backend
from autosim.hardware.esp32_gateway.serial_node import SerialNode
from autosim.hardware.esp32_gateway.sim_node import SimNode

SIM_PREFIX = "SIM"


class NodeManager:
    def __init__(self, eventbus: EventBus | None = None) -> None:
        self.eventbus = eventbus
        self.nodes: dict[str, SerialNode] = {}
        self._sims: dict[str, SimNode] = {}

    def scan(self) -> list[dict]:
        """List serial ports (no probing; use probe() to identify a node)."""
        from serial.tools import list_ports
        return [{"port": p.device, "description": p.description, "hwid": p.hwid}
                for p in sorted(list_ports.comports(), key=lambda p: p.device)]

    def probe(self, port: str) -> dict | None:
        """Open the port briefly and send HELLO; returns node info or None if it does not answer."""
        node = SerialNode(port)
        try:
            return node.connect()
        except Exception:
            return None
        finally:
            node.close()

    def create_backend(self, port: str, channel: str, transport_factory=None) -> Esp32Backend:
        """Build (but do not open) a backend. Ports named 'SIM…' use the built-in simulated node;
        `transport_factory` lets tests plug in any byte transport (e.g. the firmware simulator)."""
        if channel in self.nodes:
            raise ValueError(f"channel {channel!r} already in use")
        if transport_factory is not None:
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
        return Esp32Backend(node)

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
