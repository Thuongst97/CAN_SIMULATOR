# ruff: noqa: F811  (the imported `win` fixture is requested by name in the test arguments)
"""Desktop measurement Start / Stop (CANoe style): the app starts stopped; only a running measurement sends,
answers and logs the trace."""
from pathlib import Path

from tests.test_gui import win  # noqa: F401  (the `win` fixture, used as a test argument)

DATA = Path(__file__).parent / "data"


def connect_and_add(win, qtbot):
    c = win.can_conn
    c.port.setCurrentText("SIM1")
    assert c.connect_node(interactive=False)
    assert win.dbc.load_file(str(DATA / "sample.dbc"))
    card = win.signals_panel.add_message("EngineData")
    card.cycle.setValue(10)
    return card


def test_app_starts_stopped_and_shows_it(win):
    m = win.measurement
    assert not win.ctx.can.measuring and not m.running
    ctl = m.controls                                           # top of the rail, above the Setup button
    assert ctl.isVisible() and win.rail.isAncestorOf(ctl)
    setup = win.rail.buttons[0]
    assert setup.text() == "Setup" and ctl.mapTo(win.rail, ctl.rect().bottomLeft()).y() < setup.mapTo(win.rail, setup.rect().topLeft()).y()
    assert ctl.start_btn.isEnabled() and not ctl.stop_btn.isEnabled() and ctl.state.text() == "Stopped"
    assert win.status.rec.text() == "■ Stopped" and win.status.elapsed.text() == "00:00:00"
    texts = [a.text() for a in win.menu.menus["Simulation"].actions()]
    assert texts[0] == "Start measurement\tF9" and texts[1] == "Stop measurement\tShift+F9"
    assert texts[2] == "Clear trace on Start"


def test_nothing_is_sent_or_logged_until_start(win, qtbot):
    card = connect_and_add(win, qtbot)
    assert not card.row.once.isEnabled() and "Start the measurement" in card.row.once.toolTip()
    card.enable.setChecked(True)                               # Cyclic ticked while stopped: armed
    card.send_once()                                           # refused, said in the Log
    qtbot.wait(500)
    assert card.job.count == 0 and win.trace.model.rowCount() == 0
    assert "not sent: the measurement is stopped" in win.log.text.toPlainText()

    win.commands.run("measurement.start")                      # F9
    ctl = win.measurement.controls
    assert win.ctx.can.measuring and not ctl.start_btn.isEnabled() and ctl.stop_btn.isEnabled()
    assert ctl.state.text().startswith("● 00:00") and card.row.once.isEnabled()
    qtbot.waitUntil(lambda: card.job.count > 20 and win.trace.model.rowCount() > 20, timeout=3000)
    assert win.status.rec.text() == "● Recording"
    card.send_once()                                           # Send works now

    ctl.stop_btn.click()                                       # Stop on the menu bar
    assert not win.ctx.can.measuring and win.status.rec.text() == "■ Stopped"
    qtbot.wait(100)
    n, rows = card.job.count, win.trace.model.rowCount()
    qtbot.wait(500)
    assert card.job.count == n and win.trace.model.rowCount() == rows
    assert card.enable.isChecked()                             # still armed: it resumes with the next start
    assert not card.row.once.isEnabled()


def test_start_clears_the_trace_unless_switched_off(win, qtbot):
    card = connect_and_add(win, qtbot)
    card.enable.setChecked(True)
    win.measurement.start()
    qtbot.waitUntil(lambda: win.trace.model.rowCount() > 10, timeout=3000)
    win.measurement.stop()
    qtbot.wait(100)
    win.measurement.start()                                    # Clear trace on Start (default): starts empty
    assert win.trace.model.rowCount() < 5
    qtbot.waitUntil(lambda: win.trace.model.rowCount() > 10, timeout=3000)
    win.measurement.stop()
    qtbot.wait(100)
    win.measurement.set_clear_on_start(False)
    kept = win.trace.model.rowCount()
    win.measurement.start()
    assert win.trace.model.rowCount() >= kept                  # kept this time
    win.measurement.stop()
    win.measurement.set_clear_on_start(True)


def test_running_time_and_commands(win, qtbot):
    m = win.measurement
    assert win.commands.commands["measurement.start"].is_enabled()
    assert not win.commands.commands["measurement.stop"].is_enabled()
    m.start()
    qtbot.wait(1100)
    m.tick()
    win._update_status()
    assert m.controls.state.text() == "● 00:00:01" and win.status.elapsed.text() == "00:00:01"
    assert win.commands.commands["measurement.stop"].is_enabled()
    win.commands.run("measurement.stop")                       # Shift+F9
    assert not m.running and m.controls.state.text() == "Stopped"
