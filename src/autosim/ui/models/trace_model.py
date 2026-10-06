"""Trace table model: ring buffer + live filter, batched inserts."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from autosim.core.can_engine.rx_dispatcher import TraceRow
from autosim.core.common import clock
from autosim.ui.theme import accent, data_color, id_color, mono_font, tx_color


@dataclass
class TraceFilter:
    ids: set[int] = field(default_factory=set)       # empty = any
    name: str = ""
    channel: str = ""
    direction: str = ""                              # "", "Rx", "Tx"

    def match(self, r: TraceRow) -> bool:
        f = r.frame
        if self.ids and f.arbitration_id not in self.ids:
            return False
        if self.name and self.name.lower() not in r.name.lower():
            return False
        if self.channel and self.channel.lower() not in f.channel.lower():
            return False
        if self.direction and (self.direction == "Rx") != f.is_rx:
            return False
        return True

    @staticmethod
    def parse_ids(text: str) -> set[int]:
        out = set()
        for tok in text.replace(";", ",").replace(" ", ",").split(","):
            tok = tok.strip()
            if tok:
                try:
                    out.add(int(tok, 16))
                except ValueError:
                    pass
        return out


FLASH_S = 0.6
MONO_COLUMNS = (0, 3, 5, 6, 7)


class TraceModel(QAbstractTableModel):
    COLUMNS = ["Time [s]", "Channel", "Dir", "ID", "Name", "DLC", "Data", "Cycle [ms]"]

    def __init__(self, max_rows: int = 200_000, parent=None) -> None:
        super().__init__(parent)
        self.max_rows = max_rows
        self._all: deque[TraceRow] = deque()
        self._view: list[TraceRow] = []
        self.filter = TraceFilter()
        self.flash_enabled = True
        self._mono = mono_font()

    # ---- model API ---------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._view)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.COLUMNS[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self._view[index.row()]
        f, c = r.frame, index.column()
        if role == Qt.FontRole:
            return self._mono if c in MONO_COLUMNS else None        # time, ID, DLC, data, cycle
        if role == Qt.DisplayRole:
            if c == 0:
                return f"{f.timestamp:.6f}"
            if c == 1:
                return f.channel
            if c == 2:
                return "Rx" if f.is_rx else "Tx"
            if c == 3:
                return f"{f.arbitration_id:08X}" if f.is_extended else f"{f.arbitration_id:03X}"
            if c == 4:
                return r.name
            if c == 5:
                return str(len(f.data)) + (" FD" if f.is_fd else "")
            if c == 6:
                return bytes(f.data).hex(" ").upper()
            if c == 7:
                return "" if r.cycle_ms is None else f"{r.cycle_ms:.1f}"
        elif role == Qt.ForegroundRole:
            if c == 3:
                return QColor(id_color())            # IDs: soft cyan
            if c == 6:
                return QColor(data_color())          # data bytes: soft orange
            if c == 2 and not f.is_rx:
                return QColor(tx_color())            # direction marker for transmitted frames
        elif role == Qt.BackgroundRole and self.flash_enabled:
            age = clock.now() - f.timestamp
            if 0 <= age < FLASH_S:                      # fading highlight for freshly updated IDs
                c = QColor(accent())
                c.setAlpha(int(95 * (1.0 - age / FLASH_S)))
                return c      # Tx rows: readable on both themes
        elif role == Qt.TextAlignmentRole and c in (0, 5, 7):
            return int(Qt.AlignRight | Qt.AlignVCenter)

    # ---- data --------------------------------------------------------------
    def rows(self) -> list[TraceRow]:
        """Rows currently visible (after filtering), for export."""
        return list(self._view)

    def add_batch(self, batch: list[TraceRow]) -> None:
        if not batch:
            return
        self._all.extend(batch)
        new = [r for r in batch if self.filter.match(r)]
        if new:
            self.beginInsertRows(QModelIndex(), len(self._view), len(self._view) + len(new) - 1)
            self._view.extend(new)
            self.endInsertRows()
        excess = len(self._all) - self.max_rows
        if excess > 0:
            for _ in range(excess):
                self._all.popleft()
            oldest = self._all[0].frame.timestamp
            k = 0
            while k < len(self._view) and self._view[k].frame.timestamp < oldest:
                k += 1
            if k:
                self.beginRemoveRows(QModelIndex(), 0, k - 1)
                del self._view[:k]
                self.endRemoveRows()

    def refresh_recent(self, max_rows: int = 400) -> None:
        """Repaint the newest rows so their flash highlight fades out (called by a UI timer)."""
        n = len(self._view)
        if not self.flash_enabled or n == 0:
            return
        if clock.now() - self._view[-1].frame.timestamp > FLASH_S + 0.3:
            return
        first = max(0, n - max_rows)
        self.dataChanged.emit(self.index(first, 0), self.index(n - 1, len(self.COLUMNS) - 1), [Qt.BackgroundRole])

    def set_filter(self, flt: TraceFilter) -> None:
        self.beginResetModel()
        self.filter = flt
        self._view = [r for r in self._all if flt.match(r)]
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._all.clear()
        self._view = []
        self.endResetModel()
