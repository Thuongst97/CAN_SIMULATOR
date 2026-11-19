"""Database window (Setup desktop, beside Channels), a tree like CANoe's database view:

    [Add database…] [⟳] [🗑]  [ Search … ]
    ┌ Tree ───────────────────────────────┐ ┌ Properties ──────────────┐
    │ ▾ vw_mqb.dbc          in use · All   │ │ Name        ACC_06       │
    │   ▾ Nodes                            │ │ ID          0x122        │
    │     ▾ ACC                            │ │ DLC         8            │
    │       ▾ Tx messages                  │ │ Cycle time  100 ms       │
    │         ▸ ACC_06            0x122    │ │ Senders     ACC          │
    │   ▾ Messages                         │ │ Signals     17           │
    │     ▸ ACC_06            0x122        │ │ Comment     …            │
    │         CHECKSUM                     │ └──────────────────────────┘
    └──────────────────────────────────────┘

Every database of the configuration is listed, in use or not. Selecting a database shows, in its properties,
one Channel choice: All channels, one channel, or Not used (unloaded, still listed); right-click offers the same.
The search matches message names, signal names and hex IDs. Double-click a message (or right-click > Add to
Signals) to open it in the Signals window; database files can be dropped on the window.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autosim.core.can_engine.database import ANY, DatabaseError
from autosim.core.can_engine.signal_codec import signal_initial
from autosim.ui.icons import make_icon
from autosim.ui.theme import ThemeManager, icon_color, token
from autosim.ui.widgets import EmptyState

ALL_CHANNELS = "All channels"
NOT_USED = "Not used"
UNUSED = ""                         # Channel choice data for "Not used"
DB_FILTER = "CAN databases (*.dbc *.arxml *.kcd *.sym);;All files (*)"
DB_SUFFIXES = (".dbc", ".arxml", ".kcd", ".sym")
KIND = Qt.UserRole                  # ("db", path) | ("folder", name) | ("node", name) | ("msg", name) | ("sig", msg, sig)


def _fmt(v) -> str:
    return "" if v is None else f"{v:g}"


def _id(m) -> str:
    return f"0x{m.frame_id:X}" + (" x" if m.is_extended_frame else "")


def _is_fd(m) -> bool:
    """CAN FD frame: marked so in the database, or longer than classic CAN allows (8 bytes)."""
    return bool(m.is_fd) or m.length > 8


def _dlc(m) -> str:
    """Tree DLC column: payload bytes, "FD" for CAN FD frames (64 FD, 8 FD, 8)."""
    return f"{m.length} FD" if _is_fd(m) else str(m.length)


def _section(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("db-section")
    return lb


class DbcExplorer(QWidget):
    messageActivated = Signal(str)       # message name -> add to the Signals window

    def __init__(self, ctx, bridge, library, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.library = library
        self.channels: list[str] = []                    # open channels, for the Channel choice of a database
        self.channel_box: QComboBox | None = None          # Channel choice of the selected database
        self.setAcceptDrops(True)
        self.load_btn = QPushButton("Add database…")
        self.load_btn.setToolTip("Add DBC / ARXML / KCD / SYM files to the configuration (or drop them here)")
        self.reload_btn = self._icon_btn("Reload the selected database from disk", "Reload database")
        self.remove_btn = self._icon_btn("Remove the selected database from the configuration (Delete)",
                                         "Remove database")
        self.search = QLineEdit(placeholderText="Search message, signal or ID")
        self.search.setClearButtonEnabled(True)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(self.load_btn)
        bar.addWidget(self.reload_btn)
        bar.addWidget(self.remove_btn)
        bar.addSpacing(6)
        bar.addWidget(self.search, 1)

        self.tree = QTreeWidget()
        self.tree.setObjectName("db-tree")
        self.tree.setHeaderLabels(["Name", "ID", "DLC"])
        for c in (1, 2):
            self.tree.headerItem().setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)   # like the values
        self.tree.headerItem().setToolTip(2, "Payload length in bytes; FD = CAN FD frame")
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        hh = self.tree.header()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        self.props = QTableWidget(0, 2)
        self.props.setObjectName("db-props")
        self.props.setHorizontalHeaderLabels(["Property", "Value"])
        self.props.verticalHeader().setVisible(False)
        self.props.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.props.setWordWrap(True)
        ph = self.props.horizontalHeader()
        ph.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        ph.setSectionResizeMode(1, QHeaderView.Stretch)
        self.props_title = _section("Properties")
        props_box = QWidget()
        pl = QVBoxLayout(props_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(4)
        pl.addWidget(self.props_title)
        pl.addWidget(self.props, 1)

        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.tree)
        self.split.addWidget(props_box)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)

        self.empty = EmptyState("database", "No database in the configuration",
                                "Add a DBC or ARXML file, or drop it here, to browse its nodes, messages and "
                                "signals, and to choose on which channel it is used.")
        self.empty.add_button("Add database…").clicked.connect(self.load_dialog)
        empty_page = QWidget()
        el = QVBoxLayout(empty_page)
        el.addStretch()
        el.addWidget(self.empty, 0, Qt.AlignHCenter)
        el.addStretch(2)
        browser = QWidget()
        bl = QVBoxLayout(browser)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        bl.addLayout(bar)
        bl.addWidget(self.split, 1)
        self.stack = QStackedWidget()
        self.stack.addWidget(empty_page)
        self.stack.addWidget(browser)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.stack)

        self.load_btn.clicked.connect(self.load_dialog)
        self.reload_btn.clicked.connect(lambda: [self.reload_file(p) for p in self._target_paths()])
        self.remove_btn.clicked.connect(lambda: self.remove_selected())
        self.search.textChanged.connect(self._filter)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.currentItemChanged.connect(lambda cur, _prev: self._show_props(cur))
        self.tree.itemSelectionChanged.connect(self._sync_buttons)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        sc = QShortcut(QKeySequence.Delete, self.tree)
        sc.setContext(Qt.WidgetShortcut)
        sc.activated.connect(lambda: self.remove_selected())
        from autosim.ui.widgets import install_copy
        install_copy(self.tree, context_menu=False)       # Ctrl+C: the current name / ID
        install_copy(self.props)                          # property values
        self.props.setSelectionMode(QAbstractItemView.ExtendedSelection)
        ThemeManager.instance().changed.connect(self._refresh_icons)      # bound: auto-disconnects
        self._refresh_icons()
        library.changed.connect(self.refresh)
        self.refresh()

    @staticmethod
    def _icon_btn(tip: str, name: str) -> QToolButton:
        b = QToolButton()
        b.setObjectName("db-tool")
        b.setIconSize(QSize(16, 16))
        b.setToolTip(tip)
        b.setAccessibleName(name)
        return b

    def _refresh_icons(self, _dark: bool = True) -> None:
        self.remove_btn.setIcon(make_icon("trash", icon_color()))
        self.reload_btn.setIcon(make_icon("refresh", icon_color()))

    # ---- files ------------------------------------------------------------------------------
    def load_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add database", "", DB_FILTER)
        for p in paths:
            self.load_file(p)

    def load_file(self, path: str) -> bool:
        """Add a file to the configuration and use it (on every channel); an error is shown, not raised."""
        try:
            self.library.add(path)
            return True
        except DatabaseError as e:
            QMessageBox.critical(self, "Cannot load database", str(e))
            return False

    def reload_file(self, path: str) -> bool:
        """Read the file again (after editing it); a failed parse keeps the version already loaded."""
        try:
            self.library.reload(path)
            return True
        except DatabaseError as e:
            QMessageBox.critical(self, "Cannot load database", str(e))
            return False

    def remove_file(self, path: str, confirm: bool = True) -> bool:
        """Remove a database from the configuration. Messages of it in the Signals window go with it (cyclic TX
        stops); with `confirm` the user is asked first when that happens."""
        e = self.library.entry(path)
        if e is None:
            return False
        reg, tx = self.ctx.can.registry, self.ctx.can.tx
        in_use = [j for j in tx.jobs() if reg.path_of(j.message) is not None and reg._same(reg.path_of(j.message), path)]
        if confirm and in_use:
            running = sum(1 for j in in_use if j.enabled)
            text = (f"Remove {Path(path).name}?\n{len(in_use)} message(s) from it will be removed from the Signals "
                    f"panel" + (f", {running} of them sending cyclically" if running else "") + ".")
            if QMessageBox.question(self, "Remove database", text, QMessageBox.Yes | QMessageBox.Cancel,
                                    QMessageBox.Cancel) != QMessageBox.Yes:
                return False
        self.library.remove(path)
        return True

    def _target_paths(self) -> list[str]:
        """Databases of the selected tree items, or the only database when just one is listed."""
        paths = []
        for it in self.tree.selectedItems():
            p = self._db_path(it)
            if p and p not in paths:
                paths.append(p)
        if not paths and len(self.library.entries) == 1:
            paths = [self.library.entries[0].path]
        return paths

    def remove_selected(self, confirm: bool = True) -> int:
        return sum(self.remove_file(p, confirm) for p in list(self._target_paths()))

    def _sync_buttons(self) -> None:
        on = bool(self._target_paths())
        self.remove_btn.setEnabled(on)
        self.reload_btn.setEnabled(on)

    def dragEnterEvent(self, e) -> None:
        if any(u.toLocalFile().lower().endswith(DB_SUFFIXES) for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(DB_SUFFIXES):
                self.load_file(u.toLocalFile())

    # ---- tree -----------------------------------------------------------------------------
    def refresh(self) -> None:
        expanded = {self._key(it) for it in self._all_items() if it.isExpanded()}
        cur = self.tree.currentItem()
        current = self._key(cur) if cur is not None else None
        self.tree.blockSignals(True)
        self.tree.clear()
        muted = QColor(token("TEXT_MUTED"))
        for e in self.library.entries:
            db = self.library.database(e.path)
            state = ("in use · " + ("all channels" if e.channel == "*" else e.channel)) if e.active else "not used"
            top = QTreeWidgetItem([e.name, state])
            top.setData(0, KIND, ("db", e.path))
            top.setToolTip(0, f"{e.path}\n{state}")
            top.setForeground(1, muted)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self.tree.addTopLevelItem(top)
            if db is None:
                continue
            nodes = QTreeWidgetItem(["Nodes", ""])
            nodes.setData(0, KIND, ("folder", "nodes"))
            top.addChild(nodes)
            by_sender: dict[str, list] = {n.name: [] for n in db.nodes}
            for m in db.messages:
                for s in (m.senders or []):
                    by_sender.setdefault(s, []).append(m)
            for name in sorted(by_sender):
                n_item = QTreeWidgetItem([name, ""])
                n_item.setData(0, KIND, ("node", name))
                nodes.addChild(n_item)
                if by_sender[name]:
                    tx = QTreeWidgetItem(["Tx messages", ""])
                    tx.setData(0, KIND, ("folder", "tx"))
                    n_item.addChild(tx)
                    for m in sorted(by_sender[name], key=lambda m: m.name):
                        tx.addChild(self._message_item(m))
            msgs = QTreeWidgetItem([f"Messages ({len(db.messages)})", ""])
            msgs.setData(0, KIND, ("folder", "messages"))
            top.addChild(msgs)
            for m in sorted(db.messages, key=lambda m: m.name):
                msgs.addChild(self._message_item(m))
            top.setExpanded(True)
            msgs.setExpanded(True)
        restored = None
        for it in self._all_items():
            if self._key(it) in expanded:
                it.setExpanded(True)
            if current is not None and restored is None and self._key(it) == current:
                restored = it
        if restored is not None:
            self.tree.setCurrentItem(restored)
        self.tree.blockSignals(False)
        self._show_props(restored)                         # the selection (and its Use / Channel) survives
        self.stack.setCurrentIndex(1 if self.library.entries else 0)
        self._filter(self.search.text())
        self._sync_buttons()

    def set_channels(self, channels: list[str]) -> None:
        self.channels = list(channels)
        self._show_props(self.tree.currentItem())         # the Channel choice lists the open channels

    def _apply_active(self, path: str, on: bool) -> None:
        try:
            self.library.set_active(path, on)
        except DatabaseError as e:
            QMessageBox.critical(self, "Cannot load database", str(e))
            self.refresh()

    def _db_editor(self, path: str, e) -> None:
        """Channel choice of a database, in its properties: All channels / a channel / Not used."""
        ch = QComboBox()
        ch.addItem(ALL_CHANNELS, ANY)
        for c in sorted(set(self.channels) | ({e.channel} - {ANY} if e else set())):
            ch.addItem(c, c)
        ch.addItem(NOT_USED, UNUSED)
        ch.setCurrentIndex(max(0, ch.findData(e.channel if e and e.active else UNUSED)))
        ch.setToolTip("Where the database is used (decoding, simulation, auto response): all channels, one channel\n"
                      "(e.g. powertrain DBC on CAN1, body DBC on CAN2), or not at all (it stays in the list)")
        ch.currentIndexChanged.connect(lambda _i, p=path, c=ch: self._choose(p, c.currentData()))
        self.channel_box = ch
        box = QWidget()
        bl = QHBoxLayout(box)
        bl.setContentsMargins(4, 0, 4, 0)
        bl.addWidget(ch)
        bl.addStretch()
        self.props.setCellWidget(1, 1, box)

    def _choose(self, path: str, channel: str) -> None:
        # Deferred: the library change rebuilds the tree and the properties, which deletes this combo while it is
        # still emitting the signal that asked for it.
        QTimer.singleShot(0, self, lambda: self._use_on(path, channel) if channel != UNUSED
                          else self._apply_active(path, False))

    def _message_item(self, m) -> QTreeWidgetItem:
        it = QTreeWidgetItem([m.name, _id(m)])
        it.setData(0, KIND, ("msg", m.name))
        it.setData(1, KIND, m.frame_id)
        it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        it.setText(2, _dlc(m))
        it.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
        it.setToolTip(2, f"{'CAN FD' if _is_fd(m) else 'Classic CAN'} frame, {m.length} bytes")
        if _is_fd(m):
            it.setForeground(2, QColor(token("ACCENT")))
        for s in sorted(m.signals, key=lambda s: s.start):
            si = QTreeWidgetItem([s.name, ""])
            si.setData(0, KIND, ("sig", m.name, s.name))
            it.addChild(si)
        return it

    def _all_items(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.child(i) for i in range(it.childCount()))

    def _key(self, it) -> tuple:
        parts = []
        while it is not None:
            parts.append(str(it.data(0, KIND)))
            it = it.parent()
        return tuple(parts)

    def _filter(self, text: str) -> None:
        t = text.lower().strip()
        tid = (t[2:] if t.startswith("0x") else t).lstrip("0") or ("0" if t else "")
        is_hex = bool(tid) and all(ch in "0123456789abcdef" for ch in tid)

        def visit(it) -> bool:
            kind = it.data(0, KIND) or ("",)
            own = bool(t) and (t in it.text(0).lower()
                               or (kind[0] == "msg" and is_hex and f"{it.data(1, KIND):x}".startswith(tid)))
            if kind[0] == "msg" and own:                  # a matching message shows all of its signals
                for i in range(it.childCount()):
                    it.child(i).setHidden(False)
                hit = True
            else:
                hit = own
                for i in range(it.childCount()):
                    hit = visit(it.child(i)) or hit
            if kind[0] == "db":
                it.setHidden(False)                       # files stay listed
            else:
                it.setHidden(bool(t) and not hit)
            if t and hit and kind[0] not in ("msg", "sig"):
                it.setExpanded(True)
            return hit

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def _db_path(self, item) -> str | None:
        while item is not None:
            kind = item.data(0, KIND)
            if kind and kind[0] == "db":
                return kind[1]
            item = item.parent()
        return None

    def _msg_name(self, item) -> str | None:
        while item is not None:
            kind = item.data(0, KIND)
            if kind and kind[0] == "msg":
                return kind[1]
            item = item.parent()
        return None

    def _message(self, item):
        name, path = self._msg_name(item), self._db_path(item)
        db = self.library.database(path) if path else None
        if not name or db is None:
            return None
        return next((m for m in db.messages if m.name == name), None)

    # ---- properties ----------------------------------------------------------------------------
    def _show_props(self, item) -> None:
        rows: list[tuple[str, str]] = []
        kind = item.data(0, KIND) if item is not None else None
        title = "Properties"
        self.channel_box = None
        self.props.clearContents()
        if kind and kind[0] == "db":
            e = self.library.entry(kind[1])
            db = self.library.database(kind[1])
            title = f"Database {e.name if e else ''}"
            rows = [("File", kind[1]), ("Channel", "")]                         # a choice (see _db_editor)
            if db is not None:
                rows += [("Nodes", str(len(db.nodes))), ("Messages", str(len(db.messages))),
                         ("Signals", str(sum(len(m.signals) for m in db.messages)))]
        elif kind and kind[0] in ("msg", "sig"):
            m = self._message(item)
            if m is not None and kind[0] == "msg":
                title = f"Message {m.name}"
                rows = [("Name", m.name), ("ID", _id(m)),
                        ("Frame", ("CAN FD" if _is_fd(m) else "CAN") + (", extended" if m.is_extended_frame else "")),
                        ("DLC", f"{m.length} bytes"), ("Cycle time", f"{m.cycle_time} ms" if m.cycle_time else "—"),
                        ("Senders", ", ".join(m.senders or []) or "—"), ("Signals", str(len(m.signals)))]
                if m.comment:
                    rows.append(("Comment", str(m.comment)))
            elif m is not None:
                s = next((s for s in m.signals if s.name == kind[2]), None)
                if s is not None:
                    title = f"Signal {s.name}"
                    rows = [("Name", s.name), ("Message", m.name), ("Start bit", str(s.start)),
                            ("Length", f"{s.length} bit"),
                            ("Byte order", "Intel" if s.byte_order == "little_endian" else "Motorola"),
                            ("Signed", "yes" if s.is_signed else "no"), ("Factor", _fmt(s.scale)),
                            ("Offset", _fmt(s.offset)), ("Minimum", _fmt(s.minimum)), ("Maximum", _fmt(s.maximum)),
                            ("Unit", s.unit or ""), ("Initial", _fmt(signal_initial(s)))]
                    if s.choices:
                        rows.append(("Values", "\n".join(f"{int(k)} = {v}" for k, v in s.choices.items())))
                    if s.comment:
                        rows.append(("Comment", str(s.comment)))
        elif kind and kind[0] == "node":
            title = f"Node {kind[1]}"
            rows = [("Name", kind[1])]
        self.props_title.setText(title)
        self.props.setRowCount(len(rows))
        for r, (k, v) in enumerate(rows):
            self.props.setItem(r, 0, QTableWidgetItem(k))
            self.props.setItem(r, 1, QTableWidgetItem(v))
        if kind and kind[0] == "db":
            self._db_editor(kind[1], self.library.entry(kind[1]))
        self.props.resizeRowsToContents()

    # ---- actions -----------------------------------------------------------------------------------
    def _double_clicked(self, item, _col) -> None:
        kind = item.data(0, KIND)
        if kind and kind[0] == "msg" and self.ctx.can.registry.message_by_name(kind[1]) is not None:
            self.messageActivated.emit(kind[1])

    def _use_on(self, path: str, channel: str) -> None:
        self.library.set_channel(path, channel)            # from a menu: nothing of ours is mid-signal
        e = self.library.entry(path)
        if e is not None and not e.active:
            self._apply_active(path, True)

    def _tree_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction("Copy name\tCtrl+C", lambda: QApplication.clipboard().setText(item.text(0)))
        menu.addSeparator()
        name = self._msg_name(item)
        if name and self.ctx.can.registry.message_by_name(name) is not None:
            menu.addAction("Add to Signals", lambda: self.messageActivated.emit(name))
        path = self._db_path(item)
        if path:
            e = self.library.entry(path)
            if menu.actions():
                menu.addSeparator()
            if e is not None:
                use = menu.addMenu("Use on")
                targets = [(ALL_CHANNELS, ANY)] + [(c, c) for c in sorted(set(self.channels) | ({e.channel} - {ANY}))]
                for label, ch in targets:
                    a = use.addAction(label, lambda c=ch: self._use_on(path, c))
                    a.setCheckable(True)
                    a.setChecked(e.active and e.channel == ch)
                if e.active:
                    menu.addAction("Do not use", lambda: self._apply_active(path, False))
            menu.addAction("Reload", lambda: self.reload_file(path))
            menu.addAction("Copy path", lambda: QApplication.clipboard().setText(path))
            menu.addSeparator()
            menu.addAction(f"Remove {Path(path).name}", lambda: self.remove_file(path))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
