"""UI performance probe on real CANable adapters: CPU load and UI frame rate while the bus trace is fed.

    uv run python tools/ui/perf_trace.py --messages 1 --seconds 15          # 1 message per channel, 10 ms
    uv run python tools/ui/perf_trace.py --messages 10 --seconds 15         # stress: 10 per channel

Needs two CANables on one bus (Connect all -> CAN1, CAN2, 500k / 2M FD+BRS). Each channel sends `--messages`
32-byte FD messages of the Hyundai CAN FD DBC every `--cycle` ms, so the trace receives
2 * messages * 1000/cycle Tx rows plus as many Rx rows per second.

Measured per trace mode (fixed, chronological), after a 2 s warm-up:
  cpu_pct        process CPU, % of one core (psutil; 100 = one core fully busy)
  trace_fps      repaints of the visible trace viewport per second (the UI frame rate of the trace)
  lag_ms         event-loop latency: how late a 10 ms QTimer fires (mean / p95 / max) - input responsiveness
  rows_per_s     trace rows delivered by the engine per second
Settings go to a throw-away INI folder (the user's layout / theme are not touched). Prints one JSON line.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--messages", type=int, default=1)
    ap.add_argument("--cycle", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--theme", default="dark")
    a = ap.parse_args()
    import faulthandler
    faulthandler.dump_traceback_later(2 * a.seconds + 60, exit=True)    # a hang prints every thread's stack

    import psutil
    from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QSettings, QTimer
    from PySide6.QtWidgets import QApplication
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp(prefix="autosim-perf-"))

    from autosim.context import AppContext
    from autosim.ui.main_window import MainWindow
    from autosim.ui.theme import apply_theme

    app = QApplication(sys.argv)
    dark = apply_theme(app, a.theme)
    ctx = AppContext()
    ctx.start()
    win = MainWindow(ctx)
    win.plot.apply_theme(dark)
    win.resize(1600, 1000)
    win.show()
    proc = psutil.Process()

    class PaintCounter(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.n = 0

        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Paint:
                self.n += 1
            return False

    painter = PaintCounter()
    views = [w for w in (getattr(win.trace, "tree", None), win.trace.view) if w is not None]
    for v in views:
        v.viewport().installEventFilter(painter)

    lags: list[float] = []
    clock = QElapsedTimer()
    probe = QTimer()
    probe.setInterval(10)
    last = [0]

    def on_probe() -> None:
        now = clock.elapsed()
        if last[0]:
            lags.append(max(0.0, now - last[0] - 10))
        last[0] = now
    probe.timeout.connect(on_probe)

    results: dict = {"messages_per_channel": a.messages, "cycle_ms": a.cycle, "cores": psutil.cpu_count()}
    phases = ["fixed", "chronological"] if getattr(win.trace, "fixed", None) is not None else ["chronological"]

    def setup() -> None:
        print("setup", file=sys.stderr, flush=True)
        if not win.dbc.load_file(str(ROOT / "tests" / "data" / "real_dbc" / "hyundai_canfd.dbc")):
            fail("DBC load failed")
        c = win.can_conn
        c.nominal.setCurrentIndex(c.nominal.findData(500_000))
        c.data.setCurrentIndex(c.data.findData(2_000_000))
        opened = c.connect_all(interactive=False)
        if len(opened) < 2:
            fail(f"needs two CANables, opened {opened}")
        msgs = [m for m in ctx.can.registry.messages() if m.length == 32 and m.signals]
        for i in range(a.messages * 2):
            card = win.signals_panel.add_message(msgs[i].name, opened[i % 2])
            card.fd.setChecked(True)
            card.brs.setChecked(True)
            card.cycle.setValue(a.cycle)
            card.enable.setChecked(True)
        clock.start()
        probe.start()
        QTimer.singleShot(2000, lambda: run_phase(0))

    def run_phase(i: int) -> None:
        if i >= len(phases):
            return finish()
        mode = phases[i]
        print("phase", mode, file=sys.stderr, flush=True)
        if hasattr(win.trace, "fixed"):
            win.trace.fixed.setChecked(mode == "fixed")
        QTimer.singleShot(500, lambda: start_measure(i, mode))

    def start_measure(i: int, mode: str) -> None:
        painter.n = 0
        lags.clear()
        proc.cpu_percent(None)
        rows0, t0 = ctx.can.rx.rx_count, time.perf_counter()

        def stop() -> None:
            dt = time.perf_counter() - t0
            cpu = proc.cpu_percent(None)
            s = sorted(lags) or [0.0]
            results[mode] = {
                "cpu_pct": round(cpu, 1),
                "trace_fps": round(painter.n / dt, 1),
                "lag_ms": {"mean": round(statistics.fmean(s), 2), "p95": round(s[int(len(s) * 0.95) - 1], 1),
                           "max": round(s[-1], 1)},
                "rows_per_s": round((ctx.can.rx.rx_count - rows0) / dt),
            }
            run_phase(i + 1)
        QTimer.singleShot(int(a.seconds * 1000), stop)

    def finish() -> None:
        print("finish", file=sys.stderr, flush=True)
        win.signals_panel.stop_all()
        st = {ch: {"rx": n.status.rx_count, "tx": n.status.tx_count, "errors": n.status.err_count, "dropped": n.status.dropped}
              for ch, n in ctx.nodes.nodes.items()}
        results["nodes"] = st
        win.close()
        app.exit(0)

    def fail(msg: str) -> None:
        results["error"] = msg
        win.close()
        app.exit(0)

    QTimer.singleShot(300, setup)
    app.exec()
    print(json.dumps(results))
    return 1 if "error" in results else 0


if __name__ == "__main__":
    sys.exit(main())
