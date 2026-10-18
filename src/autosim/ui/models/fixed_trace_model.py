"""Fixed ("overwrite") trace: one row per message (channel, direction, id) updated in place, with one child
row per signal showing its latest decoded value - like the fixed trace mode of CANoe / CANalyzer."""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from autosim.core.can_engine.rx_dispatcher import TraceRow
from autosim.core.common import clock
from autosim.ui.models.trace_model import FLASH_S, TraceFilter
from autosim.ui.theme import ThemeManager, accent, data_color, id_color, data_font, rx_color, token, tx_color

COLUMNS = ["Time [s]", "Channel", "Dir", "ID", "Name / Signal", "DLC", "Data / Value", "Cycle [ms]", "Count"]
COL_NAME, COL_VALUE = 4, 6
_TOP = 0                         # internalId of top-level (message) rows; children store parent row + 1


@dataclass
class _Entry:
    key: tuple
    row: TraceRow
    count: int = 1
    signals: list = field(default_factory=list)        # cantools signals of the message (child rows)
    decoded_ts: float = -1.0
    decoded: dict = field(default_factory=dict)


class FixedTraceModel(QAbstractItemModel):
    def __init__(self, registry, parent=None) -> None:
        super().__init__(parent)
        self.registry = registry
        self.filter = TraceFilter()
        self.flash_enabled = True
        self._entries: list[_Entry] = []
        self._index: dict[tuple, int] = {}
        self._visible: list[int] = []                   # entry indices passing the filter, in display order
        self._mono = data_font()
        self._refresh_colors()
        ThemeManager.instance().changed.connect(self._refresh_colors)   # bound: auto-disconnects

    def _refresh_colors(self, _dark: bool = True) -> None:
        self._colors = {"id": QColor(id_color()), "data": QColor(data_color()), "rx": QColor(rx_color()),
                        "tx": QColor(tx_color()), "value": QColor(token("TEXT_PRIMARY")),
                        "signal": QColor(token("TEXT_SECONDARY"))}

    # ---- structure ---------------------------------------------------------------------------
    def index(self, row: int, column: int, parent=QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column, _TOP)
        if parent.internalId() == _TOP:
            return self.createIndex(row, column, parent.row() + 1)
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid() or index.internalId() == _TOP:
            return QModelIndex()
        return self.createIndex(index.internalId() - 1, 0, _TOP)

    def rowCount(self, parent=QModelIndex()) -> int:
        if not parent.isValid():
            return len(self._visible)
        if parent.internalId() == _TOP and parent.column() == 0:
            return len(self._entries[self._visible[parent.row()]].signals)
        return 0

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]

    # ---- content ---------------------------------------------------------------------------------
    def entry(self, row: int) -> _Entry:
        return self._entries[self._visible[row]]

    def _decoded(self, e: _Entry) -> dict:
        ts = e.row.frame.timestamp
        if ts != e.decoded_ts:                          # decode lazily, only when a signal row is painted
            res = self.registry.decode(e.row.frame)
            e.decoded = res[1] if res else {}
            e.decoded_ts = ts
        return e.decoded

    def signal_text(self, e: _Entry, sig) -> str:
        d = self._decoded(e)
        if sig.name not in d:
            return "—"                              # inactive multiplexed signal
        v = d[sig.name]
        txt = f"{v:g}" if isinstance(v, (int, float)) else str(v)
        if sig.unit:
            txt += f" {sig.unit}"
        if sig.choices:
            try:
                txt += f"  ({sig.choices[int(v)]})"
            except (KeyError, ValueError, TypeError):
                pass
        return txt

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        c = index.column()
        if index.internalId() != _TOP:                   # ---- signal row
            e = self._entries[self._visible[index.internalId() - 1]]
            sig = e.signals[index.row()]
            if role == Qt.DisplayRole:
                if c == COL_NAME:
                    return sig.name
                if c == COL_VALUE:
                    return self.signal_text(e, sig)
            elif role == Qt.ForegroundRole and c == COL_VALUE:
                return self._colors["value"]
            elif role == Qt.ForegroundRole and c == COL_NAME:
                return self._colors["signal"]
            elif role == Qt.FontRole and c == COL_VALUE:
                return self._mono
            return None
        e = self.entry(index.row())                      # ---- message row
        f = e.row.frame
        if role == Qt.DisplayRole:
            if c == 0:
                return f"{f.timestamp:.6f}"
            if c == 1:
                return f.channel
            if c == 2:
                return "Rx" if f.is_rx else "Tx"
            if c == 3:
                return f"{f.arbitration_id:08X}" if f.is_extended else f"{f.arbitration_id:03X}"
            if c == COL_NAME:
                return e.row.name
            if c == 5:
                return str(len(f.data)) + (" FD" if f.is_fd else "")
            if c == COL_VALUE:
                return bytes(f.data).hex(" ").upper()
            if c == 7:
                return "" if e.row.cycle_ms is None else f"{e.row.cycle_ms:.1f}"
            if c == 8:
                return str(e.count)
        elif role == Qt.ForegroundRole:
            if c == 3:
                return self._colors["id"]
            if c == COL_VALUE:
                return self._colors["data"]
            if c == 2:
                return self._colors["rx" if f.is_rx else "tx"]
        elif role == Qt.FontRole and c in (0, 3, 5, COL_VALUE, 7, 8):
            return self._mono
        elif role == Qt.TextAlignmentRole and c in (0, 5, 7, 8):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        elif role == Qt.BackgroundRole and self.flash_enabled:
            age = clock.now() - f.timestamp
            if 0 <= age < FLASH_S:
                col = QColor(accent())
                col.setAlpha(int(95 * (1.0 - age / FLASH_S)))
                return col
        return None

    # ---- updates -------------------------------------------------------------------------------------
    def _key(self, r: TraceRow) -> tuple:
        f = r.frame
        return (f.channel, f.is_rx, f.arbitration_id, f.is_extended)

    def _signals_for(self, r: TraceRow) -> list:
        msg = self.registry.message_by_id(r.frame.channel, r.frame.arbitration_id)
        return list(msg.signals) if msg is not None else []

    def add_batch(self, batch: list[TraceRow]) -> None:
        changed: set[int] = set()
        for r in batch:
            k = self._key(r)
            i = self._index.get(k)
            if i is not None:
                e = self._entries[i]
                e.row = r
                e.count += 1
                changed.add(i)
                continue
            e = _Entry(k, r, signals=self._signals_for(r))
            self._entries.append(e)
            i = len(self._entries) - 1
            self._index[k] = i
            if self.filter.match(r):
                n = len(self._visible)
                self.beginInsertRows(QModelIndex(), n, n)
                self._visible.append(i)
                self.endInsertRows()
        if not changed:
            return
        pos = {ei: row for row, ei in enumerate(self._visible)}
        last = len(COLUMNS) - 1
        for i in changed:
            row = pos.get(i)
            if row is None:
                continue
            top = self.index(row, 0)
            self.dataChanged.emit(top, self.index(row, last))
            n = len(self._entries[i].signals)
            if n:                                        # refresh the signal values of this message
                self.dataChanged.emit(self.index(0, COL_VALUE, top), self.index(n - 1, COL_VALUE, top))

    def refresh_recent(self) -> None:
        """Repaint message rows so the update highlight fades (called by a UI timer)."""
        if self.flash_enabled and self._visible:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._visible) - 1, len(COLUMNS) - 1),
                                  [Qt.BackgroundRole])

    def set_filter(self, flt: TraceFilter) -> None:
        self.beginResetModel()
        self.filter = flt
        self._visible = [i for i, e in enumerate(self._entries) if flt.match(e.row)]
        self.endResetModel()

    def databases_changed(self) -> None:
        """A DBC was loaded: attach names and signals to the messages seen so far."""
        self.beginResetModel()
        for e in self._entries:
            e.signals = self._signals_for(e.row)
            res = self.registry.decode(e.row.frame)
            if res:
                e.row.name = res[0].name
            e.decoded_ts = -1.0
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._entries.clear()
        self._index.clear()
        self._visible.clear()
        self.endResetModel()

    def message_count(self) -> int:
        return len(self._visible)
