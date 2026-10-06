"""Single host time base (seconds since process start) shared by TX, RX and the trace."""
from __future__ import annotations

import time

_T0 = time.perf_counter()


def now() -> float:
    return time.perf_counter() - _T0
