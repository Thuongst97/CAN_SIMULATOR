"""Thread-safe pub/sub bridge between engine threads and the Qt UI.

Engines publish plain Python callbacks; the UI layer wraps them into Qt signals
(see ui/models) so no engine module imports PySide6.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Callable

Handler = Callable[[Any], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, topic: str, handler: Handler) -> None:
        with self._lock:
            self._subs[topic].append(handler)

    def publish(self, topic: str, payload: Any = None) -> None:
        with self._lock:
            handlers = list(self._subs[topic])
        for h in handlers:
            h(payload)


# Topic names
CAN_RX = "can.rx"
CAN_TX = "can.tx"
CAN_DB_LOADED = "can.db_loaded"
NODE_STATE = "hw.node_state"
SOMEIP_EVENT = "someip.event"
SOMEIP_SD = "someip.sd"
SOMEIP_RESPONSE = "someip.response"
SOMEIP_METHOD = "someip.method"
LOG = "log"
