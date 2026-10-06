"""Signal value generators: constant, sine, ramp, toggle, square."""
from __future__ import annotations

import math
from abc import ABC, abstractmethod


class Generator(ABC):
    kind = "base"

    @abstractmethod
    def value(self, t: float) -> float:
        """Value at time t seconds since the generator started."""


class Constant(Generator):
    kind = "constant"

    def __init__(self, v: float = 0.0):
        self.v = v

    def value(self, t: float) -> float:
        return self.v


class Sine(Generator):
    kind = "sine"

    def __init__(self, amplitude: float, freq_hz: float, offset: float = 0.0, phase: float = 0.0):
        self.amplitude, self.freq_hz, self.offset, self.phase = amplitude, freq_hz, offset, phase

    def value(self, t: float) -> float:
        return self.offset + self.amplitude * math.sin(2 * math.pi * self.freq_hz * t + self.phase)


class Ramp(Generator):
    """saw=True: lo->hi then jump back; saw=False: triangle lo->hi->lo."""
    kind = "ramp"

    def __init__(self, lo: float, hi: float, period_s: float, saw: bool = True):
        self.lo, self.hi, self.period_s, self.saw = lo, hi, max(period_s, 1e-6), saw

    def value(self, t: float) -> float:
        ph = (t / self.period_s) % 1.0
        if not self.saw:
            ph = 2 * ph if ph < 0.5 else 2 * (1 - ph)
        return self.lo + (self.hi - self.lo) * ph


class Square(Generator):
    kind = "square"

    def __init__(self, lo: float, hi: float, freq_hz: float, duty: float = 0.5):
        self.lo, self.hi, self.freq_hz, self.duty = lo, hi, max(freq_hz, 1e-9), duty

    def value(self, t: float) -> float:
        return self.hi if (t * self.freq_hz) % 1.0 < self.duty else self.lo


class Toggle(Generator):
    """Cycles through `values`, advancing every `interval_s`."""
    kind = "toggle"

    def __init__(self, values: list[float], interval_s: float):
        self.values, self.interval_s = list(values) or [0.0], max(interval_s, 1e-6)

    def value(self, t: float) -> float:
        return self.values[int(t / self.interval_s) % len(self.values)]


# (label, factory taking p1,p2,p3) used by the UI; parameter meaning is in PARAM_LABELS.
PARAM_LABELS = {
    "sine": ("Amplitude", "Freq Hz", "Offset"),
    "ramp": ("Low", "High", "Period s"),
    "square": ("Low", "High", "Freq Hz"),
    "toggle": ("Value A", "Value B", "Interval s"),
}


def make_generator(kind: str, p1: float, p2: float, p3: float) -> Generator | None:
    if kind == "sine":
        return Sine(p1, p2, p3)
    if kind == "ramp":
        return Ramp(p1, p2, p3)
    if kind == "square":
        return Square(p1, p2, p3)
    if kind == "toggle":
        return Toggle([p1, p2], p3)
    return None
