"""Periodic / on-event TX scheduling on a single high-resolution thread."""
from __future__ import annotations

import heapq
import itertools
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from autosim.core.can_engine.generators import Generator
from autosim.core.can_engine.signal_codec import MessageState
from autosim.core.common.frame import CanFrame


@dataclass(eq=False)
class TxJob:
    channel: str
    message: object                      # cantools Message
    state: MessageState
    cycle_ms: int | None                 # None => event-only
    fd: bool = False
    brs: bool = False
    enabled: bool = False
    count: int = 0
    bindings: dict[str, Generator] = field(default_factory=dict)
    _gen: int = 0
    _next: float = 0.0
    _t0: float = 0.0

    @property
    def name(self) -> str:
        return self.message.name


class TxScheduler:
    """One scheduler thread (heap of next-fire times) for all messages.

    Sleeps on a condition until ~1.5 ms before the deadline, then spins; next fire time
    advances by cycle (not now+cycle) so there is no cumulative drift. If the thread falls
    more than one cycle behind, the schedule is re-based rather than bursting.
    """

    def __init__(self, send: Callable[[str, CanFrame], None],
                 on_error: Callable[[TxJob, Exception], None] | None = None) -> None:
        self._send = send
        self._on_error = on_error or (lambda job, e: None)
        self._cv = threading.Condition()
        self._heap: list[tuple[float, int, TxJob, int]] = []
        self._seq = itertools.count()
        self._jobs: list[TxJob] = []
        self._thread: threading.Thread | None = None
        self._running = False

    # ---- job management -------------------------------------------------
    def add_job(self, channel: str, message, cycle_ms: int | None = None,
                fd: bool | None = None, brs: bool | None = None) -> TxJob:
        if fd is None:
            fd = bool(message.is_fd) or message.length > 8
        job = TxJob(channel, message, MessageState(message), cycle_ms,
                    fd=fd, brs=fd if brs is None else brs)
        with self._cv:
            self._jobs.append(job)
        return job

    def jobs(self) -> list[TxJob]:
        return list(self._jobs)

    def remove_job(self, job: TxJob) -> None:
        self.stop(job)
        with self._cv:
            if job in self._jobs:
                self._jobs.remove(job)

    def set_cycle(self, job: TxJob, cycle_ms: int | None) -> None:
        with self._cv:
            job.cycle_ms = cycle_ms
            if job.enabled:
                self._schedule(job, time.perf_counter())

    def start(self, job: TxJob) -> None:
        if not job.cycle_ms:
            return
        with self._cv:
            job.enabled = True
            job._t0 = time.perf_counter()
            self._schedule(job, job._t0)

    def stop(self, job: TxJob) -> None:
        with self._cv:
            job.enabled = False
            job._gen += 1
            self._cv.notify()

    def trigger(self, job: TxJob) -> None:
        """Event-driven send, on the caller's thread."""
        self._emit(job)

    def bind_generator(self, job: TxJob, signal: str, gen: Generator | None) -> None:
        job.state.signal(signal)  # validates the name
        with self._cv:
            if gen is None:
                job.bindings.pop(signal, None)
            else:
                job.bindings[signal] = gen
                job._t0 = job._t0 or time.perf_counter()

    # ---- thread ----------------------------------------------------------
    def start_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.winmm.timeBeginPeriod(1)
            except Exception:
                pass
        self._running = True
        self._thread = threading.Thread(target=self._run, name="can-tx", daemon=True)
        self._thread.start()

    def stop_thread(self) -> None:
        with self._cv:
            self._running = False
            self._cv.notify()
        if self._thread:
            self._thread.join(2)
        self._thread = None
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass

    def _schedule(self, job: TxJob, when: float) -> None:
        job._gen += 1
        job._next = when
        heapq.heappush(self._heap, (when, next(self._seq), job, job._gen))
        self._cv.notify()

    def _run(self) -> None:
        pc = time.perf_counter
        while True:
            with self._cv:
                if not self._running:
                    return
                while self._heap and self._heap[0][3] != self._heap[0][2]._gen:
                    heapq.heappop(self._heap)          # stale entry
                if not self._heap:
                    self._cv.wait(0.5)
                    continue
                entry = self._heap[0]
                delay = entry[0] - pc()
                if delay > 0.002:
                    self._cv.wait(delay - 0.0015)
                    continue
            while pc() < entry[0]:                      # final approach: spin
                time.sleep(0)
            job = entry[2]
            with self._cv:
                if not self._heap or self._heap[0] is not entry:
                    continue                            # schedule changed meanwhile
                heapq.heappop(self._heap)
                if not job.enabled or not job.cycle_ms:
                    continue
                period = job.cycle_ms / 1000.0
                nxt = entry[0] + period
                if nxt < pc() - period:
                    nxt = pc() + period                 # fell behind: re-base
                gen = job._gen
                job._next = nxt
                heapq.heappush(self._heap, (nxt, next(self._seq), job, gen))
            self._emit(job)

    def _emit(self, job: TxJob) -> None:
        try:
            if job.bindings:
                t = time.perf_counter() - job._t0
                for sig, gen in list(job.bindings.items()):
                    job.state.set_signal(sig, gen.value(t))
            m = job.message
            frame = CanFrame(m.frame_id, job.state.encode(), m.is_extended_frame,
                             job.fd, job.fd and job.brs)
            self._send(job.channel, frame)
            job.count += 1
        except Exception as e:
            self._on_error(job, e)
