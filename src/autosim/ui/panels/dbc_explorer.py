"""Loaded database files (load / reload / remove) and the tree of Nodes > Messages > Signals."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autosim.core.can_engine.database import DatabaseError
from autosim.ui.icons import make_icon
from autosim.ui.theme import ThemeManager, icon_color
from autosim.ui.widgets import caption

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
        self.remove_btn = QToolButton()
        self.remove_btn.setObjectName("panel-btn")
        self.remove_btn.setIconSize(QSize(16, 16))
        self.remove_btn.setToolTip("Remove the selected database file (Delete)")
        self.remove_btn.setAccessibleName("Remove database")
        self.reload_btn = QToolButton()
        self.reload_btn.setObjectName("panel-btn")
        self.reload_btn.setIconSize(QSize(16, 16))
        self.reload_btn.setToolTip("Reload the selected database file from disk")
        self.reload_btn.setAccessibleName("Reload database")
        # loaded files: select one and press Remove / Delete, or right-click for reload / remove
        self.files = QListWidget()
        self.files.setObjectName("dbc-files")
        self.files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files.setContextMenuPolicy(Qt.CustomContextMenu)
        self.files.setToolTip("Loaded databases - right-click to reload or remove, Delete removes the selection")
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
        row.addStretch()                       # buttons keep their size in a wide Databases window
        # "Loaded files [reload] [remove]" header, left aligned so the buttons stay visible in a narrow sidebar
        self.files_box = QWidget()
        fv = QVBoxLayout(self.files_box)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(2)
        head.addWidget(caption("Loaded files"))
        head.addSpacing(6)
        head.addWidget(self.reload_btn)
        head.addWidget(self.remove_btn)
        head.addStretch()
        fv.addLayout(head)
        fv.addWidget(self.files)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(row)
        lay.addWidget(self.files_box)
        lay.addWidget(self.search)
        lay.addWidget(self.tree, 1)            # the message tree takes the free height, not the gaps

        load.clicked.connect(self.load_dialog)
        add.clicked.connect(self._add_selected)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.search.textChanged.connect(self._filter)
        self.remove_btn.clicked.connect(lambda: self.remove_selected())
        self.reload_btn.clicked.connect(lambda: [self.reload_file(p) for p in self._target_paths()])
        ThemeManager.instance().changed.connect(self._refresh_icons)      # bound: auto-disconnects
        self._refresh_icons()
        self.files.itemSelectionChanged.connect(self._sync_remove)
        self.files.customContextMenuRequested.connect(self._files_menu)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        sc = QShortcut(QKeySequence.Delete, self.files)
        sc.setContext(Qt.WidgetShortcut)
        sc.activated.connect(lambda: self.remove_selected())
        bridge.dbLoaded.connect(lambda _p: self.refresh())
        bridge.dbUnloaded.connect(lambda _p: self.refresh())
        self.refresh()

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

    # ---- loaded files ---------------------------------------------------------------------
    def _refresh_files(self) -> None:
        selected = set(self.selected_paths())
        self.files.clear()
        for f in self.ctx.can.registry.loaded():
            it = QListWidgetItem(f"{Path(f['path']).name}  ·  {f['messages']} messages")
            it.setData(Qt.UserRole, f["path"])
            it.setToolTip(f["path"] + ("" if f["channel"] == "*" else f"  (channel {f['channel']})"))
            self.files.addItem(it)
            it.setSelected(f["path"] in selected)
        n = self.files.count()
        self.files_box.setVisible(n > 0)
        row_h = self.files.sizeHintForRow(0) if n else 0
        self.files.setFixedHeight(min(n, 4) * row_h + 2 * self.files.frameWidth() + 4 if n else 0)
        self._sync_remove()

    def selected_paths(self) -> list[str]:
        return [it.data(Qt.UserRole) for it in self.files.selectedItems()]

    def _refresh_icons(self, _dark: bool = True) -> None:
        self.remove_btn.setIcon(make_icon("trash", icon_color()))
        self.reload_btn.setIcon(make_icon("refresh", icon_color()))

    def _target_paths(self) -> list[str]:
        """The selected files, or the only file when just one is loaded."""
        paths = self.selected_paths()
        if not paths and self.files.count() == 1:
            paths = [self.files.item(0).data(Qt.UserRole)]
        return paths

    def _sync_remove(self) -> None:
        on = bool(self._target_paths())
        self.remove_btn.setEnabled(on)
        self.reload_btn.setEnabled(on)

    def remove_selected(self, confirm: bool = True) -> int:
        """Remove the selected files (or the only file when just one is loaded). Returns how many were removed."""
        return sum(self.remove_file(p, confirm) for p in list(self._target_paths()))

    def remove_file(self, path: str, confirm: bool = True) -> bool:
        """Unload one database. Messages of that file in the Signals panel go with it (cyclic TX stops);
        with `confirm` the user is asked first when that happens."""
        reg, tx = self.ctx.can.registry, self.ctx.can.tx
        if not reg.is_loaded(path):
            return False
        in_use = [j for j in tx.jobs() if reg.path_of(j.message) is not None and reg._same(reg.path_of(j.message), path)]
        if confirm and in_use:
            running = sum(1 for j in in_use if j.enabled)
            text = (f"Remove {Path(path).name}?\n{len(in_use)} message(s) from it will be removed from the Signals "
                    f"panel" + (f", {running} of them sending cyclically" if running else "") + ".")
            if QMessageBox.question(self, "Remove database", text, QMessageBox.Yes | QMessageBox.Cancel,
                                    QMessageBox.Cancel) != QMessageBox.Yes:
                return False
        try:
            self.ctx.can.unload_database(path)
        except KeyError:
            return False
        return True

    def reload_file(self, path: str) -> bool:
        """Read the file again (after editing it); a failed parse keeps the version already loaded."""
        return self.load_file(path)

    def _files_menu(self, pos) -> None:
        item = self.files.itemAt(pos)
        if item is None:
            return
        path = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.addAction("Reload", lambda: self.reload_file(path))
        menu.addAction("Copy path", lambda: QApplication.clipboard().setText(path))
        menu.addSeparator()
        menu.addAction(f"Remove {Path(path).name}", lambda: self.remove_file(path))
        menu.exec(self.files.viewport().mapToGlobal(pos))

    def _tree_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        name = self._msg_name(item) if item is not None else None
        menu = QMenu(self)
        if name:
            menu.addAction("Add to signals", lambda: self.messageActivated.emit(name))
            m = self.ctx.can.registry.message_by_name(name)
            path = self.ctx.can.registry.path_of(m) if m is not None else None
            if path:
                menu.addSeparator()
                menu.addAction(f"Remove database {Path(path).name}", lambda: self.remove_file(path))
        if menu.isEmpty():
            return
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ---- tree -----------------------------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_files()
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
