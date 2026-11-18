"""EventBus -> Qt signals. Engine threads publish; signals are delivered queued on the GUI thread.

CAN traffic is not forwarded one queued signal per batch: under heavy load (thousands of frames per second) the GUI
thread could fall behind and the queue of pending signals grows without bound - the UI then lags further and
further behind the bus. Instead the engine thread only appends to a buffer and a GUI timer drains it at a fixed
rate (FLUSH_MS). When the GUI is slow, the next drain simply takes everything that accumulated: one canBatch per
tick, never a backlog.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QTimer, Signal

from autosim.core.common import events
from autosim.core.common.events import EventBus

FLUSH_MS = 40                         # 25 UI updates per second: smooth, and cheap at any bus load


class QtBridge(QObject):
    canBatch = Signal(object)        # list[TraceRow], emitted on the GUI thread at most every FLUSH_MS
    log = Signal(str)
    nodeState = Signal(object)       # dict
    dbLoaded = Signal(str)
    dbUnloaded = Signal(str)

    def __init__(self, bus: EventBus, parent=None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._pending: list = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.flush)
        self._timer.start(FLUSH_MS)
        bus.subscribe(events.CAN_RX, self._on_can)
        bus.subscribe(events.LOG, self.log.emit)
        bus.subscribe(events.NODE_STATE, self.nodeState.emit)
        bus.subscribe(events.CAN_DB_LOADED, self.dbLoaded.emit)
        bus.subscribe(events.CAN_DB_UNLOADED, self.dbUnloaded.emit)

    def _on_can(self, batch) -> None:             # any thread
        with self._lock:
            self._pending.extend(batch)

    def flush(self) -> None:
        """GUI thread: hand everything received since the last tick to the panels as one batch."""
        with self._lock:
            if not self._pending:
                return
            batch, self._pending = self._pending, []
        self.canBatch.emit(batch)
