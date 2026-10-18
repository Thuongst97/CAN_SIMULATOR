"""Bus trace: toolbar (pause / clear / export / autoscroll / filters), row flash and two views:
  * Fixed (default): one row per message updated in place, expandable to one row per signal (latest value)
  * Chronological: every frame as its own row (this is what Export writes)"""
from __future__ import annotations

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHeaderView, QLabel, QLineEdit, QMessageBox, QSizePolicy,
                               QStackedWidget, QTableView, QToolBar, QTreeView, QVBoxLayout, QWidget)

from autosim.core.can_engine.logger_io import export_by_extension
from autosim.ui.icons import make_icon
from autosim.ui.models.fixed_trace_model import FixedTraceModel
from autosim.ui.models.trace_model import TraceFilter, TraceModel
from autosim.ui.theme import icon_color
from autosim.ui.widgets import toolbar_divider

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
        self.model = TraceModel()                       # chronological (export source)
        self.fixed_model = FixedTraceModel(ctx.can.registry)
        self.dropped_while_paused = 0

        # ---- toolbar ----
        self.toolbar = QToolBar()
        self.toolbar.setMovable(False)
        self.toolbar.setIconSize(QSize(18, 18))        # with 32 px items: natural height, equal margins
        self.toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.pause = QAction("Pause", self, checkable=True)
        self.pause.setToolTip("Freeze the display (frames received meanwhile are not shown)")
        self.clear_act = QAction("Clear", self)
        self.export_act = QAction("Export", self)
        self.export_act.setToolTip("Export the visible rows to CSV / ASC / BLF")
        self.autoscroll = QAction("Follow", self, checkable=True)
        self.autoscroll.setChecked(True)
        self.autoscroll.setToolTip("Follow: keep the newest frame in view (chronological view)")
        self.flash = QAction("Highlight", self, checkable=True)
        self.flash.setChecked(True)
        self.flash.setToolTip("Highlight: briefly tint rows whose message was just updated")
        self.fixed = QAction("Fixed", self, checkable=True)
        self.fixed.setChecked(True)
        self.fixed.setToolTip("Fixed: one row per message with its signals' latest values\n"
                              "Off: chronological, one row per received / sent frame")
        for a in (self.fixed, self.pause, self.clear_act, self.export_act, self.autoscroll, self.flash):
            self.toolbar.addAction(a)
        self.toolbar.addWidget(toolbar_divider())          # 1 px line (QSS draws ::separator as a box)

        self.f_id = QLineEdit(placeholderText="ID (hex)")
        self.f_id.setToolTip("Filter by ID in hex; several separated by commas, e.g. 100, 1A0")
        self.f_id.setClearButtonEnabled(True)
        self.f_id.setMaximumWidth(170)
        self.f_id.setMinimumWidth(96)
        self.f_name = QLineEdit(placeholderText="Search name")
        self.f_name.setClearButtonEnabled(True)
        self.f_name.setMaximumWidth(170)
        self.f_name.setMinimumWidth(120)
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
        for i, w in enumerate((110, 80, 44, 80, 180, 64, 400, 104)):     # "Cycle [ms]" header fits
            self.view.setColumnWidth(i, w)
        hh.setSectionResizeMode(6, QHeaderView.Stretch)

        # ---- fixed view: message rows with signal children ----
        self.tree = QTreeView()
        self.tree.setObjectName("trace")
        self.tree.setModel(self.fixed_model)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionBehavior(QTreeView.SelectRows)
        self.tree.setAnimated(False)
        th = self.tree.header()
        th.setSectionResizeMode(QHeaderView.Interactive)
        th.setStretchLastSection(False)
        th.setFixedHeight(34)
        for i, w in enumerate((116, 80, 44, 80, 220, 64, 360, 104, 72)):  # "Cycle [ms]" header fits
            self.tree.setColumnWidth(i, w)
        th.setSectionResizeMode(6, QHeaderView.Stretch)

        for v in (self.tree, self.view):                  # the trace keeps the Space = pause shortcut
            v.setProperty("singleKeyShortcuts", True)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.view)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.stack)

        bridge.canBatch.connect(self.on_batch)
        for w in (self.f_id, self.f_name):
            w.textChanged.connect(self.apply_filter)
        self.f_chan.currentIndexChanged.connect(self.apply_filter)
        self.f_dir.currentIndexChanged.connect(self.apply_filter)
        self.clear_act.triggered.connect(self.clear)
        self.export_act.triggered.connect(self.export)
        self.pause.toggled.connect(self._pause_toggled)
        self.flash.toggled.connect(self._flash_toggled)
        self.fixed.toggled.connect(self._fixed_toggled)
        bridge.dbLoaded.connect(lambda _p: self.fixed_model.databases_changed())
        self._timer = QTimer(self)                      # fades the row highlight
        self._timer.timeout.connect(lambda: self.current_model().refresh_recent())
        self._timer.start(120)

    def current_model(self):
        return self.fixed_model if self.fixed.isChecked() else self.model

    def _flash_toggled(self, on: bool) -> None:
        self.model.flash_enabled = on
        self.fixed_model.flash_enabled = on

    def _fixed_toggled(self, on: bool) -> None:
        self.stack.setCurrentWidget(self.tree if on else self.view)
        self._update_count()

    def _update_count(self) -> None:
        if self.fixed.isChecked():
            self.count.setText(f"{self.fixed_model.message_count()} messages")
        else:
            self.count.setText(f"{self.model.rowCount()} rows")

    def refresh_icons(self) -> None:
        """(Re)paint toolbar icons in the current theme's accent color."""
        c = icon_color()
        self.pause.setIcon(make_icon("play" if self.pause.isChecked() else "pause", c))
        self.clear_act.setIcon(make_icon("trash", c))
        self.export_act.setIcon(make_icon("save", c))
        self.autoscroll.setIcon(make_icon("autoscroll", c))
        self.flash.setIcon(make_icon("flash", c))
        self.fixed.setIcon(make_icon("database", c))
        self.filter_label.setPixmap(make_icon("filter", c).pixmap(16, 16))

    def _pause_toggled(self, on: bool) -> None:
        self.pause.setText("Resume" if on else "Pause")
        self.refresh_icons()

    def on_batch(self, batch) -> None:
        if self.pause.isChecked():
            self.dropped_while_paused += len(batch)
            return
        self.model.add_batch(batch)
        self.fixed_model.add_batch(batch)
        self._update_count()
        if self.autoscroll.isChecked() and not self.fixed.isChecked():
            self.view.scrollToBottom()

    def apply_filter(self) -> None:
        d, ch = self.f_dir.currentText(), self.f_chan.currentText()
        flt = TraceFilter(TraceFilter.parse_ids(self.f_id.text()), self.f_name.text().strip(),
                          "" if ch == ALL else ch, "" if d == "Rx + Tx" else d)
        self.model.set_filter(flt)
        self.fixed_model.set_filter(flt)
        self._update_count()

    def clear(self) -> None:
        self.model.clear()
        self.fixed_model.clear()
        self._update_count()

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
