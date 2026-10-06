"""Frame queue -> trace rows (name, cycle time) -> batched EventBus publish."""
from __future__ import annotations

import queue
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from autosim.core.can_engine.bus_manager import BusManager
from autosim.core.can_engine.database import DatabaseRegistry
from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.core.common.frame import CanFrame


@dataclass(slots=True)
class TraceRow:
    frame: CanFrame
    name: str = ""
    cycle_ms: float | None = None


class RxDispatcher:
    """Consumes BusManager.frame_q on a worker thread, annotates frames and publishes
    list[TraceRow] batches on events.CAN_RX at most every `flush_s` (keeps the GUI cheap)."""

    def __init__(self, registry: DatabaseRegistry, bus: BusManager, eventbus: EventBus,
                 flush_s: float = 0.033) -> None:
        self.registry, self.bus, self.eventbus, self.flush_s = registry, bus, eventbus, flush_s
        self._last_ts: dict[tuple[str, int, bool], float] = {}
        self._watch: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=20_000))
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.rx_count = 0

    # ---- signal watching (plot) ------------------------------------------
    def watch(self, message: str, signal: str) -> deque:
        with self._lock:
            return self._watch[(message, signal)]

    def snapshot(self, message: str, signal: str) -> list[tuple[float, float]]:
        """Thread-safe copy of the samples collected for a watched signal."""
        with self._lock:
            return list(self._watch.get((message, signal), ()))

    def unwatch(self, message: str, signal: str) -> None:
        with self._lock:
            self._watch.pop((message, signal), None)

    def watched(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._watch)

    # ---- thread -----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="can-rx", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)
        self._thread = None

    def annotate(self, f: CanFrame) -> TraceRow:
        key = (f.channel, f.arbitration_id, f.is_rx)
        last = self._last_ts.get(key)
        self._last_ts[key] = f.timestamp
        cycle = (f.timestamp - last) * 1000.0 if last is not None else None
        decoded = self.registry.decode(f)
        name = decoded[0].name if decoded else ""
        if decoded and self._watch:
            with self._lock:
                for (mname, sname), buf in self._watch.items():
                    if mname == name and sname in decoded[1]:
                        buf.append((f.timestamp, float(decoded[1][sname])))
        return TraceRow(f, name, cycle)

    def _run(self) -> None:
        batch: list[TraceRow] = []
        deadline = time.monotonic() + self.flush_s
        while not self._stop.is_set():
            try:
                f = self.bus.frame_q.get(timeout=self.flush_s)
                batch.append(self.annotate(f))
                self.rx_count += 1
            except queue.Empty:
                pass
            if batch and time.monotonic() >= deadline:
                self.eventbus.publish(events.CAN_RX, batch)
                batch = []
            if time.monotonic() >= deadline:
                deadline = time.monotonic() + self.flush_s
        if batch:
            self.eventbus.publish(events.CAN_RX, batch)
