"""Software model of the PEAK PCAN-Basic library with PCAN adapters on one CAN wire.

`FakePeak.install(monkeypatch)` replaces the PCAN-Basic library that python-can's `pcan` interface loads, so the
real `PcanBus` code (initialisation strings, message structures, polling, status) runs against this model:

* every `FakeAdapter` is one PCAN channel (PCAN_USBBUS1, …) with a device name and FD capability;
* frames written by an open channel reach every other open channel on the wire whose bit rates match; a
  channel with other rates sees an error frame instead, and the sender gets no acknowledgement;
* unacknowledged frames raise the transmit error counter: error passive after 8, bus-off after 32 (the queue then
  reports PCAN_ERROR_QXMTFULL); an acknowledged frame resets it;
* `unplug()` makes every call on that channel return PCAN_ERROR_ILLHW, like a real adapter pulled out;
* `FakePeak(driver=False)` behaves as if the PEAK driver were not installed (the library cannot be loaded).
"""
from __future__ import annotations

import threading
from collections import deque

from can.interfaces.pcan import basic as B
from can.util import dlc2len

API_VERSION = b"4.8.0.123"


def _v(x) -> int:
    return x.value if hasattr(x, "value") else int(x)


def classic_rate(btr: int) -> int:
    btr0, btr1 = btr >> 8, btr & 0xFF
    brp = (btr0 & 0x3F) + 1
    tseg1, tseg2 = (btr1 & 0x0F) + 1, ((btr1 >> 4) & 0x07) + 1
    return 8_000_000 // (brp * (1 + tseg1 + tseg2))


def parse_fd(text: bytes) -> dict:
    d = dict(kv.strip().split("=") for kv in text.decode().split(","))
    d = {k: int(v) for k, v in d.items()}
    clock = d.get("f_clock") or d["f_clock_mhz"] * 1_000_000
    nominal = clock // (d["nom_brp"] * (1 + d["nom_tseg1"] + d["nom_tseg2"]))
    data = clock // (d["data_brp"] * (1 + d["data_tseg1"] + d["data_tseg2"]))
    return {"clock": clock, "nominal": nominal, "data": data, **d}


class FakeAdapter:
    def __init__(self, peak: FakePeak, channel: str, name: str, fd: bool, device_id: int) -> None:
        self.peak, self.channel, self.name, self.fd, self.device_id = peak, channel, name, fd, device_id
        self.handle = _v(B.PCAN_CHANNEL_NAMES[channel])
        self.open = False
        self.fd_mode = False
        self.nominal = self.data = 0
        self.fd_params: dict = {}
        self.rx: deque = deque()
        self.sent: list[tuple[int, bytes, bool, bool]] = []      # (id, data, fd, brs) put on the wire
        self.tec = 0
        self.plugged = True
        self.occupied = False                                     # opened by another application
        self.init_calls: list = []

    def unplug(self) -> None:
        self.plugged = False

    def status(self) -> int:
        if not self.plugged:
            return _v(B.PCAN_ERROR_ILLHW)
        if self.tec >= 32:
            return _v(B.PCAN_ERROR_BUSOFF) | _v(B.PCAN_ERROR_QXMTFULL)
        if self.tec >= 8:
            return _v(B.PCAN_ERROR_BUSPASSIVE)
        return _v(B.PCAN_ERROR_OK)

    def accepts(self, nominal: int, data: int, fd: bool, brs: bool) -> bool:
        if not self.open or self.nominal != nominal:
            return False
        if fd and not self.fd_mode:
            return False
        return not (fd and brs and self.data != data)


class FakePeak:
    def __init__(self, driver: bool = True) -> None:
        self.driver = driver
        self.adapters: dict[int, FakeAdapter] = {}
        self.lock = threading.Lock()

    def add(self, channel: str = "PCAN_USBBUS1", name: str = "PCAN-USB FD", fd: bool = True,
            device_id: int = 0xFF) -> FakeAdapter:
        a = FakeAdapter(self, channel, name, fd, device_id)
        self.adapters[a.handle] = a
        return a

    def install(self, monkeypatch) -> FakePeak:
        from can.interfaces.pcan import pcan as pc
        monkeypatch.setattr(pc, "PCANBasic", self.library())
        monkeypatch.setattr(pc, "HAS_EVENTS", False)         # polling: no OS event objects needed
        return self

    # ---- the library class -------------------------------------------------------------------------
    def library(self):
        peak = self

        class FakePCANBasic:
            def __init__(self) -> None:
                if not peak.driver:
                    raise OSError("The PCAN-Basic API could not be loaded. (PCANBasic.dll)")

            # -- helpers
            def _a(self, h) -> FakeAdapter | None:
                return peak.adapters.get(_v(h))

            # -- API
            def Initialize(self, h, btr, hwtype=0, ioport=0, interrupt=0):
                a = self._a(h)
                if a is None or not a.plugged:
                    return B.PCAN_ERROR_ILLHW
                if a.occupied:
                    return B.PCAN_ERROR_HWINUSE
                a.init_calls.append(("classic", _v(btr)))
                a.open, a.fd_mode, a.nominal, a.data, a.tec = True, False, classic_rate(_v(btr)), 0, 0
                return B.PCAN_ERROR_OK

            def InitializeFD(self, h, bitrate):
                a = self._a(h)
                if a is None or not a.plugged:
                    return B.PCAN_ERROR_ILLHW
                if not a.fd:
                    return B.PCAN_ERROR_ILLOPERATION
                if a.occupied:
                    return B.PCAN_ERROR_HWINUSE
                p = parse_fd(bytes(bitrate))
                a.init_calls.append(("fd", bytes(bitrate)))
                a.open, a.fd_mode, a.nominal, a.data, a.fd_params, a.tec = True, True, p["nominal"], p["data"], p, 0
                return B.PCAN_ERROR_OK

            def Uninitialize(self, h):
                a = self._a(h)
                if a is not None:
                    a.open = False
                    a.rx.clear()
                return B.PCAN_ERROR_OK

            def Reset(self, h):
                a = self._a(h)
                if a is not None:
                    a.rx.clear()
                    a.tec = 0
                return B.PCAN_ERROR_OK

            def GetStatus(self, h):
                a = self._a(h)
                return B.PCAN_ERROR_ILLHW if a is None else a.status()

            def GetErrorText(self, error, language=0):
                names = {v: k for k, v in vars(B).items() if k.startswith("PCAN_ERROR_") and isinstance(v, int)}
                return B.PCAN_ERROR_OK, names.get(int(error), f"error {int(error):#x}").encode()

            def SetValue(self, h, param, value):
                return B.PCAN_ERROR_OK

            def GetValue(self, h, param):
                p = _v(param)
                if p == _v(B.PCAN_API_VERSION):
                    return B.PCAN_ERROR_OK, API_VERSION
                if p == _v(B.PCAN_ATTACHED_CHANNELS):
                    infos = []
                    for a in peak.adapters.values():
                        if not a.plugged:
                            continue
                        ci = B.TPCANChannelInformation()
                        ci.channel_handle = a.handle
                        ci.device_type = 0x05
                        ci.controller_number = 0
                        ci.device_features = B.FEATURE_FD_CAPABLE if a.fd else 0
                        ci.device_name = a.name.encode()
                        ci.device_id = a.device_id
                        ci.channel_condition = (B.PCAN_CHANNEL_OCCUPIED if a.occupied
                                                else B.PCAN_CHANNEL_AVAILABLE)
                        infos.append(ci)
                    return B.PCAN_ERROR_OK, infos
                if p == _v(B.PCAN_DEVICE_NUMBER):
                    a = self._a(h)
                    return (B.PCAN_ERROR_OK, a.device_id) if a else (B.PCAN_ERROR_ILLHW, 0)
                return B.PCAN_ERROR_OK, 0

            def _read(self, h, fd: bool):
                a = self._a(h)
                empty = (B.TPCANMsgFD() if fd else B.TPCANMsg()), (B.TPCANTimestampFD(0) if fd
                                                                   else B.TPCANTimestamp())
                if a is None or not a.plugged:
                    return (B.PCAN_ERROR_ILLHW, *empty)
                with peak.lock:
                    if not a.rx:
                        return (B.PCAN_ERROR_QRCVEMPTY, *empty)
                    can_id, data, ext, is_fd, brs, err = a.rx.popleft()
                if fd:
                    m = B.TPCANMsgFD()
                    from can.util import len2dlc
                    m.DLC = len2dlc(len(data))
                    ts = B.TPCANTimestampFD(1000)
                else:
                    m = B.TPCANMsg()
                    m.LEN = len(data)
                    ts = B.TPCANTimestamp()
                m.ID = can_id
                t = _v(B.PCAN_MESSAGE_EXTENDED) if ext else _v(B.PCAN_MESSAGE_STANDARD)
                if is_fd:
                    t |= _v(B.PCAN_MESSAGE_FD)
                if brs:
                    t |= _v(B.PCAN_MESSAGE_BRS)
                if err:
                    t |= _v(B.PCAN_MESSAGE_ERRFRAME)
                m.MSGTYPE = t
                for i, b in enumerate(data):
                    m.DATA[i] = b
                return B.PCAN_ERROR_OK, m, ts

            def Read(self, h):
                return self._read(h, False)

            def ReadFD(self, h):
                return self._read(h, True)

            def _write(self, h, m, fd: bool):
                a = self._a(h)
                if a is None or not a.plugged:
                    return B.PCAN_ERROR_ILLHW
                if not a.open:
                    return B.PCAN_ERROR_INITIALIZE
                if a.tec >= 32:
                    return B.PCAN_ERROR_QXMTFULL
                t = _v(m.MSGTYPE)
                is_fd = bool(t & _v(B.PCAN_MESSAGE_FD))
                brs = bool(t & _v(B.PCAN_MESSAGE_BRS))
                ext = bool(t & _v(B.PCAN_MESSAGE_EXTENDED))
                n = dlc2len(m.DLC) if fd else m.LEN
                data = bytes(m.DATA[:n])
                acked = False
                with peak.lock:
                    a.sent.append((m.ID, data, is_fd, brs))
                    for other in peak.adapters.values():
                        if other is a or not other.open or not other.plugged:
                            continue
                        if other.accepts(a.nominal, a.data, is_fd, brs):
                            other.rx.append((m.ID, data, ext, is_fd, brs, False))
                            acked = True
                        else:
                            other.rx.append((0, bytes(8), False, False, False, True))     # error frame
                a.tec = 0 if acked else a.tec + 1
                return B.PCAN_ERROR_OK

            def Write(self, h, m):
                return self._write(h, m, False)

            def WriteFD(self, h, m):
                return self._write(h, m, True)

        return FakePCANBasic
