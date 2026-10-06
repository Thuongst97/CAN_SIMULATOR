"""Trace export: CSV, ASC, BLF (python-can writers)."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

import can

from autosim.core.can_engine.rx_dispatcher import TraceRow

CSV_HEADER = ["Time [s]", "Channel", "Dir", "ID", "Name", "FD", "DLC", "Data", "Cycle [ms]"]


def _rows(rows: Iterable[TraceRow]) -> list[TraceRow]:
    return list(rows)


def export_csv(rows: Iterable[TraceRow], path: str | Path) -> int:
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_HEADER)
        for r in rows:
            f = r.frame
            idtxt = f"0x{f.arbitration_id:08X}" if f.is_extended else f"0x{f.arbitration_id:03X}"
            w.writerow([f"{f.timestamp:.6f}", f.channel, "Rx" if f.is_rx else "Tx", idtxt, r.name,
                        int(f.is_fd), len(f.data), bytes(f.data).hex(" ").upper(),
                        "" if r.cycle_ms is None else f"{r.cycle_ms:.3f}"])
            n += 1
    return n


def _to_messages(rows: Iterable[TraceRow]):
    chan_ids: dict[str, int] = {}
    for r in rows:
        f = r.frame
        ch = chan_ids.setdefault(f.channel, len(chan_ids) + 1)
        m = can.Message(timestamp=f.timestamp, arbitration_id=f.arbitration_id, data=f.data,
                        is_extended_id=f.is_extended, is_fd=f.is_fd,
                        bitrate_switch=f.bitrate_switch, is_rx=f.is_rx, channel=ch - 1)
        yield m


def _export(writer_cls, rows, path) -> int:
    n = 0
    w = writer_cls(str(path))
    try:
        for m in _to_messages(rows):
            w.on_message_received(m)
            n += 1
    finally:
        w.stop()
    return n


def export_asc(rows: Iterable[TraceRow], path: str | Path) -> int:
    return _export(can.ASCWriter, rows, path)


def export_blf(rows: Iterable[TraceRow], path: str | Path) -> int:
    return _export(can.BLFWriter, rows, path)


def export_by_extension(rows: Iterable[TraceRow], path: str | Path) -> int:
    ext = Path(path).suffix.lower()
    fn = {".csv": export_csv, ".asc": export_asc, ".blf": export_blf}.get(ext)
    if fn is None:
        raise ValueError(f"unsupported export format {ext!r} (use .csv, .asc or .blf)")
    return fn(rows, path)
