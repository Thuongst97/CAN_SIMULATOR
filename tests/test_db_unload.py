"""Removing a loaded database: registry, engine (TX jobs, plot watches, events) and the UI (DBC explorer list,
Remove button / Delete / context menu, Signals panel, plot, trace)."""
# ruff: noqa: F811  (the imported `win` fixture is requested by name in the test arguments)
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from autosim.core.can_engine.database import DatabaseError, DatabaseRegistry
from autosim.core.can_engine.engine import CanEngine
from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.core.common.frame import CanFrame
from tests.test_gui import win  # noqa: F401  (fixture)

DATA = Path(__file__).parent / "data"
SAMPLE, VHAL = DATA / "sample.dbc", DATA / "vhal_demo.dbc"


# ---- registry ------------------------------------------------------------------------------------------
def test_unload_removes_messages_and_ids():
    reg = DatabaseRegistry()
    reg.load(SAMPLE)
    reg.load(VHAL)
    assert [Path(f["path"]).name for f in reg.loaded()] == ["sample.dbc", "vhal_demo.dbc"]
    db = reg.unload(VHAL)
    assert db is not None and {m.name for m in db.messages} == {"HU_Request", "GW_Response", "GW_StatusFD"}
    assert reg.message_by_name("HU_Request") is None and reg.message_by_id("CAN1", 0x21) is None
    assert reg.decode(CanFrame(0x21, b"\x0a", channel="CAN1")) is None
    assert reg.message_by_name("EngineData") is not None                 # the other file stays
    assert reg.unload(VHAL) is None                                      # not loaded any more
    assert [Path(p).name for p in reg.paths] == ["sample.dbc"]


def test_loading_the_same_file_twice_reloads_instead_of_duplicating(tmp_path):
    p = tmp_path / "a.dbc"
    shutil.copy(SAMPLE, p)
    reg = DatabaseRegistry()
    reg.load(p)
    reg.load(str(p))
    assert len(reg.loaded()) == 1 and len(reg.messages()) == 2
    p.write_text(p.read_text().replace("EngineData", "EngineData2"))     # edited file: reload picks it up
    reg.load(p)
    assert reg.message_by_name("EngineData2") is not None and reg.message_by_name("EngineData") is None
    p.write_text("this is not a dbc")                                    # a broken edit keeps the old version
    with pytest.raises(DatabaseError):
        reg.load(p)
    assert reg.message_by_name("EngineData2") is not None and len(reg.loaded()) == 1


def test_id_defined_twice_falls_back_to_the_remaining_file(tmp_path):
    other = tmp_path / "other.dbc"
    other.write_text(SAMPLE.read_text().replace("EngineData", "Engine_v2"))
    reg = DatabaseRegistry()
    reg.load(SAMPLE)
    reg.load(other)
    assert reg.message_by_id("x", 256).name == "Engine_v2"               # later file wins
    reg.unload(other)
    assert reg.message_by_id("x", 256).name == "EngineData"              # and the earlier one is back
    assert reg.path_of(reg.message_by_name("EngineData")) == str(SAMPLE)


# ---- engine --------------------------------------------------------------------------------------------
def test_engine_unload_removes_jobs_watches_and_publishes():
    eb = EventBus()
    seen = []
    eb.subscribe(events.CAN_DB_UNLOADED, seen.append)
    eng = CanEngine(eb)
    eng.load_database(SAMPLE)
    eng.load_database(VHAL)
    job_a = eng.add_tx_message("CAN1", "EngineData")
    job_b = eng.add_tx_message("CAN1", "HU_Request")
    eng.rx.watch("HU_Request", "ReqValue")
    eng.rx.watch("EngineData", "Speed")
    removed = eng.unload_database(VHAL)
    assert removed == [job_b] and eng.tx.jobs() == [job_a]
    assert eng.rx.watched() == [("EngineData", "Speed")]
    assert seen == [str(VHAL)]
    with pytest.raises(KeyError):
        eng.unload_database(VHAL)


# ---- UI ------------------------------------------------------------------------------------------------
def test_remove_button_and_list(win):
    d = win.dbc
    assert not d.files.isVisible() or d.files.count() == 0
    assert d.load_file(str(SAMPLE)) and d.load_file(str(VHAL))
    assert d.files.count() == 2 and d.files.isVisible()
    assert "sample.dbc" in d.files.item(0).text() and "2 messages" in d.files.item(0).text()
    assert not d.remove_btn.isEnabled()                                  # two files, none selected
    d.files.item(1).setSelected(True)
    assert d.remove_btn.isEnabled()
    d.remove_btn.click()                                                 # nothing uses it: no question asked
    assert [Path(f["path"]).name for f in win.ctx.can.registry.loaded()] == ["sample.dbc"]
    assert d.files.count() == 1 and d.remove_btn.isEnabled()             # the only file: Remove works directly
    names = {d.tree.topLevelItem(i).child(j).text(0) for i in range(d.tree.topLevelItemCount())
             for j in range(d.tree.topLevelItem(i).childCount())}
    assert names == {"EngineData", "Status"}
    assert d.remove_selected(confirm=False) == 1
    assert d.files.count() == 0 and d.tree.topLevelItemCount() == 0 and not d.remove_btn.isEnabled()
    assert win.signals_panel.empty.title.text() == "No DBC Database Loaded"
    assert "Database removed: sample.dbc" in win.log.text.toPlainText()


def test_removing_a_database_in_use_asks_and_cleans_up(win, qtbot, monkeypatch):
    d, sp = win.dbc, win.signals_panel
    assert d.load_file(str(SAMPLE)) and d.load_file(str(VHAL))
    win.can_conn.connect_node()                                          # SIM1 / CAN1
    qtbot.waitUntil(lambda: win.can_conn.table.rowCount() == 1, timeout=3000)
    keep = sp.add_message("EngineData")
    gone = sp.add_message("HU_Request")
    gone.cycle.setValue(100)
    gone.enable.setChecked(True)                                         # cyclic TX running
    assert gone.job.enabled
    win.plot.add_signal("HU_Request", "ReqValue")
    win.plot.add_signal("EngineData", "Speed")
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: (asked.append(a[2]), QMessageBox.Cancel)[1]))
    assert not d.remove_file(str(VHAL))                                  # user cancels: nothing changes
    assert "vhal_demo.dbc" in asked[0] and "1 message(s)" in asked[0] and "sending cyclically" in asked[0]
    assert win.ctx.can.registry.is_loaded(VHAL) and gone in sp.cards
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    assert d.remove_file(str(VHAL))
    assert sp.cards == [keep] and gone.job not in win.ctx.can.tx.jobs()
    assert list(win.plot.curves) == [("EngineData", "Speed")]
    assert win.plot.msg.findText("HU_Request") < 0
    assert sp.stack.currentIndex() == 1                                  # EngineData still shown


def test_trace_forgets_names_of_a_removed_database(win, qtbot):
    from autosim.core.can_engine.rx_dispatcher import TraceRow
    from autosim.core.common import clock
    d, t = win.dbc, win.trace
    assert d.load_file(str(VHAL))
    f = CanFrame(0x21, bytes([10]) + bytes(7), channel="CAN1", timestamp=clock.now())
    t.on_batch([TraceRow(f, "HU_Request")])
    m = t.fixed_model
    assert m.data(m.index(0, 4), Qt.DisplayRole) == "HU_Request"
    assert d.remove_file(str(VHAL), confirm=False)
    assert m.data(m.index(0, 4), Qt.DisplayRole) in ("", None)


def test_reload_keeps_the_file_once(win, tmp_path):
    p = tmp_path / "x.dbc"
    shutil.copy(SAMPLE, p)
    d = win.dbc
    assert d.load_file(str(p)) and d.reload_file(str(p)) and d.load_file(str(p))
    assert d.files.count() == 1 and len(win.ctx.can.registry.messages()) == 2
