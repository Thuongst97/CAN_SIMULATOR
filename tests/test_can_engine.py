import time
from pathlib import Path

import pytest

from autosim.core.can_engine.bus_manager import BusManager, PythonCanBackend
from autosim.core.can_engine.database import DatabaseRegistry
from autosim.core.can_engine.engine import CanEngine
from autosim.core.can_engine.generators import Ramp, Sine, Square, Toggle
from autosim.core.can_engine.logger_io import export_by_extension
from autosim.core.can_engine.signal_codec import MessageState
from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.core.common.frame import CanChannelConfig

DBC = Path(__file__).parent / "data" / "sample.dbc"


def test_registry_tree_and_decode():
    reg = DatabaseRegistry()
    reg.load(DBC)
    tree = {n["name"]: n for n in reg.tree()}
    assert {m["name"] for m in tree["ECU1"]["messages"]} == {"EngineData"}
    sig = tree["ECU1"]["messages"][0]["signals"][0]
    assert sig["factor"] == 0.1 and sig["unit"] == "km/h" and sig["length"] == 16
    assert reg.message_by_id("x", 256).name == "EngineData"


def test_state_encode_decode_roundtrip_and_clamp():
    reg = DatabaseRegistry()
    reg.load(DBC)
    st = MessageState(reg.message_by_name("EngineData"))
    assert st.set_signal("Speed", 999) == 250          # clamped
    st.set_signal("Speed", 120.5)
    st.set_signal("Temp", 25)
    out = st.decode(st.encode())
    assert out["Speed"] == pytest.approx(120.5) and out["Temp"] == 25


def test_generators():
    assert Sine(2, 1, 1).value(0.25) == pytest.approx(3)
    assert Ramp(0, 10, 1).value(0.5) == pytest.approx(5)
    assert Ramp(0, 10, 1, saw=False).value(0.5) == pytest.approx(10)
    assert Square(0, 1, 1).value(0.25) == 1 and Square(0, 1, 1).value(0.75) == 0
    assert Toggle([1, 2], 1).value(1.5) == 2


def make_engine():
    eb = EventBus()
    eng = CanEngine(eb)
    eng.load_database(DBC)
    return eb, eng


def test_tx_rx_over_virtual_bus_with_generator_and_export(tmp_path):
    eb, eng = make_engine()
    got = []
    eb.subscribe(events.CAN_RX, got.extend)
    eng.start()
    try:
        cfg = CanChannelConfig(fd_enabled=False)
        eng.open_channel("A", PythonCanBackend("virtual", "t1"), cfg)
        peer = BusManager()
        peer.add_channel("B", PythonCanBackend("virtual", "t1"), cfg)
        job = eng.add_tx_message("A", "EngineData")      # cycle 10 ms from DBC attribute
        assert job.cycle_ms == 10
        eng.bind_generator(job, "Speed", Sine(100, 1, 100))
        eng.tx.start(job)
        time.sleep(0.5)
        eng.tx.stop(job)
        assert 25 <= job.count <= 60
        rx = []
        while not peer.frame_q.empty():
            rx.append(peer.frame_q.get())
        assert len(rx) >= 25 and rx[0].arbitration_id == 256
        speeds = {round(eng.registry.decode(f)[1]["Speed"]) for f in rx}
        assert len(speeds) > 5                            # generator moves the signal
        time.sleep(0.2)
        txrows = [r for r in got if not r.frame.is_rx]
        assert txrows and txrows[0].name == "EngineData"
        assert any(r.cycle_ms and 5 < r.cycle_ms < 25 for r in txrows)
        for ext in ("csv", "asc", "blf"):
            p = tmp_path / f"t.{ext}"
            assert export_by_extension(txrows, p) == len(txrows)
            assert p.stat().st_size > 0
        assert "EngineData" in (tmp_path / "t.csv").read_text()
        peer.close_all()
    finally:
        eng.shutdown()


def test_event_trigger_and_tx_error_stops_job():
    eb, eng = make_engine()
    logs = []
    eb.subscribe(events.LOG, logs.append)
    eng.start()
    try:
        eng.open_channel("A", PythonCanBackend("virtual", "t2"), CanChannelConfig(fd_enabled=False))
        job = eng.add_tx_message("A", "Status")
        assert job.cycle_ms is None
        eng.tx.trigger(job)
        assert job.count == 1
        eng.bus._channels["A"].close()                    # break the bus
        eng.tx.trigger(job)
        assert any("TX error" in l for l in logs)
    finally:
        eng.shutdown()
