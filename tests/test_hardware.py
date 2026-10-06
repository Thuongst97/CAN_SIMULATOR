import time

import pytest

from autosim.core.can_engine.engine import CanEngine
from autosim.core.can_engine.generators import Ramp
from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.core.common.frame import CanChannelConfig, CanFrame
from autosim.hardware.esp32_gateway.manager import NodeManager
from autosim.hardware.esp32_gateway.serial_node import NodeError, NodeState, SerialNode
from autosim.hardware.esp32_gateway.sim_node import SimNode
from tests.test_can_engine import DBC


def wait_for(cond, t=2.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_node_handshake_config_loopback_fd():
    host, sim = SimNode.create_pair()
    node = SerialNode("SIM", "ch1", transport_factory=lambda: host)
    got = []
    node.on_rx = got.append
    info = node.connect()
    assert info["node_id"] == "SIM-NODE" and info["proto"] == 1
    node.configure(CanChannelConfig(500_000, 5_000_000, True, True, iso_mode=False))
    assert sim.config["data"] == 5_000_000 and sim.config["flags"] & 4   # non-ISO flag
    node.start()
    assert node.state == NodeState.RUNNING
    node.send_frame(CanFrame(0x1ABCDE, bytes(range(64)), True, True, True))
    assert wait_for(lambda: got)
    f = got[0]
    assert (f.arbitration_id, f.is_extended, f.is_fd, f.bitrate_switch) == (0x1ABCDE, True, True, True)
    assert f.data == bytes(range(64)) and f.channel == "ch1"
    node.close()
    sim.shutdown()


def test_retry_survives_lost_ack_and_unknown_node_fails():
    host, sim = SimNode.create_pair()
    sim.drop_next_acks = 1                  # first HELLO is dropped -> retry succeeds
    node = SerialNode("SIM", transport_factory=lambda: host)
    assert node.connect()
    node.close()
    host, sim = SimNode.create_pair()
    sim.drop_next_acks = 99
    node = SerialNode("SIM", transport_factory=lambda: host)
    with pytest.raises(NodeError):
        node.connect()
    sim.shutdown()


def test_garbage_on_the_wire_is_counted_and_ignored():
    host, sim = SimNode.create_pair()
    node = SerialNode("SIM", transport_factory=lambda: host)
    node.connect()
    sim.t.write(b"\x05garbage\x00\x01\x00")   # node -> host: bad COBS / short packet
    assert wait_for(lambda: node.crc_errors >= 2)
    node.configure(CanChannelConfig())        # reader is still alive afterwards
    node.close()
    sim.shutdown()


def test_heartbeat_detects_dead_node():
    host, sim = SimNode.create_pair()
    node = SerialNode("SIM", transport_factory=lambda: host, ping_s=0.1, dead_after_s=0.4)
    node.connect()
    node.configure(CanChannelConfig())
    node.start()
    sim.shutdown()                           # node stops answering
    assert wait_for(lambda: node.state == NodeState.ERROR, 3)
    node.close()


def test_engine_with_simulated_node_end_to_end():
    eb = EventBus()
    eng = CanEngine(eb)
    eng.load_database(DBC)
    mgr = NodeManager(eb)
    rows, states = [], []
    eb.subscribe(events.CAN_RX, rows.extend)
    eb.subscribe(events.NODE_STATE, states.append)
    eng.start()
    try:
        backend = mgr.create_backend("SIM1", "SIM1")
        eng.open_channel("SIM1", backend, CanChannelConfig())
        job = eng.add_tx_message("SIM1", "EngineData")
        eng.bind_generator(job, "Speed", Ramp(0, 200, 1))
        eng.tx.start(job)
        assert wait_for(lambda: sum(1 for r in rows if r.frame.is_rx) > 20)
        eng.tx.stop(job)
        assert any(s["state"] == "running" for s in states)
        rx = [r for r in rows if r.frame.is_rx]
        assert rx[0].name == "EngineData" and rx[0].frame.channel == "SIM1"
        eng.close_channel("SIM1")
        mgr.release("SIM1")
    finally:
        eng.shutdown()
