"""Signals panel: one flat section per TX message; every signal is a collapsible row (collapsed by default) whose
details hold the slider / value input, value table, generator and signal layout."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QStackedWidget,
                               QToolButton,
                               QVBoxLayout, QWidget)

from autosim.core.can_engine.generators import PARAM_LABELS, make_generator
from autosim.ui.theme import ThemeManager
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


# grid columns of a message section (collapsed signal rows)
C_NAME, C_VALUE, C_RAW, C_GEN = range(4)
COLUMN_TITLES = ("Signal", "Value", "Raw", "Generator")
NAME_W, VALUE_MIN_W, RAW_W, GEN_STATUS_W = 168, 110, 56, 76
SPIN_W, TABLE_W, GEN_W, DETAIL_CAPTION_W = 112, 160, 92, 88
GRID_GAP, PANEL_MARGIN = 8, 12
GRID_MIN_WIDTH = NAME_W + VALUE_MIN_W + RAW_W + GEN_STATUS_W + 3 * GRID_GAP
GEN_ITEMS = (("Off", "none"), ("Sine", "sine"), ("Ramp", "ramp"), ("Square", "square"), ("Toggle", "toggle"))


class _RowToggle(QToolButton):
    """Chevron + signal name; a QObject, so its theme connection ends with the widget."""

    def __init__(self) -> None:
        super().__init__()
        self.toggled.connect(lambda _on: self.refresh_icon())
        ThemeManager.instance().changed.connect(self.refresh_icon)
        self.refresh_icon()

    def refresh_icon(self, _dark: bool = True) -> None:
        from autosim.ui.icons import make_icon
        from autosim.ui.theme import icon_color
        self.setIcon(make_icon("chevron-down" if self.isChecked() else "chevron-right", icon_color()))


class SignalRow:
    """One signal. Collapsed (default): ▸ name | value + unit | raw | generator status. Expanded: the details
    to configure it underneath - slider + value input, value table, generator (+ parameters), signal layout."""

    def __init__(self, card: "MessageCard", sig, grid: QGridLayout, row: int) -> None:
        self.card, self.sig = card, sig
        self.lo, self.hi, bounded = signal_range(sig)
        factor = abs(sig.scale) or 1.0
        if float(factor).is_integer() and not sig.is_float:
            self.decimals = 0
        else:
            self.decimals = min(6, max(1, len(f"{factor:.6f}".rstrip("0").split(".")[1])))

        # ---- collapsed row ----
        self.toggle = _RowToggle()
        self.toggle.setObjectName("row-toggle")
        self.toggle.setCheckable(True)
        self.toggle.setAutoRaise(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setFixedWidth(NAME_W)
        self.toggle.setText(self.toggle.fontMetrics().elidedText(sig.name, Qt.ElideRight, NAME_W - 30))
        self.toggle.setToolTip(f"{sig.name} - click to show / hide its settings")
        self.toggle.setAccessibleName(f"{sig.name} details")
        self.summary = QLabel()
        self.summary.setObjectName("signal-value")
        self.summary.setMinimumWidth(VALUE_MIN_W)
        self.raw = QLabel("—")
        self.raw.setObjectName("raw-value")
        self.raw.setFixedWidth(RAW_W)
        self.raw.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.gen_status = QLabel("")
        self.gen_status.setObjectName("gen-status")
        self.gen_status.setFixedWidth(GEN_STATUS_W)

        # the details (slider, value input, value table, generator) are built on first expand: a message with many
        # signals stays cheap (fewer widgets to create, restyle on a theme switch and lay out)
        self._grid, self._grid_row, self._bounded, self._factor = grid, row, bounded, factor
        self._detail = None

        grid.addWidget(self.toggle, row, C_NAME)
        grid.addWidget(self.summary, row, C_VALUE)
        grid.addWidget(self.raw, row, C_RAW)
        grid.addWidget(self.gen_status, row, C_GEN)
        self.toggle.toggled.connect(self.set_expanded)
        self.set_display(card.job.state.get_signal(sig.name))

    # ---- details (built on first use) -----------------------------------------
    def _ensure_detail(self) -> None:
        if self._detail is not None:
            return
        sig = self.sig
        self._detail = QFrame()
        self._detail.setObjectName("signal-detail")
        dg = QGridLayout(self._detail)
        dg.setContentsMargins(24, 6, 0, 10)
        dg.setHorizontalSpacing(GRID_GAP)
        dg.setVerticalSpacing(6)

        self._slider = None
        if self._bounded and self.hi > self.lo:
            self._slider = QSlider(Qt.Horizontal)
            self._slider.setRange(0, 1000)
            self._slider.setMinimumWidth(72)
            self._slider.setAccessibleName(f"{sig.name} slider")
            self._slider.valueChanged.connect(self._slider_moved)
        self._spin = _spin(self.lo, self.hi, self.decimals, self._factor)
        self._spin.setFixedWidth(SPIN_W)
        if sig.unit:
            self._spin.setSuffix(f" {sig.unit}")
        self._spin.setAccessibleName(f"{sig.name} value")
        self._spin.valueChanged.connect(self._user)

        self._choice = None
        if sig.choices:
            self._choice = QComboBox()
            self._choice.setPlaceholderText("—")                   # value not in the table
            for k, v in sorted(sig.choices.items()):
                self._choice.addItem(f"{int(k)}: {v}", int(k))
            self._choice.setMinimumWidth(TABLE_W)
            self._choice.setToolTip("Value table: pick a named value")
            self._choice.currentIndexChanged.connect(self._choice_picked)
            table = self._choice
        else:
            table = QLabel("—")
            table.setObjectName("empty-cell")
            table.setToolTip("This signal has no value table")

        self._gen = QComboBox()
        for text, kind in GEN_ITEMS:
            self._gen.addItem(text, kind)
        self._gen.setFixedWidth(GEN_W)
        self._gen.setToolTip("Generator: drive this signal automatically (sine, ramp, square, toggle)")
        self._params = [_spin(-UNBOUNDED, UNBOUNDED, 3, 0.5) for _ in range(3)]
        self._param_row = QWidget()
        pl = QHBoxLayout(self._param_row)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(GRID_GAP)
        for p in self._params:
            p.setMinimumWidth(96)
            p.valueChanged.connect(self._rebind)
            pl.addWidget(p, 1)
        self._param_row.hide()
        self._gen.currentIndexChanged.connect(lambda _i: self._gen_changed(self._gen.currentData()))

        order = "Intel" if sig.byte_order == "little_endian" else "Motorola"
        rng = f"{self.lo:g} … {self.hi:g}" if self._bounded else "unbounded"
        info = QLabel(f"start bit {sig.start} · {sig.length} bit · {order} · "
                      f"{'signed' if sig.is_signed else 'unsigned'}{' float' if sig.is_float else ''} · "
                      f"factor {sig.scale:g} · offset {sig.offset:g} · range {rng}"
                      + (f" {sig.unit}" if sig.unit and self._bounded else "")
                      + ("" if sig.minimum is not None or not self._bounded else " (from the bit length)"))
        info.setProperty("role", "muted")
        info.setWordWrap(True)

        def cap(text: str) -> QLabel:
            lb = caption(text)
            lb.setFixedWidth(DETAIL_CAPTION_W)
            return lb
        dg.addWidget(cap("Value"), 0, 0)
        value_row = QHBoxLayout()
        value_row.setSpacing(GRID_GAP)
        if self._slider is not None:
            value_row.addWidget(self._slider, 1)
        value_row.addWidget(self._spin)
        if self._slider is None:
            value_row.addStretch(1)
        dg.addLayout(value_row, 0, 1)
        dg.addWidget(cap("Value table"), 1, 0)
        dg.addWidget(table, 1, 1, Qt.AlignLeft)
        dg.addWidget(cap("Generator"), 2, 0)
        gen_row = QHBoxLayout()
        gen_row.setSpacing(GRID_GAP)
        gen_row.addWidget(self._gen)
        gen_row.addWidget(self._param_row, 1)
        gen_row.addStretch(0)                              # keeps the combo left while no parameters are shown
        dg.addLayout(gen_row, 2, 1)
        dg.addWidget(cap("Layout"), 3, 0, Qt.AlignTop)
        dg.addWidget(info, 3, 1)
        dg.setColumnStretch(1, 1)
        self._detail.setVisible(self.toggle.isChecked())
        self._grid.addWidget(self._detail, self._grid_row + 1, 0, 1, len(COLUMN_TITLES))
        self._tips = {w: w.toolTip() for w in (self._spin, self._slider, self._choice) if w is not None}
        self.set_display(self.card.job.state.get_signal(sig.name))

    def _lazy(name):
        def get(self):
            self._ensure_detail()
            return getattr(self, "_" + name)
        return property(get)

    detail = _lazy("detail")
    slider = _lazy("slider")
    spin = _lazy("spin")
    choice = _lazy("choice")
    gen = _lazy("gen")
    params = _lazy("params")
    param_row = _lazy("param_row")
    del _lazy

    # ---- expand / collapse --------------------------------------------------
    @property
    def expanded(self) -> bool:
        return self.toggle.isChecked()

    def set_expanded(self, on: bool) -> None:
        if self.toggle.isChecked() != on:
            self.toggle.setChecked(on)                    # re-enters through toggled
            return
        if on:
            self._ensure_detail()                         # built the first time it is opened
        if self._detail is not None:
            self._detail.setVisible(on)

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

    def value_text(self, v: float) -> str:
        """Collapsed-row display: physical value + unit, plus the value-table name when there is one."""
        text = f"{v:.{self.decimals}f}" + (f" {self.sig.unit}" if self.sig.unit else "")
        if self.sig.choices:
            try:
                text += f"  ({self.sig.choices[int(round(v))]})"
            except (KeyError, ValueError):
                pass
        return text

    def _sync(self, v: float) -> None:
        """Summary, slider position, raw value and value-table entry follow the physical value."""
        self.summary.setText(self.value_text(v))
        built = self._detail is not None
        if built and self._slider is not None:
            self._slider.blockSignals(True)
            self._slider.setValue(int(round((v - self.lo) / (self.hi - self.lo) * 1000)))
            self._slider.blockSignals(False)
        if self.sig.is_float or not self.sig.scale:
            self.raw.setText("—")
            self.raw.setToolTip("Raw value: float signal")
        else:
            raw = int(round((v - self.sig.offset) / self.sig.scale))
            self.raw.setText(str(raw))
            self.raw.setToolTip(f"Raw value {raw} = 0x{raw & ((1 << self.sig.length) - 1):X}  "
                                f"(physical = raw × {self.sig.scale:g} + {self.sig.offset:g})")
        if built and self._choice is not None:
            self._choice.blockSignals(True)
            self._choice.setCurrentIndex(self._choice.findData(int(round(v))))   # -1 shows the "—" placeholder
            self._choice.blockSignals(False)

    def set_display(self, v: float) -> None:
        """Show a value without writing it back (used for generator feedback)."""
        if self._detail is not None:
            self._spin.blockSignals(True)
            self._spin.setValue(v)
            self._spin.blockSignals(False)
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
        self.gen_status.setText(self.gen.currentText() if active else "")
        self.gen_status.setToolTip(f"Driven by the {self.gen.currentText().lower()} generator" if active else "")
        for w, tip in self._tips.items():
            w.setEnabled(not active)
            w.setToolTip("Driven by the generator - set Generator to Off to edit" if active else tip)
        self._rebind()

    def _rebind(self) -> None:
        kind = self.gen.currentData()
        gen = make_generator(kind, *(p.value() for p in self.params)) if kind != "none" else None
        self.card.ctx.can.bind_generator(self.card.job, self.sig.name, gen)

    def generator_active(self) -> bool:
        return self._detail is not None and self._gen.currentData() != "none"


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
        meta.setMinimumWidth(0)                            # may shrink (clip) so the header always fits
        meta.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        meta.setToolTip(meta.text())
        self.copy = QPushButton("+ Channel")
        set_kind(self.copy, "ghost")
        self.copy.setToolTip("Also send this message on another open channel (one section per channel)")
        self.remove = QPushButton("✕")
        set_kind(self.remove, "icon")
        self.remove.setToolTip("Remove this message")
        self.remove.setAccessibleName(f"Remove {message.name}")
        self.expand_all = QPushButton("Expand signals")
        set_kind(self.expand_all, "ghost")
        self.expand_all.setToolTip("Show / hide the settings of every signal of this message")
        self.fold = _RowToggle()                           # collapse the whole message to its header line
        self.fold.setObjectName("msg-fold")
        self.fold.setCheckable(True)
        self.fold.setChecked(True)
        self.fold.setAutoRaise(True)
        self.fold.setToolTip("Collapse / expand this message")
        self.fold.setAccessibleName(f"Collapse {message.name}")
        title.mousePressEvent = lambda _e: self.fold.toggle()
        title.setCursor(Qt.PointingHandCursor)
        self.state = QLabel("")                            # one-line summary while collapsed
        self.state.setProperty("role", "muted")
        self.state.hide()
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self.fold)
        head.addWidget(title)
        head.addWidget(meta, 1)
        self.state.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        head.addWidget(self.state, 2)
        head.addWidget(self.expand_all)
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
        self.cycle.setFixedWidth(112)                      # "60000 ms" fits
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
            col_head = caption(text)
            if col == C_NAME:
                col_head.setContentsMargins(26, 0, 0, 0)               # aligned with the names after the chevron
            if col == C_RAW:
                col_head.setAlignment(Qt.AlignRight | Qt.AlignVCenter)     # over right-aligned numbers
            self.grid.addWidget(col_head, 0, col)
        self.grid.setColumnStretch(C_VALUE, 1)
        self.rows: list[SignalRow] = []
        for i, sig in enumerate(message.signals):
            self.rows.append(SignalRow(self, sig, self.grid, 1 + 2 * i))

        self.body = QWidget()                              # everything below the header (hidden when collapsed)
        inner = QVBoxLayout(self.body)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(8)
        inner.addLayout(ctl)
        inner.addLayout(ctl2)
        inner.addLayout(self.grid)
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        body.addLayout(head)
        body.addWidget(self.body)
        self.fold.toggled.connect(self.set_open)

        self.channel.currentTextChanged.connect(self._channel_changed)
        self.cycle.valueChanged.connect(self._cycle_changed)
        self.fd.toggled.connect(lambda v: setattr(self.job, "fd", v))
        self.brs.toggled.connect(lambda v: setattr(self.job, "brs", v))
        self.enable.toggled.connect(self._enable_toggled)
        self.once.clicked.connect(self._send_once)
        self.remove.clicked.connect(lambda: panel.remove_card(self))
        self.copy.clicked.connect(self._copy_to_channel)
        self.expand_all.clicked.connect(lambda: self.set_all_expanded(not any(r.expanded for r in self.rows)))
        for r in self.rows:
            r.toggle.toggled.connect(lambda _on: self._sync_expand_button())
        self._channel_changed(self.channel.currentText())

    @property
    def is_open(self) -> bool:
        return self.fold.isChecked()

    def set_open(self, on: bool) -> None:
        """Collapsed: only the header line (with a channel · cycle · TX · sent summary) remains."""
        if self.fold.isChecked() != on:
            self.fold.setChecked(on)                       # re-enters through toggled
            return
        self.body.setVisible(on)
        self.expand_all.setVisible(on)
        self.state.setVisible(not on)
        self._update_state()

    def _update_state(self) -> None:
        if self.state.isVisible():
            cycle = f"{self.job.cycle_ms} ms" if self.job.cycle_ms else "event"
            tx = "TX on" if self.job.enabled else "TX off"
            self.state.setText(f"· {self.job.channel or 'no channel'} · {cycle} · {tx} · {self.job.count} sent")

    def set_all_expanded(self, on: bool) -> None:
        for r in self.rows:
            r.set_expanded(on)
        self._sync_expand_button()

    def _sync_expand_button(self) -> None:
        self.expand_all.setText("Collapse signals" if any(r.expanded for r in self.rows) else "Expand signals")

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
        if not pick and self.enable.isChecked():          # the channel went away: cyclic TX stops
            self.enable.setChecked(False)

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
        if self.job.channel not in self.ctx.can.bus.channels:
            self.sync_channels(self.ctx.can.bus.channels)     # added before connecting: pick an open channel now
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
        self._update_state()
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
        self.scroll = scroll
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
        self.refresh_empty()
        return card

    def set_all_open(self, on: bool) -> None:
        """Collapse / expand every message (Signals panel header)."""
        for c in self.cards:
            c.set_open(on)

    def channels_changed(self, channels: list[str]) -> None:
        for c in self.cards:
            c.sync_channels(channels)

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
