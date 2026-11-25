"""Bottom status bar: per-channel bus load + frames/s | error frames | trace size | recording | elapsed.
(The Log toggle it creates is placed on the left rail.)

Updated once per second by the main window (cheap: a handful of label texts)."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QStatusBar, QToolButton, QWidget

from autosim.ui.theme import data_font


def _seg(text: str = "", tip: str = "", mono: bool = False) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("status-seg")
    lb.setToolTip(tip)
    if mono:
        lb.setFont(data_font())
    return lb


def fmt_elapsed(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class AppStatusBar(QStatusBar):
    def __init__(self) -> None:
        super().__init__()
        self.setSizeGripEnabled(False)
        self.log_btn = QToolButton()                     # shown on the left rail (MainWindow), not in this bar
        self.log_btn.setText("Log")
        self.log_btn.setCheckable(True)
        self.log_btn.setChecked(True)

        self.channels_box = QWidget()
        self.channels_lay = QHBoxLayout(self.channels_box)
        self.channels_lay.setContentsMargins(0, 0, 0, 0)
        self.channels_lay.setSpacing(16)
        self.channel_labels: dict[str, QLabel] = {}         # "CAN1 2.7% est. · 213 fr/s"
        self.channel_dots: dict[str, QLabel] = {}           # coloured state dot before each channel
        self.channel_boxes: dict[str, QWidget] = {}
        self.idle = _seg("No channel open", "Connect an adapter: Setup desktop, Channels window (Ctrl+1)")
        self.channels_lay.addWidget(self.idle)
        self.errors = _seg("", "Error frames / error counter reported by the adapters", mono=True)
        self.trace = _seg("", "Messages in the bus trace", mono=True)
        self.rec = _seg("", "Bus trace capture state (Space pauses / resumes)")
        self.elapsed = _seg("00:00:00", "Measurement time (since Start)", mono=True)
        for w in (self.channels_box, self.errors, self.trace, self.rec, self.elapsed):
            self.addPermanentWidget(w)
        self.set_errors(0)
        self.set_recording(True)

    STATE_TOKENS = {"running": "SUCCESS", "hello": "WARNING", "configured": "WARNING", "error": "ERROR"}

    def set_channels(self, loads: dict[str, tuple[float, float]], open_channels: list[str],
                     states: dict[str, dict] | None = None) -> None:
        """loads: {channel: (bus load %, frames/s)} for the last second; states: {channel: node state report}.
        Each channel shows a coloured dot (green running, amber connecting, red error) before its load."""
        states = states or {}
        for ch in list(self.channel_labels):
            if ch not in open_channels:
                self.channel_labels.pop(ch)
                self.channel_dots.pop(ch)
                self.channel_boxes.pop(ch).deleteLater()
        for ch in open_channels:
            lb = self.channel_labels.get(ch)
            if lb is None:
                box = QWidget()
                bl = QHBoxLayout(box)
                bl.setContentsMargins(0, 0, 0, 0)
                bl.setSpacing(5)
                dot = QLabel("●")
                dot.setObjectName("status-dot")
                lb = _seg("", "", mono=True)
                bl.addWidget(dot)
                bl.addWidget(lb)
                self.channel_boxes[ch], self.channel_dots[ch], self.channel_labels[ch] = box, dot, lb
                self.channels_lay.addWidget(box)
            self._paint_state(ch, states.get(ch, {}))
            load, fps = loads.get(ch, (0.0, 0.0))
            lb.setText(f"{ch} {load:.1f}% est. · {fps:.0f} fr/s")
        self.idle.setVisible(not open_channels)

    def set_states(self, states: dict[str, dict]) -> None:
        """Recolour the dots right away when a node reports (the loads update once per second)."""
        for ch in self.channel_dots:
            self._paint_state(ch, states.get(ch, {}))

    def _paint_state(self, ch: str, st: dict) -> None:
        from autosim.ui.theme import token
        state = st.get("state") or "running"                     # an open channel without a report: up
        shown = "connecting" if state in ("hello", "configured") else state
        dot = self.channel_dots[ch]
        dot.setProperty("state", shown)
        dot.setStyleSheet(f"color: {token(self.STATE_TOKENS.get(state, 'TEXT_MUTED'))};")
        tip = f"{ch}: {shown}" + (f" on {st['port']}" if st.get("port") else "") + \
            " - estimated bus load (from frame lengths and bit rates) and frames per second"
        for w in (self.channel_boxes[ch], dot, self.channel_labels[ch]):
            w.setToolTip(tip)

    def set_errors(self, n: int) -> None:
        self.errors.setText(f"Err {n}")
        self.errors.setProperty("tone", "warning" if n else "")
        self.errors.style().unpolish(self.errors)
        self.errors.style().polish(self.errors)

    def set_trace(self, messages: int, frames: int) -> None:
        self.trace.setText(f"{messages} msg · {frames} fr")

    def set_recording(self, on: bool) -> None:
        self.rec.setText("● Recording" if on else "❚❚ Paused")
        self.rec.setToolTip("Bus trace capture state (Space pauses / resumes)")
        self.rec.setProperty("tone", "success" if on else "warning")
        self.rec.style().unpolish(self.rec)
        self.rec.style().polish(self.rec)

    def set_stopped(self) -> None:
        """Measurement stopped: nothing is logged (Start: F9)."""
        self.rec.setText("■ Stopped")
        self.rec.setToolTip("Measurement stopped: nothing is sent, answered or logged - Start (F9) on the menu bar")
        self.rec.setProperty("tone", "muted")
        self.rec.style().unpolish(self.rec)
        self.rec.style().polish(self.rec)

    def set_elapsed(self, seconds: float) -> None:
        self.elapsed.setText(fmt_elapsed(seconds))
