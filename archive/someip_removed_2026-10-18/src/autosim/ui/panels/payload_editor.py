"""Payload editor: raw hex / SOME/IP primitives / Protobuf form, with decode-back support."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from autosim.core.someip_engine.payload import PayloadError, hex_to_bytes, make_codec
from autosim.ui.panels.proto_form import ProtoForm

MODES = ["Raw hex", "Primitives", "Protobuf"]


class PayloadEditor(QWidget):
    def __init__(self, protos, parent=None) -> None:
        super().__init__(parent)
        self.protos = protos
        self.mode = QComboBox()
        self.mode.addItems(MODES)
        self.stack = QStackedWidget()

        self.raw = QLineEdit()
        self.raw.setPlaceholderText("DE AD BE EF")
        self.stack.addWidget(self.raw)

        prim = QWidget()
        pf = QFormLayout(prim)
        self.types = QLineEdit()
        self.types.setPlaceholderText("u8,u16,i32,f32,bool,string")
        self.values = QLineEdit()
        self.values.setPlaceholderText("1, 500, -7, 3.5, true, hello")
        pf.addRow("Types", self.types)
        pf.addRow("Values", self.values)
        self.stack.addWidget(prim)

        pb = QWidget()
        pv = QVBoxLayout(pb)
        pv.setContentsMargins(0, 0, 0, 0)
        self.msg = QComboBox()
        self.form = ProtoForm()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form)
        scroll.setMinimumHeight(130)
        pv.addWidget(self.msg)
        pv.addWidget(scroll)
        self.stack.addWidget(pb)

        top = QHBoxLayout()
        top.addWidget(QLabel("Payload"))
        top.addWidget(self.mode)
        top.addStretch()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.stack)

        self.mode.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.msg.currentTextChanged.connect(self._schema_changed)
        self.refresh_messages()

    def refresh_messages(self) -> None:
        cur = self.msg.currentText()
        self.msg.blockSignals(True)
        self.msg.clear()
        self.msg.addItems(self.protos.messages())
        if cur:
            self.msg.setCurrentText(cur)
        self.msg.blockSignals(False)
        self._schema_changed(self.msg.currentText())

    def _schema_changed(self, name: str) -> None:
        if name:
            self.form.set_schema(self.protos.field_schema(name))

    # ---- spec <-> UI --------------------------------------------------------
    def spec(self) -> str:
        i = self.mode.currentIndex()
        if i == 1:
            return "prim:" + self.types.text().strip()
        if i == 2:
            return "proto:" + self.msg.currentText()
        return ""

    def set_spec(self, spec: str) -> None:
        kind, _, rest = (spec or "").partition(":")
        if kind == "prim":
            self.mode.setCurrentIndex(1)
            self.types.setText(rest)
        elif kind == "proto":
            self.mode.setCurrentIndex(2)
            self.msg.setCurrentText(rest)
        else:
            self.mode.setCurrentIndex(0)

    # ---- encode / decode ----------------------------------------------------
    def encode(self) -> bytes:
        """Raises PayloadError with a user-presentable message."""
        i = self.mode.currentIndex()
        if i == 0:
            return hex_to_bytes(self.raw.text())
        if i == 1:
            return make_codec(self.spec()).encode(self.values.text())
        if not self.msg.currentText():
            raise PayloadError("no protobuf message selected (load a .proto first)")
        return make_codec(self.spec(), self.protos).encode(self.form.values())

    def show_bytes(self, data: bytes) -> str:
        """Fill the editor from received bytes; returns a one-line text rendering."""
        self.raw.setText(data.hex(" ").upper())
        i = self.mode.currentIndex()
        if i == 0:
            return data.hex(" ").upper()
        try:
            decoded = make_codec(self.spec(), self.protos).decode(data)
        except Exception as e:
            return f"<cannot decode: {e}>  raw: {data.hex(' ').upper()}"
        if i == 1:
            self.values.setText(", ".join(str(v) for v in decoded))
            return ", ".join(str(v) for v in decoded)
        self.form.set_values(decoded)
        return str(decoded)
