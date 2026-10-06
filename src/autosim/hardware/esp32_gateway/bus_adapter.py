"""Adapts SerialNode to core.can_engine.bus_manager.CanBackend."""
from __future__ import annotations

from autosim.core.can_engine.bus_manager import CanBackend
from autosim.core.common.frame import CanChannelConfig
from autosim.hardware.esp32_gateway.serial_node import SerialNode


class Esp32Backend(CanBackend):
    name = "esp32"

    def __init__(self, node: SerialNode) -> None:
        self.node = node

    def set_rx_callback(self, cb) -> None:
        self.node.on_rx = cb

    def open(self, cfg: CanChannelConfig) -> None:
        if self.node.state.value == "disconnected":
            self.node.connect()
        self.node.configure(cfg)
        self.node.start()

    def close(self) -> None:
        self.node.close()

    def send(self, frame) -> None:
        self.node.send_frame(frame)
