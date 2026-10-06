"""AppContext: the services shared by the UI panels."""
from __future__ import annotations

from autosim.core.can_engine.engine import CanEngine
from autosim.core.common.config import ProjectConfig
from autosim.core.common.events import EventBus
from autosim.core.someip_engine.config_loader import ServiceDef
from autosim.core.someip_engine.engine import SomeIpEngine
from autosim.core.someip_engine.proto_loader import ProtoRegistry
from autosim.hardware.esp32_gateway.manager import NodeManager


class AppContext:
    def __init__(self, thread_factory=None) -> None:
        """thread_factory: runs the SOME/IP asyncio loop (the GUI passes a QThread-based one)."""
        self.events = EventBus()
        self.can = CanEngine(self.events)
        self.nodes = NodeManager(self.events)
        self.someip = SomeIpEngine(self.events, thread_factory) if thread_factory else SomeIpEngine(self.events)
        self.protos = ProtoRegistry()
        self.config = ProjectConfig()
        self.services: list[ServiceDef] = []     # service definitions known to the SOME/IP panel

    def start(self) -> None:
        self.can.start()

    def shutdown(self) -> None:
        self.someip.stop()
        self.can.shutdown()
        for ch in list(self.nodes.nodes):
            self.nodes.release(ch)
