"""Software model of CANable 2.0 adapters (normaldotcom/canable2 firmware) sharing one CAN bus.

Each FakeCanable answers the slcan command set on an in-memory pipe exactly like the firmware: no command
acknowledgements, 'V' version line, 'E' error register without '\\r'. Frames sent by an open adapter are
delivered to every other open adapter on the same bus with the same bit rates. If nobody can acknowledge,
the frame stays queued; after 64 queued frames the 'CAN TX buffer full' error bit is set (like the real one).
"""
from __future__ import annotations

import threading

from autosim.hardware.esp32_gateway.transport import make_pipe
from autosim.hardware.slcan import protocol as S

VERSION = "16e7497-dirty github.com/normaldotcom/canable2.git"


class FakeBus:
    def __init__(self) -> None:
        self.adapters: list[FakeCanable] = []
        self.lock = threading.Lock()

    def adapter(self) -> tuple[object, "FakeCanable"]:
        host, dev = make_pipe()
        a = FakeCanable(self, dev)
        self.adapters.append(a)
        return host, a


class FakeCanable:
    def __init__(self, bus: FakeBus, pipe) -> None:
        self.bus, self.pipe = bus, pipe
        self.open = False
        self.nominal, self.data = 4, 2              # firmware defaults: 125k / 2M
        self.silent = False
        self.err = 0                                # latched like the firmware (never cleared)
        self.pending = 0                            # unacknowledged frames in the TX queue
        self.sent: list[str] = []
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def shutdown(self) -> None:
        self._stop.set()

    def _out(self, text: str) -> None:
        self.pipe.write(text.encode("ascii"))

    def _run(self) -> None:
        buf = ""
        while not self._stop.is_set():
            chunk = self.pipe.read_some()
            if not chunk:
                continue
            buf += chunk.decode("ascii")
            while "\r" in buf:
                line, buf = buf.split("\r", 1)
                self._command(line)

    def _command(self, line: str) -> None:
        if not line:
            return
        c = line[0]
        if c == "O":
            self.open = True
        elif c == "C":
            self.open = False
            self.pending = 0
        elif c == "S" and len(line) > 1 and line[1] in "0123456789":
            self.nominal = int(line[1])
        elif c == "Y" and line[1:] in ("2", "5"):
            self.data = int(line[1:])
        elif c == "M":
            self.silent = line[1:] == "1"
        elif c == "V":
            self._out(VERSION + "\r")
        elif c == "E":
            self._out(f"CANable Error Register: {self.err:X}")          # no '\r', like the firmware
        elif c in "tTrRdDbB" and self.open:
            frame = S.decode_frame(line)
            if frame is None:
                return
            self.sent.append(line)
            with self.bus.lock:
                peers = [a for a in self.bus.adapters if a is not self and a.open
                         and a.nominal == self.nominal and (a.data == self.data or not frame.is_fd)]
                acked = any(not p.silent for p in peers)
                for p in peers:
                    p._out(line + "\r")                                    # same text format on RX
            if not acked:
                self.pending += 1
                if self.pending >= 64:
                    self.err |= 1 << S.ERR_FULLBUF_CANTX
