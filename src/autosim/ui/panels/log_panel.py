"""Application log."""
from __future__ import annotations

import time

from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget


class LogPanel(QWidget):
    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.text = QPlainTextEdit()
        self.text.setObjectName("log")
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.text)
        bridge.log.connect(self.append)

    def append(self, msg: str) -> None:
        self.text.appendPlainText(f"{time.strftime('%H:%M:%S')}  {msg}")
