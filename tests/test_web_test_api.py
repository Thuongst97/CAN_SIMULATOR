"""Web mode Test API: CAN signals published for test systems (GET the value on the bus, PUT sends a frame), the
call log, persistence, and the Test API tab in a real browser."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer               # noqa: E402

from autosim.core.common.frame import CanFrame                       # noqa: E402
from autosim.web.server import make_app                              # noqa: E402
from autosim.web.session import WebSession                           # noqa: E402
from autosim.web.test_api import ApiError                            # noqa: E402

DATA = Path(__file__).parent / "data"
SAMPLE = DATA / "sample.dbc"


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
    monkeypatch.setattr(NodeManager, "scan", lambda self: [])
    s = WebSession(tmp_path / "web")
    s.add_database(str(SAMPLE))
    yield s
    s.shutdown()


def test_endpoints_start_at_the_dbc_initial_value_and_stay_unique(session, tmp_path):
    api = session.api
    gear = api.add("EngineData", "Gear")
    assert (gear.name, gear.access, gear.value) == ("Gear", "get", 0)          # DBC initial (0 when not given)
    assert api.add("EngineData", "Gear") is gear                              # double-click again: no duplicate
    other = api.add("EngineData", "Gear", channel="CAN2")                     # same signal elsewhere: own name
    assert other.name == "EngineData_Gear"
    named = api.add("EngineData", "Temp", name="CoolantTemp", access="getset", value=25)
    assert named.value == 25
    for bad in ({"message": "Nope", "signal": "X"}, {"message": "EngineData", "signal": "Nope"}):
        with pytest.raises(ApiError) as e:
            api.add(**bad)
        assert e.value.status == 404
    with pytest.raises(ApiError, match="outside"):
        api.add("EngineData", "Gear", name="G2", value=9)                     # range 0 … 7
    with pytest.raises(ApiError, match="letters"):
        api.add("EngineData", "Gear", name="bad name")
    api.update("CoolantTemp", new_name="Coolant", stale_ms=200, access="get")
    assert "Coolant" in api.endpoints and api.endpoints["Coolant"].stale_ms == 200
    api.remove("EngineData_Gear")
    assert list(api.endpoints) == ["Gear", "Coolant"]
    assert api.read("Gear")["status"] == "no_data"                            # nothing on the bus yet


def test_get_reads_the_bus_and_put_sends_one_frame(session):
    api = session.api
    api.add("EngineData", "Gear", access="getset")
    api.add("EngineData", "Speed", access="getset", value=12.5)
    api.add("Status", "Flag")
    with pytest.raises(ApiError) as e:
        api.write("Gear", {"value": 3})
    assert e.value.status == 409                                              # no channel open
    session.connect("SIM1")
    r = api.write("Gear", {"text": "d"})                                      # value table name, any case
    assert r["status"] == "sent" and r["value"] == 3 and r["text"] == "D" and r["sent"]["channel"] == "CAN1"
    assert wait_for(lambda: api.read("Gear")["status"] == "ok")
    g = api.read("Gear")
    assert (g["value"], g["text"], g["channel"]) == (3, "D", "CAN1")
    assert api.read("Speed")["value"] == 12.5                                 # the other endpoint's value went along
    assert api.write("Speed", {"raw": 880})["value"] == 88                    # raw x 0.1
    for body, why in (({"value": 9}, "outside"), ({"text": "X"}, "no value named"), ({}, "Give"),
                      ({"value": "abc"}, "not a number"), ({"raw": "1.5"}, "integer")):
        with pytest.raises(ApiError, match=why) as e:
            api.write("Gear", body)
        assert e.value.status == 400
    with pytest.raises(ApiError) as e:
        api.write("Flag", {"value": 1})
    assert e.value.status == 403                                              # read only
    # the generator is not involved: no TX job was created
    assert session.ctx.can.tx.jobs() == [] and session.cards == {}


def test_values_sent_by_others_and_stale_status(session):
    api = session.api
    session.connect("SIM1")
    api.add("Status", "Flag", stale_ms=100)
    session.ctx.can.bus.send("CAN1", CanFrame(0x200, b"\x01"))
    assert wait_for(lambda: api.read("Flag")["status"] == "ok")
    assert api.read("Flag")["value"] == 1
    assert wait_for(lambda: api.read("Flag")["status"] == "stale", 2.0)       # no frame for > 100 ms
    session.set_paused(True)                                                  # trace paused: the API still reads
    session.ctx.can.bus.send("CAN1", CanFrame(0x200, b"\x00"))
    assert wait_for(lambda: api.read("Flag")["value"] == 0)
    many = api.read_many(None)["signals"]
    assert list(many) == ["Flag"] and api.endpoints["Flag"].gets >= 3


def test_endpoints_survive_a_restart_and_export_import(session, tmp_path, monkeypatch):
    api = session.api
    api.add("EngineData", "Gear", access="getset")
    session.connect("SIM1")
    api.write("Gear", {"value": 2})
    data = api.export()
    assert data["kind"] == "autosim-test-api" and data["apis"][0]["value"] == 2
    api.flush()
    s2 = WebSession(session.data_dir)
    try:
        ep = s2.api.endpoints["Gear"]
        assert (ep.access, ep.value) == ("getset", 2)                         # the last value written is kept
        assert s2.api.import_({"apis": [{"name": "X", "message": "EngineData", "signal": "Speed"}]}) == []
        assert list(s2.api.endpoints) == ["X"]
        assert s2.api.import_({"apis": [{"name": "Y", "message": "Gone", "signal": "Z"}]}, replace=False)
        with pytest.raises(ApiError):
            s2.api.import_({"nope": 1})
    finally:
        s2.shutdown()


def test_http_routes_and_call_log(session):
    async def go():
        c = TestClient(TestServer(make_app(session)))
        await c.start_server()
        try:
            r = await c.post("/api/testapi/add", json={"message": "EngineData", "signal": "Gear", "access": "getset"})
            assert (await r.json())["name"] == "Gear"
            r = await c.post("/api/testapi/add", json={"message": "EngineData", "signal": "Nope"})
            assert r.status == 404
            r = await c.put("/api/v1/signals/Gear", json={"value": 1})
            assert r.status == 409 and "not open" in (await r.json())["error"]
            await c.post("/api/connect", json={"port": "SIM1"})
            r = await c.put("/api/v1/signals/Gear", json={"text": "R"})
            out = await r.json()
            assert r.status == 200 and out["status"] == "sent" and out["sent"]["id"] == "0x100"
            r = await c.post("/api/v1/signals/Gear", data="not json")
            assert r.status == 400
            for _ in range(50):
                g = await (await c.get("/api/v1/signals/Gear")).json()
                if g["status"] == "ok":
                    break
                await asyncio.sleep(0.05)
            assert g["value"] == 1 and g["text"] == "R"
            assert (await c.get("/api/v1/signals/Nope")).status == 404
            many = await (await c.get("/api/v1/signals?names=Gear")).json()
            assert list(many["signals"]) == ["Gear"]
            ta = await (await c.get("/api/testapi")).json()
            assert ta["apis"][0]["name"] == "Gear" and ta["auth"] is False
            log = ta["log"]
            assert [(e["method"], e["status"]) for e in log][:3] == [("PUT", 409), ("PUT", 200), ("POST", 400)]
            assert log[1]["sent"]["channel"] == "CAN1" and log[1]["request"] == '{"text": "R"}'
            ids = [e["id"] for e in log]
            assert ids == sorted(ids) and len(set(ids)) == len(ids)          # rows are opened by id
            full = json.loads(log[1]["response"])                            # the whole answer, for the open row
            assert full["status"] == "sent" and full["sent"]["id"] == "0x100" and full["text"] == "R"
            assert log[1]["headers"]["Content-Type"].startswith("application/json") and log[1]["ms"] >= 0
            assert json.loads(log[0]["response"]) == {"error": "Channel (none) is not open - connect it in Setup > Channels"}
            assert log[-1]["path"] == "/api/v1/signals?names=Gear" and "Gear=1" in log[-1]["answer"]
            ws = await c.ws_connect("/ws")
            await c.get("/api/v1/signals/Gear")
            seen = None
            end = time.monotonic() + 3
            while time.monotonic() < end and seen is None:
                m = json.loads((await ws.receive(timeout=3)).data)
                if m["type"] == "api" and m["log"]:
                    seen = m
            assert seen and seen["live"][0]["name"] == "Gear" and seen["log"][-1]["method"] == "GET"
            await ws.close()
            r = await c.post("/api/testapi/update", json={"name": "Gear", "access": "get"})
            assert r.status == 200
            assert (await c.put("/api/v1/signals/Gear", json={"value": 1})).status == 403
            exp = await c.get("/api/testapi/export")
            assert "attachment" in exp.headers["Content-Disposition"] and (await exp.json())["apis"][0]["name"] == "Gear"
            await c.post("/api/testapi/log/clear")
            assert (await (await c.get("/api/testapi")).json())["log"] == []
            assert (await c.post("/api/testapi/remove", json={"name": "Gear"})).status == 200
            assert (await c.get("/api/v1/signals/Gear")).status == 404
        finally:
            await c.close()
    asyncio.run(go())


def test_token_protects_the_test_api(session):
    async def go():
        c = TestClient(TestServer(make_app(session, "secret")))
        await c.start_server()
        try:
            assert (await c.get("/api/v1/signals")).status == 401
            r = await c.get("/api/v1/signals", headers={"X-AutoSim-Token": "secret"})
            assert r.status == 200
            ta = await (await c.get("/api/testapi", headers={"X-AutoSim-Token": "secret"})).json()
            assert ta["auth"] is True
            logged = json.dumps(ta["log"])
            assert "secret" not in logged and '"X-AutoSim-Token": "(given)"' in logged   # the token is never logged
        finally:
            await c.close()
    asyncio.run(go())


def test_browser_test_api_tab(tmp_path):
    """Double-click a signal, Add API with GET + SET and a value table pick, then an outside client GETs / PUTs:
    the table shows the bus value and the call log the requests and the frame AutoSim sent."""
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
                            "--dbc", str(SAMPLE), "--no-browser"], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
    try:
        assert "web mode" in srv.stdout.readline()
        out = subprocess.run([node, str(Path(__file__).parent / "web_testapi_e2e.js"), str(tmp_path),
                              f"http://127.0.0.1:{port}/"], capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stdout + out.stderr
        info = json.loads(out.stdout.splitlines()[0])
        assert info["initial"] == "0" and "DBC initial 0" in info["info"]
        assert info["url"].endswith("/api/v1/signals/GearPosition")
        assert info["put"]["status"] == 200 and info["put"]["body"]["text"] == "D"
        assert info["get"]["value"] == 3 and info["get"]["status"] == "ok" and info["ro"] == 403
        assert info["all"] == ["Speed", "GearPosition"]
        assert info["row"][3] == "GET + SET" and info["row"][4] == "3 (D)" and info["row"][6] == "ok"
        log = info["log"]
        assert log[0][2].startswith("PUT /api/v1/signals/GearPosition") and log[0][3] == "200"
        assert log[0][5].startswith("CAN1 0x100 [")
        assert log[2][3] == "403"
        req, resp, frame = info["detail"]                                 # the PUT call, opened by a click
        assert req["h"] == "Request" and req["pre"].startswith("PUT http://127.0.0.1:")
        assert "Content-Type: application/json" in req["pre"] and '"text": "D"' in req["pre"]
        assert resp["h"].startswith("Response · 200 · ") and resp["h"].endswith(" ms")
        assert '"status": "sent"' in resp["pre"] and '"text": "D"' in resp["pre"]
        assert frame["h"] == "Frame sent by AutoSim" and "ID:      0x100" in frame["pre"] and "DLC:     8 bytes" in frame["pre"]
        assert info["follow"] is False                                    # reading a call: no jump to the bottom
        assert info["stillOpen"] == 1 and info["closed"] == 0
        assert len(info["err"]) == 2 and info["err"][1].startswith("Response · 403")
        assert "read only" in info["err"][1]
        assert out.stdout.splitlines()[-1] == "ERRORS []", out.stdout
    finally:
        srv.terminate()
        srv.wait(10)


def test_can_fd_message_of_a_real_dbc(tmp_path, monkeypatch):
    """LKAS (0x50, 16 bytes, CAN FD) of a production DBC: PUT sends an FD frame of the right length, the value table
    names (with spaces) work both ways."""
    from autosim.hardware.esp32_gateway.manager import NodeManager
    monkeypatch.setattr(NodeManager, "scan", lambda self: [])
    s = WebSession(tmp_path / "fd")
    try:
        s.add_database(str(DATA / "real_dbc" / "hyundai_canfd.dbc"))
        s.connect("SIM1")
        api = s.api
        api.add("LKAS", "LKA_RcgSta", access="getset")
        r = api.write("LKA_RcgSta", {"text": "full lane recognized"})
        assert r["sent"]["fd"] is True and len(r["sent"]["data"].split()) == 16 and r["value"] == 1
        assert wait_for(lambda: api.read("LKA_RcgSta")["status"] == "ok")
        g = api.read("LKA_RcgSta")
        assert (g["value"], g["text"], g["id"]) == (1, "Full Lane Recognized", "0x50")
    finally:
        s.shutdown()


def test_two_apis_of_one_message_share_the_frame(session):
    """Each PUT carries the current value of every API of the message (the others keep theirs)."""
    api = session.api
    session.connect("SIM1")
    api.add("EngineData", "Gear", access="getset")
    api.add("EngineData", "Speed", access="getset")
    api.write("Speed", {"value": 50})
    api.write("Gear", {"value": 2})                         # Speed 50 goes along
    assert wait_for(lambda: api.read("Gear")["value"] == 2 and api.read("Speed")["value"] == 50)
    api.write("Speed", {"value": 60})                       # Gear 2 goes along
    assert wait_for(lambda: api.read("Speed")["value"] == 60 and api.read("Gear")["value"] == 2)


def test_database_removed_while_an_api_exists(session):
    api = session.api
    api.add("EngineData", "Gear", access="getset")
    session.remove_database(session.library[0].path)
    g = api.read("Gear")
    assert g["status"] == "error" and "not in the databases" in g["error"]
    with pytest.raises(ApiError) as e:
        api.write("Gear", {"value": 1})
    assert e.value.status == 404
    session.add_database(str(SAMPLE))                       # back: the API works again
    assert api.read("Gear")["status"] == "no_data"


def test_burst_of_calls_is_fast_and_saves_settings_at_most_once_per_second(session, monkeypatch):
    api = session.api
    session.connect("SIM1")
    api.add("EngineData", "Gear", access="getset")
    saves = []
    real = session._save
    monkeypatch.setattr(session, "_save", lambda: (saves.append(1), real()))
    api._last_save = time.monotonic()                     # just saved
    t0 = time.perf_counter()
    for i in range(300):
        api.write("Gear", {"value": i % 8})
        api.read("Gear")
    dt = time.perf_counter() - t0
    assert dt < 3.0, dt                                     # 600 calls well under 10 ms each
    assert len(saves) <= 3 and api._dirty                   # throttled; the rest is written by flush_if_dirty
    session.status()                                        # (called once per second by the server)
    assert not api._dirty and api.endpoints["Gear"].sets == 300
    assert json.loads(session.settings_path.read_text())["test_api"][0]["value"] == 299 % 8


def test_stored_token_stays_the_same(tmp_path):
    from autosim.web.server import TOKEN_FILE, stored_token
    t1 = stored_token(tmp_path)
    assert t1 == stored_token(tmp_path) == (tmp_path / TOKEN_FILE).read_text().strip() and len(t1) >= 16
    t2 = stored_token(tmp_path, renew=True)                   # --new-token
    assert t2 != t1 and stored_token(tmp_path) == t2
    (tmp_path / TOKEN_FILE).write_text("bad token\n")         # damaged file: a new valid one
    t3 = stored_token(tmp_path)
    assert t3 not in ("bad token", t2) and stored_token(tmp_path) == t3


def _start_and_read_banner(args, data_dir):
    """Start autosim-web, return the lines it prints before serving, then stop it."""
    import socket
    import subprocess
    import sys
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    env = {**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"}
    p = subprocess.Popen([sys.executable, "-m", "autosim.web", "--port", str(port), "--data-dir", str(data_dir),
                          "--no-browser", *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        lines = []
        while True:
            line = p.stdout.readline()
            if not line:
                break
            lines.append(line.strip())
            if line.startswith("Close this window"):
                break
        return lines, port
    finally:
        p.terminate()
        p.wait(10)


def _token_of(lines):
    url = next(x for x in lines if x.startswith("AutoSim Studio web mode:"))
    return url.split("?token=")[1] if "?token=" in url else ""


def test_network_token_is_kept_across_restarts(tmp_path):
    d = tmp_path / "data"
    first, _ = _start_and_read_banner(["--host", "0.0.0.0"], d)
    tok = _token_of(first)
    assert tok and any(f"X-AutoSim-Token): {tok}" in x and "token.txt" in x for x in first), first
    again, _ = _start_and_read_banner(["--host", "0.0.0.0"], d)
    assert _token_of(again) == tok                            # same token after a restart
    renewed, _ = _start_and_read_banner(["--host", "0.0.0.0", "--new-token"], d)
    assert _token_of(renewed) not in ("", tok)
    given, _ = _start_and_read_banner(["--host", "0.0.0.0", "--token", "MyLabToken123"], d)
    assert _token_of(given) == "MyLabToken123" and any("given with --token" in x for x in given)
    after, _ = _start_and_read_banner(["--host", "0.0.0.0"], d)
    assert _token_of(after) == _token_of(renewed)             # --token does not replace the stored one
    local, _ = _start_and_read_banner([], d)
    assert _token_of(local) == "" and not any("X-AutoSim-Token" in x for x in local)   # this PC only: none
    nothing, _ = _start_and_read_banner(["--host", "0.0.0.0", "--no-token"], d)
    assert _token_of(nothing) == ""


def test_test_api_tab_gets_the_token_for_its_curl_commands(session):
    async def go():
        c = TestClient(TestServer(make_app(session, "Fixed123")))
        await c.start_server()
        try:
            ta = await (await c.get("/api/testapi", headers={"X-AutoSim-Token": "Fixed123"})).json()
            assert ta["auth"] is True and ta["token"] == "Fixed123"
            assert (await c.get("/api/testapi")).status == 401      # only an authorised browser sees it
        finally:
            await c.close()
    asyncio.run(go())
