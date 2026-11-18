"""Databases window of the Setup desktop: the databases of the configuration, which ones are used and on which
channel (CANoe: Simulation Setup > Databases of a network).

    Use  Database          Messages   Channel
    [x]  vw_mqb.dbc             113   [All channels ▾]
    [ ]  body.dbc                45   [CAN2         ▾]
                                                  [Manage databases…]

Files are added / removed on the Database desktop; here the user only picks from that list.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QPushButton,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from autosim.core.can_engine.database import ANY, DatabaseError
from autosim.ui.widgets import EmptyState

ALL_CHANNELS = "All channels"
C_USE, C_NAME, C_MSGS, C_CHANNEL = range(4)


class DatabaseSetup(QWidget):
    manageRequested = Signal()                   # open the Database desktop

    def __init__(self, library, parent=None) -> None:
        super().__init__(parent)
        self.library = library
        self.channels: list[str] = []
        self._paths: list[str] | None = None              # files shown, in order
        self.table = QTableWidget(0, 4)
        self.table.setObjectName("db-setup")
        self.table.setHorizontalHeaderLabels(["Use", "Database", "Messages", "Channel"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(C_NAME, QHeaderView.Stretch)
        hh.setFixedHeight(32)
        self.manage_btn = QPushButton("Manage databases…")
        self.manage_btn.setToolTip("Add, remove and browse databases (Database desktop)")
        self.manage_btn.clicked.connect(self.manageRequested)
        foot = QHBoxLayout()
        foot.addStretch()
        foot.addWidget(self.manage_btn)
        listing = QWidget()
        ll = QVBoxLayout(listing)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)
        ll.addWidget(self.table, 1)
        ll.addLayout(foot)

        self.empty = EmptyState("database", "No databases yet",
                                "Add DBC or ARXML files on the Database desktop; they are listed here to choose "
                                "which ones are used, and on which channel.")
        self.empty.add_button("Add databases…").clicked.connect(self.manageRequested)
        empty_page = QWidget()
        el = QVBoxLayout(empty_page)
        el.addStretch()
        el.addWidget(self.empty, 0, Qt.AlignHCenter)
        el.addStretch(2)
        self.stack = QStackedWidget()
        self.stack.addWidget(empty_page)
        self.stack.addWidget(listing)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.stack)
        library.changed.connect(self.refresh)
        self.refresh()

    def set_channels(self, channels: list[str]) -> None:
        self.channels = list(channels)
        self._paths = None                                # rebuild: the channel lists change
        self.refresh()

    def refresh(self) -> None:
        entries = self.library.entries
        self.stack.setCurrentIndex(1 if entries else 0)
        if [e.path for e in entries] == self._paths:      # same files: update in place (a change usually comes
            for r, e in enumerate(entries):               # from one of these widgets - never delete it mid-signal)
                use, ch = self.use_box(r), self.channel_box(r)
                use.blockSignals(True)
                use.setChecked(e.active)
                use.blockSignals(False)
                if ch.findData(e.channel) < 0:
                    ch.addItem(e.channel, e.channel)
                ch.blockSignals(True)
                ch.setCurrentIndex(max(0, ch.findData(e.channel)))
                ch.blockSignals(False)
            return
        self._paths = [e.path for e in entries]
        self.table.setRowCount(len(entries))
        for r, e in enumerate(entries):
            use = QCheckBox()
            use.setChecked(e.active)
            use.setToolTip("Use this database (decode, simulate, auto response)")
            use.toggled.connect(lambda on, p=e.path: self._set_active(p, on))
            box = QWidget()                                  # centred checkbox
            bl = QHBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.addWidget(use, 0, Qt.AlignCenter)
            self.table.setCellWidget(r, C_USE, box)
            name = QTableWidgetItem(e.name)
            name.setToolTip(e.path)
            self.table.setItem(r, C_NAME, name)
            db = self.library.database(e.path)
            n = QTableWidgetItem("" if db is None else str(len(db.messages)))
            n.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, C_MSGS, n)
            ch = QComboBox()
            ch.addItem(ALL_CHANNELS, ANY)
            for c in sorted(set(self.channels) | ({e.channel} - {ANY})):
                ch.addItem(c, c)
            ch.setCurrentIndex(max(0, ch.findData(e.channel)))
            ch.setToolTip("Channel the database applies to")
            ch.setMinimumWidth(150)
            ch.currentIndexChanged.connect(lambda _i, p=e.path, c=ch: self.library.set_channel(p, c.currentData()))
            self.table.setCellWidget(r, C_CHANNEL, ch)
        self.table.resizeRowsToContents()

    def _set_active(self, path: str, on: bool) -> None:
        try:
            self.library.set_active(path, on)
        except DatabaseError as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Cannot load database", str(e))
            self.refresh()

    def use_box(self, row: int) -> QCheckBox:
        return self.table.cellWidget(row, C_USE).findChild(QCheckBox)

    def channel_box(self, row: int) -> QComboBox:
        return self.table.cellWidget(row, C_CHANNEL)
