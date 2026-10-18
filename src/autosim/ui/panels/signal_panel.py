"""Signals panel: one flat section per TX message (signal grid with sliders, value tables, generators)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget,
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
    """(low, high, bounded) physical range of a signal: the DBC min / max when given (max > min), otherwise the
    range the raw bits can encode (bit length, signedness, factor, offset). Floats without a range are unbounded."""
    if sig.minimum is not None and sig.maximum is not None and sig.maximum > sig.minimum:
        return float(sig.minimum), float(sig.maximum), True
    if sig.is_float or sig.length > 32:
        return -UNBOUNDED, UNBOUNDED, False
    if sig.is_signed:
        raw_lo, raw_hi = -(1 << (sig.length - 1)), (1 << (sig.length - 1)) - 1
    else:
        raw_lo, raw_hi = 0, (1 << sig.length) - 1
    a, b = raw_lo * sig.scale + sig.offset, raw_hi * sig.scale + sig.offset
    return float(min(a, b)), float(max(a, b)), True


# grid columns of a message section
C_NAME, C_SLIDER, C_VALUE, C_RAW, C_TABLE, C_GEN = range(6)
COLUMN_TITLES = ("Signal", "", "Value", "Raw", "Value table", "Generator")
NAME_W, VALUE_W, RAW_W, TABLE_W, GEN_W = 116, 108, 52, 100, 92
GRID_GAP, PANEL_MARGIN = 8, 12
GRID_MIN_WIDTH = NAME_W + VALUE_W + RAW_W + TABLE_W + GEN_W + 4 * GRID_GAP   # every column but the slider
SLIDER_MIN = 72
GEN_ITEMS = (("Off", "none"), ("Sine", "sine"), ("Ramp", "ramp"), ("Square", "square"), ("Toggle", "toggle"))


class SignalRow:
    """One signal = one grid row: name | slider | value (unit inline) | raw | value table | generator, plus a
    parameter row under it while a generator is active."""

    def __init__(self, card: "MessageCard", sig, grid: QGridLayout, row: int) -> None:
        self.card, self.sig = card, sig
        self.lo, self.hi, bounded = signal_range(sig)
        factor = abs(sig.scale) or 1.0
        if float(factor).is_integer() and not sig.is_float:
            decimals = 0
        else:
            decimals = min(6, max(1, len(f"{factor:.6f}".rstrip("0").split(".")[1])))

        self.name = QLabel()
        self.name.setFixedWidth(NAME_W)
        self.name.setText(self.name.fontMetrics().elidedText(sig.name, Qt.ElideRight, NAME_W - 4))
        tip = f"{sig.name}\n{sig.length} bit @ {sig.start}, factor {sig.scale:g}, offset {sig.offset:g}"
        if bounded:
            tip += f"\nrange {self.lo:g} … {self.hi:g}" + ("" if sig.minimum is not None else " (from the bit length)")
        self.name.setToolTip(tip)

        self.slider = None
        if bounded and self.hi > self.lo:
            self.slider = QSlider(Qt.Horizontal)
            self.slider.setRange(0, 1000)
            self.slider.setMinimumWidth(48)
            self.slider.setAccessibleName(f"{sig.name} slider")
            self.slider.valueChanged.connect(self._slider_moved)

        self.spin = _spin(self.lo, self.hi, decimals, factor)
        self.spin.setFixedWidth(VALUE_W)
        if sig.unit:
            self.spin.setSuffix(f" {sig.unit}")
        self.spin.setAccessibleName(f"{sig.name} value")
        self.spin.valueChanged.connect(self._user)

        self.raw = QLabel("—")
        self.raw.setObjectName("raw-value")
        self.raw.setFixedWidth(RAW_W)
        self.raw.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.choice = None
        if sig.choices:
            self.choice = QComboBox()
            self.choice.setPlaceholderText("—")                   # value not in the table
            for k, v in sorted(sig.choices.items()):
                self.choice.addItem(f"{int(k)}: {v}", int(k))
            self.choice.setFixedWidth(TABLE_W)
            self.choice.setToolTip("Value table: pick a named value")
            self.choice.currentIndexChanged.connect(self._choice_picked)
            table = self.choice
        else:
            table = QLabel("—")
            table.setObjectName("empty-cell")
            table.setFixedWidth(TABLE_W)
            table.setToolTip("This signal has no value table")

        self.gen = QComboBox()
        for text, kind in GEN_ITEMS:
            self.gen.addItem(text, kind)
        self.gen.setFixedWidth(GEN_W)
        self.gen.setToolTip("Generator: drive this signal automatically (sine, ramp, square, toggle)")
        self.params = [_spin(-UNBOUNDED, UNBOUNDED, 3, 0.5) for _ in range(3)]
        self.param_row = QWidget()
        pl = QHBoxLayout(self.param_row)
        pl.setContentsMargins(0, 0, 0, 4)
        pl.setSpacing(8)
        for p in self.params:
            p.setFixedWidth(150)
            p.valueChanged.connect(self._rebind)
            pl.addWidget(p)
        pl.addStretch()
        self.param_row.hide()
        self.gen.currentIndexChanged.connect(lambda _i: self._gen_changed(self.gen.currentData()))
        self._tips = {w: w.toolTip() for w in (self.spin, self.slider, self.choice) if w is not None}

        grid.addWidget(self.name, row, C_NAME)
        if self.slider is not None:
            grid.addWidget(self.slider, row, C_SLIDER)
        grid.addWidget(self.spin, row, C_VALUE)
        grid.addWidget(self.raw, row, C_RAW)
        grid.addWidget(table, row, C_TABLE)
        grid.addWidget(self.gen, row, C_GEN)
        grid.addWidget(self.param_row, row + 1, C_SLIDER, 1, 5)
        self.set_display(card.job.state.get_signal(sig.name))

    # ---- value editing ------------------------------------------------------
    def _slider_moved(self, pos: int) -> None:
        if self.slider.signalsBlocked():
            return
        self.spin.setValue(self.lo + (self.hi - self.lo) * pos / 1000.0)    # -> _user

    def _choice_picked(self, _i: int) -> None:
        if self.choice.currentIndex() >= 0:
            self.spin.setValue(float(self.choice.currentData()))

    def _user(self, value) -> None:
        stored = self.card.ctx.can.set_signal(self.card.job, self.sig.name, float(value))
        self._sync(stored)

    def _sync(self, v: float) -> None:
        """Slider position, raw value and value-table entry follow the physical value."""
        if self.slider is not None:
            self.slider.blockSignals(True)
            self.slider.setValue(int(round((v - self.lo) / (self.hi - self.lo) * 1000)))
            self.slider.blockSignals(False)
        if self.sig.is_float or not self.sig.scale:
            self.raw.setText("—")
            self.raw.setToolTip("Raw value: float signal")
        else:
            raw = int(round((v - self.sig.offset) / self.sig.scale))
            self.raw.setText(str(raw))
            self.raw.setToolTip(f"Raw value {raw} = 0x{raw & ((1 << self.sig.length) - 1):X}  "
                                f"(physical = raw × {self.sig.scale:g} + {self.sig.offset:g})")
        if self.choice is not None:
            self.choice.blockSignals(True)
            self.choice.setCurrentIndex(self.choice.findData(int(round(v))))   # -1 shows the "—" placeholder
            self.choice.blockSignals(False)

    def set_display(self, v: float) -> None:
        """Show a value without writing it back (used for generator feedback)."""
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        self._sync(v)

    # ---- generators ---------------------------------------------------------
    def _gen_changed(self, kind: str) -> None:
        active = kind != "none"
        if active:
            bounded = abs(self.hi) < UNBOUNDED
            lo, hi = (self.lo, self.hi) if bounded else (0.0, 100.0)
            defaults = {"sine": ((hi - lo) / 2, 1.0, (hi + lo) / 2), "ramp": (lo, hi, 5.0),
                        "square": (lo, hi, 1.0), "toggle": (lo, hi, 1.0)}[kind]
            for p, d, label in zip(self.params, defaults, PARAM_LABELS[kind]):
                p.blockSignals(True)
                p.setValue(d)
                p.blockSignals(False)
                p.setToolTip(label)
                p.setPrefix(label + "  ")
        self.param_row.setVisible(active)
        for w, tip in self._tips.items():
            w.setEnabled(not active)
            w.setToolTip("Driven by the generator - set Generator to Off to edit" if active else tip)
        self._rebind()

    def _rebind(self) -> None:
        kind = self.gen.currentData()
        gen = make_generator(kind, *(p.value() for p in self.params)) if kind != "none" else None
        self.card.ctx.can.bind_generator(self.card.job, self.sig.name, gen)

    def generator_active(self) -> bool:
        return self.gen.currentData() != "none"

    def set_slider_visible(self, on: bool) -> None:
        if self.slider is not None:
            self.slider.setVisible(on)


class MessageCard(QFrame):
    """A TX message as a flat section of the Signals panel (no nested box): header, controls, signal grid."""

    def __init__(self, ctx, panel: "SignalPanel", message, channel: str) -> None:
        super().__init__()
        self.setObjectName("msg-section")
        self.ctx, self.panel, self.message = ctx, panel, message
        self.job = ctx.can.tx.add_job(channel, message, int(message.cycle_time) if message.cycle_time else None)
        ext = "x" if message.is_extended_frame else ""

        title = QLabel(message.name)
        title.setObjectName("msg-title")
        meta = QLabel(f"0x{message.frame_id:X}{ext} · {message.length} bytes"
                      + (" · CAN FD" if message.length > 8 else ""))
        meta.setProperty("role", "muted")
        self.copy = QPushButton("+ Channel")
        set_kind(self.copy, "ghost")
        self.copy.setToolTip("Also send this message on another open channel (one section per channel)")
        self.remove = QPushButton("✕")
        set_kind(self.remove, "icon")
        self.remove.setToolTip("Remove this message")
        self.remove.setAccessibleName(f"Remove {message.name}")
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(title)
        head.addWidget(meta)
        head.addStretch()
        head.addWidget(self.copy)
        head.addWidget(self.remove)

        self.channel = ChannelCombo(ctx)
        if channel:
            self.channel.setCurrentText(channel)       # the section's own channel, not simply the first open one
        self.channel.setMinimumWidth(96)
        self.cycle = QSpinBox()
        self.cycle.setRange(0, 60000)
        self.cycle.setSuffix(" ms")
        self.cycle.setSpecialValueText("event")
        self.cycle.setValue(self.job.cycle_ms or 0)
        self.cycle.setFixedWidth(96)
        self.fd = QCheckBox("FD")
        self.fd.setChecked(self.job.fd)
        self.brs = QCheckBox("BRS")
        self.brs.setChecked(self.job.brs)
        self.enable = QCheckBox("Cyclic TX")
        self.once = QPushButton("Send once")
        self.count = QLabel("0 sent")
        self.count.setProperty("role", "muted")
        ctl = QHBoxLayout()                    # two short rows: the panel never needs to be wide
        ctl.setSpacing(8)
        for w in (caption("Channel"), self.channel, caption("Cycle"), self.cycle, self.fd, self.brs):
            ctl.addWidget(w)
        ctl.addStretch()
        ctl2 = QHBoxLayout()
        ctl2.setSpacing(8)
        ctl2.addWidget(self.enable)
        ctl2.addWidget(self.once)
        ctl2.addStretch()
        ctl2.addWidget(self.count)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(GRID_GAP)
        self.grid.setVerticalSpacing(4)
        for col, text in enumerate(COLUMN_TITLES):
            if text:
                col_head = caption(text)
                if col == C_RAW:
                    col_head.setAlignment(Qt.AlignRight | Qt.AlignVCenter)     # over right-aligned numbers
                self.grid.addWidget(col_head, 0, col)
        self.grid.setColumnStretch(C_SLIDER, 1)
        self.rows: list[SignalRow] = []
        for i, sig in enumerate(message.signals):
            self.rows.append(SignalRow(self, sig, self.grid, 1 + 2 * i))

        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        body.addLayout(head)
        body.addLayout(ctl)
        body.addLayout(ctl2)
        body.addLayout(self.grid)

        self.channel.currentTextChanged.connect(self._channel_changed)
        self.cycle.valueChanged.connect(self._cycle_changed)
        self.fd.toggled.connect(lambda v: setattr(self.job, "fd", v))
        self.brs.toggled.connect(lambda v: setattr(self.job, "brs", v))
        self.enable.toggled.connect(self._enable_toggled)
        self.once.clicked.connect(self._send_once)
        self.remove.clicked.connect(lambda: panel.remove_card(self))
        self.copy.clicked.connect(self._copy_to_channel)
        self._channel_changed(self.channel.currentText())

    def set_sliders_visible(self, on: bool) -> None:
        for r in self.rows:
            r.set_slider_visible(on)

    def _channel_changed(self, ch: str) -> None:
        self.job.channel = ch

    def _copy_to_channel(self) -> None:
        taken = {c.job.channel for c in self.panel.cards if c.message.name == self.message.name}
        free = [ch for ch in self.ctx.can.bus.channels if ch not in taken]
        if not free:
            QMessageBox.information(self, "Add channel", f"{self.message.name} is already sent on every open "
                                    "channel. Connect another adapter first.")
            return
        self.panel.add_message(self.message.name, free[0])

    def _cycle_changed(self, ms: int) -> None:
        self.ctx.can.tx.set_cycle(self.job, ms or None)
        if not ms and self.enable.isChecked():
            self.enable.setChecked(False)

    def _need_channel(self) -> bool:
        if not self.job.channel or self.job.channel not in self.ctx.can.bus.channels:
            QMessageBox.warning(self, "No channel", "Open a CAN channel in the sidebar (Channels) first, "
                                "then pick it here.")
            return False
        return True

    def _enable_toggled(self, on: bool) -> None:
        if not on:
            self.ctx.can.tx.stop(self.job)
            return
        if not self.job.cycle_ms:
            QMessageBox.information(self, "Cycle time", "Set a cycle time (ms) for cyclic transmission.")
            self.enable.setChecked(False)
        elif not self._need_channel():
            self.enable.setChecked(False)
        else:
            self.ctx.can.tx.start(self.job)

    def _send_once(self) -> None:
        if self._need_channel():
            self.ctx.can.tx.trigger(self.job)

    def refresh(self) -> None:
        """Called by the panel timer: tx counter + live generator values."""
        self.count.setText(f"{self.job.count} sent")
        if self.enable.isChecked() and not self.job.enabled:      # stopped by an error
            self.enable.blockSignals(True)
            self.enable.setChecked(False)
            self.enable.blockSignals(False)
        for r in self.rows:
            if r.generator_active():
                r.set_display(self.job.state.get_signal(r.sig.name))


DB_SUFFIXES = (".dbc", ".arxml", ".kcd", ".sym")


class SignalPanel(QFrame):
    """Workspace for TX message cards. Shows an empty-state card until there is something to show."""

    openDbcRequested = Signal()
    filesDropped = Signal(list)

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("signals-panel")
        self.setAcceptDrops(True)
        self.ctx = ctx
        self.cards: list[MessageCard] = []

        self.empty = EmptyState("database", "No DBC Database Loaded",
                                "Drag & drop a .dbc / .arxml file here or click below to start "
                                "simulating CAN signals.")
        self.open_btn = self.empty.add_button("Open DBC File", "primary")
        self.open_btn.clicked.connect(self.openDbcRequested)

        self.box = QVBoxLayout()
        self.box.setAlignment(Qt.AlignTop)
        self.box.setContentsMargins(12, 12, 12, 12)       # padding under the dock title
        self.box.setSpacing(12)
        inner = QWidget()
        inner.setObjectName("signals-inner")
        inner.setLayout(self.box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # vertical scrolling only
        scroll.setWidget(inner)
        scroll.viewport().installEventFilter(self)
        self.scroll = scroll
        self._sliders_on = True
        self.setMinimumWidth(GRID_MIN_WIDTH + 2 * PANEL_MARGIN + 14)    # + scrollbar: no column is ever cut
        self.stack = QStackedWidget()
        self.stack.addWidget(centered(self.empty))     # page 0: empty state
        self.stack.addWidget(scroll)                   # page 1: message cards
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)

        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: [c.refresh() for c in self.cards])
        self.timer.start(150)
        self.refresh_empty()

    # ---- responsive sliders -----------------------------------------------------------
    def eventFilter(self, obj, ev) -> bool:
        from PySide6.QtCore import QEvent
        if obj is self.scroll.viewport() and ev.type() == QEvent.Resize:
            self._apply_slider_rule()
        return False

    def _apply_slider_rule(self) -> None:
        """Sliders are the first thing to go when the panel is narrow (never a horizontal scrollbar)."""
        on = self.scroll.viewport().width() >= GRID_MIN_WIDTH + GRID_GAP + SLIDER_MIN + 2 * PANEL_MARGIN
        self._sliders_on = on
        for c in self.cards:
            c.set_sliders_visible(on)

    # ---- empty state ----------------------------------------------------------------
    def refresh_empty(self) -> None:
        """Pick the page: cards if any; otherwise the empty state matching what is missing."""
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

    # ---- cards ---------------------------------------------------------------------------
    def add_message(self, name: str, channel: str | None = None) -> MessageCard | None:
        """Card for message `name` on `channel` (None: the existing card of that message, else the first open
        channel). The same message can have one card per channel, e.g. sent by CAN1 and by CAN2."""
        for c in self.cards:
            if c.message.name == name and (channel is None or c.job.channel == channel):
                return c
        msg = self.ctx.can.registry.message_by_name(name)
        if msg is None:
            return None
        chans = self.ctx.can.bus.channels
        card = MessageCard(self.ctx, self, msg, channel or (chans[0] if chans else ""))
        if self.cards:                                    # thin rule between message sections
            card.separator = QFrame()
            card.separator.setObjectName("msg-sep")
            card.separator.setFixedHeight(1)
            self.box.addWidget(card.separator)
        else:
            card.separator = None
        self.cards.append(card)
        self.box.addWidget(card)
        card.set_sliders_visible(self._sliders_on)
        self.refresh_empty()
        return card

    def remove_card(self, card: MessageCard) -> None:
        self.ctx.can.tx.remove_job(card.job)
        self.cards.remove(card)
        if card.separator is not None:
            card.separator.setParent(None)
            card.separator.deleteLater()
        elif self.cards and self.cards[0].separator is not None:   # the new first section needs no rule above
            self.cards[0].separator.setParent(None)
            self.cards[0].separator.deleteLater()
            self.cards[0].separator = None
        card.setParent(None)
        card.deleteLater()
        self.refresh_empty()

    def stop_all(self) -> None:
        for c in self.cards:
            c.enable.setChecked(False)
