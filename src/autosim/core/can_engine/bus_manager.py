"""Multi-channel abstraction over python-can buses and ESP32 nodes."""
from __future__ import annotations

import queue
import threading
from abc import ABC, abstractmethod
from typing import Callable

import can

from autosim.core.common import clock
from autosim.core.common.frame import CanChannelConfig, CanFrame


class CanBackend(ABC):
    """Uniform backend. Implementations: Esp32Backend (hardware/esp32_gateway) and
    PythonCanBackend (any python-can interface; 'virtual' is used for development/tests)."""

    name: str = "backend"

    @abstractmethod
    def open(self, cfg: CanChannelConfig) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def send(self, frame: CanFrame) -> None: ...

    @abstractmethod
    def set_rx_callback(self, cb: Callable[[CanFrame], None]) -> None: ...


class PythonCanBackend(CanBackend):
    name = "python-can"

    def __init__(self, interface: str = "virtual", channel: str = "autosim", **kwargs) -> None:
        self.interface, self.channel_id, self.kwargs = interface, channel, kwargs
        self._bus: can.BusABC | None = None
        self._cb: Callable[[CanFrame], None] = lambda f: None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def set_rx_callback(self, cb) -> None:
        self._cb = cb

    def open(self, cfg: CanChannelConfig) -> None:
        self._bus = can.Bus(interface=self.interface, channel=self.channel_id,
                            bitrate=cfg.nominal_bitrate, fd=cfg.fd_enabled,
                            data_bitrate=cfg.data_bitrate, **self.kwargs)
        self._stop.clear()
        self._thread = threading.Thread(target=self._rx_loop, name="pycan-rx", daemon=True)
        self._thread.start()

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            msg = self._bus.recv(timeout=0.05)
            if msg is None or msg.is_error_frame:
                continue
            self._cb(CanFrame(msg.arbitration_id, bytes(msg.data), msg.is_extended_id,
                              bool(msg.is_fd), bool(msg.bitrate_switch), clock.now(), "", True))

    def send(self, frame: CanFrame) -> None:
        if self._bus is None:
            raise RuntimeError("channel not open")
        self._bus.send(can.Message(arbitration_id=frame.arbitration_id, data=frame.data,
                                   is_extended_id=frame.is_extended, is_fd=frame.is_fd,
                                   bitrate_switch=frame.bitrate_switch))

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(1)
        if self._bus:
            self._bus.shutdown()
        self._bus = None


class BusManager:
    """Registry channel_name -> CanBackend. Routes send(); RX and TX frames both go to
    `frame_q` (consumed by RxDispatcher) so the trace shows both directions."""

    def __init__(self) -> None:
        self._channels: dict[str, CanBackend] = {}
        self.frame_q: queue.Queue[CanFrame] = queue.Queue(maxsize=200_000)
        self._lock = threading.Lock()

    @property
    def channels(self) -> list[str]:
        return list(self._channels)

    def add_channel(self, name: str, backend: CanBackend, cfg: CanChannelConfig | None = None) -> None:
        with self._lock:
            if name in self._channels:
                raise ValueError(f"channel {name!r} already exists")
            backend.set_rx_callback(lambda f, n=name: self._on_rx(n, f))
            if cfg is not None:
                backend.open(cfg)
            self._channels[name] = backend

    def remove_channel(self, name: str) -> None:
        with self._lock:
            backend = self._channels.pop(name, None)
        if backend:
            backend.close()

    def close_all(self) -> None:
        for n in list(self._channels):
            self.remove_channel(n)

    def _on_rx(self, channel: str, frame: CanFrame) -> None:
        frame.channel, frame.is_rx = channel, True
        if not frame.timestamp:
            frame.timestamp = clock.now()
        try:
            self.frame_q.put_nowait(frame)
        except queue.Full:
            pass  # drop under overload rather than block the hardware reader

    def send(self, channel: str, frame: CanFrame) -> None:
        backend = self._channels.get(channel)
        if backend is None:
            raise KeyError(f"unknown channel {channel!r}")
        backend.send(frame)
        frame.channel, frame.is_rx, frame.timestamp = channel, False, clock.now()
        try:
            self.frame_q.put_nowait(frame)
        except queue.Full:
            pass
