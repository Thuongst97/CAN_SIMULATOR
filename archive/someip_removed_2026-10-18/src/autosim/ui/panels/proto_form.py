"""Dynamic form generated from a protobuf message schema (ProtoRegistry.field_schema)."""
from __future__ import annotations

import base64
import json
from typing import Callable

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGroupBox, QLineEdit, QWidget

from autosim.core.someip_engine.payload import PayloadError, hex_to_bytes


class _Field:
    """getter() -> JSON-compatible value (or _SKIP), setter(value)."""
    def __init__(self, name: str, getter: Callable, setter: Callable) -> None:
        self.name, self.get, self.set = name, getter, setter


class ProtoForm(QWidget):
    """Scalars, enums, bools and nested messages get native widgets. Repeated fields, map fields
    and repeated messages are edited as JSON text (e.g. [1, 2, 3])."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._fields: list[_Field] = []
        self._layout = QFormLayout(self)

    def set_schema(self, schema: list[dict]) -> None:
        while self._layout.rowCount():
            self._layout.removeRow(0)
        self._fields = self._build(schema, self._layout)

    def _build(self, schema: list[dict], layout: QFormLayout) -> list[_Field]:
        out = []
        for f in schema:
            name, kind = f["name"], f["kind"]
            if f["repeated"] or f.get("json"):
                w = QLineEdit()
                w.setPlaceholderText("JSON, e.g. [1, 2, 3]" if f["repeated"] else "JSON object")
                layout.addRow(name + " [ ]" if f["repeated"] else name + " { }", w)
                out.append(_Field(name, self._json_get(w, f["repeated"]),
                                  lambda v, w=w: w.setText(json.dumps(v))))
            elif kind == "message":
                box = QGroupBox(name)
                inner = QFormLayout(box)
                sub = self._build(f["fields"], inner)
                layout.addRow(box)
                out.append(_Field(name, lambda sub=sub: {x.name: x.get() for x in sub},
                                  lambda v, sub=sub: [x.set(v[x.name]) for x in sub if x.name in v]))
            elif kind == "bool":
                w = QCheckBox()
                layout.addRow(name, w)
                out.append(_Field(name, w.isChecked, w.setChecked))
            elif kind == "enum":
                w = QComboBox()
                w.addItems(f["enum_values"])
                layout.addRow(name, w)
                out.append(_Field(name, w.currentText,
                                  lambda v, w=w: w.setCurrentText(v if isinstance(v, str) else
                                                                    (f["enum_values"][v] if 0 <= v < len(f["enum_values"]) else ""))))
            elif kind in ("int", "float"):
                w = QLineEdit("0")
                layout.addRow(name, w)
                conv = int if kind == "int" else float
                out.append(_Field(name, lambda w=w, conv=conv, n=name: self._num(w, conv, n),
                                  lambda v, w=w: w.setText(str(v))))
            elif kind == "bytes":
                w = QLineEdit()
                w.setPlaceholderText("hex bytes")
                layout.addRow(name, w)
                out.append(_Field(name, lambda w=w: base64.b64encode(hex_to_bytes(w.text())).decode(),
                                  lambda v, w=w: w.setText(base64.b64decode(v).hex(" ").upper())))
            else:
                w = QLineEdit()
                layout.addRow(name, w)
                out.append(_Field(name, w.text, w.setText))
        return out

    @staticmethod
    def _num(w: QLineEdit, conv, name: str):
        try:
            return conv(w.text().strip() or 0)
        except ValueError as e:
            raise PayloadError(f"{name}: {e}") from e

    @staticmethod
    def _json_get(w: QLineEdit, is_list: bool):
        def get():
            t = w.text().strip()
            if not t:
                return [] if is_list else {}
            try:
                return json.loads(t)
            except json.JSONDecodeError as e:
                raise PayloadError(f"invalid JSON: {e}") from e
        return get

    def values(self) -> dict:
        return {f.name: f.get() for f in self._fields}

    def set_values(self, values: dict) -> None:
        for f in self._fields:
            if f.name in values:
                f.set(values[f.name])
