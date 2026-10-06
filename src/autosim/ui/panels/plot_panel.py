"""Live plot of decoded signals (pyqtgraph)."""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLabel, QPushButton, QToolBar, QVBoxLayout, QWidget

from autosim.core.common import clock

COLORS = ["#38BDF8", "#34D399", "#FBBF24", "#F87171", "#A78BFA", "#2DD4BF", "#FB923C", "#818CF8"]

pg.setConfigOptions(antialias=True)


class PlotPanel(QWidget):
    def __init__(self, ctx, bridge, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.curves: dict[tuple[str, str], pg.PlotDataItem] = {}
        self.msg = QComboBox()
        self.sig = QComboBox()
        self.window = QDoubleSpinBox()
        self.window.setRange(1, 600)
        self.window.setValue(20)
        self.window.setSuffix(" s")
        add, rem = QPushButton("Add signal"), QPushButton("Remove")
        from autosim.ui.widgets import set_kind
        set_kind(add, "primary")
        self.toolbar = QToolBar()            # collapses into an overflow menu when the dock is narrow
        self.toolbar.setMovable(False)
        for w in (QLabel("Message"), self.msg, QLabel("Signal"), self.sig, add, rem, QLabel("Window"), self.window):
            self.toolbar.addWidget(w)

        self.plot = pg.PlotWidget()
        self.apply_theme(True)
        self.plot.addLegend()
        self.plot.setLabel("bottom", "time", "s")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.plot)

        self.msg.currentTextChanged.connect(self._fill_signals)
        add.clicked.connect(self.add_signal)
        rem.clicked.connect(self.remove_signal)
        bridge.dbLoaded.connect(lambda _p: self._fill_messages())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_curves)
        self.timer.start(50)

    def apply_theme(self, dark: bool) -> None:
        self.plot.setBackground("#0B0C0E" if dark else "#ffffff")
        line, text = ("#2E3036", "#8F92A1") if dark else ("#9aa0b4", "#2b2b2b")
        for axis in ("left", "bottom"):
            ax = self.plot.getAxis(axis)
            ax.setPen(line)             # also the grid colour (drawn with alpha below)
            ax.setTextPen(text)
        self.plot.showGrid(x=True, y=True, alpha=0.5 if dark else 0.25)

    def _fill_messages(self) -> None:
        self.msg.clear()
        self.msg.addItems(sorted(m.name for m in self.ctx.can.registry.messages()))

    def _fill_signals(self, name: str) -> None:
        self.sig.clear()
        m = self.ctx.can.registry.message_by_name(name)
        if m:
            self.sig.addItems([s.name for s in m.signals])

    def add_signal(self, message: str | None = None, signal: str | None = None) -> None:
        key = (message or self.msg.currentText(), signal or self.sig.currentText())
        if not all(key) or key in self.curves:
            return
        self.ctx.can.rx.watch(*key)
        color = COLORS[len(self.curves) % len(COLORS)]
        self.curves[key] = self.plot.plot(name=f"{key[0]}.{key[1]}", pen=pg.mkPen(color, width=2.2), antialias=True)

    def remove_signal(self) -> None:
        key = (self.msg.currentText(), self.sig.currentText())
        item = self.curves.pop(key, None)
        if item is not None:
            self.plot.removeItem(item)
            self.ctx.can.rx.unwatch(*key)
            legend = self.plot.plotItem.legend
            if legend:
                legend.removeItem(item)

    def update_curves(self) -> None:
        if not self.curves or not self.isVisible():
            return
        now, win = clock.now(), self.window.value()
        for key, curve in self.curves.items():
            data = self.ctx.can.rx.snapshot(*key)
            pts = [(t - now, v) for t, v in data if t >= now - win]
            if pts:
                xs, ys = zip(*pts)
                curve.setData(xs, ys)
        self.plot.setXRange(-win, 0, padding=0)
