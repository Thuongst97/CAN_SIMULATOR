"""Measurement Start / Stop at the top of the left rail, like CANoe.

The desktop app starts with the measurement **stopped**: channels can be connected, messages added and Cyclic ticked,
but nothing is sent, answered (auto response) or logged in the trace. **Start** (F9) runs it: armed cyclic messages
begin, Send works, the trace records (cleared first, by default). **Stop** (Shift+F9) silences everything again;
cyclic messages stay armed for the next start.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from autosim.ui.icons import make_icon
from autosim.ui.settings_store import app_settings
from autosim.ui.theme import ThemeManager, token

CLEAR_KEY = "measurement/clear_trace_on_start"


class MeasurementControls(QWidget):
    """Start / Stop buttons and the state, at the top of the left rail (above the desktops), like CANoe's
    measurement buttons: ▶ Start and ■ Stop side by side, "Stopped" or the running time below."""

    startRequested = Signal()
    stopRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("measurement")
        self.setFixedWidth(74)
        self.start_btn = self._button("Start measurement (F9): send, answer and log the trace")
        self.stop_btn = self._button("Stop measurement (Shift+F9): nothing is sent, answered or logged")
        self.start_btn.setObjectName("meas-start")
        self.stop_btn.setObjectName("meas-stop")
        self.state = QLabel()
        self.state.setObjectName("meas-state")
        self.state.setAlignment(Qt.AlignCenter)
        line = QFrame()
        line.setObjectName("meas-sep")
        line.setFixedHeight(1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        lay.addLayout(row)
        lay.addWidget(self.state)
        lay.addSpacing(4)
        lay.addWidget(line)
        self.start_btn.clicked.connect(self.startRequested)
        self.stop_btn.clicked.connect(self.stopRequested)
        self.running = False
        ThemeManager.instance().changed.connect(self._theme_changed)
        self.set_running(False)

    @staticmethod
    def _button(tip: str) -> QToolButton:
        b = QToolButton()
        b.setToolTip(tip)
        b.setAccessibleName(tip.split(" (")[0])
        b.setFocusPolicy(Qt.NoFocus)
        b.setIconSize(QSize(20, 20))
        b.setFixedSize(36, 34)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def _theme_changed(self, _dark: bool = True) -> None:
        self.set_running(self.running, self.elapsed_text)

    elapsed_text = ""

    def set_running(self, on: bool, elapsed: str = "") -> None:
        self.running = on
        self.elapsed_text = elapsed
        muted = token("TEXT_DISABLED")
        self.start_btn.setIcon(make_icon("play", muted if on else token("SUCCESS")))
        self.stop_btn.setIcon(make_icon("stop", token("ERROR") if on else muted))
        self.start_btn.setEnabled(not on)
        self.stop_btn.setEnabled(on)
        color = QColor(token("SUCCESS") if on else token("TEXT_MUTED")).name()
        self.state.setText(f"● {elapsed or '00:00:00'}" if on else "Stopped")
        self.state.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.state.setToolTip("Measurement running (time since Start): frames are sent, answered and logged" if on
                              else "Measurement stopped: nothing is sent, answered or logged. Start: F9")


class Measurement:
    """Start / stop of the desktop app's measurement (engine gate + UI state)."""

    def __init__(self, win) -> None:
        self.win = win
        self.t0: float | None = None
        self.controls = MeasurementControls()
        self.controls.startRequested.connect(self.start)
        self.controls.stopRequested.connect(self.stop)
        win.ctx.can.set_measuring(False)                    # the desktop app starts stopped, like CANoe
        win.signals_panel.set_measuring(False)

    @property
    def running(self) -> bool:
        return self.win.ctx.can.measuring

    @staticmethod
    def clear_on_start() -> bool:
        return str(app_settings().value(CLEAR_KEY, "true")).lower() in ("true", "1")

    @staticmethod
    def set_clear_on_start(on: bool) -> None:
        app_settings().setValue(CLEAR_KEY, "true" if on else "false")

    def start(self) -> None:
        if self.running:
            return
        if self.clear_on_start():
            self.win.trace.clear()
            self.win._trace_cleared()
        self.t0 = time.monotonic()
        self.win.ctx.can.set_measuring(True)
        self._changed()
        self.win.ctx.events.publish("log", "Measurement started")

    def stop(self) -> None:
        if not self.running:
            return
        self.win.ctx.can.set_measuring(False)
        self._changed()
        self.win.ctx.events.publish("log", "Measurement stopped")

    def toggle(self) -> None:
        (self.stop if self.running else self.start)()

    def elapsed(self) -> float:
        return 0.0 if self.t0 is None or not self.running else time.monotonic() - self.t0

    def _changed(self) -> None:
        from autosim.ui.shell.status_bar import fmt_elapsed
        on = self.running
        self.controls.set_running(on, fmt_elapsed(self.elapsed()) if on else "")
        self.win.signals_panel.set_measuring(on)
        self.win._update_status()

    def tick(self) -> None:
        """Once per second (main window timer): the running time next to the buttons."""
        if self.running:
            from autosim.ui.shell.status_bar import fmt_elapsed
            self.controls.set_running(True, fmt_elapsed(self.elapsed()))
