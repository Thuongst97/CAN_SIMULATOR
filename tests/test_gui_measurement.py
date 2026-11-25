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
    assert ctl.button.text() == "Start" and ctl.button.isEnabled() and ctl.state.text() == "Stopped"   # one button
    assert win.status.rec.text() == "■ Stopped" and win.status.elapsed.text() == "00:00:00"
    texts = [a.text() for a in win.menu.menus["Simulation"].actions()]
    assert texts[0] == "Measurement running\tF9" and texts[1] == "Clear trace on Start"


def test_nothing_is_sent_or_logged_until_start(win, qtbot):
    card = connect_and_add(win, qtbot)
    assert not card.row.once.isEnabled() and "Start the measurement" in card.row.once.toolTip()
    card.enable.setChecked(True)                               # Cyclic ticked while stopped: armed
    card.send_once()                                           # refused, said in the Log
    qtbot.wait(500)
    assert card.job.count == 0 and win.trace.model.rowCount() == 0
    assert "not sent: the measurement is stopped" in win.log.text.toPlainText()

    ctl = win.measurement.controls
    ctl.button.click()                                         # Start: the same button turns into Stop
    assert win.ctx.can.measuring and ctl.button.text() == "Stop" and ctl.button.property("running")
    assert ctl.state.text().startswith("● 00:00") and card.row.once.isEnabled()
    qtbot.waitUntil(lambda: card.job.count > 20 and win.trace.model.rowCount() > 20, timeout=3000)
    assert win.status.rec.text() == "● Recording"
    card.send_once()                                           # Send works now

    ctl.button.click()                                         # Stop: back to Start
    assert not win.ctx.can.measuring and ctl.button.text() == "Start" and win.status.rec.text() == "■ Stopped"
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
    win.commands.run("measurement.toggle")                     # F9 stops ...
    assert not m.running and m.controls.state.text() == "Stopped"
    win.answers.append("Start anyway")                         # (no channel: the Start question, answered)
    win.commands.run("measurement.toggle")                     # ... and starts again
    assert m.running and m.controls.button.text() == "Stop"
    m.stop()


def node_error(win, ch="CAN1"):
    win.bridge.nodeState.emit({"channel": ch, "port": "SIM1", "state": "error", "rx": 0, "tx": 0, "errors": 1,
                               "info": {}})


def test_start_without_channel_asks_first(win, qtbot):
    m = win.measurement
    ctl = m.controls
    ctl.button.click()                                         # no channel: question, Cancel (default answer)
    _title, text, buttons = win.asked[-1]
    assert "No CAN channel is connected" in text and buttons == ["Connect all", "Start anyway", "Cancel"]
    assert not m.running and ctl.button.text() == "Start"
    win.answers.append("Connect all")                          # no adapter found: not started, said in a toast
    ctl.button.click()
    assert not m.running and win._toast.isVisible() and "No adapter could be connected" in win._toast.text()
    assert win._toast.property("tone") == "error"

    def connect_sim(interactive=True):                         # an adapter present: Connect all opens it
        win.can_conn.port.setCurrentText("SIM1")
        win.can_conn.connect_node(interactive=False)
        return ["CAN1"]
    win.can_conn.connect_all = connect_sim
    win.answers.append("Connect all")
    ctl.button.click()
    assert m.running and win.ctx.can.bus.channels == ["CAN1"]
    m.stop()
    win.can_conn.disconnect_all()
    win.answers.append("Start anyway")
    ctl.button.click()
    assert m.running and win.ctx.can.bus.channels == []         # started on purpose without a channel
    m.stop()


def test_start_lists_problems_first(win, qtbot):
    m = win.measurement
    card = connect_and_add(win, qtbot)
    card.enable.setChecked(True)
    node_error(win)                                            # the adapter reports an error before Start
    m.controls.button.click()
    _t, text, buttons = win.asked[-1]
    assert "CAN1: the adapter reports an error" in text and buttons == ["Start anyway", "Cancel"]
    assert not m.running
    win.answers.append("Start anyway")
    m.controls.button.click()
    assert m.running
    m.stop()
    n = len(win.asked)
    win._node_states["CAN1"]["state"] = "running"              # fine again: no question at all
    m.controls.button.click()
    assert m.running and len(win.asked) == n
    m.stop()


def test_adapter_failure_while_running_is_shown_and_can_stop(win, qtbot):
    m = win.measurement
    card = connect_and_add(win, qtbot)
    card.enable.setChecked(True)
    m.start()
    qtbot.waitUntil(lambda: card.job.count > 5, timeout=3000)
    win.ctx.can.bus._channels["CAN1"].close()                  # the adapter is gone
    qtbot.waitUntil(lambda: bool(m.problems), timeout=3000)
    assert m.running                                           # keeps running (default) ...
    assert m.controls.state.text().startswith("⚠") and "could not be sent" in m.controls.state.toolTip()
    assert win._toast.isVisible() and "EngineData could not be sent" in win._toast.text()
    qtbot.waitUntil(lambda: not card.enable.isChecked(), timeout=2000)   # that message stopped
    assert card.resume
    m.stop()
    assert m.controls.state.text() == "Stopped" and not m.problems

    m.set_stop_on_failure(True)                                # ... or stops the measurement, if chosen
    try:
        m.start()
        node_error(win)
        assert not m.running and "The measurement was stopped" in win._toast.text()
    finally:
        m.set_stop_on_failure(False)
    texts = [a.text() for a in win.menu.menus["Simulation"].actions()]
    assert "Stop measurement when an adapter fails" in texts


def test_cyclic_resumes_when_the_channel_is_back(win, qtbot):
    m = win.measurement
    c = win.can_conn
    card = connect_and_add(win, qtbot)
    card.enable.setChecked(True)
    m.start()
    qtbot.waitUntil(lambda: card.job.count > 5, timeout=3000)
    c._close_channel("CAN1")                                   # disconnected while running
    assert not card.enable.isChecked() and card.resume
    c.port.setCurrentText("SIM1")
    c.channel.setText("CAN1")
    assert c.connect_node(interactive=False)                   # back: ticked again and sending
    assert card.enable.isChecked() and not card.resume
    n = card.job.count
    qtbot.waitUntil(lambda: card.job.count > n + 5, timeout=3000)
    assert "cyclic sending resumed on CAN1" in win.log.text.toPlainText()

    win.ctx.can.bus._channels["CAN1"].close()                  # adapter error: stopped ...
    qtbot.waitUntil(lambda: not card.enable.isChecked() and card.resume, timeout=3000)
    c._close_channel("CAN1")                                   # ... the user reconnects it
    c.port.setCurrentText("SIM1")
    c.channel.setText("CAN1")
    assert c.connect_node(interactive=False)
    assert card.enable.isChecked()
    n = card.job.count
    qtbot.waitUntil(lambda: card.job.count > n + 5, timeout=3000)

    card.enable.setChecked(False)                              # unticked by the user: stays off
    c._close_channel("CAN1")
    c.port.setCurrentText("SIM1")
    c.channel.setText("CAN1")
    assert c.connect_node(interactive=False)
    assert not card.enable.isChecked() and not card.resume
    m.stop()
