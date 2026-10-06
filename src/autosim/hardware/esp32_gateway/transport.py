"""Byte transports under SerialNode: real serial port and an in-memory duplex pipe."""
from __future__ import annotations

import queue
from typing import Protocol


class Transport(Protocol):
    def read_some(self) -> bytes:
        """Return available bytes, or b'' after a short timeout."""
    def write(self, data: bytes) -> None: ...
    def close(self) -> None: ...


import os

# 921600 matches the firmware default (AUTOSIM_UART_BAUD). Native-USB boards (ESP32-S3/C3) ignore it.
# Override for faster USB-UART bridges with AUTOSIM_BAUD (and the same value in the firmware build).
DEFAULT_BAUD = int(os.environ.get("AUTOSIM_BAUD", "921600"))


class SerialTransport:
    def __init__(self, port: str, baud: int = DEFAULT_BAUD) -> None:
        import serial
        self._s = serial.serial_for_url(port, baudrate=baud, timeout=0.05, write_timeout=1.0)

    def read_some(self) -> bytes:
        return self._s.read(self._s.in_waiting or 1)

    def write(self, data: bytes) -> None:
        self._s.write(data)

    def close(self) -> None:
        self._s.close()


class PipeEnd:
    """One end of an in-memory duplex byte pipe (used by the simulated node and tests)."""

    def __init__(self, rx: queue.Queue, tx: queue.Queue) -> None:
        self._rx, self._tx, self.closed = rx, tx, False

    def read_some(self) -> bytes:
        try:
            return self._rx.get(timeout=0.05)
        except queue.Empty:
            return b""

    def write(self, data: bytes) -> None:
        if self.closed:
            raise OSError("pipe closed")
        self._tx.put(bytes(data))

    def close(self) -> None:
        self.closed = True


def make_pipe() -> tuple[PipeEnd, PipeEnd]:
    a, b = queue.Queue(), queue.Queue()
    return PipeEnd(a, b), PipeEnd(b, a)
