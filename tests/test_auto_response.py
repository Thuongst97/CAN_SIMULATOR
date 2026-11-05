"""Auto response engine: conditions, payload building, expressions, delays, loop guard, persistence and the
end-to-end path through CanEngine (frame received on a channel -> response transmitted on it)."""
import time
from pathlib import Path

import pytest

from autosim.core.can_engine.auto_response import (
    AutoResponder,
    ResponseRule,
    RuleError,
    compile_value,
    parse_hex,
    parse_int,
)
from autosim.core.can_engine.database import DatabaseRegistry
from autosim.core.can_engine.engine import CanEngine
from autosim.core.common import events
from autosim.core.common.config import ProjectConfig
from autosim.core.common.events import EventBus
from autosim.core.common.frame import CanChannelConfig, CanFrame

DBC = Path(__file__).parent / "data" / "vhal_demo.dbc"


class Sink:
    def __init__(self):
        self.sent: list[tuple[str, CanFrame]] = []

    def __call__(self, ch, frame):
        self.sent.append((ch, frame))

    def wait(self, n, timeout=2.0):
        end = time.monotonic() + timeout
        while len(self.sent) < n and time.monotonic() < end:
            time.sleep(0.002)
        return len(self.sent) >= n


@pytest.fixture
def reg():
    r = DatabaseRegistry()
    r.load(DBC)
    return r


@pytest.fixture
def responder(reg):
    sink = Sink()
    logs = []
    ar = AutoResponder(reg, sink, logs.append)
    ar.sink, ar.logs = sink, logs
    ar.enabled = True
    ar.start()
    yield ar
    ar.stop()


def rx(can_id, data, ch="CAN1", ext=False, fd=False):
    return CanFrame(can_id, bytes(data), ext, fd, False, 1.0, ch, True)


def user_example_raw():
    """The user's example without a DBC: 0x21 byte 0 == 10 -> 0x27 value 10; == 20 -> 0x27 value 100."""
    return [ResponseRule("ten", rx_id=0x21, match="raw", op="==", value="10", tx_id=0x27, tx_value="10"),
            ResponseRule("twenty", rx_id=0x21, match="raw", op="==", value="20", tx_id=0x27, tx_value="100")]


def test_user_example_raw_bytes(responder):
    assert responder.set_rules(user_example_raw()) == []
    responder.on_frame(rx(0x21, [10, 0, 0, 0, 0, 0, 0, 0]))
    assert responder.sink.wait(1)
    responder.on_frame(rx(0x21, [20, 0, 0, 0, 0, 0, 0, 0]))
    assert responder.sink.wait(2)
    responder.on_frame(rx(0x21, [30, 0, 0, 0, 0, 0, 0, 0]))          # no rule for 30
    responder.on_frame(rx(0x22, [10]))                                # other id
    time.sleep(0.05)
    (c1, f1), (c2, f2) = responder.sink.sent
    assert len(responder.sink.sent) == 2
    assert c1 == c2 == "CAN1"                                          # answered on the trigger's channel
    assert f1.arbitration_id == 0x27 and f1.data == bytes([10, 0, 0, 0, 0, 0, 0, 0]) and not f1.is_fd
    assert f2.arbitration_id == 0x27 and f2.data == bytes([100, 0, 0, 0, 0, 0, 0, 0])
    a, b = responder.rules
    assert responder.stats_of(a).hits == 1 and responder.stats_of(b).hits == 1


def test_user_example_with_dbc_signals(responder):
    rules = [ResponseRule(rx_id=0x21, match="signal", signal="ReqValue", op="==", value="10",
                          tx_id=0x27, tx_mode="signal", tx_signals={"RespValue": "10", "RespStatus": "1"}),
             ResponseRule(rx_id=0x21, match="signal", signal="ReqValue", op="==", value="20",
                          tx_id=0x27, tx_mode="signal", tx_signals={"RespValue": "100"})]
    assert responder.set_rules(rules) == []
    req = responder.registry.message_by_name("HU_Request")
    responder.on_frame(rx(0x21, req.encode({"ReqValue": 10, "ReqCounter": 3})))
    responder.on_frame(rx(0x21, req.encode({"ReqValue": 20, "ReqCounter": 4})))
    assert responder.sink.wait(2)
    resp = responder.registry.message_by_name("GW_Response")
    d1 = resp.decode(responder.sink.sent[0][1].data, decode_choices=False)
    d2 = resp.decode(responder.sink.sent[1][1].data, decode_choices=False)
    assert d1["RespValue"] == 10 and d1["RespStatus"] == 1
    assert d2["RespValue"] == 100 and d2["RespStatus"] == 0           # unset signal: initial value


def test_expressions_and_operators(responder):
    responder.set_rules([
        ResponseRule(rx_id=0x21, match="raw", op=">=", value="0x10", tx_id=0x27, tx_value="rx * 10",
                     tx_size=2, tx_order="big"),
    ])
    responder.on_frame(rx(0x21, [0x0F]))                               # below threshold
    responder.on_frame(rx(0x21, [0x20]))
    assert responder.sink.wait(1)
    time.sleep(0.03)
    assert len(responder.sink.sent) == 1
    assert responder.sink.sent[0][1].data[:2] == (320).to_bytes(2, "big")


def test_signal_echo_with_scaling_and_fd_message(responder):
    """rx expression into a scaled signal of an FD message: payload padded to a valid FD length."""
    responder.set_rules([ResponseRule(rx_id=0x21, match="signal", signal="ReqValue", op="!=", value="0",
                                      tx_id=0x28, tx_mode="signal", tx_signals={"Speed": "rx * 1.5"},
                                      tx_brs=True)])
    req = responder.registry.message_by_name("HU_Request")
    responder.on_frame(rx(0x21, req.encode({"ReqValue": 81, "ReqCounter": 0})))
    assert responder.sink.wait(1)
    f = responder.sink.sent[0][1]
    assert f.is_fd and f.bitrate_switch and len(f.data) == 16
    assert responder.registry.message_by_name("GW_StatusFD").decode(f.data)["Speed"] == pytest.approx(121.5)


def test_raw_multibyte_condition_little_and_big_endian(responder):
    responder.set_rules([
        ResponseRule("le", rx_id=0x100, match="raw", raw_offset=2, raw_size=2, raw_order="little",
                     op="==", value="0x1234", tx_id=0x101, tx_data="AA BB", tx_dlc=2),
        ResponseRule("be", rx_id=0x100, match="raw", raw_offset=2, raw_size=2, raw_order="big",
                     op="==", value="0x1234", tx_id=0x102, tx_dlc=0),
    ])
    responder.on_frame(rx(0x100, [0, 0, 0x34, 0x12]))                 # little-endian 0x1234
    assert responder.sink.wait(1)
    responder.on_frame(rx(0x100, [0, 0, 0x12, 0x34]))                 # big-endian 0x1234
    assert responder.sink.wait(2)
    responder.on_frame(rx(0x100, [0, 0, 0x12]))                       # too short: no match, no error
    time.sleep(0.03)
    assert [f.arbitration_id for _, f in responder.sink.sent] == [0x101, 0x102]
    assert responder.sink.sent[0][1].data == b"\xaa\xbb" and responder.sink.sent[1][1].data == b""


def test_channel_filters_and_fixed_response_channel(responder):
    responder.set_rules([ResponseRule(rx_channel="CAN2", rx_id=0x21, tx_id=0x27, tx_channel="CAN3",
                                      tx_value="1")])
    responder.on_frame(rx(0x21, [1], ch="CAN1"))                       # wrong channel
    responder.on_frame(rx(0x21, [1], ch="CAN2"))
    assert responder.sink.wait(1)
    time.sleep(0.03)
    assert [c for c, _ in responder.sink.sent] == ["CAN3"]


def test_extended_ids_are_distinct_from_standard(responder):
    responder.set_rules([ResponseRule(rx_id=0x21, rx_extended=True, tx_id=0x18FF0027, tx_extended=True,
                                      tx_value="7")])
    responder.on_frame(rx(0x21, [1]))                                  # standard 0x21: not the trigger
    responder.on_frame(rx(0x21, [1], ext=True))
    assert responder.sink.wait(1)
    f = responder.sink.sent[-1][1]
    assert len(responder.sink.sent) == 1 and f.is_extended and f.arbitration_id == 0x18FF0027


def test_delay_is_respected_and_order_kept(responder):
    responder.set_rules([ResponseRule("slow", rx_id=0x21, tx_id=0x27, tx_value="1", delay_ms=120),
                         ResponseRule("fast", rx_id=0x21, tx_id=0x28, tx_value="2", delay_ms=0)])
    t0 = time.perf_counter()
    stamps = []
    orig = responder._send
    responder._send = lambda ch, f: (stamps.append((f.arbitration_id, time.perf_counter() - t0)), orig(ch, f))
    responder.on_frame(rx(0x21, [0]))
    assert responder.sink.wait(2)
    (id1, t1), (id2, t2) = stamps
    assert id1 == 0x28 and t1 < 0.05
    assert id2 == 0x27 and 0.11 <= t2 < 0.4


def test_disabled_master_switch_and_tx_frames_are_ignored(responder):
    responder.set_rules(user_example_raw())
    responder.enabled = False
    responder.on_frame(rx(0x21, [10]))
    responder.enabled = True
    tx = rx(0x21, [10])
    tx.is_rx = False                                                   # our own transmission
    responder.on_frame(tx)
    time.sleep(0.05)
    assert responder.sink.sent == []


def test_loop_guard_switches_rule_off(reg):
    sink = Sink()
    logs = []
    ar = AutoResponder(reg, sink, logs.append, max_rate=50)
    ar.enabled = True
    ar.start()
    try:
        rule = ResponseRule("echo", rx_id=0x27, tx_id=0x27, tx_value="1")
        ar.set_rules([rule])
        for _ in range(200):
            ar.on_frame(rx(0x27, [1]))
        assert not rule.enabled and "switched off" in ar.stats_of(rule).disabled_reason
        assert any("switched off" in line for line in logs)
        n = len(sink.sent)
        ar.on_frame(rx(0x27, [1]))
        time.sleep(0.05)
        assert len(sink.sent) == n <= 50
    finally:
        ar.stop()


def test_send_errors_are_counted_and_logged_once(reg):
    logs = []

    def broken(ch, f):
        raise KeyError(f"unknown channel {ch!r}")
    ar = AutoResponder(reg, broken, logs.append)
    ar.enabled = True
    ar.start()
    try:
        rule = ResponseRule(rx_id=0x21, tx_id=0x27, tx_channel="NOPE", tx_value="1")
        ar.set_rules([rule])
        for _ in range(5):
            ar.on_frame(rx(0x21, [1]))
        end = time.monotonic() + 2
        while ar.stats_of(rule).errors < 5 and time.monotonic() < end:
            time.sleep(0.01)
        assert ar.stats_of(rule).errors == 5 and ar.stats_of(rule).hits == 0
        assert len([line for line in logs if "NOPE" in line]) == 1
    finally:
        ar.stop()


@pytest.mark.parametrize("rule, text", [
    (ResponseRule(rx_id=0x800, tx_id=1), "11 bits"),
    (ResponseRule(rx_id=1, tx_id=0x20000000, tx_extended=True), "29 bits"),
    (ResponseRule(rx_id=1, tx_id=1, match="signal"), "choose the signal"),
    (ResponseRule(rx_id=1, tx_id=1, match="raw", value="abc"), "not a number"),
    (ResponseRule(rx_id=1, tx_id=1, tx_dlc=12), "need CAN FD"),
    (ResponseRule(rx_id=1, tx_id=1, tx_value="1", tx_offset=7, tx_size=2), "does not fit"),
    (ResponseRule(rx_id=1, tx_id=1, tx_value="rx"), "condition reads no value"),
    (ResponseRule(rx_id=1, tx_id=1, tx_value="__import__('os')"), "only numbers"),
    (ResponseRule(rx_id=1, tx_id=1, tx_data="ABC"), "odd number"),
    (ResponseRule(rx_id=1, tx_id=1, delay_ms=-1), "delay"),
])
def test_invalid_rules_are_reported(reg, rule, text):
    ar = AutoResponder(reg, Sink())
    with pytest.raises(RuleError, match=text):
        ar.compile(rule)
    problems = ar.set_rules([rule])
    assert len(problems) == 1 and text in problems[0]


def test_helpers():
    assert parse_int("0x21") == 33 and parse_int(" 10 ") == 10 and parse_int("0b11") == 3
    assert parse_hex("0A 00:ff") == b"\x0a\x00\xff" and parse_hex("") == b""
    f = compile_value("(rx + 2) * 10 // 3")
    assert f(4) == 20 and f.uses_rx
    assert compile_value("-5")() == -5 and not compile_value("0x64").uses_rx
    with pytest.raises(RuleError):
        compile_value("rx.__class__")


def test_rules_roundtrip_through_project_file(tmp_path, reg):
    ar = AutoResponder(reg, Sink())
    ar.set_rules(user_example_raw() + [ResponseRule(rx_id=0x21, match="signal", signal="ReqValue",
                                                    tx_id=0x27, tx_mode="signal",
                                                    tx_signals={"RespValue": "rx"})])
    ar.enabled = True
    cfg = ProjectConfig(auto_response=ar.to_config())
    p = tmp_path / "p.json"
    cfg.save(p)
    back = ProjectConfig.load(p)
    ar2 = AutoResponder(reg, Sink())
    assert ar2.load_config(back.auto_response) == []
    assert ar2.enabled and [r.to_dict() for r in ar2.rules] == [r.to_dict() for r in ar.rules]
    assert ProjectConfig.load(p).auto_response["rules"][2]["tx_signals"] == {"RespValue": "rx"}


def test_old_project_files_without_auto_response_load(tmp_path):
    p = tmp_path / "old.json"
    p.write_text('{"dbc_paths": [], "nodes": []}')
    assert ProjectConfig.load(p).auto_response == {}


def test_end_to_end_through_engine_with_virtual_bus():
    """Peer (the 'Android' side) sends 0x21 on a python-can virtual bus; the engine's responder answers 0x27
    on the same channel and the peer receives it; the response also shows up in the trace as TX."""
    from autosim.core.can_engine.bus_manager import BusManager, PythonCanBackend
    eb = EventBus()
    eng = CanEngine(eb)
    eng.load_database(DBC)
    rows = []
    eb.subscribe(events.CAN_RX, rows.extend)
    eng.start()
    peer = BusManager()
    try:
        cfg = CanChannelConfig(fd_enabled=False)
        eng.open_channel("CAN1", PythonCanBackend("virtual", "ar-e2e"), cfg)
        peer.add_channel("HU", PythonCanBackend("virtual", "ar-e2e"), cfg)
        eng.responder.set_rules(user_example_raw())
        eng.responder.enabled = True
        for v in (10, 20, 30):
            peer.send("HU", CanFrame(0x21, bytes([v]) + bytes(7)))
        got = []
        end = time.monotonic() + 3
        while len(got) < 2 and time.monotonic() < end:
            while not peer.frame_q.empty():
                f = peer.frame_q.get()
                if f.is_rx:
                    got.append(f)
            time.sleep(0.005)
        assert [(f.arbitration_id, f.data[0]) for f in got] == [(0x27, 10), (0x27, 100)]
        time.sleep(0.15)
        tx = [r for r in rows if not r.frame.is_rx]
        assert [r.name for r in tx] == ["GW_Response", "GW_Response"]
    finally:
        peer.close_all()
        eng.shutdown()
