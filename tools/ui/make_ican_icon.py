"""Render the ICAN app icon at every size, for Windows (taskbar, Start menu, installer), Tauri and the PySide6 app.

    python3 tools/ui/make_ican_icon.py          # needs Pillow and the Nimbus Sans Narrow Bold font (or a path:
                                                # ICAN_FONT=/path/to/bold-condensed.ttf)

Why not one SVG scaled down: at 16 - 48 px (the taskbar) a scaled 256 px drawing has a wide margin and tiny letters.
Here each size is drawn on its own:
- the tile fills the canvas: no transparent margin;
- "ICAN" in a bold condensed font is stretched to the tile width, then the image is supersampled 8x and reduced,
  so the letters stay as large and sharp as the pixels allow.

Outputs:
- packaging/autosim.ico (installers, PyInstaller);
- src/autosim/ui/assets/icon/app_<size>.png (PySide6 window icon);
- tauri/app-icon.png (1024 px, for `npx tauri icon`) and tauri/src-tauri/icons/icon.ico.
"""
from __future__ import annotations

import io
import os
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
BLUE, BLUE_DARK = (15, 108, 189), (12, 88, 158)
FONT = os.environ.get("ICAN_FONT", "/usr/share/fonts/opentype/urw-base35/NimbusSansNarrow-Bold.otf")
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
PNG_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(size: int) -> Image.Image:
    ss = 8 if size <= 64 else 4                              # supersampling
    n = size * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = 0 if size <= 32 else round(n * 0.02)               # small sizes: full bleed
    radius = round(n * (0.16 if size <= 32 else 0.2))
    # tile with a faint vertical gradient (lighter top) for depth at large sizes
    tile = Image.new("RGBA", (n, n))
    for y in range(n):
        t = y / max(1, n - 1)
        tile.paste(tuple(round(BLUE[i] + (BLUE_DARK[i] - BLUE[i]) * t) for i in range(3)) + (255,), (0, y, n, y + 1))
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle((pad, pad, n - 1 - pad, n - 1 - pad), radius=radius, fill=255)
    img.paste(tile, (0, 0), mask)
    # "ICAN": as wide as the tile allows (side margin 8 % / 11 % of the tile), centred on the cap height
    inner = n - 2 * pad
    margin = inner * (0.08 if size <= 32 else 0.11)
    target_w = inner - 2 * margin
    font = ImageFont.truetype(FONT, 100)
    l, t_, r, b = font.getbbox("ICAN")
    scale = target_w / (r - l)
    font = ImageFont.truetype(FONT, max(1, round(100 * scale)))
    l, t_, r, b = font.getbbox("ICAN")
    cap_h = b - t_
    if cap_h > inner * 0.52:                                 # keep some air above / below the letters
        font = ImageFont.truetype(FONT, max(1, round(font.size * inner * 0.52 / cap_h)))
        l, t_, r, b = font.getbbox("ICAN")
    x = (n - (r - l)) / 2 - l
    y = (n - (b - t_)) / 2 - t_
    d.text((x, y), "ICAN", font=font, fill=(255, 255, 255, 255))
    return img.resize((size, size), Image.LANCZOS)


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def write_ico(images: dict[int, bytes], path: Path) -> None:
    """ICONDIR + ICONDIRENTRY[] + PNG blobs (Windows Vista and later read PNG entries)."""
    entries, blobs, offset = [], [], 6 + 16 * len(images)
    for size, blob in sorted(images.items()):
        dim = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset))
        blobs.append(blob)
        offset += len(blob)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(blobs))


def main() -> None:
    imgs = {s: render(s) for s in sorted(set(ICO_SIZES) | set(PNG_SIZES))}
    blobs = {s: png(imgs[s]) for s in ICO_SIZES}
    write_ico(blobs, ROOT / "packaging" / "autosim.ico")
    write_ico(blobs, ROOT / "tauri" / "src-tauri" / "icons" / "icon.ico")
    out = ROOT / "src" / "autosim" / "ui" / "assets" / "icon"
    out.mkdir(parents=True, exist_ok=True)
    for s in PNG_SIZES:
        imgs[s].save(out / f"app_{s}.png", optimize=True)
    render(1024).save(ROOT / "tauri" / "app-icon.png", optimize=True)
    print("ICO:", ", ".join(map(str, ICO_SIZES)), "px; PNGs in", out, "; tauri/app-icon.png (1024 px)")


if __name__ == "__main__":
    main()
