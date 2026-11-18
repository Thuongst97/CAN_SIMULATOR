"""Databases window, laid out like CANoe's symbol explorer:

    [Load database…] [⟳] [🗑]   [ Search message, signal or ID ............ ]          [Add to Signals]
    ┌ Files ───────────────┐ ┌ Messages (113) ─────────────────────────────────────────────┐
    │ All databases     113│ │ Name            ID      DLC   Cycle   Sender     Signals    │
    │ vw_mqb.dbc        113│ │ ACC_06          0x122   8     100 ms  ACC        17         │
    │ sample.dbc          2│ ├ Signals of ACC_06 (17) ─────────────────────────────────────┤
    └──────────────────────┘ │ Signal          Start  Len  Order   Factor  Offset  Range  Unit │
                             └─────────────────────────────────────────────────────────────┘

* Files: select one to browse only its messages; Reload / Remove act on the selection (or on the only file).
* Messages: one flat, sortable table (no node grouping: a message sent by two nodes is listed once); the search
  matches message names, signal names and hex IDs. Double-click or *Add to Signals* opens it in the Signals window.
* Signals of the selected message, with their layout and scaling; value tables are the tooltip of the name.
* Nothing loaded: an empty state with the Load button; database files can also be dropped on the window.
"""
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
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autosim.core.can_engine.database import DatabaseError
from autosim.ui.icons import make_icon
from autosim.ui.theme import ThemeManager, icon_color
from autosim.ui.widgets import EmptyState, set_kind

COLS = ["Name", "ID", "DLC", "Cycle", "Sender", "Signals"]
SIG_COLS = ["Signal", "Start", "Length", "Order", "Factor", "Offset", "Range", "Unit"]
ALL = "__all__"
DB_FILTER = "CAN databases (*.dbc *.arxml *.kcd *.sym);;All files (*)"
DB_SUFFIXES = (".dbc", ".arxml", ".kcd", ".sym")


def _fmt(v) -> str:
    return "" if v is None else f"{v:g}"


def _section(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("db-section")
    return lb


class _MessageItem(QTreeWidgetItem):
    """Sorts by the underlying number for ID / DLC / cycle / signal count, by text otherwise."""

    def __lt__(self, other) -> bool:
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a, b = self.data(col, Qt.UserRole + 1), other.data(col, Qt.UserRole + 1)
        if a is not None and b is not None:
            return a < b
        return self.text(col).lower() < other.text(col).lower()


class DbcExplorer(QWidget):
    messageActivated = Signal(str)       # message name -> add to the Signals window

    def __init__(self, ctx, bridge, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setAcceptDrops(True)
        # ---- toolbar ----
        self.load_btn = QPushButton("Load database…")
        self.load_btn.setToolTip("Load DBC / ARXML / KCD / SYM files (or drop them on this window)")
        self.reload_btn = self._icon_btn("Reload the selected database from disk", "Reload database")
        self.remove_btn = self._icon_btn("Remove the selected database (Delete)", "Remove database")
        self.search = QLineEdit(placeholderText="Search message, signal or ID")
        self.search.setClearButtonEnabled(True)
        self.add_btn = QPushButton("Add to Signals")
        set_kind(self.add_btn, "primary")
        self.add_btn.setToolTip("Open the selected messages in the Signals window (or double-click a message)")
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(self.load_btn)
        bar.addWidget(self.reload_btn)
        bar.addWidget(self.remove_btn)
        bar.addSpacing(6)
        bar.addWidget(self.search, 1)
        bar.addSpacing(6)
        bar.addWidget(self.add_btn)

        # ---- files ----
        self.files = QListWidget()
        self.files.setObjectName("dbc-files")
        self.files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files.setContextMenuPolicy(Qt.CustomContextMenu)
        self.files.setToolTip("Select a file to browse only its messages - right-click to reload or remove")
        files_box = QWidget()
        fl = QVBoxLayout(files_box)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(4)
        fl.addWidget(_section("Files"))
        fl.addWidget(self.files, 1)

        # ---- messages ----
        self.tree = QTreeWidget()
        self.tree.setObjectName("dbc-messages")
        self.tree.setHeaderLabels(COLS)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        hh = self.tree.header()
        for i in range(len(COLS)):                                       # names and numbers are never cut
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)                                   # the last column takes what is left
        self.msg_title = _section("Messages")
        msg_box = QWidget()
        ml = QVBoxLayout(msg_box)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(4)
        ml.addWidget(self.msg_title)
        ml.addWidget(self.tree, 1)

        # ---- signals of the selected message ----
        self.signals = QTreeWidget()
        self.signals.setObjectName("dbc-signals")
        self.signals.setHeaderLabels(SIG_COLS)
        self.signals.setRootIsDecorated(False)
        self.signals.setAlternatingRowColors(True)
        self.signals.setUniformRowHeights(True)
        sh = self.signals.header()
        for i in range(len(SIG_COLS)):
            sh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        sh.setStretchLastSection(True)
        self.sig_title = _section("Signals")
        sig_box = QWidget()
        sl = QVBoxLayout(sig_box)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(4)
        sl.addWidget(self.sig_title)
        sl.addWidget(self.signals, 1)

        right = QSplitter(Qt.Vertical)
        right.setChildrenCollapsible(False)
        right.addWidget(msg_box)
        right.addWidget(sig_box)
        right.setSizes([600, 400])
        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(files_box)
        self.split.addWidget(right)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([140, 800])

        # ---- empty state ----
        self.empty = EmptyState("database", "No database loaded",
                                "Load a DBC or ARXML file, or drop it here, to browse its messages and signals.")
        self.empty.add_button("Load database…").clicked.connect(self.load_dialog)
        empty_page = QWidget()
        el = QVBoxLayout(empty_page)
        el.addStretch()
        el.addWidget(self.empty, 0, Qt.AlignHCenter)
        el.addStretch(2)
        self.browser = QWidget()
        bl = QVBoxLayout(self.browser)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        bl.addLayout(bar)
        bl.addWidget(self.split, 1)
        self.stack = QStackedWidget()
        self.stack.addWidget(empty_page)
        self.stack.addWidget(self.browser)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.stack)

        # ---- wiring ----
        self.load_btn.clicked.connect(self.load_dialog)
        self.add_btn.clicked.connect(self._add_selected)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemSelectionChanged.connect(self._show_signals)
        self.search.textChanged.connect(self._filter)
        self.remove_btn.clicked.connect(lambda: self.remove_selected())
        self.reload_btn.clicked.connect(lambda: [self.reload_file(p) for p in self._target_paths()])
        ThemeManager.instance().changed.connect(self._refresh_icons)      # bound: auto-disconnects
        self._refresh_icons()
        self.files.itemSelectionChanged.connect(self._files_selected)
        self.files.customContextMenuRequested.connect(self._files_menu)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        sc = QShortcut(QKeySequence.Delete, self.files)
        sc.setContext(Qt.WidgetShortcut)
        sc.activated.connect(lambda: self.remove_selected())
        bridge.dbLoaded.connect(lambda _p: self.refresh())
        bridge.dbUnloaded.connect(lambda _p: self.refresh())
        self.refresh()

    @staticmethod
    def _icon_btn(tip: str, name: str) -> QToolButton:
        b = QToolButton()
        b.setObjectName("db-tool")
        b.setIconSize(QSize(16, 16))
        b.setToolTip(tip)
        b.setAccessibleName(name)
        return b

    # ---- loading ----------------------------------------------------------------------------
    def load_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Load database", "", DB_FILTER)
        for p in paths:
            self.load_file(p)

    def load_file(self, path: str) -> bool:
        try:
            self.ctx.can.load_database(path)
            return True
        except DatabaseError as e:
            QMessageBox.critical(self, "Cannot load database", str(e))
            return False

    def dragEnterEvent(self, e) -> None:
        if any(u.toLocalFile().lower().endswith(DB_SUFFIXES) for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(DB_SUFFIXES):
                self.load_file(u.toLocalFile())

    # ---- files ------------------------------------------------------------------------------
    def _refresh_files(self) -> None:
        selected = set(self.selected_paths())
        self.files.blockSignals(True)
        self.files.clear()
        for f in self.ctx.can.registry.loaded():
            it = QListWidgetItem(f"{Path(f['path']).name}\n{f['messages']} messages")     # name over count
            it.setData(Qt.UserRole, f["path"])
            it.setToolTip(f["path"] + ("" if f["channel"] == "*" else f"  (channel {f['channel']})"))
            self.files.addItem(it)
            it.setSelected(f["path"] in selected)
        self.files.blockSignals(False)
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

    def _files_selected(self) -> None:
        self._sync_remove()
        self._fill_messages()

    def remove_selected(self, confirm: bool = True) -> int:
        """Remove the selected files (or the only file when just one is loaded). Returns how many were removed."""
        return sum(self.remove_file(p, confirm) for p in list(self._target_paths()))

    def remove_file(self, path: str, confirm: bool = True) -> bool:
        """Unload one database. Messages of that file in the Signals window go with it (cyclic TX stops);
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
            menu.addAction("Add to Signals", lambda: self.messageActivated.emit(name))
            m = self.ctx.can.registry.message_by_name(name)
            path = self.ctx.can.registry.path_of(m) if m is not None else None
            if path:
                menu.addSeparator()
                menu.addAction(f"Remove database {Path(path).name}", lambda: self.remove_file(path))
        if menu.isEmpty():
            return
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ---- messages / signals -------------------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_files()
        self.stack.setCurrentIndex(1 if self.files.count() else 0)
        self._fill_messages()

    def _fill_messages(self) -> None:
        reg = self.ctx.can.registry
        only = set(self.selected_paths())
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for m in reg.messages():
            if only and not any(reg._same(reg.path_of(m) or "", p) for p in only):
                continue
            it = _MessageItem([
                m.name,
                f"0x{m.frame_id:X}" + (" x" if m.is_extended_frame else ""),
                str(m.length) + (" FD" if m.is_fd else ""),
                f"{m.cycle_time} ms" if m.cycle_time else "",
                ", ".join(m.senders or []),
                str(len(m.signals))])
            it.setData(0, Qt.UserRole, ("msg", m.name))
            it.setData(1, Qt.UserRole + 1, m.frame_id)
            it.setData(2, Qt.UserRole + 1, m.length)
            it.setData(3, Qt.UserRole + 1, m.cycle_time or 0)
            it.setData(5, Qt.UserRole + 1, len(m.signals))
            it.setData(0, Qt.UserRole + 2, " ".join(s.name.lower() for s in m.signals))
            for c in (1, 2, 3, 5):
                it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
            self.tree.addTopLevelItem(it)
        self.tree.setSortingEnabled(True)
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        t = text.lower().strip()
        tid = (t[2:] if t.startswith("0x") else t).lstrip("0") or ("0" if t else "")
        is_hex = bool(tid) and all(ch in "0123456789abcdef" for ch in tid)
        shown = 0
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            hit = (not t or t in it.text(0).lower() or t in (it.data(0, Qt.UserRole + 2) or "")
                   or (is_hex and f"{it.data(1, Qt.UserRole + 1):x}".startswith(tid)))
            it.setHidden(not hit)
            shown += bool(hit)
        total = self.tree.topLevelItemCount()
        self.msg_title.setText(f"Messages ({shown} of {total})" if t else f"Messages ({total})")
        if self.tree.currentItem() is None or self.tree.currentItem().isHidden():
            first = next((self.tree.topLevelItem(i) for i in range(total)
                          if not self.tree.topLevelItem(i).isHidden()), None)
            self.tree.setCurrentItem(first)
        self._show_signals()

    def _show_signals(self) -> None:
        self.signals.clear()
        it = self.tree.currentItem()
        name = self._msg_name(it) if it is not None and not it.isHidden() else None
        self.add_btn.setEnabled(bool(self.tree.selectedItems()) or name is not None)
        m = self.ctx.can.registry.message_by_name(name) if name else None
        if m is None:
            self.sig_title.setText("Signals")
            return
        self.sig_title.setText(f"Signals of {m.name} ({len(m.signals)})")
        t = self.search.text().lower().strip()
        for s in sorted(m.signals, key=lambda s: s.start):
            lo, hi = _fmt(s.minimum), _fmt(s.maximum)
            row = QTreeWidgetItem([
                s.name, str(s.start), str(s.length), "Intel" if s.byte_order == "little_endian" else "Motorola",
                _fmt(s.scale), _fmt(s.offset), f"{lo} … {hi}" if lo or hi else "", s.unit or ""])
            for c in (1, 2, 4, 5):
                row.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
            if s.choices:
                row.setToolTip(0, "\n".join(f"{int(k)} = {v}" for k, v in s.choices.items()))
            if t and t in s.name.lower():
                f = row.font(0)
                f.setBold(True)
                row.setFont(0, f)
            self.signals.addTopLevelItem(row)

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
        items = self.tree.selectedItems() or ([self.tree.currentItem()] if self.tree.currentItem() else [])
        for it in items:
            name = self._msg_name(it)
            if name:
                self.messageActivated.emit(name)
