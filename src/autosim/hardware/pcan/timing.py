"""Bit timing for PCAN adapters from bit rates and sample points.

Classic CAN (PCAN-Basic `Initialize`): SJA1000-style BTR0/BTR1 registers on an 8 MHz clock.
CAN FD (`InitializeFD`): an 80 MHz clock with separate nominal and data prescalers / segments.

The search prefers the smallest prescaler (most time quanta per bit, finest sample point) whose sample point is
within 2.5 % of the target, and uses the same prescaler for both phases when it can (required by many FD
controllers for transmitter delay compensation). SJW = phase segment 2 (within the controller's limit), as for the
ESP32 node.
"""
from __future__ import annotations

from dataclasses import dataclass

import can

CLASSIC_CLOCK = 8_000_000
FD_CLOCK = 80_000_000


@dataclass(frozen=True)
class _Limits:
    brp: int
    tseg1: int
    tseg2: int
    sjw: int
    min_tq: int = 8


CLASSIC = _Limits(brp=64, tseg1=16, tseg2=8, sjw=4)
FD_NOMINAL = _Limits(brp=1024, tseg1=256, tseg2=128, sjw=128)
FD_DATA = _Limits(brp=1024, tseg1=32, tseg2=16, sjw=16, min_tq=5)


class TimingError(ValueError):
    pass


def _candidates(clock: int, bitrate: int, sp: float, lim: _Limits):
    """(brp, tseg1, tseg2, sjw, sample point error) for every prescaler that gives an integer number of quanta."""
    for brp in range(1, lim.brp + 1):
        if clock % (brp * bitrate):
            continue
        tq = clock // (brp * bitrate)
        if tq < lim.min_tq or tq > 1 + lim.tseg1 + lim.tseg2:
            continue
        tseg2 = max(1, min(lim.tseg2, round(tq * (1 - sp))))
        tseg1 = tq - 1 - tseg2
        if not 1 <= tseg1 <= lim.tseg1:
            continue
        yield brp, tseg1, tseg2, min(tseg2, lim.sjw), abs((1 + tseg1) / tq - sp)


def _pick(cands, tolerance: float = 0.025):
    cands = list(cands)
    if not cands:
        return None
    good = [c for c in cands if c[4] <= tolerance]
    return min(good, key=lambda c: c[0]) if good else min(cands, key=lambda c: (c[4], c[0]))


def classic_timing(bitrate: int, sample_point: float = 0.8) -> can.BitTiming:
    c = _pick(_candidates(CLASSIC_CLOCK, bitrate, sample_point, CLASSIC))
    if c is None:
        raise TimingError(f"{bitrate} bit/s cannot be set on a PCAN adapter")
    brp, t1, t2, sjw, _ = c
    return can.BitTiming(f_clock=CLASSIC_CLOCK, brp=brp, tseg1=t1, tseg2=t2, sjw=sjw)


def fd_timing(nominal: int, data: int, sp_nominal: float = 0.8, sp_data: float = 0.75) -> can.BitTimingFd:
    noms = list(_candidates(FD_CLOCK, nominal, sp_nominal, FD_NOMINAL))
    dats = list(_candidates(FD_CLOCK, data, sp_data, FD_DATA))
    if not noms:
        raise TimingError(f"nominal rate {nominal} bit/s cannot be set on a PCAN FD adapter")
    if not dats:
        raise TimingError(f"data rate {data} bit/s cannot be set on a PCAN FD adapter (80 MHz clock)")
    d = _pick(dats)
    same = [n for n in noms if n[0] == d[0]]                    # same prescaler in both phases when possible
    n = _pick(same) if same and _pick(same)[4] <= 0.025 else _pick(noms)
    return can.BitTimingFd(f_clock=FD_CLOCK,
                           nom_brp=n[0], nom_tseg1=n[1], nom_tseg2=n[2], nom_sjw=n[3],
                           data_brp=d[0], data_tseg1=d[1], data_tseg2=d[2], data_sjw=d[3])


def describe(t) -> str:
    """Short text for the log, e.g. '500 kbit/s @ 80.0 % / 2 Mbit/s @ 75.0 %'."""
    def r(v):
        return f"{v / 1e6:g} Mbit/s" if v >= 1_000_000 else f"{v / 1e3:g} kbit/s"
    if isinstance(t, can.BitTimingFd):
        return (f"{r(t.nom_bitrate)} @ {t.nom_sample_point:.1f} % / {r(t.data_bitrate)} @ "
                f"{t.data_sample_point:.1f} %")
    return f"{r(t.bitrate)} @ {t.sample_point:.1f} %"
