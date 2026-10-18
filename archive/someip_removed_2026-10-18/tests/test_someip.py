import time
from pathlib import Path

import pytest

from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.core.someip_engine.config_loader import (ConfigError, EventDef, MethodDef, ServiceDef,
                                                       load_arxml_services, load_vsomeip_json)
from autosim.core.someip_engine.engine import NetConfig, SomeIpEngine
from autosim.core.someip_engine.payload import PayloadError, make_codec
from autosim.core.someip_engine.proto_loader import ProtoRegistry
from autosim.core.someip_engine.wire import (IPv4Option, MsgType, SdEntry, SdType, SomeIpMessage, WireError,
                                              is_sd, pack_sd, unpack_sd)

DATA = Path(__file__).parent / "data"


def wait_for(cond, t=5.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_header_roundtrip_and_validation():
    m = SomeIpMessage(0x1234, 0x0001, 7, 9, MsgType.REQUEST, 0, b"\x01\x02\x03")
    raw = m.pack()
    assert len(raw) == 19 and raw[4:8] == (11).to_bytes(4, "big")
    assert SomeIpMessage.unpack(raw) == m
    with pytest.raises(WireError):
        SomeIpMessage.unpack(raw[:10])
    with pytest.raises(WireError):
        SomeIpMessage.unpack(raw[:-1])


def test_sd_roundtrip():
    entries = [
        SdEntry(SdType.OFFER, 0x1234, 1, 2, 5, minor=3, options=[IPv4Option("10.0.0.5", 30501)]),
        SdEntry(SdType.SUBSCRIBE, 0x1234, 1, 2, 3, eventgroup_id=0x4465, counter=2,
                options=[IPv4Option("10.0.0.6", 40000)]),
        SdEntry(SdType.FIND, 0x1234, 0xFFFF, 0xFF, 3),
    ]
    msg = SomeIpMessage.unpack(pack_sd(entries, 5))
    assert is_sd(msg) and msg.session_id == 5
    assert unpack_sd(msg) == entries
    with pytest.raises(WireError):
        unpack_sd(SomeIpMessage(0xFFFF, 0x8100, payload=b"\x00" * 6))


def test_payload_codecs():
    c = make_codec("prim:u8,i16,f32,bool,string")
    raw = c.encode("200, -5, 1.5, true, hello")
    assert c.decode(raw) == [200, -5, 1.5, True, "hello"]
    assert make_codec("").encode("DE AD be ef") == bytes.fromhex("deadbeef")
    with pytest.raises(PayloadError):
        c.encode("1,2")
    with pytest.raises(PayloadError):
        make_codec("prim:u8,wat")
    with pytest.raises(PayloadError):
        make_codec("prim:u8").decode(b"")


def test_protobuf_registry_encode_decode_and_schema():
    reg = ProtoRegistry()
    names = reg.load(DATA / "sample.proto")
    assert "demo.VehicleState" in names
    vals = {"speed": 120, "temperature": 21.5, "locked": True, "vin": "ABC", "mode": "SPORT",
            "inner": {"level": 3, "tag": "x"}, "history": [1, 2, 3]}
    out = reg.decode("demo.VehicleState", reg.encode("demo.VehicleState", vals))
    assert out["speed"] == 120 and out["mode"] == "SPORT" and out["inner"]["tag"] == "x"
    assert out["history"] == [1, 2, 3]
    schema = {f["name"]: f for f in reg.field_schema("demo.VehicleState")}
    assert schema["mode"]["enum_values"] == ["OFF", "ECO", "SPORT"]
    assert schema["history"]["repeated"] and schema["inner"]["fields"][0]["name"] == "level"
    codec = make_codec("proto:demo.VehicleState", reg)
    assert codec.decode(codec.encode('{"speed": 7}'))["speed"] == 7
    with pytest.raises(Exception):
        reg.load(DATA / "missing.proto")


ARXML = """<AUTOSAR xmlns="http://autosar.org/schema/r4.0"><AR-PACKAGES><AR-PACKAGE><ELEMENTS>
<SOMEIP-SERVICE-INTERFACE-DEPLOYMENT><SHORT-NAME>Door</SHORT-NAME>
<EVENT-DEPLOYMENTS><SOMEIP-EVENT-DEPLOYMENT><SHORT-NAME>Opened</SHORT-NAME><EVENT-ID>32769</EVENT-ID></SOMEIP-EVENT-DEPLOYMENT></EVENT-DEPLOYMENTS>
<EVENT-GROUPS><SOMEIP-EVENT-GROUP><SHORT-NAME>G</SHORT-NAME><EVENT-GROUP-ID>1</EVENT-GROUP-ID>
<EVENT-REFS><EVENT-REF>/x/Door/Opened</EVENT-REF></EVENT-REFS></SOMEIP-EVENT-GROUP></EVENT-GROUPS>
<METHOD-DEPLOYMENTS><SOMEIP-METHOD-DEPLOYMENT><SHORT-NAME>Open</SHORT-NAME><METHOD-ID>1</METHOD-ID></SOMEIP-METHOD-DEPLOYMENT></METHOD-DEPLOYMENTS>
<SERVICE-INTERFACE-ID>4660</SERVICE-INTERFACE-ID>
</SOMEIP-SERVICE-INTERFACE-DEPLOYMENT></ELEMENTS></AR-PACKAGE></AR-PACKAGES></AUTOSAR>"""


def test_vsomeip_and_arxml_loading(tmp_path):
    s = load_vsomeip_json(DATA / "vsomeip.json")[0]
    assert (s.service_id, s.instance_id, s.port) == (0x1234, 0x5678, 30509)
    assert [(e.event_id, e.eventgroup_id) for e in s.events] == [(0x8778, 0x4465), (0x8779, 0x4465)]
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    with pytest.raises(ConfigError):
        load_vsomeip_json(bad)
    arxml = tmp_path / "s.arxml"
    arxml.write_text(ARXML)
    a = load_arxml_services(arxml)[0]
    assert (a.name, a.service_id) == ("Door", 4660)
    assert a.events[0].event_id == 32769 and a.events[0].eventgroup_id == 1 and a.methods[0].method_id == 1


SVC = ServiceDef("Demo", 0x1234, 1, 1, 0,
                 methods=[MethodDef("Echo", 1), MethodDef("Static", 2), MethodDef("Boom", 3)],
                 events=[EventDef("Speed", 0x8001, 0x10)], port=30501)


@pytest.fixture
def engine():
    eb = EventBus()
    eng = SomeIpEngine(eb)
    eng.log = {"sd": [], "log": [], "method": []}
    eb.subscribe(events.SOMEIP_SD, eng.log["sd"].append)
    eb.subscribe(events.LOG, eng.log["log"].append)
    eb.subscribe(events.SOMEIP_METHOD, eng.log["method"].append)
    eng.start(NetConfig("127.0.0.1", sd_port=30499))
    yield eng
    eng.stop()


def test_discovery_subscribe_event_and_methods(engine):
    prov = engine.add_provider(SVC)
    cons = engine.add_consumer(SVC)
    ups, got = [], []
    cons.on_availability = ups.append
    cons.on_event = lambda eid, p: got.append((eid, p))
    cons.start()
    cons.subscribe(0x10)                       # subscribe before the offer exists
    assert not cons.available
    prov.start()
    assert wait_for(lambda: cons.available) and ups == [True]
    assert wait_for(lambda: 0x10 in cons.acked)
    assert wait_for(lambda: prov.subscribers)

    prov.publish_event(0x8001, b"\x00\x2a")
    assert wait_for(lambda: got) and got[0] == (0x8001, b"\x00\x2a")

    r = cons.call_method(1, b"ping")
    assert r.ok and r.payload == b"ping"                                  # default: echo
    prov.set_response(2, b"\x01\x02")
    assert cons.call_method(2, b"x").payload == b"\x01\x02"
    prov.handlers[3] = lambda p: 1 / 0
    r = cons.call_method(3, b"")
    assert not r.ok and r.return_code == 1
    r = cons.call_method(99, b"")
    assert not r.ok and "UNKNOWN_METHOD" in r.error
    assert cons.call_method(1, b"", fire_and_forget=True).ok
    assert wait_for(lambda: sum(1 for m in engine.log["method"] if m["method_id"] == 1) >= 2)

    cons.unsubscribe(0x10)
    assert wait_for(lambda: not prov.subscribers)
    assert any("OFFER" in e["entry"] for e in engine.log["sd"])
    assert any("SUBSCRIBE_ACK" in e["entry"] for e in engine.log["sd"])

    prov.stop()                                # stop-offer => consumer sees the service go away
    assert wait_for(lambda: not cons.available) and ups == [True, False]
    r = cons.call_method(1, b"", timeout=0.3)
    assert not r.ok and "not available" in r.error


def test_cyclic_publish_and_unknown_eventgroup_rejected(engine):
    prov = engine.add_provider(SVC)
    cons = engine.add_consumer(SVC)
    got = []
    cons.on_event = lambda eid, p: got.append(p)
    prov.start()
    cons.start()
    assert wait_for(lambda: cons.available)
    cons.subscribe(0x99)                       # not an eventgroup of the service
    assert wait_for(lambda: any("rejected" in str(m) for m in engine.log["log"]))
    assert not prov.subscribers
    cons.subscribe(0x10)
    assert wait_for(lambda: prov.subscribers)
    n = iter(range(1000))
    prov.start_cyclic(0x8001, 50, lambda: bytes([next(n) % 256]))
    assert wait_for(lambda: len(got) >= 5)
    prov.stop_cyclic(0x8001)
    time.sleep(0.15)
    k = len(got)
    time.sleep(0.2)
    assert len(got) == k


def test_call_timeout_when_provider_silent(engine):
    prov = engine.add_provider(SVC)
    cons = engine.add_consumer(SVC)
    prov.start()
    cons.start()
    assert wait_for(lambda: cons.available)
    prov._ep.handler = lambda d, a: None       # provider stops answering requests
    r = cons.call_method(1, b"", timeout=0.3)
    assert not r.ok and "timeout" in r.error


def test_tcp_methods_and_events(engine):
    svc = ServiceDef("Rel", 0x2222, 1, 1, 0, methods=[MethodDef("Echo", 1)],
                     events=[EventDef("Ev", 0x8002, 0x20)], port=30512, reliable=True)
    prov = engine.add_provider(svc)
    cons = engine.add_consumer(svc)
    got = []
    cons.on_event = lambda eid, p: got.append((eid, p))
    prov.start()
    cons.start()
    assert wait_for(lambda: cons.available and cons.uses_tcp)
    assert any("/tcp" in e["entry"] for e in engine.log["sd"])           # offer advertises a TCP endpoint

    big = bytes(range(256)) * 20                                          # > one segment, exercises framing
    r = cons.call_method(1, big)
    assert r.ok and r.payload == big
    assert cons._tcp_w is not None and not cons._tcp_w.is_closing()     # went over the TCP connection

    cons.subscribe(0x20)
    assert wait_for(lambda: prov.subscribers)
    assert prov.subscribers[0].proto == 0x06                              # subscribed with a TCP endpoint
    prov.publish_event(0x8002, b"\x01\x02")
    assert wait_for(lambda: got) and got[0] == (0x8002, b"\x01\x02")

    cons.stop()                                                           # TCP connection is dropped
    assert wait_for(lambda: not prov._tcp_conns and not prov.subscribers)
    prov.stop()
