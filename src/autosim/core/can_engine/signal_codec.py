"""Signal <-> raw payload encode/decode wrapper with per-message state."""
from __future__ import annotations

import threading


class MessageState:
    """Current physical value of every signal in one message; encodes to bytes on demand."""

    def __init__(self, message) -> None:
        self.message = message
        self._lock = threading.RLock()
        self.values: dict[str, float] = {}
        for s in message.signals:
            init = s.initial if s.initial is not None else 0
            try:
                init = float(init)
            except (TypeError, ValueError):
                init = 0.0
            self.values[s.name] = self._clamp(s, init)

    @staticmethod
    def _clamp(sig, value: float) -> float:
        if sig.minimum is not None:
            value = max(value, sig.minimum)
        if sig.maximum is not None:
            value = min(value, sig.maximum)
        return value

    def signal(self, name: str):
        for s in self.message.signals:
            if s.name == name:
                return s
        raise KeyError(f"{self.message.name} has no signal {name!r}")

    def set_signal(self, name: str, value: float) -> float:
        """Clamp to min/max and store; returns the stored value."""
        v = self._clamp(self.signal(name), float(value))
        with self._lock:
            self.values[name] = v
        return v

    def get_signal(self, name: str) -> float:
        with self._lock:
            return self.values[name]

    def _encodable(self) -> dict[str, float]:
        vals = dict(self.values)
        m = self.message
        if not m.is_multiplexed():
            return vals
        out = {}
        for s in m.signals:
            if s.is_multiplexer or s.multiplexer_ids is None:
                out[s.name] = vals[s.name]
            elif s.multiplexer_signal in vals and int(vals[s.multiplexer_signal]) in s.multiplexer_ids:
                out[s.name] = vals[s.name]
        return out

    def encode(self) -> bytes:
        with self._lock:
            data = self._encodable()
        return bytes(self.message.encode(data, scaling=True, padding=True, strict=False))

    def decode(self, data: bytes) -> dict[str, float]:
        if len(data) < self.message.length:
            data = bytes(data) + bytes(self.message.length - len(data))
        return self.message.decode(bytes(data), decode_choices=False, scaling=True,
                                   allow_truncated=True, allow_excess=True)
