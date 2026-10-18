"""Render the app icon (src/autosim/ui/assets/app_icon.svg) to PNGs and a multi-size Windows .ico.

    uv run python tools/ui/make_app_icon.py

Outputs: src/autosim/ui/assets/icon/app_<size>.png (16 … 256, used for the window / taskbar icon) and
packaging/autosim.ico (PyInstaller exe icon and installer icon; PNG-compressed entries, Windows Vista+).
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[2]
SVG = ROOT / "src" / "autosim" / "ui" / "assets" / "app_icon.svg"
PNG_DIR = ROOT / "src" / "autosim" / "ui" / "assets" / "icon"
ICO = ROOT / "packaging" / "autosim.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(str(SVG)).render(p)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def write_ico(images: dict[int, bytes], path: Path) -> None:
    """ICONDIR + ICONDIRENTRY[] + PNG blobs."""
    entries, blobs = [], []
    offset = 6 + 16 * len(images)
    for size, blob in sorted(images.items()):
        dim = 0 if size >= 256 else size                 # 0 means 256 in the ICO directory
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset))
        blobs.append(blob)
        offset += len(blob)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(blobs))


def main() -> int:
    QGuiApplication(sys.argv)
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    blobs = {}
    for s in SIZES:
        img = render(s)
        img.save(str(PNG_DIR / f"app_{s}.png"))
        blobs[s] = png_bytes(img)
    write_ico({s: b for s, b in blobs.items() if s in (16, 20, 24, 32, 40, 48, 64, 256)}, ICO)
    print(f"{len(SIZES)} PNGs in {PNG_DIR}, {ICO} ({ICO.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
