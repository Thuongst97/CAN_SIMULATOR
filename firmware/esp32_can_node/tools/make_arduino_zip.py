"""Package lib/AutoSimCanNode as an Arduino library ZIP (Sketch > Include Library > Add .ZIP Library...).

    python firmware/esp32_can_node/tools/make_arduino_zip.py      -> firmware/esp32_can_node/dist/AutoSimCanNode-<version>.zip
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


def build(out_dir: Path = FW / "dist") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"AutoSimCanNode-{version()}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(LIB.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, Path("AutoSimCanNode") / p.relative_to(LIB))     # one top folder, like the IDE expects
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else FW / "dist"))
