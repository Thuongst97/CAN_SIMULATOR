"""Auto response panel: a table of rules "when this frame is received, send that frame", a master switch and
the rule editor dialog. The rules live in the engine (core/can_engine/auto_response.py); this panel edits them,
shows how often each one fired, and persists them (QSettings between runs, and in the project file).

Typical use (Android VHAL bridge): load the DBC, press *Add rule*, pick the request message the head unit sends
(e.g. HU_Request 0x021), condition *Signal value* ReqValue == 10, response GW_Response 0x027 with RespValue = 10;
a second rule ReqValue == 20 -> RespValue = 100; switch *Auto response* on.
"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from autosim.core.can_engine.auto_response import (
    MATCH_ANY,
    MATCH_RAW,
    MATCH_SIGNAL,
    OPS,
    RAW_SIZES,
    TX_RAW,
    TX_SIGNAL,
    ResponseRule,
    RuleError,
    id_text,
    parse_int,
)
from autosim.core.common.frame import CanFrame
from autosim.ui.settings_store import app_settings
from autosim.ui.theme import palette
from autosim.ui.widgets import CONTROL_HEIGHT, set_kind

SETTINGS_KEY = "auto_response/config"
COLS = ["On", "Name", "Received", "Condition", "Response", "Payload", "Delay", "Hits"]
C_ON, C_NAME, C_RX, C_COND, C_TX, C_PAYLOAD, C_DELAY, C_HITS = range(len(COLS))
ANY_CHANNEL = "Any channel"
SAME_CHANNEL = "Same channel as received"
ORDERS = [("Intel (LE)", "little"), ("Motorola (BE)", "big")]


def _msg_label(m) -> str:
    return f"{m.name}  ({id_text(m.frame_id, m.is_extended_frame)})"


def message_label(registry, can_id: int, ext: bool) -> str:
    """'GW_Response (0x027)' when the id is in a loaded database, else just the id."""
    m = registry.message_by_id("*", can_id) if registry is not None else None
    if m is not None and bool(m.is_extended_frame) == ext:
        return _msg_label(m)
    return id_text(can_id, ext)


# ---- message picker: DBC messages or a typed id ------------------------------------------------------------
class MessagePicker(QWidget):
    """Editable combo listing the database's messages; any typed id (0x21, 33, 0x18FF0027) is accepted too.
    `ext` follows the chosen DBC message and can be set by hand for typed ids."""
    changed = Signal()

    def __init__(self, registry, parent=None) -> None:
        super().__init__(parent)
        self.registry = registry
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.NoInsert)
        self.combo.lineEdit().setPlaceholderText("DBC message or id, e.g. 0x21")
        self.combo.setFixedHeight(CONTROL_HEIGHT)
        self.ext = QCheckBox("29-bit")
        self.ext.setToolTip("Extended (29-bit) identifier")
        for m in sorted(registry.messages(), key=lambda m: (m.frame_id, m.name)) if registry else []:
            self.combo.addItem(_msg_label(m), m)
        self.combo.setCurrentIndex(-1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.combo, 1)
        lay.addWidget(self.ext)
        self.combo.currentIndexChanged.connect(self._picked)
        self.combo.editTextChanged.connect(lambda _t: self.changed.emit())
        self.ext.toggled.connect(lambda _on: self.changed.emit())

    def _picked(self, i: int) -> None:
        m = self.combo.itemData(i) if i >= 0 else None
        if m is not None:
            self.ext.blockSignals(True)
            self.ext.setChecked(bool(m.is_extended_frame))
            self.ext.blockSignals(False)
        self.changed.emit()

    def message(self):
        """The DBC message matching the current id, or None."""
        try:
            can_id = self.can_id()
        except RuleError:
            return None
        m = self.registry.message_by_id("*", can_id) if self.registry else None
        return m if m is not None and bool(m.is_extended_frame) == self.ext.isChecked() else None

    def can_id(self) -> int:
        i = self.combo.findText(self.combo.currentText())
        if i >= 0 and self.combo.itemData(i) is not None:
            return self.combo.itemData(i).frame_id
        text = self.combo.currentText().strip()
        if not text:
            raise RuleError("choose a message or type an id")
        head = text.split()[0]
        if not head.lower().startswith("0x") and not head.isdigit():
            raise RuleError(f"{text!r} is neither a database message nor an id (e.g. 0x21)")
        return parse_int(head)

    def set_id(self, can_id: int, ext: bool) -> None:
        m = self.registry.message_by_id("*", can_id) if self.registry else None
        if m is not None and bool(m.is_extended_frame) == ext:
            i = self.combo.findText(_msg_label(m))
            self.combo.setCurrentIndex(i)
        else:
            self.combo.setCurrentIndex(-1)
            self.combo.setEditText(id_text(can_id, ext))
        self.ext.setChecked(ext)


def _ops_combo() -> QComboBox:
    c = QComboBox()
    c.addItems(list(OPS))
    c.setFixedHeight(CONTROL_HEIGHT)
    return c


def _size_combo() -> QComboBox:
    c = QComboBox()
    for n in RAW_SIZES:
        c.addItem(f"{n} byte{'s' if n > 1 else ''}", n)
    c.setSizeAdjustPolicy(QComboBox.AdjustToContents)
    c.setToolTip("Size of the integer")
    return c


def _order_combo() -> QComboBox:
    c = QComboBox()
    for label, v in ORDERS:
        c.addItem(label, v)
    c.setSizeAdjustPolicy(QComboBox.AdjustToContents)
    c.setToolTip("Byte order: Intel = little-endian (lowest byte first), Motorola = big-endian")
    return c


def _set_data(combo: QComboBox, value) -> None:
    i = combo.findData(value)
    if i >= 0:
        combo.setCurrentIndex(i)


# ---- rule editor --------------------------------------------------------------------------------------------
class RuleDialog(QDialog):
    """Edit one rule. OK validates through the engine (AutoResponder.compile) and shows the problem if any."""

    def __init__(self, ctx, rule: ResponseRule | None = None, channels: list[str] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Auto response rule")
        self.setMinimumWidth(660)
        self.ctx = ctx
        reg = ctx.can.registry
        channels = channels if channels is not None else list(ctx.can.bus.channels)

        self.name = QLineEdit(placeholderText="optional, e.g. Request 10 -> 10")
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(True)

        # -- trigger --
        self.rx_channel = QComboBox()
        self.rx_channel.setEditable(True)
        self.rx_channel.addItem(ANY_CHANNEL)
        self.rx_channel.addItems(channels)
        self.rx_msg = MessagePicker(reg)
        self.match = QComboBox()
        self.match.addItem("Any data", MATCH_ANY)
        self.match.addItem("Signal value (DBC)", MATCH_SIGNAL)
        self.match.addItem("Raw bytes", MATCH_RAW)
        self.signal = QComboBox()
        self.op = _ops_combo()
        self.value = QLineEdit("10")
        self.value.setToolTip("Compared value: physical signal value, or for raw bytes an integer (10, 0x0A)")
        self.raw_offset = QSpinBox()
        self.raw_offset.setRange(0, 63)
        self.raw_size = _size_combo()
        self.raw_order = _order_combo()

        sig_page = QWidget()
        sl = QHBoxLayout(sig_page)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(QLabel("Signal"))
        sl.addWidget(self.signal, 1)
        raw_page = QWidget()
        rl = QHBoxLayout(raw_page)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("Byte"))
        rl.addWidget(self.raw_offset)
        rl.addWidget(self.raw_size)
        rl.addWidget(self.raw_order, 1)
        self.cond_stack = QStackedWidget()
        self.cond_stack.addWidget(QWidget())
        self.cond_stack.addWidget(sig_page)
        self.cond_stack.addWidget(raw_page)
        self.cmp_row = QWidget()
        cl = QHBoxLayout(self.cmp_row)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(QLabel("is"))
        cl.addWidget(self.op)
        cl.addWidget(self.value, 1)

        rx_box = QGroupBox("When received")
        rf = QFormLayout(rx_box)
        rf.addRow("Channel", self.rx_channel)
        rf.addRow("Message", self.rx_msg)
        rf.addRow("Condition", self.match)
        rf.addRow("", self.cond_stack)
        rf.addRow("", self.cmp_row)

        # -- response --
        self.tx_channel = QComboBox()
        self.tx_channel.setEditable(True)
        self.tx_channel.addItem(SAME_CHANNEL)
        self.tx_channel.addItems(channels)
        self.tx_msg = MessagePicker(reg)
        self.tx_fd, self.tx_brs = QCheckBox("FD"), QCheckBox("BRS")
        self.tx_mode = QComboBox()
        self.tx_mode.addItem("Raw bytes", TX_RAW)
        self.tx_mode.addItem("Signal values (DBC)", TX_SIGNAL)
        self.tx_dlc = QSpinBox()
        self.tx_dlc.setRange(0, 64)
        self.tx_dlc.setValue(8)
        self.tx_data = QLineEdit(placeholderText="base bytes in hex, e.g. 00 00 00 00 (rest = 00)")
        self.tx_value = QLineEdit("10")
        self.tx_value.setToolTip("Number (10, 0x64) or an expression of rx - the value the condition read:\n"
                                 "rx, rx * 10, rx + 1. Empty = keep the hex bytes as they are.")
        self.tx_offset = QSpinBox()
        self.tx_offset.setRange(0, 63)
        self.tx_size = _size_combo()
        self.tx_order = _order_combo()
        self.sig_table = QTableWidget(0, 2)
        self.sig_table.setHorizontalHeaderLabels(["Signal", "Value (empty = initial)"])
        self.sig_table.verticalHeader().setVisible(False)
        self.sig_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.sig_table.horizontalHeader().setStretchLastSection(True)
        self.sig_table.setMinimumHeight(110)

        self.tx_dlc.setToolTip("Payload length in bytes (CAN FD lengths above 8 are rounded up: 12, 16 ... 64)")
        self.tx_data.setToolTip("Base payload in hex; missing bytes are 00")
        self.tx_dlc.setFixedWidth(72)
        self.tx_offset.setFixedWidth(72)
        self.tx_value.setMaximumWidth(120)
        raw_tx = QWidget()
        g = QGridLayout(raw_tx)
        g.setContentsMargins(0, 0, 0, 0)
        g.setVerticalSpacing(6)
        g.addWidget(QLabel("Length"), 0, 0)
        g.addWidget(self.tx_dlc, 0, 1)
        g.addWidget(QLabel("Data"), 0, 2)
        g.addWidget(self.tx_data, 0, 3, 1, 3)
        g.addWidget(QLabel("Value"), 1, 0)
        g.addWidget(self.tx_value, 1, 1)
        g.addWidget(QLabel("at byte"), 1, 2)
        g.addWidget(self.tx_offset, 1, 3)
        g.addWidget(self.tx_size, 1, 4)
        g.addWidget(self.tx_order, 1, 5)
        g.setColumnStretch(1, 0)
        g.setColumnStretch(5, 1)
        self.tx_stack = QStackedWidget()
        self.tx_stack.addWidget(raw_tx)
        self.tx_stack.addWidget(self.sig_table)
        flags = QHBoxLayout()
        flags.addWidget(self.tx_fd)
        flags.addWidget(self.tx_brs)
        flags.addStretch()
        self.delay = QSpinBox()
        self.delay.setRange(0, 600_000)
        self.delay.setSuffix(" ms")

        tx_box = QGroupBox("Then send")
        tf = QFormLayout(tx_box)
        tf.addRow("Channel", self.tx_channel)
        tf.addRow("Message", self.tx_msg)
        tf.addRow("Frame", flags)
        tf.addRow("Payload", self.tx_mode)
        tf.addRow("", self.tx_stack)
        tf.addRow("Delay", self.delay)

        self.preview = QLabel()
        self.preview.setObjectName("rule-preview")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        set_kind(self.buttons.button(QDialogButtonBox.Ok), "primary")

        top = QHBoxLayout()
        top.addWidget(QLabel("Name"))
        top.addWidget(self.name, 1)
        top.addWidget(self.enabled)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(rx_box)
        lay.addWidget(tx_box)
        lay.addWidget(self.preview)
        lay.addWidget(self.buttons)

        self.match.currentIndexChanged.connect(self._match_changed)
        self.rx_msg.changed.connect(self._rx_msg_changed)
        self.tx_msg.changed.connect(self._tx_msg_changed)
        self.tx_mode.currentIndexChanged.connect(self._tx_mode_changed)
        self.tx_fd.toggled.connect(lambda on: (self.tx_brs.setEnabled(on), self.update_preview()))
        for w in (self.value, self.tx_data, self.tx_value, self.name):
            w.textChanged.connect(self.update_preview)
        for w in (self.op, self.raw_size, self.raw_order, self.tx_size, self.tx_order, self.signal,
                  self.rx_channel, self.tx_channel):
            w.currentIndexChanged.connect(self.update_preview)
        for w in (self.raw_offset, self.tx_offset, self.tx_dlc, self.delay):
            w.valueChanged.connect(self.update_preview)
        self.tx_brs.toggled.connect(self.update_preview)
        self.sig_table.itemChanged.connect(lambda _i: self.update_preview())
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        self._loading = False
        self.set_rule(rule or ResponseRule())
        self._match_changed()
        self._tx_mode_changed()
        self.tx_brs.setEnabled(self.tx_fd.isChecked())
        self.update_preview()

    # ---- dynamic parts ---------------------------------------------------------------------------------------
    def _match_changed(self) -> None:
        mode = self.match.currentData()
        self.cond_stack.setCurrentIndex({MATCH_ANY: 0, MATCH_SIGNAL: 1, MATCH_RAW: 2}[mode])
        self.cond_stack.setVisible(mode != MATCH_ANY)
        self.cmp_row.setVisible(mode != MATCH_ANY)
        self.update_preview()

    def _rx_msg_changed(self) -> None:
        cur = self.signal.currentText()
        self.signal.blockSignals(True)
        self.signal.clear()
        m = self.rx_msg.message()
        if m is not None:
            self.signal.addItems([s.name for s in m.signals])
            if self.signal.findText(cur) >= 0:
                self.signal.setCurrentText(cur)
        self.signal.blockSignals(False)
        if m is not None and not self._loading and self.match.currentData() == MATCH_ANY and m.signals:
            self.match.setCurrentIndex(self.match.findData(MATCH_SIGNAL))      # a DBC message: compare a signal
        self.update_preview()

    def _tx_msg_changed(self) -> None:
        m = self.tx_msg.message()
        old = self._signal_values()
        self.sig_table.blockSignals(True)
        self.sig_table.setRowCount(0)
        if m is not None:
            for s in m.signals:
                r = self.sig_table.rowCount()
                self.sig_table.insertRow(r)
                name = QTableWidgetItem(s.name)
                name.setFlags(Qt.ItemIsEnabled)
                unit = f" [{s.unit}]" if s.unit else ""
                rng = f"{s.minimum:g}..{s.maximum:g}" if s.minimum is not None and s.maximum is not None else ""
                name.setToolTip(f"{s.name}{unit}  {rng}  initial {s.initial if s.initial is not None else 0}")
                self.sig_table.setItem(r, 0, name)
                self.sig_table.setItem(r, 1, QTableWidgetItem(old.get(s.name, "")))
            if not self._loading:
                self.tx_mode.setCurrentIndex(self.tx_mode.findData(TX_SIGNAL))
                fd = bool(m.is_fd) or m.length > 8
                self.tx_fd.setChecked(fd)
                self.tx_brs.setChecked(fd)
                self.tx_dlc.setValue(m.length)
        self.sig_table.blockSignals(False)
        self.update_preview()

    def _tx_mode_changed(self) -> None:
        self.tx_stack.setCurrentIndex(0 if self.tx_mode.currentData() == TX_RAW else 1)
        self.update_preview()

    def _signal_values(self) -> dict[str, str]:
        out = {}
        for r in range(self.sig_table.rowCount()):
            v = self.sig_table.item(r, 1)
            if v is not None and v.text().strip():
                out[self.sig_table.item(r, 0).text()] = v.text().strip()
        return out

    # ---- rule <-> widgets ------------------------------------------------------------------------------------
    def set_rule(self, r: ResponseRule) -> None:
        self._loading = True
        try:
            self.name.setText(r.name)
            self.enabled.setChecked(r.enabled)
            self.rx_channel.setCurrentText(r.rx_channel or ANY_CHANNEL)
            if r.rx_id or r.tx_id or r.name:
                self.rx_msg.set_id(r.rx_id, r.rx_extended)
                self.tx_msg.set_id(r.tx_id, r.tx_extended)
            self.match.setCurrentIndex(self.match.findData(r.match))
            self._rx_msg_changed()
            if r.signal:
                if self.signal.findText(r.signal) < 0:
                    self.signal.addItem(r.signal)
                self.signal.setCurrentText(r.signal)
            self.op.setCurrentText(r.op)
            self.value.setText(str(r.value))
            self.raw_offset.setValue(r.raw_offset)
            _set_data(self.raw_size, r.raw_size)
            _set_data(self.raw_order, r.raw_order)
            self.tx_channel.setCurrentText(r.tx_channel or SAME_CHANNEL)
            self.tx_fd.setChecked(r.tx_fd)
            self.tx_brs.setChecked(r.tx_brs)
            self.tx_mode.setCurrentIndex(self.tx_mode.findData(r.tx_mode))
            self.tx_dlc.setValue(r.tx_dlc)
            self.tx_data.setText(r.tx_data)
            self.tx_value.setText(r.tx_value)
            self.tx_offset.setValue(r.tx_offset)
            _set_data(self.tx_size, r.tx_size)
            _set_data(self.tx_order, r.tx_order)
            self._tx_msg_changed()
            for row in range(self.sig_table.rowCount()):
                name = self.sig_table.item(row, 0).text()
                if name in r.tx_signals:
                    self.sig_table.item(row, 1).setText(str(r.tx_signals[name]))
            self.delay.setValue(r.delay_ms)
        finally:
            self._loading = False

    def rule(self) -> ResponseRule:
        """Rule from the widgets; raises RuleError when an id cannot be read."""
        rx_ch = self.rx_channel.currentText().strip()
        tx_ch = self.tx_channel.currentText().strip()
        return ResponseRule(
            name=self.name.text().strip(), enabled=self.enabled.isChecked(),
            rx_channel="" if rx_ch in ("", ANY_CHANNEL) else rx_ch,
            rx_id=self.rx_msg.can_id(), rx_extended=self.rx_msg.ext.isChecked(),
            match=self.match.currentData(), signal=self.signal.currentText(), op=self.op.currentText(),
            value=self.value.text().strip(), raw_offset=self.raw_offset.value(),
            raw_size=self.raw_size.currentData(), raw_order=self.raw_order.currentData(),
            tx_channel="" if tx_ch in ("", SAME_CHANNEL) else tx_ch,
            tx_id=self.tx_msg.can_id(), tx_extended=self.tx_msg.ext.isChecked(),
            tx_fd=self.tx_fd.isChecked(), tx_brs=self.tx_fd.isChecked() and self.tx_brs.isChecked(),
            tx_mode=self.tx_mode.currentData(), tx_dlc=self.tx_dlc.value(), tx_data=self.tx_data.text().strip(),
            tx_value=self.tx_value.text().strip(), tx_offset=self.tx_offset.value(),
            tx_size=self.tx_size.currentData(), tx_order=self.tx_order.currentData(),
            tx_signals=self._signal_values(), delay_ms=self.delay.value())

    def check(self) -> tuple[ResponseRule | None, str]:
        """(rule, preview text) or (None, problem)."""
        try:
            r = self.rule()
            responder = self.ctx.can.responder
            c = responder.compile(r)
            rx_val = None
            if r.match == MATCH_SIGNAL:
                rx_val = float(r.value)
            elif r.match == MATCH_RAW:
                rx_val = float(parse_int(r.value))
            trigger = CanFrame(r.rx_id, b"", r.rx_extended, channel=r.rx_channel or "CAN1")
            ch, f = responder.build(c, trigger, rx_val)
        except (RuleError, ValueError) as e:
            return None, str(e)
        when = f"when {id_text(r.rx_id, r.rx_extended)} arrives" + \
            ("" if r.match == MATCH_ANY else f" with {r.condition_text()}")
        sent = f"{'FD ' if f.is_fd else ''}{id_text(f.arbitration_id, f.is_extended)} " \
               f"[{f.data.hex(' ').upper() or '-'}]"
        where = "on the same channel" if not r.tx_channel else f"on {ch}"
        delay = f" after {r.delay_ms} ms" if r.delay_ms else ""
        return r, f"{when}, send {sent} {where}{delay}"

    def update_preview(self) -> None:
        if self._loading:
            return
        r, text = self.check()
        color = palette()["TEXT_MUTED"] if r is not None else palette()["ERROR"]
        self.preview.setStyleSheet(f"color: {color};")
        self.preview.setText(("Preview: " if r is not None else "Problem: ") + text)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(r is not None)

    def _accept(self) -> None:
        r, _text = self.check()
        if r is None:
            self.update_preview()
            return
        self.accept()


# ---- panel --------------------------------------------------------------------------------------------------
class AutoResponsePanel(QWidget):
    rulesChanged = Signal()
    _engineChanged = Signal()           # emitted on engine threads, delivered queued on the GUI thread

    def __init__(self, ctx, bridge, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.responder = ctx.can.responder
        self.master = QCheckBox("Auto response")
        self.master.setObjectName("auto-response-master")
        self.master.setToolTip("Answer received frames with the rules below (rules are checked on every channel)")
        self.state = QLabel()
        self.state.setProperty("role", "caption")
        self.add_btn = QPushButton("Add rule")
        set_kind(self.add_btn, "primary")
        self.edit_btn = QPushButton("Edit")
        self.del_btn = QPushButton("Remove")
        set_kind(self.del_btn, "danger")
        for b in (self.add_btn, self.edit_btn, self.del_btn):
            b.setFixedHeight(CONTROL_HEIGHT)
        self.more_btn = QToolButton()                    # less frequent actions: keeps the panel narrow
        self.more_btn.setText("More")
        self.more_btn.setPopupMode(QToolButton.InstantPopup)
        self.more_btn.setFixedHeight(CONTROL_HEIGHT)
        self.more_menu = QMenu(self.more_btn)
        self.dup_act = self.more_menu.addAction("Duplicate rule", self.duplicate_selected)
        self.more_menu.addSeparator()
        self.reset_act = self.more_menu.addAction("Reset hit counters", lambda: self._reset_hits())
        self.more_menu.addSeparator()
        self.import_act = self.more_menu.addAction("Import rules…", lambda: self.import_dialog())
        self.export_act = self.more_menu.addAction("Export rules…", lambda: self.export_dialog())
        self.more_btn.setMenu(self.more_menu)

        self.table = QTableWidget(0, len(COLS))
        self.table.setObjectName("auto-response")
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(C_PAYLOAD, QHeaderView.Stretch)
        hh.setFixedHeight(32)
        self.empty = QLabel("No rules yet. Add rule: when a frame is received (e.g. 0x21 with value 10), "
                            "send a response (e.g. 0x27 with value 10). Load a DBC to pick messages and signals "
                            "by name.")
        self.empty.setWordWrap(True)
        self.empty.setProperty("role", "caption")
        self.empty.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.state.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.table.setMinimumWidth(200)

        bar = QHBoxLayout()
        bar.addWidget(self.master)
        bar.addWidget(self.state, 1)
        for b in (self.add_btn, self.edit_btn, self.del_btn, self.more_btn):
            bar.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        lay.addLayout(bar)
        lay.addWidget(self.empty)
        lay.addWidget(self.table, 1)

        self.master.toggled.connect(self.set_enabled)
        self.add_btn.clicked.connect(lambda: self.add_rule())
        self.edit_btn.clicked.connect(lambda: self.edit_rule())
        self.del_btn.clicked.connect(self.remove_selected)
        self.table.cellDoubleClicked.connect(lambda r, c: c != C_ON and self.edit_rule(r))
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        bridge.dbLoaded.connect(lambda _p: self.refresh())
        self._engineChanged.connect(self._engine_changed, Qt.QueuedConnection)
        self.responder.on_change = self._engineChanged.emit           # loop guard switched a rule off

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_hits)
        self._timer.start(500)
        self.restore_settings()

    # ---- rules -----------------------------------------------------------------------------------------------
    @property
    def rules(self) -> list[ResponseRule]:
        return self.responder.rules

    def set_rules(self, rules: list[ResponseRule], save: bool = True) -> list[str]:
        problems = self.responder.set_rules(rules)
        for p in problems:
            self.ctx.events.publish("log", f"Auto response: {p} (rule not active)")
        self.refresh()
        if save:
            self.save_settings()
        self.rulesChanged.emit()
        return problems

    def set_enabled(self, on: bool) -> None:
        self.responder.enabled = bool(on)
        if self.master.isChecked() != bool(on):
            self.master.blockSignals(True)
            self.master.setChecked(bool(on))
            self.master.blockSignals(False)
        self._sync_state()
        self.save_settings()
        self.ctx.events.publish("log", f"Auto response {'on' if on else 'off'} ({self._active_count()} active "
                                       f"rule{'s' if self._active_count() != 1 else ''})")

    def _active_count(self) -> int:
        return sum(1 for r in self.rules if r.enabled)

    def _dialog(self, rule: ResponseRule | None) -> ResponseRule | None:
        dlg = RuleDialog(self.ctx, rule, list(self.ctx.can.bus.channels), self)
        return dlg.rule() if dlg.exec() == QDialog.Accepted else None

    def add_rule(self, rule: ResponseRule | None = None) -> ResponseRule | None:
        """Without `rule`: open the editor. With `rule`: add it as is (scripts / tests)."""
        r = rule if rule is not None else self._dialog(None)
        if r is None:
            return None
        self.set_rules(self.rules + [r])
        self.table.selectRow(len(self.rules) - 1)
        return r

    def edit_rule(self, row: int | None = None) -> None:
        row = self._selected_row() if row is None else row
        if row is None:
            return
        old = self.rules[row]
        new = self._dialog(old.copy())
        if new is None:
            return
        rules = self.rules
        rules[row] = new
        self.set_rules(rules)
        self.table.selectRow(row)

    def duplicate_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        rules = self.rules
        dup = rules[row].copy()
        dup.name = (dup.name + " (copy)").strip()
        rules.insert(row + 1, dup)
        self.set_rules(rules)
        self.table.selectRow(row + 1)

    def remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        rules = self.rules
        for r in rows:
            del rules[r]
        self.set_rules(rules)

    def _selected_row(self) -> int | None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return rows[0] if rows else None

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != C_ON or self._filling:
            return
        rules = self.rules
        if item.row() < len(rules):
            rules[item.row()].enabled = item.checkState() == Qt.Checked
            self.responder.stats_of(rules[item.row()]).disabled_reason = ""
            self.set_rules(rules)

    def _engine_changed(self) -> None:
        self.refresh()
        self.save_settings()

    def _reset_hits(self) -> None:
        self.responder.reset_stats()
        self.refresh_hits()

    # ---- table -----------------------------------------------------------------------------------------------
    _filling = False

    def refresh(self) -> None:
        reg = self.ctx.can.registry
        rules = self.rules
        self._filling = True
        try:
            self.table.setRowCount(len(rules))
            for row, r in enumerate(rules):
                on = QTableWidgetItem()
                on.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                on.setCheckState(Qt.Checked if r.enabled else Qt.Unchecked)
                rx = message_label(reg, r.rx_id, r.rx_extended) + (f"  @{r.rx_channel}" if r.rx_channel else "")
                tx = message_label(reg, r.tx_id, r.tx_extended) + (f"  @{r.tx_channel}" if r.tx_channel else "") \
                    + ("  FD" if r.tx_fd else "")
                cells = [on, QTableWidgetItem(r.name), QTableWidgetItem(rx), QTableWidgetItem(r.condition_text()),
                         QTableWidgetItem(tx), QTableWidgetItem(r.response_text()),
                         QTableWidgetItem(f"{r.delay_ms} ms" if r.delay_ms else "-"), QTableWidgetItem("0")]
                for c, it in enumerate(cells):
                    self.table.setItem(row, c, it)
                try:
                    self.responder.compile(r)
                    problem = ""
                except RuleError as e:
                    problem = str(e)
                if problem:
                    for c in range(1, len(COLS)):
                        self.table.item(row, c).setForeground(QColor(palette()["ERROR"]))
                        self.table.item(row, c).setToolTip(f"Not active: {problem}")
        finally:
            self._filling = False
        self.empty.setVisible(not rules)
        self.refresh_hits()
        self._sync_buttons()
        self._sync_state()

    def refresh_hits(self) -> None:
        for row, r in enumerate(self.rules):
            it = self.table.item(row, C_HITS)
            if it is None:
                continue
            st = self.responder.stats_of(r)
            text = str(st.hits) + (f"  ({st.errors} errors)" if st.errors else "")
            if it.text() != text:
                it.setText(text)
            tip = st.disabled_reason or (f"Last error: {st.last_error}" if st.last_error else "")
            if it.toolTip() != tip:
                it.setToolTip(tip)
                # clearing must REMOVE the override (follows the theme)
                it.setData(Qt.ForegroundRole, QColor(palette()["WARNING"]) if tip else None)

    def _sync_buttons(self) -> None:
        sel = self._selected_row() is not None
        for b in (self.edit_btn, self.del_btn, self.dup_act):
            b.setEnabled(sel)

    def _sync_state(self) -> None:
        n, total = self._active_count(), len(self.rules)
        on = self.responder.enabled
        self.state.setText(f"{'ON' if on else 'off'} · {n}/{total} rule{'s' if total != 1 else ''} active")

    # ---- persistence -----------------------------------------------------------------------------------------
    def to_config(self) -> dict:
        return self.responder.to_config()

    def load_config(self, d: dict | None, save: bool = True) -> list[str]:
        d = d or {}
        problems = self.set_rules([ResponseRule.from_dict(x) for x in d.get("rules", [])], save=False)
        self.set_enabled(bool(d.get("enabled", False)))
        if save:
            self.save_settings()
        return problems

    def save_settings(self) -> None:
        app_settings().setValue(SETTINGS_KEY, json.dumps(self.to_config()))

    def restore_settings(self) -> None:
        raw = app_settings().value(SETTINGS_KEY)
        if not raw:
            self.refresh()
            return
        try:
            d = json.loads(raw)
        except (TypeError, ValueError):
            self.refresh()
            return
        self.set_rules([ResponseRule.from_dict(x) for x in d.get("rules", [])], save=False)
        self.master.blockSignals(True)
        self.master.setChecked(bool(d.get("enabled", False)))
        self.master.blockSignals(False)
        self.responder.enabled = self.master.isChecked()
        self._sync_state()
        if self.responder.enabled:
            self.ctx.events.publish("log", f"Auto response is ON (restored, {self._active_count()} active rules)")

    def export_file(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_config(), f, indent=2)

    def import_file(self, path: str, replace: bool = True) -> list[str]:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and "auto_response" in d:          # a whole project file
            d = d["auto_response"]
        rules = [ResponseRule.from_dict(x) for x in (d.get("rules", []) if isinstance(d, dict) else d)]
        return self.set_rules(rules if replace else self.rules + rules)

    def export_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export auto response rules", "auto_response.json",
                                              "Rules (*.json)")
        if path:
            try:
                self.export_file(path)
            except OSError as e:
                QMessageBox.critical(self, "Export rules", str(e))

    def import_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import auto response rules", "",
                                              "Rules or project (*.json)")
        if not path:
            return
        try:
            problems = self.import_file(path)
        except (OSError, ValueError, TypeError, AttributeError) as e:
            QMessageBox.critical(self, "Import rules", f"{path}: {e}")
            return
        if problems:
            QMessageBox.warning(self, "Import rules", "Some rules are not active:\n" + "\n".join(problems))
