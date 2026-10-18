"""AppContext: the services shared by the UI panels."""
from __future__ import annotations

from autosim.core.can_engine.engine import CanEngine
from autosim.core.common.config import ProjectConfig
from autosim.core.common.events import EventBus
from autosim.hardware.esp32_gateway.manager import NodeManager


class AppContext:
    def __init__(self) -> None:
        self.events = EventBus()
        self.can = CanEngine(self.events)
        self.nodes = NodeManager(self.events)
        self.config = ProjectConfig()

    def start(self) -> None:
        self.can.start()

    def shutdown(self) -> None:
        self.can.shutdown()
        for ch in list(self.nodes.nodes):
            self.nodes.release(ch)
