# ruff: noqa: F811  (the imported `win` fixture is requested by name in the test arguments)
"""Bus trace name filter: several names with |, signal names, wildcards, exclusions, and the 5 recent filters."""
from pathlib import Path

from autosim.core.can_engine.rx_dispatcher import TraceRow
from autosim.core.common.frame import CanFrame
from autosim.ui.models.trace_model import NameQuery
from tests.test_gui import win  # noqa: F401  (the `win` fixture, used as a test argument)

DATA = Path(__file__).parent / "data"
SIGS = ("Speed", "Temp", "Gear")


def test_name_query_terms():
    q = NameQuery("Engine | status")
    assert q.matches("EngineData") and q.matches("Status") and not q.matches("Brake")
    q = NameQuery("Speed|Gear")                             # signal names: the message has one of them
    assert q.matches("EngineData", lambda: SIGS) and not q.matches("Status", lambda: ("Flag",))
    assert [s for s in SIGS if q.signal_shown("EngineData", s)] == ["Speed", "Gear"]
    q = NameQuery("engine")                                 # the message name matches: all its signals
    assert [s for s in SIGS if q.signal_shown("EngineData", s)] == list(SIGS)
    q = NameQuery("ADRV_*")                                 # wildcards match the whole name
    assert q.matches("ADRV_0x51") and not q.matches("CAM_ADRV")
    assert NameQuery("Ge?r").matches("X", lambda: ("Gear",)) and not NameQuery("Ge?r").matches("X", lambda: ("Gears",))
    q = NameQuery("!Temp")                                  # exclusion only: everything but Temp
    assert q.matches("EngineData", lambda: SIGS)
    assert [s for s in SIGS if q.signal_shown("EngineData", s)] == ["Speed", "Gear"]
    q = NameQuery("!Status")                                # a message name excluded: the message is hidden
    assert not q.matches("Status") and q.matches("EngineData")
    q = NameQuery("Engine|!Temp")
    assert [s for s in SIGS if q.signal_shown("EngineData", s)] == ["Speed", "Gear"]
    assert not NameQuery("") and not NameQuery(" | ! ") and NameQuery("x")
    assert not NameQuery("Speed").matches("")               # unknown frames have no name / signals


def frames():
    return [TraceRow(CanFrame(0x100, bytes(8), timestamp=0.1, channel="CAN1"), "EngineData"),
            TraceRow(CanFrame(0x200, b"\x01", timestamp=0.2, channel="CAN1"), "Status"),
            TraceRow(CanFrame(0x300, b"\x02", timestamp=0.3, channel="CAN1"), ""),
            TraceRow(CanFrame(0x100, bytes(8), timestamp=0.4, channel="CAN1"), "EngineData")]


def children(model, row):
    return [model.index(i, 4, model.index(row, 0)).data() for i in range(model.rowCount(model.index(row, 0)))]


def test_bus_trace_filters_by_signal_names(win):
    assert win.dbc.load_file(str(DATA / "sample.dbc"))
    t = win.trace
    t.on_batch(frames())
    fm, cm = t.fixed_model, t.model
    t.f_name.setText("Speed|Gear|Status")
    assert [fm.index(r, 4).data() for r in range(fm.rowCount())] == ["EngineData", "Status"]
    assert children(fm, 0) == ["Speed", "Gear"] and children(fm, 1) == ["Flag"]   # Status matched by its name
    assert all(t.tree.isExpanded(fm.index(r, 0)) for r in range(fm.rowCount()))  # opened to show them
    assert cm.rowCount() == 3                               # chronological: EngineData x2 + Status
    t.f_name.setText("!Temp|!Status")
    assert [fm.index(r, 4).data() for r in range(fm.rowCount())] == ["EngineData", ""]
    assert children(fm, 0) == ["Speed", "Gear"]
    t.f_name.setText("eng*")
    assert [fm.index(r, 4).data() for r in range(fm.rowCount())] == ["EngineData"] and len(children(fm, 0)) == 3
    t.f_id.setText("200")                                   # with the ID filter: both must pass
    assert fm.rowCount() == 0
    t.f_id.clear()
    t.f_name.clear()
    assert fm.rowCount() == 3 and children(fm, 0) == ["Speed", "Temp", "Gear"]
    # a message arriving while a signal filter is active opens too
    t.f_name.setText("Flag")
    t.clear()
    t.on_batch([TraceRow(CanFrame(0x200, b"\x01", timestamp=1.0, channel="CAN1"), "Status")])
    assert fm.rowCount() == 1 and t.tree.isExpanded(fm.index(0, 0)) and children(fm, 0) == ["Flag"]
    mirror = win.mini_trace_card.widget()                   # the Simulation trace shows the same
    assert mirror.f_name.text() == "Flag" and mirror.tree.isExpanded(fm.index(0, 0))


def test_recent_filters_keep_the_last_five(win):
    from autosim.ui.panels import trace_panel as tp
    t = win.trace
    t.clear_recent()
    for i in range(7):
        t.f_name.setText(f"Sig{i}")
        t.f_name.editingFinished.emit()                     # Enter / leaving the field
    t.f_name.setText("Sig5")                                # used again: moves to the top, no duplicate
    t.f_id.setText("100")
    t.f_id.editingFinished.emit()
    assert [tp.recent_label(e) for e in t.recent] == ["Sig5   ·   ID 100", "Sig6", "Sig5", "Sig4", "Sig3"]
    assert tp.load_recent() == t.recent                     # saved between runs
    t.f_name.clear()
    t.f_id.clear()
    t.f_name.editingFinished.emit()                         # an empty filter is not kept
    assert len(t.recent) == 5
    menu = t.recent_btn.menu()                              # clock button: pick one back
    menu.aboutToShow.emit()
    acts = [a for a in menu.actions() if a.text()]
    assert [a.text() for a in acts][:2] == ["Sig5   ·   ID 100", "Sig6"] and acts[-1].text() == "Clear recent filters"
    acts[1].trigger()
    assert t.f_name.text() == "Sig6" and t.f_id.text() == "" and t.recent[0] == {"id": "", "name": "Sig6"}
    mirror = win.mini_trace_card.widget()                   # the Simulation trace has the same list
    mm = mirror.recent_btn.menu()
    mm.aboutToShow.emit()
    assert [a.text() for a in mm.actions() if a.text()][0] == "Sig6"
    t.clear_recent()
    menu.aboutToShow.emit()
    assert not menu.actions()[0].isEnabled() and tp.load_recent() == []
