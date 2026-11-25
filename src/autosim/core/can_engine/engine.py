"""CanEngine facade used by the UI."""
from __future__ import annotations

from pathlib import Path

from autosim.core.can_engine.auto_response import AutoResponder
from autosim.core.can_engine.bus_manager import BusManager, CanBackend
from autosim.core.can_engine.database import DatabaseRegistry
from autosim.core.can_engine.generators import Generator
from autosim.core.can_engine.rx_dispatcher import RxDispatcher
from autosim.core.can_engine.tx_scheduler import TxJob, TxScheduler
from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.core.common.frame import CanChannelConfig


class CanEngine:
    """Owns DatabaseRegistry, BusManager, TxScheduler, RxDispatcher and the AutoResponder."""

    def __init__(self, eventbus: EventBus) -> None:
        self.eventbus = eventbus
        self.registry = DatabaseRegistry()
        self.bus = BusManager()
        self.tx = TxScheduler(self.bus.send, on_error=self._tx_error)
        self.rx = RxDispatcher(self.registry, self.bus, eventbus)
        self.responder = AutoResponder(self.registry, self.bus.send,
                                       log=lambda text: eventbus.publish(events.LOG, text))
        self.bus.add_rx_listener(self.responder.on_frame)

    # ---- measurement (CANoe style Start / Stop) -----------------------------------------------------------------
    @property
    def measuring(self) -> bool:
        return self.tx.active

    def set_measuring(self, on: bool) -> None:
        """Running: cyclic / event sends, auto response and the trace work. Stopped: nothing is sent, answered or
        logged; channels stay connected and cyclic messages switched on stay armed for the next start.
        The engine starts running (web mode); the desktop app starts it stopped, like CANoe."""
        on = bool(on)
        self.tx.set_active(on)
        self.responder.active = on
        self.rx.active = on
        self.eventbus.publish(events.MEASUREMENT, on)

    def start(self) -> None:
        self.tx.start_thread()
        self.rx.start()
        self.responder.start()

    def shutdown(self) -> None:
        for j in self.tx.jobs():
            self.tx.stop(j)
        self.tx.stop_thread()
        self.responder.stop()
        self.bus.close_all()
        self.rx.stop()

    def _tx_error(self, job: TxJob, e: Exception) -> None:
        self.tx.stop(job)
        job.failed = True                                 # stopped by an error (the UI may resume it later)
        self.eventbus.publish(events.LOG, f"TX error on {job.channel}/{job.name}: {e} (job stopped)")
        self.eventbus.publish(events.TX_ERROR, {"channel": job.channel, "message": job.name, "error": str(e)})

    # ---- database ---------------------------------------------------------
    def load_database(self, path: str | Path, channel: str = "*"):
        db = self.registry.load(path, channel)
        for w in self.registry.warnings:
            self.eventbus.publish(events.LOG, f"Database warning: {w}")
        self.eventbus.publish(events.CAN_DB_LOADED, str(path))
        return db

    def unload_database(self, path: str | Path) -> list:
        """Remove a loaded database file. TX jobs of its messages are stopped and removed, plot watches of
        messages no database defines any more are dropped. Returns the removed TX jobs; KeyError if the file is
        not loaded."""
        db = self.registry.unload(path)
        if db is None:
            raise KeyError(f"{path} is not loaded")
        removed = [j for j in self.tx.jobs() if any(j.message is m for m in db.messages)]
        for j in removed:
            self.tx.remove_job(j)
        still = {m.name: m for m in self.registry.messages()}
        for mname, sname in self.rx.watched():
            m = still.get(mname)
            if m is None or all(s.name != sname for s in m.signals):
                self.rx.unwatch(mname, sname)
        self.eventbus.publish(events.LOG, f"Database removed: {Path(path).name}"
                              + (f" ({len(removed)} TX message(s) removed)" if removed else ""))
        self.eventbus.publish(events.CAN_DB_UNLOADED, str(path))
        return removed

    # ---- channels ---------------------------------------------------------
    def open_channel(self, name: str, backend: CanBackend, cfg: CanChannelConfig) -> None:
        self.bus.add_channel(name, backend, cfg)
        self.eventbus.publish(events.LOG, f"Channel {name} opened ({backend.name})")

    def close_channel(self, name: str) -> None:
        for j in self.tx.jobs():
            if j.channel == name:
                self.tx.remove_job(j)
        self.bus.remove_channel(name)
        self.eventbus.publish(events.LOG, f"Channel {name} closed")

    # ---- TX ----------------------------------------------------------------
    def add_tx_message(self, channel: str, message_name: str, cycle_ms: int | None = None) -> TxJob:
        msg = self.registry.message_by_name(message_name)
        if msg is None:
            raise KeyError(f"message {message_name!r} not in any loaded database")
        if cycle_ms is None and msg.cycle_time:
            cycle_ms = int(msg.cycle_time)
        return self.tx.add_job(channel, msg, cycle_ms)

    def set_signal(self, job: TxJob, signal: str, value: float) -> float:
        return job.state.set_signal(signal, value)

    def bind_generator(self, job: TxJob, signal: str, gen: Generator | None) -> None:
        self.tx.bind_generator(job, signal, gen)
