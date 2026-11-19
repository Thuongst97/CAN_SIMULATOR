"""DBC text encoding: Vector tools write cp1252, newer editors UTF-8 (with or without BOM). Units such as °C, µs
must read the same either way (UTF-8 read as cp1252 shows 'Â°C')."""
from pathlib import Path

import pytest

from autosim.core.can_engine.database import DatabaseRegistry, text_encoding

SAMPLE = Path(__file__).parent / "data" / "sample.dbc"


def sample_text() -> str:
    text = SAMPLE.read_text(encoding="ascii")
    return (text.replace('"C" ECU2', '"°C" ECU2').replace('"km/h"', '"µs"')
            .replace('3 "D"', '3 "Drive °"'))


@pytest.mark.parametrize("kind, data", [
    ("cp1252", lambda t: t.encode("cp1252")),
    ("utf8", lambda t: t.encode("utf-8")),
    ("utf8-bom", lambda t: b"\xef\xbb\xbf" + t.encode("utf-8")),
])
def test_units_and_value_names_read_the_same_in_every_encoding(tmp_path, kind, data):
    p = tmp_path / f"{kind}.dbc"
    p.write_bytes(data(sample_text()))
    db = DatabaseRegistry().load(p)
    sigs = {s.name: s for s in db.get_message_by_name("EngineData").signals}
    assert sigs["Temp"].unit == "°C" and sigs["Speed"].unit == "µs"
    assert str(sigs["Gear"].choices[3]) == "Drive °"
    assert text_encoding(p) == ("cp1252" if kind == "cp1252" else "utf-8-sig")


def test_plain_ascii_and_unreadable_files():
    assert text_encoding(SAMPLE) == "utf-8-sig"              # ASCII is valid UTF-8 and reads the same
    assert text_encoding(Path("/nonexistent/x.dbc")) == "cp1252"


def test_the_app_shows_the_degree_sign(tmp_path, qtbot):
    p = tmp_path / "utf8.dbc"
    p.write_bytes(sample_text().encode("utf-8"))
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    from autosim.context import AppContext
    from autosim.ui.main_window import MainWindow
    ctx = AppContext()
    ctx.start()
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    assert w.dbc.load_file(str(p))
    card = w.signals_panel.add_message("EngineData")
    temp = next(r for r in card.rows if r.sig.name == "Temp")
    assert temp.text(3) == "°C"                                             # Unit column of the generator
    w.close()
