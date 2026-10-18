"""Bottom status bar: log toggle | per-channel bus load + frames/s | error frames | trace size | recording | elapsed.

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
        self.log_btn = QToolButton()
        self.log_btn.setObjectName("status-btn")
        self.log_btn.setText("Log")
        self.log_btn.setCheckable(True)
        self.log_btn.setChecked(True)
        self.addWidget(self.log_btn)

        self.channels_box = QWidget()
        self.channels_lay = QHBoxLayout(self.channels_box)
        self.channels_lay.setContentsMargins(0, 0, 0, 0)
        self.channels_lay.setSpacing(16)
        self.channel_labels: dict[str, QLabel] = {}
        self.idle = _seg("No channel open", "Connect an adapter in the sidebar (Channels)")
        self.channels_lay.addWidget(self.idle)
        self.errors = _seg("", "Error frames / error counter reported by the adapters", mono=True)
        self.trace = _seg("", "Messages in the bus trace", mono=True)
        self.rec = _seg("", "Bus trace capture state (Space pauses / resumes)")
        self.elapsed = _seg("00:00:00", "Time since the trace started (Clear resets it)", mono=True)
        for w in (self.channels_box, self.errors, self.trace, self.rec, self.elapsed):
            self.addPermanentWidget(w)
        self.set_errors(0)
        self.set_recording(True)

    def set_channels(self, loads: dict[str, tuple[float, float]], open_channels: list[str]) -> None:
        """loads: {channel: (bus load %, frames/s)} for the last second."""
        for ch in list(self.channel_labels):
            if ch not in open_channels:
                self.channel_labels.pop(ch).deleteLater()
        for ch in open_channels:
            lb = self.channel_labels.get(ch)
            if lb is None:
                lb = self.channel_labels[ch] = _seg("", f"{ch}: estimated bus load (from frame lengths and bit "
                                                        "rates) and frames per second", mono=True)
                self.channels_lay.addWidget(lb)
            load, fps = loads.get(ch, (0.0, 0.0))
            lb.setText(f"{ch} {load:.1f}% est. · {fps:.0f} fr/s")
        self.idle.setVisible(not open_channels)

    def set_errors(self, n: int) -> None:
        self.errors.setText(f"Err {n}")
        self.errors.setProperty("tone", "warning" if n else "")
        self.errors.style().unpolish(self.errors)
        self.errors.style().polish(self.errors)

    def set_trace(self, messages: int, frames: int) -> None:
        self.trace.setText(f"{messages} msg · {frames} fr")

    def set_recording(self, on: bool) -> None:
        self.rec.setText("● Recording" if on else "❚❚ Paused")
        self.rec.setProperty("tone", "success" if on else "warning")
        self.rec.style().unpolish(self.rec)
        self.rec.style().polish(self.rec)

    def set_elapsed(self, seconds: float) -> None:
        self.elapsed.setText(fmt_elapsed(seconds))
