"""Tree of Nodes > Messages > Signals with signal details."""
from __future__ import annotations


from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from autosim.core.can_engine.database import DatabaseError

COLS = ["Name", "ID", "Start", "Len", "Order", "Factor", "Offset", "Min", "Max", "Unit"]


def _fmt(v) -> str:
    return "" if v is None else f"{v:g}"


class DbcExplorer(QWidget):
    messageActivated = Signal(str)       # message name -> add to signal panel

    def __init__(self, ctx, bridge, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        load = QPushButton("Load DBC / ARXML…")
        add = QPushButton("Add to signals")
        self.search = QLineEdit(placeholderText="Search message or signal")
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(COLS)
        self.tree.setAlternatingRowColors(True)
        hh = self.tree.header()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)                  # Name takes the free space
        for i in range(1, len(COLS)):                                    # ID, Start, Len, Order, Factor ... never cut
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)

        row = QHBoxLayout()
        row.addWidget(load)
        row.addWidget(add)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(row)
        lay.addWidget(self.search)
        lay.addWidget(self.tree)

        load.clicked.connect(self.load_dialog)
        add.clicked.connect(self._add_selected)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.search.textChanged.connect(self._filter)
        bridge.dbLoaded.connect(lambda _p: self.refresh())

    def load_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Load database", "",
                                                "CAN databases (*.dbc *.arxml *.kcd *.sym);;All files (*)")
        for p in paths:
            self.load_file(p)

    def load_file(self, path: str) -> bool:
        try:
            self.ctx.can.load_database(path)
            return True
        except DatabaseError as e:
            QMessageBox.critical(self, "Cannot load database", str(e))
            return False

    def refresh(self) -> None:
        self.tree.clear()
        for node in self.ctx.can.registry.tree():
            n_item = QTreeWidgetItem([node["name"]])
            n_item.setData(0, Qt.UserRole, ("node", node["name"]))
            self.tree.addTopLevelItem(n_item)
            for m in node["messages"]:
                idtxt = f"{m['id']:X}" + ("x" if m["extended"] else "")
                m_item = QTreeWidgetItem([m["name"], idtxt, "", str(m["length"]) + (" FD" if m["fd"] else "")])
                m_item.setData(0, Qt.UserRole, ("msg", m["name"]))
                n_item.addChild(m_item)
                for s in m["signals"]:
                    child = QTreeWidgetItem([
                        s["name"], "", str(s["start"]), str(s["length"]),
                        "Intel" if s["byte_order"] == "little_endian" else "Motorola",
                        _fmt(s["factor"]), _fmt(s["offset"]), _fmt(s["min"]), _fmt(s["max"]), s["unit"]])
                    if s["choices"]:
                        child.setToolTip(0, "\n".join(f"{k} = {v}" for k, v in s["choices"].items()))
                    m_item.addChild(child)
            n_item.setExpanded(True)
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        t = text.lower().strip()
        for i in range(self.tree.topLevelItemCount()):
            node = self.tree.topLevelItem(i)
            any_msg = False
            for j in range(node.childCount()):
                msg = node.child(j)
                sigs = [msg.child(k) for k in range(msg.childCount())]
                hit_msg = not t or t in msg.text(0).lower()
                hit_sig = False
                for s in sigs:
                    sh = bool(t) and t in s.text(0).lower()
                    s.setHidden(bool(t) and not (hit_msg or sh))
                    hit_sig |= sh
                msg.setHidden(not (hit_msg or hit_sig))
                any_msg |= hit_msg or hit_sig
                if t and hit_sig:
                    msg.setExpanded(True)
            node.setHidden(not any_msg)

    def _msg_name(self, item) -> str | None:
        while item is not None:
            kind = item.data(0, Qt.UserRole)
            if kind and kind[0] == "msg":
                return kind[1]
            item = item.parent()
        return None

    def _double_clicked(self, item, _col) -> None:
        name = self._msg_name(item)
        if name:
            self.messageActivated.emit(name)

    def _add_selected(self) -> None:
        for it in self.tree.selectedItems():
            name = self._msg_name(it)
            if name:
                self.messageActivated.emit(name)
