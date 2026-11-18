"""Signal selection dialog (CANoe's symbol selection): pick messages / signals of the databases in use.

    [ Search message, signal or ID ...................... ]
    ▾ vw_mqb.dbc
      ▸ [ ] ACC_02            0x30C
      ▾ [~] ACC_06            0x122
            [x] ACC_Typ
            [ ] ACC_Status_ACC
    3 messages selected                       [Cancel] [Add]

Ticking a signal selects its message; messages already in the Signals window are shown ticked and disabled.
`selected_messages()` gives the message names to add, in tree order.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QTreeWidget,
                               QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout)

from autosim.ui.theme import token

KIND = Qt.UserRole


class SignalSelectDialog(QDialog):
    def __init__(self, library, already: set | None = None, parent=None, title: str = "Add signals",
                 mode: str = "messages") -> None:
        """mode "messages": pick messages for the Signals window (`already`: message names);
        mode "signals": pick single signals, e.g. to plot (`already`: (message, signal) pairs)."""
        super().__init__(parent)
        self.mode = mode
        self.setWindowTitle(title)
        self.resize(640, 560)
        self.library = library
        self.already = already or set()
        self.search = QLineEdit(placeholderText="Search message, signal or ID")
        self.search.setClearButtonEnabled(True)
        self.tree = QTreeWidget()
        self.tree.setObjectName("signal-select")
        self.tree.setHeaderLabels(["Name", "ID", "Unit"])
        self.tree.headerItem().setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        self.tree.setUniformRowHeights(True)
        hh = self.tree.header()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.count = QLabel()
        self.count.setProperty("role", "muted")
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Add")
        from autosim.ui.widgets import set_kind
        set_kind(self.buttons.button(QDialogButtonBox.Ok), "primary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        foot = QHBoxLayout()
        foot.addWidget(self.count, 1)
        foot.addWidget(self.buttons)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(self.search)
        lay.addWidget(self.tree, 1)
        lay.addLayout(foot)
        self._fill()
        self.search.textChanged.connect(self._filter)
        self.tree.itemChanged.connect(lambda *_: self._update_count())
        self._update_count()
        self.search.setFocus()

    def _fill(self) -> None:
        muted = QColor(token("TEXT_MUTED"))
        self.tree.blockSignals(True)
        for e in self.library.active():
            db = self.library.database(e.path)
            if db is None:
                continue
            top = QTreeWidgetItem([e.name, "", ""])
            top.setData(0, KIND, ("db", e.path))
            top.setFlags(Qt.ItemIsEnabled)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self.tree.addTopLevelItem(top)
            for m in sorted(db.messages, key=lambda m: m.name):
                done = self.mode == "messages" and m.name in self.already
                mi = QTreeWidgetItem([m.name, f"0x{m.frame_id:X}", ""])
                mi.setData(0, KIND, ("msg", m.name))
                mi.setData(1, KIND, m.frame_id)
                mi.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                if done:
                    mi.setFlags(Qt.ItemIsUserCheckable)          # shown ticked, not selectable again
                    mi.setCheckState(0, Qt.Checked)
                    mi.setToolTip(0, "Already in the Signals window")
                    for c in range(3):
                        mi.setForeground(c, muted)
                else:
                    mi.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                    mi.setCheckState(0, Qt.Unchecked)
                top.addChild(mi)
                for s in sorted(m.signals, key=lambda s: s.start):
                    si = QTreeWidgetItem([s.name, "", s.unit or ""])
                    si.setData(0, KIND, ("sig", m.name, s.name))
                    if done or (m.name, s.name) in self.already:
                        si.setFlags(Qt.ItemIsUserCheckable)
                        si.setCheckState(0, Qt.Checked)
                        si.setForeground(0, muted)
                    else:
                        si.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                        si.setCheckState(0, Qt.Unchecked)
                    mi.addChild(si)
            top.setExpanded(True)
        self.tree.blockSignals(False)

    def _filter(self, text: str) -> None:
        t = text.lower().strip()
        tid = (t[2:] if t.startswith("0x") else t).lstrip("0") or ("0" if t else "")
        is_hex = bool(tid) and all(ch in "0123456789abcdef" for ch in tid)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                mi = top.child(j)
                own = not t or t in mi.text(0).lower() or (is_hex and f"{mi.data(1, KIND):x}".startswith(tid))
                sig_hit = False
                for k in range(mi.childCount()):
                    si = mi.child(k)
                    sh = bool(t) and t in si.text(0).lower()
                    si.setHidden(bool(t) and not (own or sh))
                    sig_hit |= sh
                mi.setHidden(not (own or sig_hit))
                mi.setExpanded(bool(t) and sig_hit and not own)

    def selected_messages(self) -> list[str]:
        """Messages to add: ticked (or partly ticked through a signal) and not already in the Signals window."""
        out = []
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            kind = item.data(0, KIND)
            if (kind and kind[0] == "msg" and kind[1] not in self.already and kind[1] not in out
                    and item.checkState(0) != Qt.Unchecked):
                out.append(kind[1])
            it += 1
        return out

    def selected_signals(self) -> list[tuple[str, str]]:
        """Ticked (message, signal) pairs that were not ticked already."""
        out = []
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            kind = item.data(0, KIND)
            if (kind and kind[0] == "sig" and item.checkState(0) == Qt.Checked and item.flags() & Qt.ItemIsEnabled
                    and (kind[1], kind[2]) not in out):
                out.append((kind[1], kind[2]))
            it += 1
        return out

    def check(self, message: str, signal: str | None = None) -> None:
        """Tick a message (or one of its signals) - used by tests and keyboard users."""
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            kind = it.value().data(0, KIND)
            if kind and kind[0] == "msg" and kind[1] == message and signal is None:
                it.value().setCheckState(0, Qt.Checked)
            if kind and kind[0] == "sig" and kind[1] == message and kind[2] == signal:
                it.value().setCheckState(0, Qt.Checked)
            it += 1

    def _update_count(self) -> None:
        if self.mode == "signals":
            n = len(self.selected_signals())
            self.count.setText(f"{n} signal{'s' if n != 1 else ''} selected" if n else "Tick the signals to plot")
        else:
            n = len(self.selected_messages())
            self.count.setText(f"{n} message{'s' if n != 1 else ''} selected" if n else "Tick messages or signals")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(n > 0)
