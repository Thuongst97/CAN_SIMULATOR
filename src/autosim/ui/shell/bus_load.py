"""Bus-load estimate per channel from the frames seen in the trace (the adapters do not report bus load).

Each frame's time on the bus is computed from its length, format and the channel's bit rates (ISO CAN FD /
classic CAN frame layout). Dynamic stuff bits are not known from the payload alone, so they are estimated as
one per ten stuffable bits; the result is labelled "est." in the UI.

Frame layout used (bits):
  classic, 11-bit id : 47 + 8*n                  (SOF..IFS, incl. 15-bit CRC)
  classic, 29-bit id : 67 + 8*n
  FD, nominal phase  : 17 (11-bit) / 36 (29-bit)  SOF..BRS
                       + 13 after the data phase  CRC delimiter, ACK, ACK delimiter, EOF 7, IFS 3
  FD, data phase     : ESI 1 + DLC 4 + 8*n + stuff count 4 + CRC 17 (n <= 16) / 21 + fixed stuff bits (CRC/4)
                       at the data rate when BRS is set, else at the nominal rate.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import Callable

DYNAMIC_STUFF = 0.10


def frame_seconds(length: int, fd: bool, brs: bool, extended: bool, nominal: int, data: int) -> float:
    """Time one frame occupies the bus, in seconds."""
    if not fd:
        base = (67 if extended else 47) + 8 * length
        stuffable = base - 13                                   # CRC delimiter .. IFS are never stuffed
        return (base + stuffable * DYNAMIC_STUFF) / nominal
    arb = 36 if extended else 17
    crc = 17 if length <= 16 else 21
    data_bits = 1 + 4 + 8 * length + 4 + crc + math.ceil(crc / 4)
    stuff = (arb + 1 + 4 + 8 * length) * DYNAMIC_STUFF          # dynamic stuffing up to the stuff count
    tail = 13
    rate = data if brs else nominal
    return (arb + stuff + tail) / nominal + data_bits / rate


class BusLoadMeter:
    """Accumulates frame time per channel; `snapshot()` returns {channel: load %} since the previous call."""

    def __init__(self, rates: Callable[[str], tuple[int, int] | None], clock=time.monotonic) -> None:
        self.rates = rates
        self.clock = clock
        self._busy: dict[str, float] = defaultdict(float)
        self._frames: dict[str, int] = defaultdict(int)
        self._t0 = clock()

    def add(self, frame) -> None:
        r = self.rates(frame.channel)
        if r is None:
            return
        self._busy[frame.channel] += frame_seconds(len(frame.data), frame.is_fd, frame.bitrate_switch,
                                                   frame.is_extended, r[0], r[1])
        self._frames[frame.channel] += 1

    def add_rows(self, rows) -> None:
        for r in rows:
            self.add(r.frame)

    def snapshot(self) -> dict[str, tuple[float, float]]:
        """{channel: (load_percent, frames_per_second)} over the interval since the last snapshot."""
        now = self.clock()
        dt = max(now - self._t0, 1e-6)
        out = {ch: (min(100.0, 100.0 * busy / dt), self._frames[ch] / dt) for ch, busy in self._busy.items()}
        self._busy.clear()
        self._frames.clear()
        self._t0 = now
        return out
