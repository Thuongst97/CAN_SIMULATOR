"""Measurement Start / Stop (CANoe style): while stopped nothing is sent, answered or logged; cyclic messages switched
on meanwhile stay armed and start with the measurement."""
import time
from pathlib import Path

import pytest

from autosim.core.can_engine.auto_response import ResponseRule
from autosim.core.can_engine.bus_manager import BusManager, PythonCanBackend
from autosim.core.can_engine.engine import CanEngine
from autosim.core.can_engine.tx_scheduler import MeasurementStopped
from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.core.common.frame import CanChannelConfig, CanFrame

DBC = Path(__file__).parent / "data" / "sample.dbc"


@pytest.fixture
def bench(request):
    """Engine on channel A + a peer on the same virtual bus (B) that sees what A sends and can send to A."""
    name = f"meas-{request.node.name}"
    eb = EventBus()
    eng = CanEngine(eb)
    eng.load_database(DBC)
    rows, states = [], []
    eb.subscribe(events.CAN_RX, rows.extend)
    eb.subscribe(events.MEASUREMENT, states.append)
    eng.start()
    cfg = CanChannelConfig(fd_enabled=False)
    eng.open_channel("A", PythonCanBackend("virtual", name), cfg)
    peer = BusManager()
    peer.add_channel("B", PythonCanBackend("virtual", name), cfg)
    yield eng, peer, rows, states
    peer.close_all()
    eng.shutdown()


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get())
    return out


def test_engine_runs_by_default():
    eng = CanEngine(EventBus())
    assert eng.measuring and eng.tx.active and eng.rx.active and eng.responder.active


def test_stopped_sends_nothing_and_armed_cyclic_starts_with_the_measurement(bench):
    eng, peer, _rows, states = bench
    eng.set_measuring(False)
    assert states == [False] and not eng.measuring
    job = eng.add_tx_message("A", "EngineData")              # 10 ms
    eng.tx.start(job)                                         # Cyclic ticked while stopped: armed
    with pytest.raises(MeasurementStopped):
        eng.tx.trigger(job)                                   # Send refused
    time.sleep(0.3)
    assert job.enabled and job.count == 0 and drain(peer.frame_q) == []
    eng.set_measuring(True)                                   # Start: the armed message sends
    time.sleep(0.3)
    assert 15 <= job.count <= 40 and len(drain(peer.frame_q)) >= 15
    eng.set_measuring(False)                                  # Stop: silence, still armed
    time.sleep(0.05)
    n = job.count
    drain(peer.frame_q)
    time.sleep(0.3)
    assert job.count == n and drain(peer.frame_q) == [] and job.enabled
    eng.set_measuring(True)                                   # and again
    time.sleep(0.2)
    assert job.count > n
    eng.tx.trigger(job)                                       # Send allowed while running
    assert states == [False, True, False, True]


def test_stopped_logs_nothing_in_the_trace(bench):
    eng, peer, rows, _ = bench
    eng.set_measuring(False)
    for _ in range(5):
        peer.send("B", CanFrame(0x200, b"\x01"))
    time.sleep(0.3)
    assert rows == [] and eng.rx.rx_count == 0
    eng.set_measuring(True)
    peer.send("B", CanFrame(0x200, b"\x01"))
    time.sleep(0.3)
    assert [r.frame.arbitration_id for r in rows] == [0x200] and rows[0].name == "Status"


def test_auto_response_answers_only_while_running(bench):
    eng, peer, _, _ = bench
    eng.responder.set_rules([ResponseRule(rx_id=0x21, match="any", tx_id=0x27, tx_mode="raw", tx_dlc=1,
                                          tx_data="05")])
    eng.responder.enabled = True
    eng.set_measuring(False)
    peer.send("B", CanFrame(0x21, b"\x0a"))
    time.sleep(0.3)
    assert 0x27 not in [f.arbitration_id for f in drain(peer.frame_q)]     # (the peer's own 0x21 is in there)
    eng.set_measuring(True)
    peer.send("B", CanFrame(0x21, b"\x0a"))
    time.sleep(0.3)
    assert 0x27 in [f.arbitration_id for f in drain(peer.frame_q)]


def test_stop_drops_a_delayed_answer(bench):
    eng, peer, _, _ = bench
    eng.responder.set_rules([ResponseRule(rx_id=0x21, match="any", tx_id=0x27, tx_mode="raw", tx_dlc=1,
                                          tx_data="05", delay_ms=300)])
    eng.responder.enabled = True
    peer.send("B", CanFrame(0x21, b"\x0a"))
    time.sleep(0.1)
    eng.set_measuring(False)                                  # stopped before the 300 ms delay is over
    time.sleep(0.5)
    assert 0x27 not in [f.arbitration_id for f in drain(peer.frame_q)]
