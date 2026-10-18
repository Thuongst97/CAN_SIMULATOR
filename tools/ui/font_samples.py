"""Render candidate UI fonts in a CAN-tool context (labels, buttons, a trace row) for choosing the app font.

    uv run python tools/ui/font_samples.py docs/ui_review/review2/font_candidates.png
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontInfo, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication

CANDIDATES = [
    ("A  Segoe UI Variable", "Segoe UI Variable Text", "Windows 11 system font (crisp ClearType hinting)"),
    ("B  Segoe UI", "Segoe UI", "classic Windows UI font (CANoe / PCAN-View style)"),
    ("C  Bahnschrift", "Bahnschrift", "technical DIN-style, compact"),
    ("D  Tahoma", "Tahoma", "classic engineering-tool look, very legible"),
    ("E  Cascadia Mono", "Cascadia Mono", "monospace everywhere (IDs / hex align perfectly)"),
    ("F  Consolas", "Consolas", "monospace everywhere, compact"),
]


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "font_candidates.png"
    app = QApplication(sys.argv)
    row_h, w = 112, 1180
    img = QImage(w, row_h * len(CANDIDATES) + 10, QImage.Format_ARGB32)
    img.fill(QColor("#1B1D22"))
    p = QPainter(img)
    p.setRenderHint(QPainter.TextAntialiasing)
    for i, (label, family, note) in enumerate(CANDIDATES):
        y = 10 + i * row_h
        p.fillRect(QRectF(10, y, w - 20, row_h - 10), QColor("#212329"))
        head = QFont()
        head.setFamilies([family])
        head.setPixelSize(14)
        head.setWeight(QFont.Weight(600))
        body = QFont(head)
        body.setPixelSize(13)
        body.setWeight(QFont.Weight(400))
        p.setFont(head)
        p.setPen(QColor("#E6E8EC"))
        p.drawText(24, y + 24, f"{label}  —  Bus trace   Signals   Channels")
        p.setFont(body)
        p.setPen(QColor("#9EA2B0"))
        p.drawText(560, y + 24, f"{note}   [{QFontInfo(head).family()}]")
        p.setPen(QColor("#E6E8EC"))
        p.drawText(24, y + 50, "Channel CAN1   Nominal rate 500 kbit/s   Data rate 2 Mbit/s   Connect all   Cyclic TX   1000 ms")
        p.setPen(QColor("#B4B8C4"))
        p.drawText(24, y + 74, "Time [s]     Channel   Dir   ID     Name / Signal         DLC     Data / Value")
        p.setPen(QColor("#E6E8EC"))
        p.drawText(24, y + 94, "12.483960   CAN1        Rx   3B0   ACN1S07 · TR_TEMP    32 FD  00 1A FF 3C 8E 0B D2 7F   26.0 °C")
        p.setPen(QPen(QColor("#33363E")))
    p.end()
    img.save(out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
