"""Live plot of decoded signals (pyqtgraph)."""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QToolButton, QWidget

from autosim.core.common import clock
from autosim.ui.styles.tokens import colors, series
from autosim.ui.icons import make_icon
from autosim.ui.theme import ThemeManager, accent, icon_color, ui_font


pg.setConfigOptions(antialias=True)


class PlotPanel(QWidget):
    """The plot fills the window. Its few controls (message, signal, add, remove, time window) are `controls`,
    which the main window puts in the window header: no toolbar row, no axis title, small margins."""

    def __init__(self, ctx, bridge, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.curves: dict[tuple[str, str], pg.PlotDataItem] = {}
        self.msg = QComboBox()
        self.msg.setPlaceholderText("Message")
        self.msg.setToolTip("Message")
        self.msg.setMinimumWidth(130)
        self.sig = QComboBox()
        self.sig.setPlaceholderText("Signal")
        self.sig.setToolTip("Signal")
        self.sig.setMinimumWidth(130)
        self.window = QDoubleSpinBox()
        self.window.setRange(1, 600)
        self.window.setDecimals(0)
        self.window.setValue(20)
        self.window.setSuffix(" s")
        self.window.setToolTip("Time window shown (seconds)")
        self.window.setFixedWidth(84)
        self.add_btn = self._tool("plus", "Plot the selected signal")
        self.remove_btn = self._tool("trash", "Remove the selected signal from the plot")
        self.controls = QWidget()
        self.controls.setObjectName("plot-controls")
        cl = QHBoxLayout(self.controls)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)
        for w in (self.msg, self.sig, self.add_btn, self.remove_btn, self.window):
            cl.addWidget(w)

        self.plot = pg.PlotWidget()
        self.plot.hideButtons()                          # no "A" auto-range button in the corner
        self.plot.getPlotItem().setContentsMargins(2, 4, 6, 2)
        from autosim.ui.theme import is_dark
        self.apply_theme(is_dark())                    # the main window re-applies on every theme switch
        self.plot.addLegend(labelTextSize=f"{ui_font().pixelSize()}px", offset=(8, 8))
        lay = QGridLayout(self)                          # the hint sits on top of the empty plot
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.plot, 0, 0)
        self.hint = QLabel("Pick a message and a signal above, then +")
        self.hint.setProperty("role", "muted")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.hint, 0, 0, Qt.AlignCenter)

        self.msg.currentTextChanged.connect(self._fill_signals)
        self.add_btn.clicked.connect(lambda: self.add_signal())
        self.remove_btn.clicked.connect(self.remove_signal)
        bridge.dbLoaded.connect(lambda _p: self._fill_messages())
        bridge.dbUnloaded.connect(lambda _p: self.databases_removed())
        ThemeManager.instance().changed.connect(self._refresh_icons)
        self._refresh_icons()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_curves)
        self.timer.start(50)

    @staticmethod
    def _tool(icon: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setObjectName("panel-btn")
        b.setProperty("icon_name", icon)
        b.setIconSize(QSize(16, 16))
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        return b

    def _refresh_icons(self, _dark: bool = True) -> None:
        for b in (self.add_btn, self.remove_btn):
            b.setIcon(make_icon(b.property("icon_name"), accent() if b is self.add_btn else icon_color()))

    def apply_theme(self, dark: bool) -> None:
        t = colors(dark)
        self._dark = dark
        self.plot.setBackground(t["SURFACE_1"])
        line, text = t["BORDER_HOVER"], t["TEXT_SECONDARY"]
        for axis in ("left", "bottom"):
            ax = self.plot.getAxis(axis)
            ax.setPen(line)             # also the grid colour (drawn with alpha below)
            ax.setTextPen(text)
            ax.setStyle(tickFont=ui_font())          # same family and size as the rest of the UI
            ax.label.setFont(ui_font())
        # grid = axis colour at reduced alpha: on the graphite surface-1 this lands close to the GRID token
        self.plot.showGrid(x=True, y=True, alpha=0.35 if dark else 0.25)
        pal = series(dark)
        for i, curve in enumerate(getattr(self, "curves", {}).values()):    # re-colour existing series
            curve.setPen(pg.mkPen(pal[i % len(pal)], width=2.2))

    def _fill_messages(self) -> None:
        self.msg.clear()
        self.msg.addItems(sorted(m.name for m in self.ctx.can.registry.messages()))

    def databases_removed(self) -> None:
        """Drop the curves of signals the engine stopped watching (their database was removed)."""
        watched = set(self.ctx.can.rx.watched())
        for key in [k for k in self.curves if k not in watched]:
            item = self.curves.pop(key)
            self.plot.removeItem(item)
            legend = self.plot.plotItem.legend
            if legend:
                legend.removeItem(item)
        self._fill_messages()

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
        pal = series(getattr(self, "_dark", True))
        color = pal[len(self.curves) % len(pal)]
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
        if self.hint.isHidden() != bool(self.curves):
            self.hint.setVisible(not self.curves)
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
