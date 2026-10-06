"""Bus trace: toolbar (pause / clear / export / autoscroll / filters), virtual table, row flash."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHeaderView, QLabel, QLineEdit, QMessageBox, QSizePolicy,
                               QTableView, QToolBar, QVBoxLayout, QWidget)

from autosim.core.can_engine.logger_io import export_by_extension
from autosim.ui.icons import make_icon
from autosim.ui.models.trace_model import TraceFilter, TraceModel
from autosim.ui.theme import icon_color

ALL = "All channels"


class ChannelFilterCombo(QComboBox):
    """'All channels' + the currently open channels (refreshed whenever the popup opens)."""

    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self.setMinimumWidth(130)
        self.addItem(ALL)

    def showPopup(self) -> None:
        cur = self.currentText()
        self.blockSignals(True)
        self.clear()
        self.addItems([ALL] + self.ctx.can.bus.channels)
        self.setCurrentText(cur if self.findText(cur) >= 0 else ALL)
        self.blockSignals(False)
        super().showPopup()


class TracePanel(QWidget):
    def __init__(self, ctx, bridge, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.model = TraceModel()
        self.dropped_while_paused = 0

        # ---- toolbar ----
        self.toolbar = QToolBar()
        self.toolbar.setMovable(False)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.pause = QAction("Pause", self, checkable=True)
        self.pause.setToolTip("Freeze the display (frames received meanwhile are not shown)")
        self.clear_act = QAction("Clear", self)
        self.export_act = QAction("Export", self)
        self.export_act.setToolTip("Export the visible rows to CSV / ASC / BLF")
        self.autoscroll = QAction("Autoscroll", self, checkable=True)
        self.autoscroll.setChecked(True)
        self.flash = QAction("Highlight", self, checkable=True)
        self.flash.setChecked(True)
        self.flash.setToolTip("Briefly highlight rows of newly updated IDs")
        for a in (self.pause, self.clear_act, self.export_act, self.autoscroll, self.flash):
            self.toolbar.addAction(a)
        for a in (self.autoscroll, self.flash):          # keep these compact: icon only
            self.toolbar.widgetForAction(a).setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.toolbar.addSeparator()

        self.f_id = QLineEdit(placeholderText="ID hex  (100, 1A0)")
        self.f_id.setClearButtonEnabled(True)
        self.f_id.setMaximumWidth(170)
        self.f_name = QLineEdit(placeholderText="Search name")
        self.f_name.setClearButtonEnabled(True)
        self.f_name.setMaximumWidth(170)
        self.f_chan = ChannelFilterCombo(ctx)
        self.f_dir = QComboBox()
        self.f_dir.addItems(["Rx + Tx", "Rx", "Tx"])
        self.filter_label = QLabel()
        self.toolbar.addWidget(self.filter_label)
        for w in (self.f_id, self.f_name, self.f_chan, self.f_dir):
            self.toolbar.addWidget(w)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toolbar.addWidget(spacer)
        self.count = QLabel("0 rows")
        self.toolbar.addWidget(self.count)
        self.refresh_icons()

        # ---- table ----
        self.view = QTableView()
        self.view.setObjectName("trace")
        self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QTableView.SelectRows)
        self.view.setShowGrid(True)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(22)
        hh = self.view.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        hh.setFixedHeight(34)
        for i, w in enumerate((100, 90, 40, 80, 170, 60, 420, 90)):
            self.view.setColumnWidth(i, w)
        hh.setSectionResizeMode(6, QHeaderView.Stretch)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.view)

        bridge.canBatch.connect(self.on_batch)
        for w in (self.f_id, self.f_name):
            w.textChanged.connect(self.apply_filter)
        self.f_chan.currentIndexChanged.connect(self.apply_filter)
        self.f_dir.currentIndexChanged.connect(self.apply_filter)
        self.clear_act.triggered.connect(self.clear)
        self.export_act.triggered.connect(self.export)
        self.pause.toggled.connect(self._pause_toggled)
        self.flash.toggled.connect(lambda on: setattr(self.model, "flash_enabled", on))
        self._timer = QTimer(self)                      # fades the row highlight
        self._timer.timeout.connect(self.model.refresh_recent)
        self._timer.start(120)

    def refresh_icons(self) -> None:
        """(Re)paint toolbar icons in the current theme's accent color."""
        c = icon_color()
        self.pause.setIcon(make_icon("play" if self.pause.isChecked() else "pause", c))
        self.clear_act.setIcon(make_icon("trash", c))
        self.export_act.setIcon(make_icon("save", c))
        self.autoscroll.setIcon(make_icon("autoscroll", c))
        self.flash.setIcon(make_icon("flash", c))
        self.filter_label.setPixmap(make_icon("filter", c).pixmap(16, 16))

    def _pause_toggled(self, on: bool) -> None:
        self.pause.setText("Resume" if on else "Pause")
        self.refresh_icons()

    def on_batch(self, batch) -> None:
        if self.pause.isChecked():
            self.dropped_while_paused += len(batch)
            return
        self.model.add_batch(batch)
        self.count.setText(f"{self.model.rowCount()} rows")
        if self.autoscroll.isChecked():
            self.view.scrollToBottom()

    def apply_filter(self) -> None:
        d, ch = self.f_dir.currentText(), self.f_chan.currentText()
        self.model.set_filter(TraceFilter(TraceFilter.parse_ids(self.f_id.text()), self.f_name.text().strip(),
                                          "" if ch == ALL else ch, "" if d == "Rx + Tx" else d))
        self.count.setText(f"{self.model.rowCount()} rows")

    def clear(self) -> None:
        self.model.clear()
        self.count.setText("0 rows")

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export trace", "trace.csv",
                                              "CSV (*.csv);;Vector ASC (*.asc);;Vector BLF (*.blf)")
        if not path:
            return
        try:
            n = export_by_extension(self.model.rows(), path)
            QMessageBox.information(self, "Export", f"{n} rows written to\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
