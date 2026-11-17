"""Generate the Arduino IDE sketch (arduino/AutoSimCanNode) from the PlatformIO sources.

The PlatformIO project (src/, lib/autosim_proto/) is the single source of truth. The Arduino IDE wants one sketch
folder: <name>/<name>.ino plus a src/ sub-folder that it compiles recursively. This script copies the sources into
that layout unchanged and turns src/main.cpp into the .ino (only its #include lines get the "src/" prefix).

    python firmware/esp32_can_node/tools/make_arduino_sketch.py           regenerate after changing the firmware
    python firmware/esp32_can_node/tools/make_arduino_sketch.py --check   exit 1 if the sketch is out of date

autosim_config.h (the user's pin / crystal settings beside the .ino) is created once and never overwritten.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FW = Path(__file__).resolve().parent.parent
SKETCH_NAME = "AutoSimCanNode"
SKETCH = FW / "arduino" / SKETCH_NAME

# sources copied verbatim into <sketch>/src/
COPIED = (
    sorted((FW / "lib" / "autosim_proto").glob("*.h")) + sorted((FW / "lib" / "autosim_proto").glob("*.cpp"))
    + [p for p in sorted((FW / "src").glob("*.h"))]
    + [p for p in sorted((FW / "src").glob("*.cpp")) if p.name != "main.cpp"]
)

INO_BANNER = """\
// =====================================================================================================
// AutoSim CAN FD node - Arduino IDE sketch (ESP32 + MCP2518FD).
// GENERATED from firmware/esp32_can_node/src/main.cpp by tools/make_arduino_sketch.py: edit the PlatformIO
// sources and regenerate. Your settings (pins, crystal, baud rate) go in autosim_config.h, not here.
// Needs: board package "esp32" by Espressif (3.x) and the library "ACAN2517FD" by Pierre Molinaro.
// Guide: README.md in this folder and docs/ESP32_MCP2518FD_WIRING.md.
// =====================================================================================================
"""

USER_CONFIG = """\
// AutoSim CAN FD node - your settings for the Arduino IDE build.
// Uncomment and change what differs from the defaults. Defaults per chip are in src/board_config.h.
// This file is created once by tools/make_arduino_sketch.py and never overwritten.
#pragma once

// ---- MCP2518FD wiring (defaults: classic ESP32 SCK 18, MOSI 23, MISO 19, CS 5, INT 4;
//      ESP32-S3 12 / 11 / 13 / 10 / 4; ESP32-C3 4 / 6 / 5 / 7 / 3) -----------------------------------
// #define SCK_PIN  18
// #define MOSI_PIN 23
// #define MISO_PIN 19
// #define CS_PIN   5
// #define INT_PIN  4

// ---- MCP2518FD crystal in MHz: 40 (most modules) or 20 ----------------------------------------------
// #define OSC_FREQ 40

// ---- RX LED: -1 = off; LED_RGB 1 for an addressable (WS2812) LED -----------------------------------
// #define LED_PIN 2
// #define LED_RGB 0

// ---- UART speed when the board is used through its USB-UART bridge ("USB CDC On Boot: Disabled").
//      Must match the app (environment variable AUTOSIM_BAUD, default 921600) ----------------------------
// #define AUTOSIM_UART_BAUD 921600

// ---- Default node name (otherwise ESP32-<last 3 MAC bytes>; the app can rename it) ------------------
// #define AUTOSIM_NODE_NAME "BENCH-1"
"""


def ino_text() -> str:
    main = (FW / "src" / "main.cpp").read_text(encoding="utf-8")
    main = re.sub(r'^#include "([^"/]+)"', r'#include "src/\1"', main, flags=re.MULTILINE)
    return INO_BANNER + main


def expected() -> dict[Path, bytes]:
    out = {SKETCH / f"{SKETCH_NAME}.ino": ino_text().encode("utf-8")}
    for src in COPIED:
        out[SKETCH / "src" / src.name] = src.read_bytes()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="only verify that the sketch matches the sources")
    a = ap.parse_args(argv)
    files = expected()
    stale = [p for p, data in files.items() if not p.exists() or p.read_bytes() != data]
    extra = [p for p in (SKETCH / "src").glob("*") if p not in files] if (SKETCH / "src").exists() else []
    if a.check:
        for p in stale:
            print(f"out of date: {p.relative_to(FW)}")
        for p in extra:
            print(f"not generated (remove it): {p.relative_to(FW)}")
        return 1 if stale or extra else 0
    for p in extra:
        p.unlink()
    for p, data in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    cfg = SKETCH / "autosim_config.h"
    if not cfg.exists():
        cfg.write_text(USER_CONFIG, encoding="utf-8")
    print(f"sketch written: {SKETCH.relative_to(FW)} ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
