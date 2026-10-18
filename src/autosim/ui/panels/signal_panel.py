"""Signals panel: a tree table of TX messages and their signals.

    ▾ WBA_03  0x394 · 8 B  00 1A FF 3C 00 00 00 00   [CAN1 ▾] [10 ms] ☑FD ☑BRS ☑Cyclic [Send] 405 sent [⋯]
        Signal            Raw   Physical  Unit   Generator  Symbolic                 <- header line of this message
      ▸ AFSNID            125   12.5      km/h   Off        —                        <- signal row (edit in place)
      ▾ AFSSNG            1     1                Ramp       1: Active
          Value [────●────]   Generator Low [0] High [100] Period [5]   Layout: start bit 8 · 4 bit …   <- details

* Messages are group rows holding their transmit controls; collapse a message to hide its signals.
* Signals are table rows (CANoe interactive-generator style): Raw, Physical, Symbolic (value table) and Generator
  are edited in place (click the cell, F2 or type); the message row shows the live payload bytes.
* Expanding a signal opens a detail row (slider, generator parameters, signal layout), built on first use.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QMenu, QMessageBox, QSizePolicy, QSlider, QSpinBox,
                               QStackedWidget, QStyledItemDelegate, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from autosim.core.can_engine.generators import PARAM_LABELS, make_generator
from autosim.ui.widgets import EmptyState, caption, centered, set_kind


class ChannelCombo(QComboBox):
    """Repopulates from the open channels each time the popup opens."""

    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self.refresh()

    def refresh(self) -> None:
        cur = self.currentText()
        self.blockSignals(True)
        self.clear()
        self.addItems(self.ctx.can.bus.channels)
        if cur:
            self.setCurrentText(cur)
        self.blockSignals(False)

    def showPopup(self) -> None:
        self.refresh()
        super().showPopup()


def _spin(lo: float, hi: float, decimals: int = 3, step: float = 1.0) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setKeyboardTracking(False)
    return s


UNBOUNDED = 1e9


def signal_range(sig) -> tuple[float, float, bool]:
    """(low, high, bounded) physical range of a signal: the DBC min / max (when given, max > min) clipped to what
    the raw bits can encode (bit length, signedness, factor, offset) - real DBCs sometimes declare a range the
    signal cannot hold, and such values would make the frame unencodable. Floats without a range are unbounded."""
    dbc = sig.minimum is not None and sig.maximum is not None and sig.maximum > sig.minimum
    if sig.is_float or sig.length > 32:
        return (float(sig.minimum), float(sig.maximum), True) if dbc else (-UNBOUNDED, UNBOUNDED, False)
    if sig.is_signed:
        raw_lo, raw_hi = -(1 << (sig.length - 1)), (1 << (sig.length - 1)) - 1
    else:
        raw_lo, raw_hi = 0, (1 << sig.length) - 1
    a, b = raw_lo * sig.scale + sig.offset, raw_hi * sig.scale + sig.offset
    lo, hi = float(min(a, b)), float(max(a, b))
    if dbc:
        lo, hi = max(lo, float(sig.minimum)), min(hi, float(sig.maximum))
        if hi <= lo:                                       # inconsistent DBC: trust the bits
            lo, hi = float(min(a, b)), float(max(a, b))
    return lo, hi, True


# table columns (signal rows): raw before physical as in CANoe's interactive generator. Every message shows these
# titles in its own header line (first row under the message), so the columns are labelled where they are used.
C_NAME, C_RAW, C_VALUE, C_UNIT, C_GEN, C_TABLE = range(6)
COLUMN_TITLES = ("Signal", "Raw", "Physical", "Unit", "Generator", "Symbolic")
COLUMN_WIDTHS = (None, 76, 104, 56, 92, 124)          # None: stretches
RIGHT_ALIGNED = (C_RAW, C_VALUE)
EDITABLE = (C_RAW, C_VALUE, C_TABLE, C_GEN)
GEN_ITEMS = (("Off", "none"), ("Sine", "sine"), ("Ramp", "ramp"), ("Square", "square"), ("Toggle", "toggle"))
GEN_TEXT = {kind: text for text, kind in GEN_ITEMS}


# ======================================================================================================== signals
class SignalItem(QTreeWidgetItem):
    """One signal = one table row. The engine holds the value (TxJob.state); the row shows it."""

    def __init__(self, msg: "MessageItem", sig) -> None:
        super().__init__(msg)
        self.msg, self.sig = msg, sig
        self.lo, self.hi, self.bounded = signal_range(sig)
        self.factor = abs(sig.scale) or 1.0
        if float(self.factor).is_integer() and not sig.is_float:
            self.decimals = 0
        else:
            self.decimals = min(6, max(1, len(f"{self.factor:.6f}".rstrip("0").split(".")[1])))
        self.gen_kind = "none"
        self.gen_params = [0.0, 0.0, 0.0]
        self.detail: SignalDetail | None = None
        self.setFlags(self.flags() | Qt.ItemIsEditable)
        self.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        self.setText(C_NAME, sig.name)
        self.setText(C_UNIT, sig.unit or "")
        self.setToolTip(C_NAME, f"{sig.name}\n{self._layout_text()}")
        for c in RIGHT_ALIGNED:
            self.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
        self.setText(C_GEN, "Off")
        self.setToolTip(C_GEN, "Generator: drive this signal automatically - click to choose; its parameters are "
                               "in the signal's details")
        self.setToolTip(C_TABLE, "Symbolic value from the value table - click to pick one" if sig.choices
                        else "This signal has no value table")
        self.setToolTip(C_VALUE, "Click to edit the physical value")
        self.show_value(self.value())

    # ---- value ------------------------------------------------------------------------------------------
    @property
    def ctx(self):
        return self.msg.ctx

    def value(self) -> float:
        return self.msg.job.state.get_signal(self.sig.name)

    def set_value(self, v: float) -> float:
        """Write a value (clamped to the DBC range) to the message state; returns what was stored."""
        stored = self.ctx.can.set_signal(self.msg.job, self.sig.name, float(v))
        self.show_value(stored)
        return stored

    def value_text(self, v: float) -> str:
        return f"{v:.{self.decimals}f}"

    def raw_value(self, v: float) -> int | None:
        if self.sig.is_float or not self.sig.scale:
            return None
        return int(round((v - self.sig.offset) / self.sig.scale))

    def table_text(self, v: float) -> str:
        if not self.sig.choices:
            return "—"
        try:
            return f"{int(round(v))}: {self.sig.choices[int(round(v))]}"
        except (KeyError, ValueError):
            return "—"                                    # value has no name in the table

    def show_value(self, v: float) -> None:
        self.setText(C_VALUE, self.value_text(v))
        raw = self.raw_value(v)
        self.setText(C_RAW, "—" if raw is None else str(raw))
        self.setToolTip(C_RAW, "float signal" if raw is None else
                        f"raw {raw} = 0x{raw & ((1 << self.sig.length) - 1):X}   "
                        f"(physical = raw × {self.sig.scale:g} + {self.sig.offset:g}) - click to edit the raw value")
        self.setText(C_TABLE, self.table_text(v))
        if self.detail is not None:
            self.detail.show_value(v)

    def _layout_text(self) -> str:
        sig = self.sig
        order = "Intel" if sig.byte_order == "little_endian" else "Motorola"
        rng = f"{self.lo:g} … {self.hi:g}" if self.bounded else "unbounded"
        return (f"start bit {sig.start} · {sig.length} bit · {order} · "
                f"{'signed' if sig.is_signed else 'unsigned'}{' float' if sig.is_float else ''} · "
                f"factor {sig.scale:g} · offset {sig.offset:g} · range {rng}"
                + (f" {sig.unit}" if sig.unit and self.bounded else "")
                + ("" if sig.minimum is not None or not self.bounded else " (from the bit length)"))

    # ---- generator ----------------------------------------------------------------------------------------
    def generator_active(self) -> bool:
        return self.gen_kind != "none"

    def set_generator(self, kind: str, params: list[float] | None = None) -> None:
        if kind == self.gen_kind and params is None:
            return
        self.gen_kind = kind
        if kind != "none":
            if params is None:
                lo, hi = (self.lo, self.hi) if abs(self.hi) < UNBOUNDED else (0.0, 100.0)
                params = list({"sine": ((hi - lo) / 2, 1.0, (hi + lo) / 2), "ramp": (lo, hi, 5.0),
                               "square": (lo, hi, 1.0), "toggle": (lo, hi, 1.0)}[kind])
            self.gen_params = list(params)
        driven = kind != "none"
        self.setText(C_GEN, GEN_TEXT[kind])
        for c in (C_VALUE, C_TABLE):
            f = self.font(c)
            f.setItalic(driven)
            self.setFont(c, f)
        self.setToolTip(C_VALUE, "Driven by the generator - set Generator to Off to edit" if driven
                        else "Click to edit the physical value")
        self._rebind()
        if self.detail is not None:
            self.detail.sync_generator()
        if driven:
            self.set_expanded(True)                        # show the generator parameters

    def set_param(self, i: int, value: float) -> None:
        self.gen_params[i] = value
        self._rebind()

    def _rebind(self) -> None:
        gen = make_generator(self.gen_kind, *self.gen_params) if self.gen_kind != "none" else None
        self.ctx.can.bind_generator(self.msg.job, self.sig.name, gen)

    # ---- details --------------------------------------------------------------------------------------------
    @property
    def expanded(self) -> bool:
        return self.isExpanded()

    def set_expanded(self, on: bool) -> None:
        if on:
            self.ensure_detail()
        self.setExpanded(on)

    def ensure_detail(self) -> "SignalDetail":
        if self.detail is None:
            item = QTreeWidgetItem(self)
            item.setFlags(Qt.ItemIsEnabled)
            item.setFirstColumnSpanned(True)
            self.detail = SignalDetail(self)
            self.treeWidget().setItemWidget(item, 0, self.detail)
            self.detail_item = item
            self.detail.fit_row()
        return self.detail

    # ---- in-place editors (via SignalDelegate) ----------------------------------------------------------------
    def raw_range(self) -> tuple[int, int]:
        if self.bounded and self.sig.scale:
            a = (self.lo - self.sig.offset) / self.sig.scale
            b = (self.hi - self.sig.offset) / self.sig.scale
            return int(round(min(a, b))), int(round(max(a, b)))
        if self.sig.is_signed:
            return -(1 << (self.sig.length - 1)), (1 << (self.sig.length - 1)) - 1
        return 0, (1 << self.sig.length) - 1

    def create_editor(self, col: int, parent: QWidget) -> QWidget | None:
        if col == C_RAW:
            if self.generator_active() or self.raw_value(self.value()) is None:
                return None
            lo, hi = self.raw_range()
            ed = _spin(lo, hi, 0, 1)
            ed.setFrame(False)
            return ed
        if col == C_VALUE:
            if self.generator_active():
                return None
            ed = _spin(self.lo, self.hi, self.decimals, self.factor)
            ed.setFrame(False)
            return ed
        if col == C_TABLE:
            if not self.sig.choices or self.generator_active():
                return None
            ed = QComboBox()
            ed.setPlaceholderText("—")
            for k, v in sorted(self.sig.choices.items()):
                ed.addItem(f"{int(k)}: {v}", int(k))
            return ed
        if col == C_GEN:
            ed = QComboBox()
            for text, kind in GEN_ITEMS:
                ed.addItem(text, kind)
            return ed
        return None

    def load_editor(self, col: int, ed: QWidget) -> None:
        v = self.value()
        if col == C_RAW:
            ed.setValue(self.raw_value(v))
            ed.selectAll()
        elif col == C_VALUE:
            ed.setValue(v)
            ed.selectAll()
        elif col == C_TABLE:
            ed.setCurrentIndex(ed.findData(int(round(v))))
        elif col == C_GEN:
            ed.setCurrentIndex(ed.findData(self.gen_kind))

    def commit_editor(self, col: int, ed: QWidget) -> None:
        if col == C_RAW:
            ed.interpretText()
            self.set_value(ed.value() * self.sig.scale + self.sig.offset)
        elif col == C_VALUE:
            ed.interpretText()
            self.set_value(ed.value())
        elif col == C_TABLE and ed.currentIndex() >= 0:
            self.set_value(float(ed.currentData()))
        elif col == C_GEN:
            self.set_generator(ed.currentData())


class SignalDetail(QFrame):
    """Detail row of a signal: slider, generator parameters, bit layout."""

    def __init__(self, item: SignalItem) -> None:
        super().__init__()
        self.setObjectName("signal-detail")
        self.item = item
        g = QGridLayout(self)
        g.setContentsMargins(12, 6, 12, 8)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        self.slider = None
        if item.bounded and item.hi > item.lo:
            self.slider = QSlider(Qt.Horizontal)
            self.slider.setRange(0, 1000)
            self.slider.setAccessibleName(f"{item.sig.name} slider")
            self.slider.valueChanged.connect(self._slider_moved)
            g.addWidget(caption("Value"), 0, 0)
            g.addWidget(self.slider, 0, 1)
        self.gen_caption = caption("Generator")
        self.params = [_spin(-UNBOUNDED, UNBOUNDED, 3, 0.5) for _ in range(3)]
        self.param_row = QWidget()
        pl = QHBoxLayout(self.param_row)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        for i, p in enumerate(self.params):
            p.setMinimumWidth(110)
            p.valueChanged.connect(lambda v, i=i: self.item.set_param(i, v))
            pl.addWidget(p, 1)
        g.addWidget(self.gen_caption, 1, 0)
        g.addWidget(self.param_row, 1, 1)
        info = QLabel(item._layout_text())
        info.setProperty("role", "muted")
        info.setWordWrap(True)
        g.addWidget(caption("Layout"), 2, 0, Qt.AlignTop)
        g.addWidget(info, 2, 1)
        g.setColumnStretch(1, 1)
        self.sync_generator()
        self.show_value(item.value())

    def _slider_moved(self, pos: int) -> None:
        if self.slider.signalsBlocked():
            return
        it = self.item
        it.set_value(it.lo + (it.hi - it.lo) * pos / 1000.0)

    def show_value(self, v: float) -> None:
        if self.slider is not None:
            it = self.item
            self.slider.blockSignals(True)
            self.slider.setValue(int(round((v - it.lo) / (it.hi - it.lo) * 1000)))
            self.slider.blockSignals(False)

    def sync_generator(self) -> None:
        it = self.item
        active = it.generator_active()
        self.gen_caption.setVisible(active)
        self.param_row.setVisible(active)
        if active:
            for p, label, val in zip(self.params, PARAM_LABELS[it.gen_kind], it.gen_params):
                p.blockSignals(True)
                p.setPrefix(label + "  ")
                p.setToolTip(label)
                p.setValue(val)
                p.blockSignals(False)
        if self.slider is not None:
            self.slider.setEnabled(not active)
            self.slider.setToolTip("Driven by the generator" if active else "Drag to set the value")
        self.fit_row()

    def fit_row(self) -> None:
        """The detail row's height follows what is shown (generator parameters appear / disappear)."""
        holder = getattr(self.item, "detail_item", None)
        if holder is not None:
            self.layout().activate()
            hint = self.layout().sizeHint()                   # a valid size (an invalid one is ignored)
            holder.setSizeHint(0, QSize(max(1, hint.width()), hint.height()))


class ColumnHeaderItem(QTreeWidgetItem):
    """The column titles of a message, as the first row under it (painted like a table header)."""

    def __init__(self, msg: "MessageItem") -> None:
        super().__init__(msg)
        self.setFlags(Qt.ItemIsEnabled)                   # not selectable, not editable
        self.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
        for c, title in enumerate(COLUMN_TITLES):
            self.setText(c, title)


class SignalDelegate(QStyledItemDelegate):
    """In-place editors for signal rows (Raw / Physical / Symbolic / Generator) and the painting of the per-message
    header lines; message rows are not editable."""

    def __init__(self, tree: QTreeWidget) -> None:
        super().__init__(tree)
        self.tree = tree

    def paint(self, painter, option, index):
        if not isinstance(self.tree.itemFromIndex(index), ColumnHeaderItem):
            return super().paint(painter, option, index)
        from PySide6.QtGui import QColor, QPen
        from autosim.ui.theme import token, ui_font
        r = option.rect
        painter.save()
        painter.fillRect(r, QColor(token("SURFACE_2")))
        painter.setPen(QPen(QColor(token("BORDER")), 1))
        painter.drawLine(r.bottomLeft(), r.bottomRight())
        f = ui_font("body_strong")
        painter.setFont(f)
        painter.setPen(QColor(token("TEXT_SECONDARY")))
        align = (Qt.AlignRight if index.column() in RIGHT_ALIGNED else Qt.AlignLeft) | Qt.AlignVCenter
        painter.drawText(r.adjusted(6, 0, -6, 0), int(align), index.data())
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if isinstance(self.tree.itemFromIndex(index), ColumnHeaderItem):
            size.setHeight(max(size.height(), 26))
        return size

    def _item(self, index):
        it = self.tree.itemFromIndex(index)
        return it if isinstance(it, SignalItem) else None

    def createEditor(self, parent, option, index):
        it = self._item(index)
        if it is None or index.column() not in EDITABLE:
            return None
        ed = it.create_editor(index.column(), parent)
        if ed is not None:
            ed.setParent(parent)
            if isinstance(ed, QComboBox):          # commit as soon as an entry is picked
                ed.activated.connect(lambda _i, e=ed: (self.commitData.emit(e), self.closeEditor.emit(e)))
        return ed

    def setEditorData(self, editor, index):
        it = self._item(index)
        if it is not None:
            it.load_editor(index.column(), editor)

    def setModelData(self, editor, model, index):
        it = self._item(index)
        if it is not None:
            it.commit_editor(index.column(), editor)


# ======================================================================================================== messages
class _ElidedLabel(QLabel):
    """Label that shrinks with "…" instead of overflowing (full text in the tooltip)."""

    def minimumSizeHint(self) -> QSize:
        return QSize(72, super().minimumSizeHint().height())

    def paintEvent(self, _e) -> None:
        from PySide6.QtGui import QPainter
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.contentsRect().width())
        p.drawText(self.contentsRect(), int(Qt.AlignLeft | Qt.AlignVCenter), text)


class MessageRow(QWidget):
    """Controls of a message, shown across the whole width of its group row."""

    def __init__(self, item: "MessageItem") -> None:
        super().__init__()
        self.setObjectName("msg-row")
        self.setAttribute(Qt.WA_StyledBackground, True)
        m = item.message
        ext = "x" if m.is_extended_frame else ""
        self.title = _ElidedLabel(m.name)                  # shrinks with "…" when the panel is narrow
        self.title.setObjectName("msg-title")
        self.title.setToolTip(m.name)
        self._id_text = f"0x{m.frame_id:X}{ext} · {m.length} B"
        self.meta = QLabel(self._id_text)
        self.meta.setProperty("role", "muted")
        self.meta.setMinimumWidth(self.meta.sizeHint().width())   # the id stays visible; the payload shrinks
        self.data = QLabel("")                             # live payload bytes (hex), like CANoe's Data column
        self.data.setObjectName("msg-data")
        self.data.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.data.setMinimumWidth(0)
        self.channel = ChannelCombo(item.ctx)
        self.channel.setFixedWidth(96)
        self.channel.setToolTip("Channel that sends this message")
        self.cycle = QSpinBox()
        self.cycle.setRange(0, 60000)
        self.cycle.setSuffix(" ms")
        self.cycle.setSpecialValueText("event")
        self.cycle.setFixedWidth(96)
        self.cycle.setToolTip("Cycle time (event = sent only with Send)")
        self.enable = QCheckBox("Cyclic")
        self.enable.setToolTip("Cyclic transmission on / off")
        self.once = QToolButton()
        self.once.setObjectName("msg-btn")
        self.once.setText("Send")
        self.once.setToolTip("Send this message once now")
        self.count = QLabel("0 sent")
        self.count.setProperty("role", "muted")
        self.count.setMinimumWidth(56)
        self.count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.more = QToolButton()
        self.more.setObjectName("msg-btn")
        self.more.setText("⋯")
        self.more.setToolTip("More: send on another channel, signal details, remove")
        self.more.setPopupMode(QToolButton.InstantPopup)
        self.menu = QMenu(self.more)
        self.more.setMenu(self.menu)
        self.fd = QCheckBox("FD")
        self.fd.setToolTip("Send as a CAN FD frame")
        self.brs = QCheckBox("BRS")
        self.brs.setToolTip("Bit-rate switch: data phase at the data bit rate (CAN FD frames only)")
        self.fd.toggled.connect(lambda _on: self.update_meta())
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(6)
        lay.addWidget(self.title)
        lay.addWidget(self.meta)
        lay.addWidget(self.data, 1)
        for w in (self.channel, self.cycle, self.fd, self.brs, self.enable, self.once, self.count, self.more):
            lay.addWidget(w)

    def update_meta(self) -> None:
        self.brs.setEnabled(self.fd.isChecked())           # BRS exists only in CAN FD frames
        self.meta.setMinimumWidth(self.meta.sizeHint().width())
        self.fit()

    def fit(self) -> None:
        """In a narrow panel drop the sent counter, then the id / length text, instead of letting the controls
        overlap (Qt squeezes widgets on top of each other when a layout gets less than its minimum)."""
        lay = self.layout()
        for w in (self.meta, self.count):
            w.setVisible(True)
        for w in (self.count, self.meta):
            lay.invalidate()
            if lay.minimumSize().width() <= self.width():
                break
            w.setVisible(False)
        lay.invalidate()                                   # the payload only when a few bytes fit, not a clipped glyph
        room = self.width() - lay.minimumSize().width()
        self.data.setVisible(room >= self.data.fontMetrics().horizontalAdvance("00 00 00 00"))

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self.fit()

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), 40)


class MessageItem(QTreeWidgetItem):
    """A TX message: group row (controls) + one SignalItem per signal."""

    def __init__(self, panel: "SignalPanel", message, channel: str) -> None:
        super().__init__(panel.tree)
        self.panel, self.ctx, self.message = panel, panel.ctx, message
        self.job = self.ctx.can.tx.add_job(channel, message,
                                           int(message.cycle_time) if message.cycle_time else None)
        self.setFirstColumnSpanned(True)
        self.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.row = MessageRow(self)
        panel.tree.setItemWidget(self, 0, self.row)
        self.setSizeHint(0, self.row.sizeHint())
        r = self.row
        if channel:
            r.channel.setCurrentText(channel)
        r.cycle.setValue(self.job.cycle_ms or 0)
        r.fd.setChecked(self.job.fd)
        r.brs.setChecked(self.job.brs)
        r.update_meta()
        r.menu.addAction("Send on another channel", self._copy_to_channel)
        r.menu.addAction("Expand signal details", lambda: self.set_all_expanded(True))
        r.menu.addAction("Collapse signal details", lambda: self.set_all_expanded(False))
        r.menu.addSeparator()
        r.menu.addAction("Remove message", lambda: panel.remove_card(self))
        r.channel.currentTextChanged.connect(self._channel_changed)
        r.cycle.valueChanged.connect(self._cycle_changed)
        r.fd.toggled.connect(lambda v: setattr(self.job, "fd", v))
        r.brs.toggled.connect(lambda v: setattr(self.job, "brs", v))
        r.enable.toggled.connect(self._enable_toggled)
        r.once.clicked.connect(self.send_once)
        self.header = ColumnHeaderItem(self)              # this message's column titles
        self.rows: list[SignalItem] = [SignalItem(self, s) for s in message.signals]
        self._channel_changed(r.channel.currentText())
        r.data.setText(self.payload_hex())

    # controls, as attributes of the message (used by the main window and tests)
    channel = property(lambda self: self.row.channel)
    cycle = property(lambda self: self.row.cycle)
    fd = property(lambda self: self.row.fd)
    brs = property(lambda self: self.row.brs)
    enable = property(lambda self: self.row.enable)
    count = property(lambda self: self.row.count)

    # ---- open / closed ------------------------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self.isExpanded()

    def set_open(self, on: bool) -> None:
        self.setExpanded(on)

    def set_all_expanded(self, on: bool) -> None:
        """Expand / collapse the detail rows of every signal of this message."""
        for r in self.rows:
            r.set_expanded(on)

    # ---- channel / transmission ------------------------------------------------------------------------------
    def _channel_changed(self, ch: str) -> None:
        self.job.channel = ch

    def sync_channels(self, channels: list[str]) -> None:
        """Open channels changed: refresh the list; a message without a (still open) channel takes one - the first
        channel that does not send this message yet, else the first open channel."""
        self.channel.refresh()
        if self.job.channel in channels:
            return
        taken = {c.job.channel for c in self.panel.cards if c is not self and c.message.name == self.message.name}
        free = [ch for ch in channels if ch not in taken]
        pick = (free or channels or [""])[0]
        self.channel.blockSignals(True)
        self.channel.setCurrentText(pick)
        self.channel.blockSignals(False)
        self.job.channel = pick
        if not pick and self.enable.isChecked():
            self.enable.setChecked(False)

    def _copy_to_channel(self) -> None:
        taken = {c.job.channel for c in self.panel.cards if c.message.name == self.message.name}
        free = [ch for ch in self.ctx.can.bus.channels if ch not in taken]
        if not free:
            QMessageBox.information(self.panel, "Send on another channel",
                                    f"{self.message.name} is already sent on every open channel. "
                                    "Connect another adapter first.")
            return
        self.panel.add_message(self.message.name, free[0])

    def _cycle_changed(self, ms: int) -> None:
        self.ctx.can.tx.set_cycle(self.job, ms or None)
        if not ms and self.enable.isChecked():
            self.enable.setChecked(False)

    def _need_channel(self) -> bool:
        if self.job.channel not in self.ctx.can.bus.channels:
            self.sync_channels(self.ctx.can.bus.channels)
        if not self.job.channel or self.job.channel not in self.ctx.can.bus.channels:
            QMessageBox.warning(self.panel, "No channel", "Open a CAN channel in the sidebar (Channels) first, "
                                "then pick it here.")
            return False
        return True

    def _enable_toggled(self, on: bool) -> None:
        if not on:
            self.ctx.can.tx.stop(self.job)
            return
        if not self.job.cycle_ms:
            QMessageBox.information(self.panel, "Cycle time", "Set a cycle time (ms) for cyclic transmission.")
            self.enable.setChecked(False)
        elif not self._need_channel():
            self.enable.setChecked(False)
        else:
            self.ctx.can.tx.start(self.job)

    def send_once(self) -> None:
        if self._need_channel():
            self.ctx.can.tx.trigger(self.job)

    _send_once = send_once                                # older name

    def payload_hex(self) -> str:
        try:
            return bytes(self.job.state.encode()).hex(" ").upper()
        except Exception:                                 # a value the codec cannot encode
            return "?"

    def refresh(self) -> None:
        """Panel timer: sent counter, payload bytes, cyclic state after errors, live generator values."""
        self.count.setText(f"{self.job.count} sent")
        data = self.payload_hex()
        if data != self.row.data.text():
            self.row.data.setText(data)
            self.row.data.setToolTip(f"Payload ({self.message.length} bytes): {data}")
        if self.enable.isChecked() and not self.job.enabled:      # stopped by an error
            self.enable.blockSignals(True)
            self.enable.setChecked(False)
            self.enable.blockSignals(False)
        if self.isExpanded():
            for r in self.rows:
                if r.generator_active():
                    r.show_value(r.value())


# ======================================================================================================== panel
class SignalTree(QTreeWidget):
    deleteRequested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("signal-table")
        self.setColumnCount(len(COLUMN_TITLES))
        self.setHeaderLabels(list(COLUMN_TITLES))
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(False)
        self.setRootIsDecorated(True)
        self.setIndentation(18)
        self.setExpandsOnDoubleClick(False)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.SelectedClicked | QAbstractItemView.DoubleClicked |
                             QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        hh = self.header()
        hh.setStretchLastSection(False)
        self.setHeaderHidden(True)                         # titles are shown inside every message instead
        hh.setMinimumSectionSize(48)
        for c, w in enumerate(COLUMN_WIDTHS):
            if w is None:
                hh.setSectionResizeMode(c, QHeaderView.Stretch)
            else:
                hh.setSectionResizeMode(c, QHeaderView.Interactive)
                self.setColumnWidth(c, w)
        self.setMinimumWidth(615)                          # a compact message row (name + controls) fits
        self.setItemDelegate(SignalDelegate(self))

    def keyPressEvent(self, e) -> None:
        it = self.currentItem()
        if e.key() == Qt.Key_Delete and isinstance(it, MessageItem):
            self.deleteRequested.emit(it)
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and it is not None and self.state() != self.EditingState:
            if isinstance(it, SignalItem):
                it.set_expanded(not it.isExpanded())
            else:
                it.setExpanded(not it.isExpanded())
            return
        super().keyPressEvent(e)


DB_SUFFIXES = (".dbc", ".arxml", ".kcd", ".sym")


class SignalPanel(QFrame):
    """Workspace for TX messages. Shows an empty-state card until there is something to show."""

    openDbcRequested = Signal()
    filesDropped = Signal(list)

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("signals-panel")
        self.setAcceptDrops(True)
        self.ctx = ctx
        self.cards: list[MessageItem] = []

        self.empty = EmptyState("database", "No DBC Database Loaded",
                                "Drag & drop a .dbc / .arxml file here or click below to start "
                                "simulating CAN signals.")
        self.open_btn = self.empty.add_button("Open DBC File", "primary")
        self.open_btn.clicked.connect(self.openDbcRequested)

        self.tree = SignalTree()
        self.tree.itemExpanded.connect(self._expanded)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.deleteRequested.connect(self.remove_card)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        host = QWidget()
        hl = QVBoxLayout(host)
        hl.setContentsMargins(8, 8, 8, 8)                   # padding under the dock title
        hl.addWidget(self.tree)

        self.stack = QStackedWidget()
        self.stack.addWidget(centered(self.empty))        # page 0: empty state
        self.stack.addWidget(host)                        # page 1: the table
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)

        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: [c.refresh() for c in self.cards])
        self.timer.start(150)
        self.refresh_empty()

    # ---- empty state ----------------------------------------------------------------
    def refresh_empty(self) -> None:
        """Pick the page: the table if any message; otherwise the empty state matching what is missing."""
        if self.cards:
            self.stack.setCurrentIndex(1)
            return
        self.stack.setCurrentIndex(0)
        if self.ctx.can.registry.messages():
            self.empty.set_text("No Message Selected",
                                "Double-click a message in the DBC explorer to add its signals here.")
            self.open_btn.setText("Load Another DBC")
            set_kind(self.open_btn, "")
        else:
            self.empty.set_text("No DBC Database Loaded",
                                "Drag & drop a .dbc / .arxml file here or click below to start "
                                "simulating CAN signals.")
            self.open_btn.setText("Open DBC File")
            set_kind(self.open_btn, "primary")

    # ---- drag & drop -------------------------------------------------------------------
    @staticmethod
    def _dropped_files(event) -> list[str]:
        return [u.toLocalFile() for u in event.mimeData().urls()
                if u.isLocalFile() and u.toLocalFile().lower().endswith(DB_SUFFIXES)]

    def dragEnterEvent(self, event) -> None:
        if self._dropped_files(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        files = self._dropped_files(event)
        if files:
            event.acceptProposedAction()
            self.filesDropped.emit(files)

    # ---- tree interaction ----------------------------------------------------------------
    def _expanded(self, item) -> None:
        if isinstance(item, SignalItem):
            item.ensure_detail()

    def _double_clicked(self, item, col: int) -> None:
        if isinstance(item, MessageItem):
            item.setExpanded(not item.isExpanded())
        elif isinstance(item, SignalItem) and col not in EDITABLE:
            item.set_expanded(not item.isExpanded())

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if not isinstance(item, (SignalItem, MessageItem)):
            return
        menu = QMenu(self)
        if isinstance(item, SignalItem):
            sig_item = item
            menu.addAction("Hide details" if sig_item.isExpanded() else "Show details",
                           lambda: sig_item.set_expanded(not sig_item.isExpanded()))
            if sig_item.sig.initial is not None:
                menu.addAction("Reset to initial value", lambda: sig_item.set_value(float(sig_item.sig.initial)))
            if sig_item.generator_active():
                menu.addAction("Stop generator", lambda: sig_item.set_generator("none"))
            menu.addSeparator()
            item = sig_item.msg
        msg_item = item
        menu.addAction("Send once", msg_item.send_once)
        menu.addAction("Send on another channel", msg_item._copy_to_channel)
        menu.addAction("Expand signal details", lambda: msg_item.set_all_expanded(True))
        menu.addAction("Collapse signal details", lambda: msg_item.set_all_expanded(False))
        menu.addSeparator()
        menu.addAction("Remove message", lambda: self.remove_card(msg_item))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ---- messages ---------------------------------------------------------------------------
    def add_message(self, name: str, channel: str | None = None) -> MessageItem | None:
        """Message `name` on `channel` (None: the existing entry of that message, else the first open channel).
        The same message can be added once per channel, e.g. sent by CAN1 and by CAN2."""
        for c in self.cards:
            if c.message.name == name and (channel is None or c.job.channel == channel):
                return c
        msg = self.ctx.can.registry.message_by_name(name)
        if msg is None:
            return None
        chans = self.ctx.can.bus.channels
        item = MessageItem(self, msg, channel or (chans[0] if chans else ""))
        self.cards.append(item)
        item.setExpanded(True)                            # new messages open with their signals visible
        self.refresh_empty()
        return item

    def set_all_open(self, on: bool) -> None:
        """Collapse / expand every message (Signals panel header)."""
        for c in self.cards:
            c.set_open(on)

    def channels_changed(self, channels: list[str]) -> None:
        for c in self.cards:
            c.sync_channels(channels)

    def remove_card(self, item: MessageItem) -> None:
        self.ctx.can.tx.remove_job(item.job)
        self.cards.remove(item)
        idx = self.tree.indexOfTopLevelItem(item)
        if idx >= 0:
            self.tree.takeTopLevelItem(idx)
        self.refresh_empty()

    remove_message = remove_card

    def stop_all(self) -> None:
        for c in self.cards:
            c.enable.setChecked(False)
