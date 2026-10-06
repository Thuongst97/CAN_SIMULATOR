"""Vector icons painted with QPainter (no image files), plus small PNG assets used by the QSS."""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

_SIZE = 64          # painted at 64px and scaled by Qt; coordinates below are on a 24x24 grid
_S = _SIZE / 24.0


def _poly(*pts) -> QPolygonF:
    return QPolygonF([QPointF(x * _S, y * _S) for x, y in pts])


def _paint(name: str, color: QColor) -> QPixmap:
    pm = QPixmap(_SIZE, _SIZE)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(color, 2.0 * _S)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    fill = color
    if name == "play":
        p.setBrush(fill)
        p.drawPolygon(_poly((7, 5), (19, 12), (7, 19)))
    elif name == "pause":
        p.setBrush(fill)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(6 * _S, 5 * _S, 4 * _S, 14 * _S), 1 * _S, 1 * _S)
        p.drawRoundedRect(QRectF(14 * _S, 5 * _S, 4 * _S, 14 * _S), 1 * _S, 1 * _S)
    elif name == "trash":
        p.drawLine(QPointF(5 * _S, 7 * _S), QPointF(19 * _S, 7 * _S))
        p.drawPolyline(_poly((9, 7), (9, 4), (15, 4), (15, 7)))
        p.drawPolygon(_poly((7, 7), (8, 20), (16, 20), (17, 7)))
        p.drawLine(QPointF(10 * _S, 11 * _S), QPointF(10 * _S, 16 * _S))
        p.drawLine(QPointF(14 * _S, 11 * _S), QPointF(14 * _S, 16 * _S))
    elif name == "save":
        p.drawLine(QPointF(12 * _S, 4 * _S), QPointF(12 * _S, 14 * _S))
        p.drawPolyline(_poly((7.5, 10), (12, 14.5), (16.5, 10)))
        p.drawPolyline(_poly((5, 15), (5, 20), (19, 20), (19, 15)))
    elif name == "autoscroll":
        p.drawLine(QPointF(12 * _S, 4 * _S), QPointF(12 * _S, 16 * _S))
        p.drawPolyline(_poly((7, 11.5), (12, 16.5), (17, 11.5)))
        p.drawLine(QPointF(6 * _S, 20 * _S), QPointF(18 * _S, 20 * _S))
    elif name == "filter":
        p.drawPolygon(_poly((4, 5), (20, 5), (14, 12.5), (14, 19), (10, 17), (10, 12.5)))
    elif name == "flash":
        p.setBrush(fill)
        p.drawPolygon(_poly((13.5, 3), (6, 13.5), (11, 13.5), (10, 21), (18, 10), (12.5, 10)))
    elif name == "popout":                                   # window with an outward arrow
        p.drawPolyline(_poly((11, 5), (5, 5), (5, 19), (19, 19), (19, 13)))
        p.drawLine(QPointF(11 * _S, 13 * _S), QPointF(19.5 * _S, 4.5 * _S))
        p.drawPolyline(_poly((14, 4.5), (19.5, 4.5), (19.5, 10)))
    elif name == "gear":
        p.save()
        p.translate(12 * _S, 12 * _S)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for _ in range(8):                                   # eight teeth
            p.rotate(45)
            p.drawRoundedRect(QRectF(-1.7 * _S, -10.2 * _S, 3.4 * _S, 4.6 * _S), 0.8 * _S, 0.8 * _S)
        p.drawEllipse(QPointF(0, 0), 7.2 * _S, 7.2 * _S)    # body
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.drawEllipse(QPointF(0, 0), 3.0 * _S, 3.0 * _S)    # hub hole
        p.restore()
    elif name == "database":
        top = QRectF(5 * _S, 3.5 * _S, 14 * _S, 5 * _S)
        p.drawEllipse(top)
        p.drawLine(QPointF(5 * _S, 6 * _S), QPointF(5 * _S, 18 * _S))
        p.drawLine(QPointF(19 * _S, 6 * _S), QPointF(19 * _S, 18 * _S))
        for y in (9.5, 14.5):
            path = QPainterPath()
            r = QRectF(5 * _S, (y - 2.5 + 1.5) * _S, 14 * _S, 5 * _S)
            path.arcMoveTo(r, 180)
            path.arcTo(r, 180, 180)
            p.drawPath(path)
        bottom = QPainterPath()
        rb = QRectF(5 * _S, 16 * _S, 14 * _S, 5 * _S)
        bottom.arcMoveTo(rb, 180)
        bottom.arcTo(rb, 180, 180)
        p.drawPath(bottom)
    elif name == "network":
        pts = [(12, 6), (5.5, 18), (18.5, 18)]
        p.drawLine(QPointF(12 * _S, 6 * _S), QPointF(5.5 * _S, 18 * _S))
        p.drawLine(QPointF(12 * _S, 6 * _S), QPointF(18.5 * _S, 18 * _S))
        p.drawLine(QPointF(5.5 * _S, 18 * _S), QPointF(18.5 * _S, 18 * _S))
        p.setBrush(QColor("#161822"))
        for x, y in pts:
            p.drawEllipse(QPointF(x * _S, y * _S), 3 * _S, 3 * _S)
    elif name == "refresh":
        path = QPainterPath()
        path.arcMoveTo(QRectF(5 * _S, 5 * _S, 14 * _S, 14 * _S), 60)
        path.arcTo(QRectF(5 * _S, 5 * _S, 14 * _S, 14 * _S), 60, 270)
        p.drawPath(path)
        p.setBrush(fill)
        p.drawPolygon(_poly((17, 2.5), (19.5, 8.5), (13.5, 8)))
    p.end()
    return pm


def make_icon(name: str, color: str = "#A1A4B3", disabled: str = "#5B5E6B", active: str | None = None) -> QIcon:
    icon = QIcon()
    icon.addPixmap(_paint(name, QColor(color)), QIcon.Normal)
    icon.addPixmap(_paint(name, QColor(active or color).lighter(125)), QIcon.Active)
    icon.addPixmap(_paint(name, QColor(disabled)), QIcon.Disabled)
    return icon


# ---- PNG assets referenced from QSS (arrows, check mark) ---------------------------------
def build_qss_assets(color: str) -> dict[str, str]:
    """Paint the small images the stylesheet needs and return {name: forward-slash path}."""
    d = Path(tempfile.gettempdir()) / "autosim_ui_assets"
    d.mkdir(exist_ok=True)
    col = QColor(color)
    out: dict[str, str] = {}

    def save(name: str, w: int, h: int, draw) -> None:
        img = QImage(w, h, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        draw(p, w, h)
        p.end()
        f = d / f"{name}_{col.name()[1:]}.png"
        img.save(str(f))
        out[name] = f.as_posix()

    def tri(up: bool):
        def draw(p, w, h):
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            if up:
                p.drawPolygon(QPolygonF([QPointF(w * .2, h * .68), QPointF(w * .8, h * .68), QPointF(w * .5, h * .3)]))
            else:
                p.drawPolygon(QPolygonF([QPointF(w * .2, h * .32), QPointF(w * .8, h * .32), QPointF(w * .5, h * .7)]))
        return draw

    def check(p, w, h):
        pen = QPen(QColor("#FFFFFF"), 2.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(w * .22, h * .52), QPointF(w * .42, h * .72), QPointF(w * .78, h * .3)]))

    save("arrow_down", 16, 16, tri(False))
    save("arrow_up", 16, 16, tri(True))
    save("check", 16, 16, check)
    return out
