"""Interactive signal panel: one card per TX message with sliders, enums and generators."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGroupBox, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget,
                               QVBoxLayout, QWidget)

from autosim.core.can_engine.generators import PARAM_LABELS, make_generator
from autosim.ui.widgets import EmptyState, centered, set_kind

GEN_KINDS = ["none", "sine", "ramp", "square", "toggle"]


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


class SignalRow(QWidget):
    def __init__(self, card: "MessageCard", sig) -> None:
        super().__init__()
        self.card, self.sig = card, sig
        lo = sig.minimum if sig.minimum is not None else -1e9
        hi = sig.maximum if sig.maximum is not None else 1e9
        self.lo, self.hi = lo, hi
        factor = abs(sig.scale) or 1.0
        decimals = 0 if (float(factor).is_integer() and not sig.is_float) else min(6, max(1, len(f"{factor:.6f}".rstrip("0").split(".")[1])))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        name = QLabel(sig.name)
        name.setMinimumWidth(140)
        name.setToolTip(f"{sig.length} bit @ {sig.start}, factor {sig.scale}, offset {sig.offset}")
        lay.addWidget(name)

        self.choice = None
        self.slider = None
        self.spin = _spin(lo, hi, decimals, factor)
        if sig.choices:
            self.choice = QComboBox()
            for k, v in sorted(sig.choices.items()):
                self.choice.addItem(f"{int(k)}: {v}", int(k))
            self.choice.currentIndexChanged.connect(lambda _i: self._user(self.choice.currentData()))
            lay.addWidget(self.choice, 1)
        else:
            if sig.minimum is not None and sig.maximum is not None and hi > lo:
                self.slider = QSlider(Qt.Horizontal)
                self.slider.setRange(0, 1000)
                self.slider.valueChanged.connect(self._slider_moved)
                lay.addWidget(self.slider, 1)
            else:
                lay.addStretch(1)
            self.spin.valueChanged.connect(self._user)
            lay.addWidget(self.spin)
        lay.addWidget(QLabel(sig.unit or ""))

        self.gen = QComboBox()
        self.gen.addItems(GEN_KINDS)
        self.gen.setToolTip("Automatic value generator")
        self.params = [_spin(-1e9, 1e9, 3, 0.5) for _ in range(3)]
        lay.addWidget(self.gen)
        for p in self.params:
            p.setMaximumWidth(80)
            p.hide()
            p.valueChanged.connect(self._rebind)
            lay.addWidget(p)
        self.gen.currentTextChanged.connect(self._gen_changed)
        self.set_display(card.job.state.get_signal(sig.name))

    # ---- value editing ------------------------------------------------------
    def _slider_moved(self, pos: int) -> None:
        if self.slider.signalsBlocked():
            return
        v = self.lo + (self.hi - self.lo) * pos / 1000.0
        self.spin.setValue(v)            # triggers _user via valueChanged

    def _user(self, value) -> None:
        self.card.ctx.can.set_signal(self.card.job, self.sig.name, float(value))
        self._sync_slider(float(value))

    def _sync_slider(self, v: float) -> None:
        if self.slider is not None:
            self.slider.blockSignals(True)
            self.slider.setValue(int(round((v - self.lo) / (self.hi - self.lo) * 1000)))
            self.slider.blockSignals(False)

    def set_display(self, v: float) -> None:
        """Show a value without writing it back (used for generator feedback)."""
        if self.choice is not None:
            i = self.choice.findData(int(v))
            self.choice.blockSignals(True)
            if i >= 0:
                self.choice.setCurrentIndex(i)
            self.choice.blockSignals(False)
        else:
            self.spin.blockSignals(True)
            self.spin.setValue(v)
            self.spin.blockSignals(False)
            self._sync_slider(v)

    # ---- generators ---------------------------------------------------------
    def _gen_changed(self, kind: str) -> None:
        active = kind != "none"
        if active:
            mid = (self.hi + self.lo) / 2 if abs(self.hi) < 1e8 else 0.0
            span = (self.hi - self.lo) if abs(self.hi) < 1e8 else 100.0
            lo = self.lo if abs(self.lo) < 1e8 else 0.0
            hi = self.hi if abs(self.hi) < 1e8 else 100.0
            defaults = {"sine": (span / 2, 1.0, mid), "ramp": (lo, hi, 5.0),
                        "square": (lo, hi, 1.0), "toggle": (lo, hi, 1.0)}[kind]
            for p, d, label in zip(self.params, defaults, PARAM_LABELS[kind]):
                p.blockSignals(True)
                p.setValue(d)
                p.blockSignals(False)
                p.setToolTip(label)
                p.setPrefix(label[:3] + " ")
        for p in self.params:
            p.setVisible(active)
        self.spin.setEnabled(not active)
        if self.slider:
            self.slider.setEnabled(not active)
        if self.choice:
            self.choice.setEnabled(not active)
        self._rebind()

    def _rebind(self) -> None:
        kind = self.gen.currentText()
        gen = make_generator(kind, *(p.value() for p in self.params)) if kind != "none" else None
        self.card.ctx.can.bind_generator(self.card.job, self.sig.name, gen)

    def generator_active(self) -> bool:
        return self.gen.currentText() != "none"


class MessageCard(QGroupBox):
    def __init__(self, ctx, panel: "SignalPanel", message, channel: str) -> None:
        ext = "x" if message.is_extended_frame else ""
        super().__init__(f"{message.name}   0x{message.frame_id:X}{ext}   ({message.length} bytes)")
        self.ctx, self.panel, self.message = ctx, panel, message
        self.job = ctx.can.tx.add_job(channel, message, int(message.cycle_time) if message.cycle_time else None)

        self.channel = ChannelCombo(ctx)
        self.cycle = QSpinBox()
        self.cycle.setRange(0, 60000)
        self.cycle.setSuffix(" ms")
        self.cycle.setSpecialValueText("event")
        self.cycle.setValue(self.job.cycle_ms or 0)
        self.fd = QCheckBox("FD")
        self.fd.setChecked(self.job.fd)
        self.brs = QCheckBox("BRS")
        self.brs.setChecked(self.job.brs)
        self.enable = QCheckBox("Cyclic TX")
        self.once = QPushButton("Send once")
        self.remove = QPushButton("✕")
        set_kind(self.remove, "icon")
        self.remove.setToolTip("Remove this message")
        self.count = QLabel("0 sent")

        top = QVBoxLayout()                    # two compact rows instead of one very wide row
        top.setSpacing(8)
        r1, r2 = QHBoxLayout(), QHBoxLayout()
        r1.setSpacing(8)
        r2.setSpacing(12)
        for w in (QLabel("Channel"), self.channel, QLabel("Cycle"), self.cycle, self.fd, self.brs):
            r1.addWidget(w)
        r1.addStretch()
        r1.addWidget(self.remove)
        for w in (self.enable, self.once):
            r2.addWidget(w)
        r2.addStretch()
        r2.addWidget(self.count)
        top.addLayout(r1)
        top.addLayout(r2)

        self.rows: list[SignalRow] = []
        body = QVBoxLayout(self)
        body.addLayout(top)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        body.addWidget(line)
        for sig in message.signals:
            r = SignalRow(self, sig)
            self.rows.append(r)
            body.addWidget(r)

        self.channel.currentTextChanged.connect(self._channel_changed)
        self.cycle.valueChanged.connect(self._cycle_changed)
        self.fd.toggled.connect(lambda v: setattr(self.job, "fd", v))
        self.brs.toggled.connect(lambda v: setattr(self.job, "brs", v))
        self.enable.toggled.connect(self._enable_toggled)
        self.once.clicked.connect(self._send_once)
        self.remove.clicked.connect(lambda: panel.remove_card(self))
        self._channel_changed(self.channel.currentText())

    def _channel_changed(self, ch: str) -> None:
        self.job.channel = ch

    def _cycle_changed(self, ms: int) -> None:
        self.ctx.can.tx.set_cycle(self.job, ms or None)
        if not ms and self.enable.isChecked():
            self.enable.setChecked(False)

    def _need_channel(self) -> bool:
        if not self.job.channel or self.job.channel not in self.ctx.can.bus.channels:
            QMessageBox.warning(self, "No channel", "Open a CAN channel in the Connection panel first, "
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
        self.setObjectName("card")
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
        self.box.setContentsMargins(12, 12, 12, 12)
        self.box.setSpacing(12)
        inner = QWidget()
        inner.setLayout(self.box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)

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
    def add_message(self, name: str) -> MessageCard | None:
        for c in self.cards:
            if c.message.name == name:
                return c
        msg = self.ctx.can.registry.message_by_name(name)
        if msg is None:
            return None
        chans = self.ctx.can.bus.channels
        card = MessageCard(self.ctx, self, msg, chans[0] if chans else "")
        self.cards.append(card)
        self.box.addWidget(card)
        self.refresh_empty()
        return card

    def remove_card(self, card: MessageCard) -> None:
        self.ctx.can.tx.remove_job(card.job)
        self.cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        self.refresh_empty()

    def stop_all(self) -> None:
        for c in self.cards:
            c.enable.setChecked(False)
