"""The Arduino IDE sketch (firmware/esp32_can_node/arduino/AutoSimCanNode) is generated from the PlatformIO
sources; it must not drift from them."""
import subprocess
import sys
from pathlib import Path

FW = Path(__file__).resolve().parent.parent / "firmware" / "esp32_can_node"
SKETCH = FW / "arduino" / "AutoSimCanNode"


def test_sketch_matches_the_platformio_sources():
    r = subprocess.run([sys.executable, str(FW / "tools" / "make_arduino_sketch.py"), "--check"],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, "run tools/make_arduino_sketch.py:\n" + r.stdout


def test_sketch_layout_for_the_arduino_ide():
    ino = (SKETCH / "AutoSimCanNode.ino").read_text(encoding="utf-8")
    assert "void setup()" in ino and "void loop()" in ino
    assert '#include "src/board_config.h"' in ino and '#include "board_config.h"' not in ino
    assert (SKETCH / "autosim_config.h").exists()                  # the user's settings file
    assert '__has_include("../autosim_config.h")' in (SKETCH / "src" / "board_config.h").read_text(encoding="utf-8")
    for name in ("autosim_protocol.cpp", "autosim_node.cpp", "can_mcp2518fd.cpp", "rx_blink.h", "host_link.h"):
        assert (SKETCH / "src" / name).exists(), name
