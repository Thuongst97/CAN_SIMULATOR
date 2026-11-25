"""Measurement Start / Stop at the top of the left rail, like CANoe.

The desktop app starts with the measurement **stopped**: channels can be connected, messages added and Cyclic ticked,
but nothing is sent, answered (auto response) or logged in the trace. One button (F9) starts and stops it: **Start**
runs it - armed cyclic messages begin, Send works, the trace records (cleared first, by default) - and the button
turns into **Stop**, which silences everything again; cyclic messages stay armed for the next start.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QLabel, QToolButton, QVBoxLayout, QWidget

from autosim.ui.icons import make_icon
from autosim.ui.settings_store import app_settings
from autosim.ui.theme import ThemeManager, token

CLEAR_KEY = "measurement/clear_trace_on_start"


class MeasurementControls(QWidget):
    """One Start / Stop button at the top of the left rail (above the desktops), like CANoe: ▶ Start while the
    measurement is stopped, ■ Stop while it runs; "Stopped" or the running time below."""

    toggleRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("measurement")
        self.setFixedWidth(74)
        self.button = QToolButton()
        self.button.setObjectName("meas-toggle")
        self.button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.button.setIconSize(QSize(22, 22))
        self.button.setFixedSize(74, 52)
        self.button.setFocusPolicy(Qt.NoFocus)
        self.button.setCursor(Qt.PointingHandCursor)
        self.state = QLabel()
        self.state.setObjectName("meas-state")
        self.state.setAlignment(Qt.AlignCenter)
        line = QFrame()
        line.setObjectName("meas-sep")
        line.setFixedHeight(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self.button, 0, Qt.AlignHCenter)
        lay.addWidget(self.state)
        lay.addSpacing(4)
        lay.addWidget(line)
        self.button.clicked.connect(self.toggleRequested)
        self.running = False
        ThemeManager.instance().changed.connect(self._theme_changed)
        self.set_running(False)

    def _theme_changed(self, _dark: bool = True) -> None:
        self.set_running(self.running, self.elapsed_text)

    elapsed_text = ""

    def set_running(self, on: bool, elapsed: str = "") -> None:
        """Stopped: the button offers ▶ Start (green). Running: it offers ■ Stop (red)."""
        self.running = on
        self.elapsed_text = elapsed
        color = token("ERROR") if on else token("SUCCESS")
        self.button.setIcon(make_icon("stop" if on else "play", color))
        self.button.setText("Stop" if on else "Start")
        self.button.setProperty("running", on)
        self.button.setToolTip("Stop the measurement (F9): nothing is sent, answered or logged" if on else
                               "Start the measurement (F9): send, answer auto response and log the trace")
        self.button.setAccessibleName("Stop measurement" if on else "Start measurement")
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)
        state_color = QColor(token("SUCCESS") if on else token("TEXT_MUTED")).name()
        self.state.setText(f"● {elapsed or '00:00:00'}" if on else "Stopped")
        self.state.setStyleSheet(f"color: {state_color}; font-weight: 600;")
        self.state.setToolTip("Measurement running (time since Start): frames are sent, answered and logged" if on
                              else "Measurement stopped: nothing is sent, answered or logged. Start: F9")


class Measurement:
    """Start / stop of the desktop app's measurement (engine gate + UI state)."""

    def __init__(self, win) -> None:
        self.win = win
        self.t0: float | None = None
        self.controls = MeasurementControls()
        self.controls.toggleRequested.connect(self.toggle)
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
