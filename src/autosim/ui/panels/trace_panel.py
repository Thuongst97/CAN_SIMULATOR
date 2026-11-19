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
        self._mirrors: list[TraceMirror] = []

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

        # ---- views: chronological table, fixed tree (message rows with signal children) ----
        self.view = self.make_table()
        self.tree = self.make_tree()

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
        bridge.dbUnloaded.connect(lambda _p: self.fixed_model.databases_changed())
        self._timer = QTimer(self)                      # fades the row highlight
        self._timer.timeout.connect(lambda: self.current_model().refresh_recent())
        self._timer.start(120)

    # ---- views (the Bus trace and its mirror under the Signals window show the same models) ------------------
    def make_table(self) -> QTableView:
        from autosim.ui.widgets import fill_column, install_copy
        v = QTableView()
        v.setObjectName("trace")
        v.setModel(self.model)
        v.setAlternatingRowColors(True)
        v.setSelectionBehavior(QTableView.SelectRows)
        v.setShowGrid(True)
        v.verticalHeader().setVisible(False)
        v.verticalHeader().setDefaultSectionSize(22)
        hh = v.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)          # every column: drag its header edge
        hh.setFixedHeight(34)
        for i, w in enumerate((110, 80, 44, 80, 180, 64, 400, 104)):     # "Cycle [ms]" header fits
            v.setColumnWidth(i, w)
        fill_column(v, 6, 200)                                    # Data takes the free width
        v.setProperty("singleKeyShortcuts", True)                 # the trace keeps the Space = pause shortcut
        v.setSelectionMode(QTableView.ExtendedSelection)
        install_copy(v)                                           # Ctrl+C / right-click: copy a cell or rows
        return v

    def make_tree(self) -> QTreeView:
        from autosim.ui.widgets import fill_column, install_copy
        v = QTreeView()
        v.setObjectName("trace")
        v.setModel(self.fixed_model)
        v.setAlternatingRowColors(True)
        v.setUniformRowHeights(True)
        v.setSelectionBehavior(QTreeView.SelectRows)
        v.setAnimated(False)
        th = v.header()
        th.setSectionResizeMode(QHeaderView.Interactive)
        th.setFixedHeight(34)
        for i, w in enumerate((116, 80, 44, 80, 220, 64, 360, 104, 72)):  # "Cycle [ms]" header fits
            v.setColumnWidth(i, w)
        fill_column(v, 6, 200)
        v.setProperty("singleKeyShortcuts", True)
        v.setSelectionMode(QTreeView.ExtendedSelection)
        install_copy(v)
        return v

    def make_mirror(self) -> TraceMirror:
        """The same trace with the same toolbar and filters, e.g. under the Signals window."""
        m = TraceMirror(self)
        self._mirrors.append(m)
        return m

    def current_model(self):
        return self.fixed_model if self.fixed.isChecked() else self.model

    def _flash_toggled(self, on: bool) -> None:
        self.model.flash_enabled = on
        self.fixed_model.flash_enabled = on

    def _fixed_toggled(self, on: bool) -> None:
        self.stack.setCurrentWidget(self.tree if on else self.view)
        for m in self._mirrors:
            m.stack.setCurrentWidget(m.tree if on else m.view)
        self._update_count()

    def _update_count(self) -> None:
        if self.fixed.isChecked():
            text = f"{self.fixed_model.message_count()} messages"
        else:
            text = f"{self.model.rowCount()} rows"
        self.count.setText(text)
        for m in self._mirrors:
            m.count.setText(text)

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
        for m in getattr(self, "_mirrors", []):
            m.filter_label.setPixmap(make_icon("filter", c).pixmap(16, 16))

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
            for m in self._mirrors:
                m.view.scrollToBottom()

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


class TraceMirror(QWidget):
    """A second Bus trace (Simulation desktop): the same models, toolbar actions (Fixed, Pause, Clear, Export,
    Follow, Highlight) and filters as the Bus trace - a filter typed in one shows in the other - so it costs no
    extra work per frame."""

    def __init__(self, panel: TracePanel) -> None:
        super().__init__()
        self.panel = panel
        self.toolbar = QToolBar()
        self.toolbar.setMovable(False)
        self.toolbar.setIconSize(QSize(18, 18))
        self.toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for a in (panel.fixed, panel.pause, panel.clear_act, panel.export_act, panel.autoscroll, panel.flash):
            self.toolbar.addAction(a)                      # the same QActions: state and text follow everywhere
        self.toolbar.addWidget(toolbar_divider())
        self.filter_label = QLabel()
        self.toolbar.addWidget(self.filter_label)
        self.f_id = QLineEdit(placeholderText=panel.f_id.placeholderText())
        self.f_id.setToolTip(panel.f_id.toolTip())
        self.f_name = QLineEdit(placeholderText=panel.f_name.placeholderText())
        for mine, theirs in ((self.f_id, panel.f_id), (self.f_name, panel.f_name)):
            mine.setClearButtonEnabled(True)
            mine.setMaximumWidth(theirs.maximumWidth())
            mine.setMinimumWidth(theirs.minimumWidth())
            mine.setText(theirs.text())
            mine.textChanged.connect(lambda t, w=theirs: w.text() != t and w.setText(t))
            theirs.textChanged.connect(lambda t, w=mine: w.text() != t and w.setText(t))
        self.f_chan = ChannelFilterCombo(panel.ctx)
        self.f_dir = QComboBox()
        self.f_dir.addItems([panel.f_dir.itemText(i) for i in range(panel.f_dir.count())])
        for mine, theirs in ((self.f_chan, panel.f_chan), (self.f_dir, panel.f_dir)):
            mine.currentTextChanged.connect(lambda t, w=theirs: self._sync_combo(w, t))
            theirs.currentTextChanged.connect(lambda t, w=mine: self._sync_combo(w, t))
        for w in (self.f_id, self.f_name, self.f_chan, self.f_dir):
            self.toolbar.addWidget(w)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toolbar.addWidget(spacer)
        self.count = QLabel(panel.count.text())
        self.toolbar.addWidget(self.count)
        self.filter_label.setPixmap(make_icon("filter", icon_color()).pixmap(16, 16))

        self.view = panel.make_table()
        self.tree = panel.make_tree()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.view)
        self.stack.setCurrentWidget(self.tree if panel.fixed.isChecked() else self.view)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.stack)

    @staticmethod
    def _sync_combo(combo: QComboBox, text: str) -> None:
        if combo.currentText() == text:
            return
        if combo.findText(text) < 0:                       # a channel opened since the list was filled
            combo.addItem(text)
        combo.setCurrentText(text)

    def current_view(self):
        return self.stack.currentWidget()
