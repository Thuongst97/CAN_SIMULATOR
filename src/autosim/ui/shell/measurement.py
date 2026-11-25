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
STOP_ON_FAILURE_KEY = "measurement/stop_on_adapter_failure"


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

    problems: tuple[str, ...] = ()

    def set_problems(self, problems: list[str]) -> None:
        """Adapter failures while running: the state turns red with ⚠ (details in its tooltip)."""
        self.problems = tuple(problems)
        self.set_running(self.running, self.elapsed_text)

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
        alert = on and bool(self.problems)
        state_color = QColor(token("ERROR") if alert else token("SUCCESS") if on else token("TEXT_MUTED")).name()
        self.state.setText(f"{'⚠' if alert else '●'} {elapsed or '00:00:00'}" if on else "Stopped")
        self.state.setStyleSheet(f"color: {state_color}; font-weight: 600;")
        if alert:
            self.state.setToolTip("Measurement running with problems:\n" + "\n".join(f"• {p}" for p in self.problems))
        else:
            self.state.setToolTip("Measurement running (time since Start): frames are sent, answered and logged" if on
                                  else "Measurement stopped: nothing is sent, answered or logged. Start: F9")


class Measurement:
    """Start / stop of the desktop app's measurement (engine gate + UI state).

    Start from the button / F9 / menu checks first: no channel connected (Connect all / Start anyway / Cancel), an
    adapter in error, a cyclic message whose channel is not open (Start anyway / Cancel). While running, an adapter
    failure (a message that cannot be sent, an adapter reporting an error) shows a toast and turns the state red;
    optionally it stops the measurement."""

    def __init__(self, win) -> None:
        self.win = win
        self.t0: float | None = None
        self.problems: list[str] = []
        self.controls = MeasurementControls()
        self.controls.toggleRequested.connect(self.toggle)
        win.ctx.can.set_measuring(False)                    # the desktop app starts stopped, like CANoe
        win.signals_panel.set_measuring(False)
        win.bridge.txError.connect(self._tx_failed)
        win.bridge.nodeState.connect(self._node_state)

    @property
    def running(self) -> bool:
        return self.win.ctx.can.measuring

    @staticmethod
    def _flag(key: str, default: bool) -> bool:
        return str(app_settings().value(key, "true" if default else "false")).lower() in ("true", "1")

    @staticmethod
    def _set_flag(key: str, on: bool) -> None:
        app_settings().setValue(key, "true" if on else "false")

    @classmethod
    def clear_on_start(cls) -> bool:
        return cls._flag(CLEAR_KEY, True)

    @classmethod
    def set_clear_on_start(cls, on: bool) -> None:
        cls._set_flag(CLEAR_KEY, on)

    @classmethod
    def stop_on_failure(cls) -> bool:
        return cls._flag(STOP_ON_FAILURE_KEY, False)

    @classmethod
    def set_stop_on_failure(cls, on: bool) -> None:
        cls._set_flag(STOP_ON_FAILURE_KEY, on)

    # ---- start / stop ----------------------------------------------------------------------------------------
    def ask(self, title: str, text: str, buttons: list[str], default: str) -> str | None:
        """Modal question with named buttons; returns the label clicked (None: closed). Tests replace it."""
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(QMessageBox.Warning, title, text, parent=self.win)
        made = {}
        for label in buttons:
            role = QMessageBox.RejectRole if label == "Cancel" else QMessageBox.AcceptRole
            made[label] = box.addButton(label, role)
        box.setDefaultButton(made[default])
        box.exec()
        clicked = box.clickedButton()
        return next((label for label, b in made.items() if b is clicked), None)

    def preflight(self) -> bool:
        """Checks before a Start asked for by the user; False: do not start."""
        win = self.win
        if not win.ctx.can.bus.channels:
            choice = self.ask("Start measurement",
                              "No CAN channel is connected: nothing can be sent or received.\n\n"
                              "Connect all adapters now, or start anyway (connect later - messages start then).",
                              ["Connect all", "Start anyway", "Cancel"], "Connect all")
            if choice == "Connect all":
                win.can_conn.connect_all()
                if not win.ctx.can.bus.channels:
                    self.notify("No adapter could be connected - the measurement was not started. "
                                "Connect one in Setup > Channels (SIM1 / SIM2 work without hardware).", error=True)
                    return False
                return True
            return choice == "Start anyway"
        problems = []
        chans = set(win.ctx.can.bus.channels)
        for ch in sorted(chans):
            if (win._node_states.get(ch) or {}).get("state") == "error":
                problems.append(f"{ch}: the adapter reports an error")
        for c in win.signals_panel.cards:
            if c.enable.isChecked() and c.job.channel not in chans:
                problems.append(f"{c.message.name}: channel {c.job.channel or '(none)'} is not open")
        if problems:
            choice = self.ask("Start measurement", "Problems found:\n\n" + "\n".join(f"• {p}" for p in problems)
                              + "\n\nStart anyway?", ["Start anyway", "Cancel"], "Cancel")
            return choice == "Start anyway"
        return True

    def start(self, check: bool = False) -> bool:
        """Run the measurement. `check`: a user Start (button, F9, menu) - ask first when something is missing."""
        if self.running:
            return True
        if check and not self.preflight():
            return False
        if self.clear_on_start():
            self.win.trace.clear()
            self.win._trace_cleared()
        self.problems = []
        self.t0 = time.monotonic()
        self.win.ctx.can.set_measuring(True)
        self._changed()
        self.win.ctx.events.publish("log", "Measurement started")
        return True

    def stop(self) -> None:
        if not self.running:
            return
        self.win.ctx.can.set_measuring(False)
        self.problems = []
        self._changed()
        self.win.ctx.events.publish("log", "Measurement stopped")

    def toggle(self) -> None:
        if self.running:
            self.stop()
        else:
            self.start(check=True)

    def elapsed(self) -> float:
        return 0.0 if self.t0 is None or not self.running else time.monotonic() - self.t0

    def _changed(self) -> None:
        from autosim.ui.shell.status_bar import fmt_elapsed
        on = self.running
        self.controls.problems = tuple(self.problems)
        self.controls.set_running(on, fmt_elapsed(self.elapsed()) if on else "")
        self.win.signals_panel.set_measuring(on)
        self.win._update_status()

    def tick(self) -> None:
        """Once per second (main window timer): the running time under the button."""
        if self.running:
            from autosim.ui.shell.status_bar import fmt_elapsed
            self.controls.set_running(True, fmt_elapsed(self.elapsed()))

    # ---- failures while running ------------------------------------------------------------------------------
    def notify(self, text: str, error: bool = False) -> None:
        self.win.toast(text, error=error)

    def _tx_failed(self, info: dict) -> None:
        if self.running:
            self.failure(f"{info.get('channel') or '(no channel)'}: {info.get('message')} could not be sent "
                         f"({info.get('error')}) - its cyclic sending stopped; it resumes when the channel is back")

    def _node_state(self, st: dict) -> None:
        if self.running and st.get("state") == "error":
            self.failure(f"{st.get('channel')}: the adapter reports an error")

    def failure(self, text: str) -> None:
        """An adapter problem during the measurement: toast, red state; stop if so configured."""
        if text in self.problems:
            return
        self.problems.append(text)
        self.controls.set_problems(self.problems)
        if self.stop_on_failure():
            self.stop()
            self.notify(f"{text}\nThe measurement was stopped (Simulation > Stop measurement when an adapter "
                        f"fails).", error=True)
        else:
            self.notify(text, error=True)
