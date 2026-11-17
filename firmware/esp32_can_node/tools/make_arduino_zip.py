"""Package lib/AutoSimCanNode as an Arduino library ZIP (Sketch > Include Library > Add .ZIP Library...).

    python firmware/esp32_can_node/tools/make_arduino_zip.py            -> firmware/esp32_can_node/release/AutoSimCanNode-<version>.zip
    python firmware/esp32_can_node/tools/make_arduino_zip.py <folder>   -> <folder>/AutoSimCanNode-<version>.zip

The release/ copy is committed so the library can be downloaded straight from GitHub; tests/test_arduino_library.py
fails when it no longer matches lib/AutoSimCanNode (run this script after changing the firmware).
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

FW = Path(__file__).resolve().parent.parent
LIB = FW / "lib" / "AutoSimCanNode"


def version() -> str:
    for line in (LIB / "library.properties").read_text(encoding="utf-8").splitlines():
        if line.startswith("version="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("library.properties has no version")


def build(out_dir: Path = FW / "release") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"AutoSimCanNode-{version()}.zip"
    for old in out_dir.glob("AutoSimCanNode-*.zip"):             # keep only the current version
        old.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(LIB.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                # one top folder, like the IDE expects; fixed timestamps so an unchanged library gives the same file
                info = zipfile.ZipInfo(str(Path("AutoSimCanNode") / p.relative_to(LIB)).replace("\\", "/"),
                                       date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else FW / "release"))
