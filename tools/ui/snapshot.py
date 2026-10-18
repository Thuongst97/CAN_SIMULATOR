"""Screenshot the main window for UI reviews (no hardware: simulated nodes, real Hyundai CAN FD DBC).

    uv run python tools/ui/snapshot.py --theme dark --scale 1.25 --out docs/ui_review/step1 --tag after

Writes <tag>_<theme>_<scale>_can.png (+ close-ups of the panels) at a window of 1366x768 logical pixels (the minimum
target size); --scale emulates Windows display scaling (QT_SCALE_FACTOR), so 1.5 gives a 2049x1152 image.
Settings go to a throw-away INI folder: the user's saved layout / theme is neither used nor modified.
Prints a JSON line with the window's minimum size hint so clipping at 1366x768 can be spotted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default="dark", choices=("dark", "light"))
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--out", default="docs/ui_review")
    ap.add_argument("--tag", default="shot")
    ap.add_argument("--size", default="1366x768")
    a = ap.parse_args()
    os.environ["QT_SCALE_FACTOR"] = f"{a.scale:g}"
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication
    tmp = tempfile.mkdtemp(prefix="autosim-snap-")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmp)

    from autosim.context import AppContext
    from autosim.hardware.esp32_gateway.manager import NodeManager
    from autosim.ui.main_window import MainWindow
    from autosim.ui.theme import apply_theme
    NodeManager.scan = lambda self: []                    # screenshots must not depend on plugged-in hardware

    app = QApplication(sys.argv)
    app.setApplicationName("AutoSim Studio")
    dark = apply_theme(app, a.theme)
    ctx = AppContext()
    ctx.start()
    win = MainWindow(ctx)
    win.plot.apply_theme(dark)
    w, h = (int(v) for v in a.size.split("x"))
    win.resize(w, h)
    win.show()

    out = (ROOT / a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{a.tag}_{a.theme}_{int(round(a.scale * 100))}"
    result = {}

    def populate() -> None:
        win.dbc.load_file(str(ROOT / "tests" / "data" / "real_dbc" / "hyundai_canfd.dbc"))
        c = win.can_conn
        c.port.setCurrentText("SIM1")
        c.channel.setText("CAN1")
        c.connect_node(interactive=False)
        c.port.setCurrentText("SIM2")
        c.channel.setText("CAN2")
        c.connect_node(interactive=False)
        msgs = [m for m in ctx.can.registry.messages() if m.length == 32 and len(m.signals) > 6][:3]
        for i, m in enumerate(msgs):
            card = win.signals_panel.add_message(m.name, "CAN1" if i % 2 == 0 else "CAN2")
            card.fd.setChecked(True)
            card.brs.setChecked(True)
            card.cycle.setValue(10)
            card.enable.setChecked(True)
        if msgs:
            win.plot.msg.setCurrentText(msgs[0].name)
            win.plot.add_signal(msgs[0].name, msgs[0].signals[2].name)
        ctx.events.publish("log", "Database warning: example warning line for the review")
        QTimer.singleShot(1500, shoot)

    def shoot() -> None:
        if win.trace.fixed_model.rowCount():
            win.trace.tree.expand(win.trace.fixed_model.index(0, 0))
        app.processEvents()
        win.grab().save(str(out / f"{stem}_can.png"))
        for name, widget in (("signals", win.signals_card), ("trace", win.trace_card), ("plot", win.plot_card),
                             ("appbar", win.app_bar.widget)):
            if widget.isVisible():                         # close-ups for reviews
                widget.grab().save(str(out / f"{stem}_{name}.png"))
        hint = win.minimumSizeHint()
        result.update(file=str(out / stem), theme=a.theme, scale=a.scale, window=[win.width(), win.height()],
                      min_hint=[hint.width(), hint.height()], fits=hint.width() <= w and hint.height() <= h,
                      ui_font=app.font().family(), ui_font_px=app.font().pixelSize())
        win.signals_panel.stop_all()
        win.close()
        app.quit()

    QTimer.singleShot(400, populate)
    app.exec()
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
