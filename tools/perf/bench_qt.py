"""Benchmark of the PySide6 desktop app: database load and a heavy bus trace, without hardware (SIM1).

    QT_QPA_PLATFORM=offscreen python tools/perf/bench_qt.py --dbc big.dbc --messages 50 --seconds 10

1. Database load: time until `Add database` returns (the UI is frozen meanwhile) and until the event loop is free again.
2. Trace: `--messages` messages of the database sent every `--cycle` ms on SIM1 (each frame gives a Tx and an Rx
   row); per trace mode (fixed, chronological):
     cpu_pct      process CPU, % of one core
     views_fps    repaints of the visible tables / trees / trace per second
     lag_ms       event-loop latency (a 10 ms timer: how late it fires) - mean / p95 / max: input responsiveness
     rows_per_s   trace rows delivered per second
Prints one JSON line.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbc", required=True)
    ap.add_argument("--messages", type=int, default=50)
    ap.add_argument("--cycle", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=10.0)
    a = ap.parse_args()

    import psutil
    from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QSettings, QTimer
    from PySide6.QtWidgets import QApplication
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp(prefix="autosim-bench-"))

    from autosim.context import AppContext
    from autosim.ui.main_window import MainWindow
    from autosim.ui.theme import apply_theme

    app = QApplication(sys.argv)
    apply_theme(app, "light")
    ctx = AppContext()
    ctx.start()
    win = MainWindow(ctx)
    win.resize(1600, 1000)
    win.show()
    proc = psutil.Process()

    class PaintCounter(QObject):
        n = 0

        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Paint:
                self.n += 1
            return False

    painter = PaintCounter()
    from PySide6.QtWidgets import QAbstractScrollArea
    for v in win.findChildren(QAbstractScrollArea):        # every table / tree / trace view: repaints per second
        v.viewport().installEventFilter(painter)

    lags: list[float] = []
    clock = QElapsedTimer()
    clock.start()
    probe = QTimer()
    probe.setInterval(10)
    last = [0]

    def on_probe() -> None:
        now = clock.elapsed()
        if last[0]:
            lags.append(max(0.0, now - last[0] - 10))
        last[0] = now
    probe.timeout.connect(on_probe)

    def lag_stats() -> dict:
        s = sorted(lags) or [0.0]
        return {"mean": round(statistics.fmean(s), 2), "p95": round(s[max(0, int(len(s) * 0.95) - 1)], 1), "max": round(s[-1], 1)}

    res: dict = {"ui": "qt", "messages": a.messages, "cycle_ms": a.cycle}
    phases = [("Analysis", "fixed"), ("Analysis", "chronological"), ("Simulation", "fixed")]

    def load_db() -> None:
        probe.start()
        lags.clear()
        t0 = time.perf_counter()
        ok = win.dbc.load_file(a.dbc)
        t1 = time.perf_counter()

        def idle() -> None:
            res["db_load"] = {"ok": ok, "frozen_ms": round((t1 - t0) * 1000), "until_idle_ms": round((time.perf_counter() - t0) * 1000),
                              "messages": len(ctx.can.registry.messages())}
            QTimer.singleShot(1500, start_traffic)
        QTimer.singleShot(0, idle)

    def start_traffic() -> None:
        res["db_load"]["lag_after_ms"] = lag_stats()
        c = win.can_conn
        c.port.setCurrentText("SIM1")
        c.channel.setText("CAN1")
        if not c.connect_node(interactive=False):
            return fail("cannot open SIM1")
        msgs = ctx.can.registry.messages()[: a.messages]
        t0 = time.perf_counter()
        cards = win.signals_panel.add_messages([(m.name, "CAN1") for m in msgs])
        res["add_messages_ms"] = round((time.perf_counter() - t0) * 1000)
        for card in cards:
            if card is None:
                continue
            card.cycle.setValue(a.cycle)
            card.enable.setChecked(True)
        win.measurement.start(check=False)
        QTimer.singleShot(2000, lambda: run_phase(0))

    def run_phase(i: int) -> None:
        if i >= len(phases):
            return finish()
        desk, mode = phases[i]
        win.show_desktop(desk)
        if hasattr(win.trace, "fixed"):
            win.trace.fixed.setChecked(mode == "fixed")
        QTimer.singleShot(800, lambda: measure(i, f"{desk.lower()}_{mode}"))

    def measure(i: int, mode: str) -> None:
        painter.n = 0
        lags.clear()
        proc.cpu_percent(None)
        rows0, t0 = ctx.can.rx.rx_count, time.perf_counter()

        def stop() -> None:
            dt = time.perf_counter() - t0
            res[mode] = {"cpu_pct": round(proc.cpu_percent(None), 1), "views_fps": round(painter.n / dt, 1),
                         "lag_ms": lag_stats(), "rows_per_s": round((ctx.can.rx.rx_count - rows0) / dt)}
            run_phase(i + 1)
        QTimer.singleShot(int(a.seconds * 1000), stop)

    def finish() -> None:
        win.signals_panel.stop_all()
        win.close()
        app.exit(0)

    def fail(msg: str) -> None:
        res["error"] = msg
        win.close()
        app.exit(0)

    QTimer.singleShot(500, load_db)
    app.exec()
    print(json.dumps(res))
    return 1 if "error" in res else 0


if __name__ == "__main__":
    sys.exit(main())
