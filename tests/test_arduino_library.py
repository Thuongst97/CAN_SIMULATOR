"""The firmware is an Arduino library (firmware/esp32_can_node/lib/AutoSimCanNode) installable with
"Add .ZIP Library": check its metadata, example and ZIP layout."""
import re
import subprocess
import sys
import zipfile
from pathlib import Path

FW = Path(__file__).resolve().parent.parent / "firmware" / "esp32_can_node"
LIB = FW / "lib" / "AutoSimCanNode"


def props() -> dict:
    out = {}
    for line in (LIB / "library.properties").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def test_library_properties():
    p = props()
    for key in ("name", "version", "author", "maintainer", "sentence", "paragraph", "category", "url",
                "architectures", "depends", "includes"):
        assert p.get(key), key
    assert p["name"] == "AutoSimCanNode" and p["architectures"] == "esp32"
    assert p["depends"].startswith("ACAN2517FD") and p["includes"] == "AutoSimCanNode.h"
    assert re.fullmatch(r"\d+\.\d+\.\d+", p["version"])
    header = (LIB / "src" / "AutoSimCanNode.h").read_text(encoding="utf-8")
    major = re.search(r"#define AUTOSIM_FW_MAJOR (\d+)", header).group(1)
    minor = re.search(r"#define AUTOSIM_FW_MINOR (\d+)", header).group(1)
    assert p["version"].startswith(f"{major}.{minor}."), "library version and firmware version disagree"


def test_example_is_a_valid_sketch():
    ino = LIB / "examples" / "AutoSimCanNode" / "AutoSimCanNode.ino"            # folder name == sketch name
    text = ino.read_text(encoding="utf-8")
    assert "#include <AutoSimCanNode.h>" in text and "autosim::startCanNode(cfg)" in text
    assert "void setup()" in text and "void loop()" in text
    assert "Serial.print" not in text                                          # the port carries the protocol


def test_zip_has_one_top_folder_with_the_library(tmp_path):
    r = subprocess.run([sys.executable, str(FW / "tools" / "make_arduino_zip.py"), str(tmp_path)],
                       capture_output=True, text=True, check=True)
    z = Path(r.stdout.strip())
    assert z.name == f"AutoSimCanNode-{props()['version']}.zip"
    names = zipfile.ZipFile(z).namelist()
    assert {n.split("/")[0] for n in names} == {"AutoSimCanNode"}
    for need in ("library.properties", "src/AutoSimCanNode.h", "src/AutoSimCanNode.cpp", "src/can_mcp2518fd.cpp",
                 "examples/AutoSimCanNode/AutoSimCanNode.ino", "README.md"):
        assert f"AutoSimCanNode/{need}" in names, need


def test_committed_release_zip_matches_the_library():
    """firmware/esp32_can_node/release/AutoSimCanNode-<version>.zip is what users download from GitHub."""
    z = FW / "release" / f"AutoSimCanNode-{props()['version']}.zip"
    assert z.exists(), "run firmware/esp32_can_node/tools/make_arduino_zip.py"
    with zipfile.ZipFile(z) as zf:
        packed = {n[len("AutoSimCanNode/"):]: zf.read(n) for n in zf.namelist()}
    files = {str(p.relative_to(LIB)).replace("\\", "/"): p.read_bytes()
             for p in LIB.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert packed == files, "release ZIP is out of date: run firmware/esp32_can_node/tools/make_arduino_zip.py"
