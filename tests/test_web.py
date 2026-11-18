"""Web mode: the session (engine state behind the browser) and the HTTP / WebSocket API."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import FormData                                         # noqa: E402
from aiohttp.test_utils import TestClient, TestServer               # noqa: E402

from autosim.web.server import make_app                              # noqa: E402
from autosim.web.session import WebError, WebSession                 # noqa: E402

DATA = Path(__file__).parent / "data"
SAMPLE, VHAL = DATA / "sample.dbc", DATA / "vhal_demo.dbc"


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def session(tmp_path, monkeypatch):
    from autosim.hardware.esp32_gateway.manager import NodeManager
    monkeypatch.setattr(NodeManager, "scan", lambda self: [])            # no real adapters in tests
    s = WebSession(tmp_path / "web")
    yield s
    s.shutdown()


# ---- session ----------------------------------------------------------------------------------------------------
def test_ports_offer_simulated_nodes_and_virtual_can(session):
    ports = {p["port"]: p["kind"] for p in session.ports}
    assert ports == {"SIM1": "sim", "SIM2": "sim", "VIRTUAL CAN": "bridge"}
    assert session.data_rates("SIM1", 500_000) == [500_000, 1_000_000, 2_000_000, 4_000_000, 5_000_000]
    assert session.port_kind("bridge://10.0.0.5:29536") == "bridge"


def test_channels_connect_disconnect_and_errors(session):
    assert session.connect("SIM1") == "CAN1"
    assert session.connect("SIM2", "", 250_000, 1_000_000) == "CAN2"
    with pytest.raises(WebError, match="already open"):
        session.connect("SIM1", "CAN1")
    assert wait_for(lambda: session.nodes.get("CAN1", {}).get("state") == "running")
    st = session.state()
    assert [c["channel"] for c in st["channels"]] == ["CAN1", "CAN2"] and st["channels"][1]["nominal"] == 250_000
    session.disconnect("CAN2")
    assert session.ctx.can.bus.channels == ["CAN1"]
    with pytest.raises(WebError, match="not open"):
        session.disconnect("CAN2")
    with pytest.raises(WebError, match="No free adapter"):
        session.connect_all()                                     # simulated nodes are never "free adapters"
    assert session.disconnect_all() == 1 and session.ctx.can.bus.channels == []


def test_databases_use_channel_not_used_and_remove(session):
    session.add_database(str(SAMPLE))
    session.add_database(str(VHAL))
    dbs = {d["name"]: d for d in session.databases()}
    assert dbs["sample.dbc"]["active"] and dbs["sample.dbc"]["channel"] == "*"
    eng = next(m for m in dbs["sample.dbc"]["messages"] if m["name"] == "EngineData")
    assert eng["id"] == 0x100 and {s["name"] for s in eng["signals"]} >= {"Speed", "Gear"}
    gear = next(s for s in eng["signals"] if s["name"] == "Gear")
    assert gear["choices"]["3"] == "D"
    assert dbs["sample.dbc"]["nodes"][0]["name"] == "ECU1" and "EngineData" in dbs["sample.dbc"]["nodes"][0]["tx"]
    session.connect("SIM1")
    session.use_database(str(SAMPLE), "CAN1")
    assert session.ctx.can.registry.is_loaded(SAMPLE, "CAN1")
    session.use_database(str(VHAL), None)                          # Not used: unloaded, still listed
    assert not session.ctx.can.registry.is_loaded(VHAL) and len(session.library) == 2
    session.remove_database(str(VHAL))
    assert [e.name for e in session.library] == ["sample.dbc"]
    with pytest.raises(WebError, match="not found"):
        session.add_database("/nowhere/x.dbc")


def test_upload_rejects_non_databases(session):
    with pytest.raises(WebError, match="not a CAN database"):
        session.upload_database("notes.txt", b"hello")
    e = session.upload_database("../../evil name.dbc", SAMPLE.read_bytes())
    assert Path(e.path).parent == session.db_dir and e.name == "evil name.dbc"


def test_generator_cyclic_send_signals_and_generators(session):
    session.add_database(str(SAMPLE))
    session.connect("SIM1")
    cid = session.add_message("EngineData")
    assert session.add_message("EngineData") == cid                  # once only
    card = session.card_list()[0]
    assert card["channel"] == "CAN1" and card["cycle"] == 10 and not card["fd"]
    assert session.set_signal(cid, "Speed", 999) == pytest.approx(250)   # clamped to the signal range
    assert session.set_signal(cid, "Speed", raw=500) == pytest.approx(50.0)  # raw * 0.1
    session.set_generator(cid, "Speed", "ramp")
    g = next(s for s in session.card_list()[0]["signals"] if s["name"] == "Speed")["gen"]
    assert g == {"kind": "ramp", "params": [0.0, 250.0, 5.0]}
    session.update_card(cid, cycle=5, enabled=True)
    assert wait_for(lambda: session.card_values()[str(cid)]["count"] > 20)
    with pytest.raises(WebError, match="1 to 60000"):
        session.update_card(cid, cycle=0)
    session.stop_all()
    n = session.card_values()[str(cid)]["count"]
    session.send_once(cid)
    assert session.card_values()[str(cid)]["count"] == n + 1
    session.set_generator(cid, "Speed", None)
    assert next(s for s in session.card_list()[0]["signals"] if s["name"] == "Speed")["gen"] is None
    with pytest.raises(WebError, match="not in the databases"):
        session.add_message("Nope")
    session.remove_card(cid)
    assert session.card_list() == []


def test_message_survives_its_channel_closing_and_reopening(session):
    session.add_database(str(SAMPLE))
    session.connect("SIM1")
    cid = session.add_message("EngineData")
    session.set_signal(cid, "Temp", 80)
    session.update_card(cid, enabled=True)
    session.disconnect("CAN1")
    card = session.card_list()[0]
    assert not card["ready"] and not card["enabled"]
    with pytest.raises(WebError, match="not open"):
        session.update_card(cid, enabled=True)
    session.connect("SIM1", "CAN1")
    card = session.card_list()[0]
    assert card["ready"] and next(s for s in card["signals"] if s["name"] == "Temp")["value"] == pytest.approx(80)


def test_auto_response_rules_hits_and_trace(session):
    session.add_database(str(VHAL))
    session.connect("SIM1")
    problems = session.set_rules([{"name": "ten", "rx_id": 0x21, "match": "raw", "value": "10", "tx_id": 0x27,
                                   "tx_value": "rx*2"}], enabled=True)
    assert problems == []
    cid = session.add_message("HU_Request")
    session.set_signal(cid, "ReqValue", 10)
    session.send_once(cid)
    assert wait_for(lambda: session.status()["hits"] == [1])
    rows = []
    assert wait_for(lambda: rows.extend(session.drain()["rows"]) or any(r[3] == 0x27 and r[2] == 1 for r in rows))
    resp = next(r for r in rows if r[3] == 0x27 and r[2] == 1)
    assert resp[8].startswith("14") and resp[5] == "GW_Response"           # 20 = 10 * 2
    rule = session.rules()["rules"][0]
    assert rule["condition"] == "byte 0 == 10" and "byte 0 = rx*2" in rule["response"]
    bad = session.set_rules([{"name": "bad", "rx_id": 0x21, "tx_id": 0x27, "tx_value": "rx"}])
    assert bad and "bad" in bad[0]
    dec = session.decode("CAN1", 0x21, resp[8].replace("14", "0a", 1))
    assert dec["message"] == "HU_Request"


def test_trace_export_pause_and_clear(session):
    session.add_database(str(SAMPLE))
    session.connect("SIM1")
    cid = session.add_message("EngineData")
    session.update_card(cid, enabled=True)
    assert wait_for(lambda: len(session.trace) > 20)
    for fmt in ("csv", "asc", "blf"):
        data, name = session.export(fmt)
        assert name.endswith("." + fmt) and len(data) > 100
    with pytest.raises(WebError):
        session.export("xls")
    session.set_paused(True)
    session.drain()
    n = len(session.trace)
    time.sleep(0.2)
    assert len(session.trace) == n and session.drain()["rows"] == []
    session.set_paused(False)
    session.clear_trace()
    assert wait_for(lambda: 0 < len(session.trace) < n)


def test_settings_are_saved_and_restored(tmp_path, monkeypatch):
    from autosim.hardware.esp32_gateway.manager import NodeManager
    monkeypatch.setattr(NodeManager, "scan", lambda self: [])
    s1 = WebSession(tmp_path / "w")
    s1.add_database(str(SAMPLE))
    s1.add_database(str(VHAL))
    s1.use_database(str(VHAL), None)
    cid = s1.add_message("EngineData")
    s1.set_signal(cid, "Temp", 55)
    s1.set_generator(cid, "Speed", "square", [1, 2, 3])
    s1.set_rules([{"name": "r", "rx_id": 0x21, "tx_id": 0x27}], enabled=True)
    s1.shutdown()
    s2 = WebSession(tmp_path / "w")
    try:
        assert [(e.name, e.active) for e in s2.library] == [("sample.dbc", True), ("vhal_demo.dbc", False)]
        card = s2.card_list()[0]
        assert card["message"] == "EngineData"
        sig = {s["name"]: s for s in card["signals"]}
        assert sig["Temp"]["value"] == pytest.approx(55) and sig["Speed"]["gen"] == {"kind": "square", "params": [1, 2, 3]}
        assert s2.ctx.can.responder.enabled and s2.rules()["rules"][0]["name"] == "r"
    finally:
        s2.shutdown()


# ---- HTTP / WebSocket ----------------------------------------------------------------------------------------------
def run(coro):
    return asyncio.run(coro)


async def client_for(session, token=""):
    client = TestClient(TestServer(make_app(session, token)))
    await client.start_server()
    return client


def test_http_api_and_websocket_end_to_end(session):
    async def go():
        c = await client_for(session)
        try:
            r = await c.get("/")
            assert r.status == 200 and "AutoSim Studio" in await r.text()
            assert (await c.get("/static/app.js")).status == 200
            st = await (await c.get("/api/state")).json()
            assert st["ports"][0]["port"] == "SIM1" and st["channels"] == []
            fd = FormData()
            fd.add_field("file", SAMPLE.read_bytes(), filename="sample.dbc")
            r = await c.post("/api/databases/upload", data=fd)
            assert (await r.json())["added"] == ["sample.dbc"]
            r = await c.post("/api/connect", json={"port": "SIM1", "channel": "", "nominal": 500000, "data": 2000000})
            assert (await r.json())["channel"] == "CAN1"
            r = await c.post("/api/connect", json={"port": "SIM1", "channel": "CAN1"})
            assert r.status == 400 and "already open" in (await r.json())["error"]
            rates = await (await c.get("/api/rates?port=SIM1&nominal=1000000")).json()
            assert rates["rates"][0] == 1000000
            ws = await c.ws_connect("/ws")
            hello = json.loads((await ws.receive()).data)
            assert hello["type"] == "hello" and "rows" in hello and "logs" in hello
            ids = (await (await c.post("/api/tx/add", json={"messages": ["EngineData"]})).json())["ids"]
            await c.post("/api/tx/generator", json={"id": ids[0], "name": "Speed", "kind": "sine"})
            await c.post("/api/tx/update", json={"id": ids[0], "enabled": True, "cycle": 10})
            got_rows, got_status, got_values = 0, False, False
            end = time.monotonic() + 5
            while time.monotonic() < end and not (got_rows > 10 and got_status and got_values):
                m = json.loads((await ws.receive(timeout=3)).data)
                if m["type"] == "update":
                    got_rows += len(m["rows"])
                got_status |= m["type"] == "status" and m["channels"][0]["channel"] == "CAN1"
                got_values |= m["type"] == "values" and str(ids[0]) in m["cards"]
            assert got_rows > 10 and got_status and got_values
            r = await c.get("/api/trace/export?format=asc")
            assert r.status == 200 and "attachment" in r.headers["Content-Disposition"] and len(await r.read()) > 100
            await c.post("/api/trace/clear")
            assert await c.post("/api/tx/stop_all") and (await c.post("/api/disconnect_all")).status == 200
            r = await c.post("/api/tx/update", json={"id": 999})
            assert r.status == 400
            r = await c.put("/api/rules", json={"rules": [{"name": "x", "rx_id": 1, "tx_id": 2}], "enabled": True})
            assert (await r.json())["problems"] == []
            assert (await (await c.get("/api/rules")).json())["enabled"] is True
            dbs = (await (await c.get("/api/databases")).json())["databases"]
            assert dbs[0]["name"] == "sample.dbc"
            await ws.close()
        finally:
            await c.close()
    run(go())


def test_token_is_required_on_a_network_address(session):
    async def go():
        c = await client_for(session, token="s3cret")
        try:
            assert (await c.get("/api/state")).status == 401
            r = await c.get("/ws")
            assert r.status == 401
            assert (await c.get("/api/state", headers={"X-AutoSim-Token": "s3cret"})).status == 200
            r = await c.get("/?token=s3cret")                       # the printed address sets a cookie
            assert r.status == 200 and "autosim_token" in r.cookies
            assert (await c.get("/api/state")).status == 200        # the cookie now authorises
        finally:
            await c.close()
    run(go())


def test_loopback_detection_for_the_token():
    from autosim.web.server import is_loopback
    assert is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.20")


def test_browser_end_to_end(tmp_path):
    """The real server and the real page in Chromium (Playwright): connect, databases, generator, auto response,
    traces, theme, log - with no JavaScript error. Skipped where Node.js + Playwright are not installed."""
    import shutil
    import socket
    import subprocess
    import sys
    node = shutil.which("node")
    if not node or subprocess.run([node, "-e", "require('playwright')"], capture_output=True).returncode:
        pytest.skip("Node.js with Playwright not available")
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    env = {**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"}
    srv = subprocess.Popen([sys.executable, "-m", "autosim.web", "--port", str(port), "--data-dir", str(tmp_path / "d"),
                            "--dbc", str(SAMPLE), "--dbc", str(VHAL)], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
    try:
        assert "web mode" in srv.stdout.readline()
        out = subprocess.run([node, str(Path(__file__).parent / "web_e2e.js"), str(tmp_path), f"http://127.0.0.1:{port}/"],
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stdout + out.stderr
        info = json.loads(out.stdout.splitlines()[0])
        assert info["total"] > 100 and info["fixed"] >= 4 and info["rules"] == 1 and info["cards"] == 2
        assert out.stdout.splitlines()[-1] == "ERRORS []", out.stdout
    finally:
        srv.terminate()
        srv.wait(10)
