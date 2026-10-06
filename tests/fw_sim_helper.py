"""Build and drive the firmware simulator (firmware/esp32_can_node/tools/fw_sim.cpp).

fw_sim runs the REAL firmware protocol + node logic (lib/autosim_proto) on the PC behind a fake CAN
controller that loops frames back, so the app's SerialNode can be tested against the firmware code.
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path

FW = Path(__file__).resolve().parent.parent / "firmware" / "esp32_can_node"
SOURCES = [FW / "tools" / "fw_sim.cpp", FW / "lib" / "autosim_proto" / "autosim_protocol.cpp",
           FW / "lib" / "autosim_proto" / "autosim_node.cpp"]


def have_compiler() -> bool:
    return shutil.which("g++") is not None


def build(out_dir: Path) -> Path:
    exe = out_dir / ("fw_sim.exe" if os.name == "nt" else "fw_sim")
    cmd = ["g++", "-std=c++17", "-Wall", "-Wextra", "-O1", f"-I{FW / 'lib' / 'autosim_proto'}",
           *map(str, SOURCES), "-o", str(exe)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode:
        raise RuntimeError("fw_sim build failed:\n" + res.stderr)
    return exe


class ProcessTransport:
    """Transport (read_some/write/close) over a child process' stdin/stdout."""

    def __init__(self, exe: Path, env: dict | None = None) -> None:
        self.proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env={**os.environ, **(env or {})})
        self._q: queue.Queue[bytes] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        while True:
            chunk = self.proc.stdout.read1(4096)
            if not chunk:
                return
            self._q.put(chunk)

    def read_some(self) -> bytes:
        try:
            return self._q.get(timeout=0.05)
        except queue.Empty:
            return b""

    def write(self, data: bytes) -> None:
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
