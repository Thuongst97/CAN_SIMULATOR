"""Web mode / Tauri app features of the desktop app: measurement Start / Stop, settings, signal board and project
files (the formats of the PySide6 app, so files open in both)."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer               # noqa: E402

from autosim.core.common.config import ProjectConfig                # noqa: E402
from autosim.web.server import make_app                              # noqa: E402
from autosim.web.session import WebError, WebSession                 # noqa: E402

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
    yield s
    s.shutdown()


def test_measurement_stop_sends_and_logs_nothing_and_start_clears_the_trace(session):
    session.add_database(str(SAMPLE))
    session.connect("SIM1")
    cid = session.add_message("EngineData")
    session.update_card(cid, cycle=10, enabled=True)
    assert wait_for(lambda: len(session.trace) > 5)
    v = session.version
    session.set_measuring(False)
    assert not session.state()["measuring"] and session.version > v           # clients re-read the state
    time.sleep(0.2)
    n = len(session.trace)
    time.sleep(0.3)
    assert len(session.trace) == n and session.cards[cid].enabled              # nothing logged, still armed
    session.set_measuring(True)                                               # clear_on_start (default): empty
    assert len(session.trace) < n
    assert wait_for(lambda: len(session.trace) > 5)
    session.set_prefs(clear_on_start=False)
    session.set_measuring(False)
    n = len(session.trace)
    session.set_measuring(True)
    assert len(session.trace) >= n


def test_settings_are_kept_and_checked(session, tmp_path):
    assert session.state()["prefs"] == {"clear_on_start": True, "stop_on_failure": False, "measure_on_start": None}
    session.set_prefs(stop_on_failure=True, measure_on_start=True)
    with pytest.raises(WebError, match="Unknown setting"):
        session.set_prefs(color="red")
    session.shutdown()
    again = WebSession(tmp_path / "web")
    try:
        assert again.prefs["stop_on_failure"] is True and again.prefs["measure_on_start"] is True
    finally:
        again.shutdown()
    session.ctx = again.ctx                                                    # (the fixture shuts it down)


def test_signal_board_round_trip_in_the_desktop_format(session, tmp_path):
    session.add_database(str(SAMPLE))
    session.connect("SIM1")
    cid = session.add_message("EngineData")
    session.update_card(cid, cycle=50)
    session.set_signal(cid, "Speed", 88.5)
    session.set_generator(cid, "Temp", "sine", [10, 1, 20])
    session.set_rules([{"rx_id": 0x21, "match": "any", "tx_id": 0x27, "tx_mode": "raw", "tx_dlc": 1, "tx_data": "05"}])
    board = json.loads(json.dumps(session.board_config()))
    assert board["kind"] == "autosim-signal-board" and board["databases"] == [str(SAMPLE)]
    item = board["signals"][0]
    assert item["message"] == "EngineData" and item["cycle"] == 50 and item["values"]["Speed"] == pytest.approx(88.5)
    assert item["generators"]["Temp"] == ["sine", [10, 1, 20]] and len(board["auto_response"]["rules"]) == 1
    # a fresh session loads the database it needs, the messages and the rules; nothing starts sending
    session.remove_card(cid)
    session.set_rules([])
    session.remove_database(str(SAMPLE))
    rep = session.load_board(board)
    assert rep == {"missing": [], "gone": [], "problems": [], "cards": 1}
    c = next(iter(session.cards.values()))
    assert c.cycle == 50 and not c.enabled and c.gens["Temp"][0] == "sine"
    assert c.job.state.values["Speed"] == pytest.approx(88.5)
    assert len(session.ctx.can.responder.rules) == 1
    with pytest.raises(WebError, match="Not a signal board"):
        session.load_board({"signals": "x"})


def test_board_with_unknown_messages_and_missing_database(session):
    session.add_database(str(SAMPLE))
    rep = session.load_board({"kind": "autosim-signal-board", "databases": ["/nowhere/x.dbc"],
                              "signals": [{"message": "EngineData"}, {"message": "Nope"}]})
    assert rep["missing"] == ["/nowhere/x.dbc"] and rep["gone"] == ["Nope"] and rep["cards"] == 1


def test_project_round_trip_reconnects_channels(session, tmp_path):
    session.add_database(str(SAMPLE))
    session.connect("SIM1", "CAN1", 250_000, 1_000_000)
    session.add_message("EngineData")
    proj = session.project_config()
    path = tmp_path / "p.json"
    path.write_text(json.dumps(proj))
    cfg = ProjectConfig.load(path)                                             # the desktop app reads it
    assert cfg.dbc_paths == [str(SAMPLE)] and cfg.nodes[0].port == "SIM1" and cfg.nodes[0].autoconnect
    assert cfg.nodes[0].nominal_bitrate == 250_000 and cfg.signals[0]["message"] == "EngineData"
    session.disconnect_all()
    for cid in list(session.cards):
        session.remove_card(cid)
    rep = session.load_project(json.loads(path.read_text()))
    assert rep["failed"] == [] and session.ctx.can.bus.channels == ["CAN1"]
    assert session.channels["CAN1"]["nominal"] == 250_000 and len(session.cards) == 1
    assert next(iter(session.cards.values())).job is not None                 # channel open: ready to send
    with pytest.raises(WebError, match="Not a project"):
        session.load_project({"foo": 1})


def test_http_measurement_prefs_board_project(session):
    session.add_database(str(SAMPLE))
    asyncio.run(_http(session))


async def _http(session):
    async with TestClient(TestServer(make_app(session, ""))) as c:
        r = await c.post("/api/measurement", json={"running": False})
        assert (await r.json()) == {"measuring": False}
        assert (await (await c.get("/api/state")).json())["measuring"] is False
        r = await c.post("/api/prefs", json={"clear_on_start": False})
        assert (await r.json())["prefs"]["clear_on_start"] is False
        r = await c.post("/api/prefs", json={"nope": 1})
        assert r.status == 400
        r = await c.get("/api/board")
        assert 'filename="autosim.board.json"' in r.headers["Content-Disposition"]
        board = await r.json()
        r = await c.post("/api/board", json=board)
        assert (await r.json())["missing"] == []
        r = await c.get("/api/project")
        assert 'filename="autosim.project.json"' in r.headers["Content-Disposition"]
        r = await c.post("/api/project", json=await r.json())
        assert r.status == 200
        r = await c.post("/api/measurement", json={"running": True})
        assert (await r.json()) == {"measuring": True}
