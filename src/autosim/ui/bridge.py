"""EventBus -> Qt signals. Engine threads publish; signals are delivered queued on the GUI thread."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from autosim.core.common import events
from autosim.core.common.events import EventBus


class QtBridge(QObject):
    canBatch = Signal(object)        # list[TraceRow]
    log = Signal(str)
    nodeState = Signal(object)       # dict
    dbLoaded = Signal(str)
    sd = Signal(object)              # dict
    someipEvent = Signal(object)     # dict
    someipMethod = Signal(object)    # dict

    def __init__(self, bus: EventBus, parent=None) -> None:
        super().__init__(parent)
        bus.subscribe(events.CAN_RX, self.canBatch.emit)
        bus.subscribe(events.LOG, self.log.emit)
        bus.subscribe(events.NODE_STATE, self.nodeState.emit)
        bus.subscribe(events.CAN_DB_LOADED, self.dbLoaded.emit)
        bus.subscribe(events.SOMEIP_SD, self.sd.emit)
        bus.subscribe(events.SOMEIP_EVENT, self.someipEvent.emit)
        bus.subscribe(events.SOMEIP_METHOD, self.someipMethod.emit)
