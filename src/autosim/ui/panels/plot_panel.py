"""Live plot of decoded signals (pyqtgraph)."""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QToolButton, QWidget

from autosim.core.common import clock
from autosim.ui.styles.tokens import colors, series
from autosim.ui.icons import make_icon
from autosim.ui.theme import ThemeManager, icon_color, ui_font


pg.setConfigOptions(antialias=True)


class PlotPanel(QWidget):
    """The plot fills the window. Its controls (`controls`, placed in the window header by the main window) are
    just "Add signals…" (the searchable signal selection dialog), Clear and the time window; right-click the plot
    to remove one signal."""

    def __init__(self, ctx, bridge, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.library = None                              # set by the main window (databases in use)
        self.curves: dict[tuple[str, str], pg.PlotDataItem] = {}
        self.window = QDoubleSpinBox()
        self.window.setRange(1, 600)
        self.window.setDecimals(0)
        self.window.setValue(20)
        self.window.setSuffix(" s")
        self.window.setToolTip("Time window shown (seconds)")
        self.window.setFixedWidth(84)
        self.add_btn = QPushButton("Add signals…")
        self.add_btn.setObjectName("panel-text-btn")
        self.add_btn.setToolTip("Pick the signals to plot (search by message, signal or ID)")
        self.remove_btn = self._tool("trash", "Remove every signal from the plot (right-click: one signal)")
        self.controls = QWidget()
        self.controls.setObjectName("plot-controls")
        cl = QHBoxLayout(self.controls)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)
        for w in (self.add_btn, self.remove_btn, self.window):
            cl.addWidget(w)

        self.plot = pg.PlotWidget()
        self.plot.hideButtons()                          # no "A" auto-range button in the corner
        self.plot.setMenuEnabled(False)                  # our own context menu: remove a signal
        self.plot.setContextMenuPolicy(Qt.CustomContextMenu)
        self.plot.customContextMenuRequested.connect(self._plot_menu)
        self.plot.getPlotItem().setContentsMargins(2, 4, 6, 2)
        from autosim.ui.theme import is_dark
        self.apply_theme(is_dark())                    # the main window re-applies on every theme switch
        self.plot.addLegend(labelTextSize=f"{ui_font().pixelSize()}px", offset=(8, 8))
        lay = QGridLayout(self)                          # the hint sits on top of the empty plot
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.plot, 0, 0)
        self.hint = QLabel("Add signals to plot them")
        self.hint.setProperty("role", "muted")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.hint, 0, 0, Qt.AlignCenter)

        self.add_btn.clicked.connect(lambda: self.add_signals_dialog())
        self.remove_btn.clicked.connect(self.clear_signals)
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
        self.remove_btn.setIcon(make_icon("trash", icon_color()))

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

    def databases_removed(self) -> None:
        """Drop the curves of signals the engine stopped watching (their database was removed)."""
        watched = set(self.ctx.can.rx.watched())
        for key in [k for k in self.curves if k not in watched]:
            self._drop(key, unwatch=False)

    def add_signals_dialog(self) -> list[tuple[str, str]]:
        from autosim.ui.panels.signal_select import SignalSelectDialog
        if self.library is None or not self.library.active():
            return []
        dlg = SignalSelectDialog(self.library, set(self.curves), QWidget.window(self), "Plot signals", mode="signals")
        if dlg.exec() != SignalSelectDialog.Accepted:
            return []
        picked = dlg.selected_signals()
        for msg, sig in picked:
            self.add_signal(msg, sig)
        return picked

    def add_signal(self, message: str, signal: str) -> None:
        key = (message, signal)
        if not all(key) or key in self.curves:
            return
        self.ctx.can.rx.watch(*key)
        pal = series(getattr(self, "_dark", True))
        color = pal[len(self.curves) % len(pal)]
        self.curves[key] = self.plot.plot(name=f"{key[0]}.{key[1]}", pen=pg.mkPen(color, width=2.2), antialias=True)

    def remove_signal(self, message: str, signal: str) -> None:
        self._drop((message, signal))

    def clear_signals(self) -> None:
        for key in list(self.curves):
            self._drop(key)

    def _drop(self, key, unwatch: bool = True) -> None:
        item = self.curves.pop(key, None)
        if item is None:
            return
        self.plot.removeItem(item)
        if unwatch:
            self.ctx.can.rx.unwatch(*key)
        legend = self.plot.plotItem.legend
        if legend:
            legend.removeItem(item)

    def _plot_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction("Add signals…", lambda: self.add_signals_dialog())
        if self.curves:
            sub = menu.addMenu("Remove")
            for key in self.curves:
                sub.addAction(f"{key[0]}.{key[1]}", lambda k=key: self._drop(k))
            menu.addAction("Remove all", self.clear_signals)
        menu.exec(self.plot.mapToGlobal(pos))

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
