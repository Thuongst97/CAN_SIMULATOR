"""AutoSim Bridge Protocol (ABP v1): CAN / CAN FD frames over a TCP stream.

Spoken between AutoSim Studio (client) and ``autosim-bridge`` (server), the daemon that runs inside the Android
VM next to a SocketCAN interface (normally ``vcan0`` in place of the real ``can0``). The C implementation is
``bridge/autosim-bridge/abp.h``; both sides must stay byte-compatible (see docs/ANDROID_BRIDGE.md).

Every packet::

    offset size  field
    0      1     magic 0xAB
    1      1     type (T_*)
    2      2     payload length, little-endian (<= MAX_PAYLOAD)
    4      n     payload

FRAME payload (bridge -> AutoSim: a frame seen on the interface; AutoSim -> bridge: a frame to transmit)::

    0  4  CAN id (11 or 29 bit), little-endian
    4  1  flags (F_*)
    5  1  data length in bytes (0..8 classic, 0..64 FD: 0-8,12,16,20,24,32,48,64)
    6  2  reserved (0)
    8  8  timestamp in microseconds (sender's clock; 0 = unknown)
    16 n  data
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from autosim.core.common.frame import CanFrame

MAGIC = 0xAB
VERSION = 1
DEFAULT_PORT = 29536
MAX_PAYLOAD = 1024

T_HELLO = 0x01        # both directions: version, capabilities, interface name, software version
T_FRAME = 0x02
T_PING = 0x03         # payload echoed back in a PONG
T_PONG = 0x04
T_STATUS = 0x05       # bridge -> AutoSim, about once a second
T_ERROR = 0x06        # bridge -> AutoSim: u16 code + utf-8 text

F_EXT, F_FD, F_BRS, F_ESI, F_RTR = 0x01, 0x02, 0x04, 0x08, 0x10

CAP_FD = 0x01         # the interface accepts CAN FD frames (MTU 72)

# error codes sent by the bridge (T_ERROR)
E_CAN_WRITE = 1       # write() to the CAN socket failed (text has errno)
E_CAN_DOWN = 2        # interface missing / down; the bridge keeps retrying
E_BAD_FRAME = 3       # a FRAME from AutoSim was invalid (length, FD on a classic interface, ...)
E_PROTOCOL = 4        # unknown packet / bad magic: the bridge drops the connection

FD_LENGTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)

_HDR = struct.Struct("<BBH")
_FRAME = struct.Struct("<IBBHQ")
_HELLO = struct.Struct("<HH")
_STATUS = struct.Struct("<IIIIB")


class ProtocolError(Exception):
    """The byte stream is not ABP (bad magic, oversized packet, malformed payload)."""


def fd_length(n: int) -> int:
    """Smallest valid CAN FD payload length >= n."""
    for v in FD_LENGTHS:
        if v >= n:
            return v
    raise ProtocolError(f"CAN FD payload of {n} bytes is longer than 64")


def packet(ptype: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError(f"payload too long ({len(payload)})")
    return _HDR.pack(MAGIC, ptype, len(payload)) + payload


# ---- FRAME ------------------------------------------------------------------------------------------------
def encode_frame(frame: CanFrame, timestamp_us: int = 0) -> bytes:
    data = bytes(frame.data)
    if frame.is_fd:
        if len(data) not in FD_LENGTHS:
            data = data + bytes(fd_length(len(data)) - len(data))
    elif len(data) > 8:
        raise ProtocolError(f"classic CAN frame 0x{frame.arbitration_id:X} has {len(data)} bytes (max 8); "
                            f"enable FD")
    limit = 0x1FFFFFFF if frame.is_extended else 0x7FF
    if not 0 <= frame.arbitration_id <= limit:
        raise ProtocolError(f"CAN id 0x{frame.arbitration_id:X} does not fit "
                            f"{'29' if frame.is_extended else '11'} bits")
    flags = (F_EXT if frame.is_extended else 0) | (F_FD if frame.is_fd else 0) \
        | (F_BRS if frame.is_fd and frame.bitrate_switch else 0)
    return packet(T_FRAME, _FRAME.pack(frame.arbitration_id, flags, len(data), 0, timestamp_us) + data)


def decode_frame(payload: bytes) -> tuple[CanFrame, int, int]:
    """-> (frame, flags, timestamp_us)."""
    if len(payload) < _FRAME.size:
        raise ProtocolError("short FRAME packet")
    can_id, flags, n, _res, ts = _FRAME.unpack_from(payload)
    data = payload[_FRAME.size:]
    if len(data) != n:
        raise ProtocolError(f"FRAME length field {n} != {len(data)} data bytes")
    fd = bool(flags & F_FD)
    if (fd and n not in FD_LENGTHS) or (not fd and n > 8):
        raise ProtocolError(f"invalid {'FD' if fd else 'classic'} data length {n}")
    ext = bool(flags & F_EXT)
    return CanFrame(can_id & (0x1FFFFFFF if ext else 0x7FF), bytes(data), ext, fd,
                    fd and bool(flags & F_BRS)), flags, ts


# ---- HELLO / STATUS / ERROR / PING ------------------------------------------------------------------------
def _short_str(s: str) -> bytes:
    b = s.encode("utf-8")[:255]
    return bytes([len(b)]) + b


def encode_hello(name: str, software: str, caps: int = 0, version: int = VERSION) -> bytes:
    """name: CAN interface (bridge) or client name (AutoSim)."""
    return packet(T_HELLO, _HELLO.pack(version, caps) + _short_str(name) + _short_str(software))


def decode_hello(payload: bytes) -> dict:
    try:
        version, caps = _HELLO.unpack_from(payload)
        i = _HELLO.size
        n = payload[i]
        name = payload[i + 1:i + 1 + n].decode("utf-8", "replace")
        i += 1 + n
        m = payload[i]
        sw = payload[i + 1:i + 1 + m].decode("utf-8", "replace")
    except (struct.error, IndexError) as e:
        raise ProtocolError("malformed HELLO") from e
    return {"version": version, "caps": caps, "name": name, "software": sw}


@dataclass
class BridgeStatus:
    rx: int = 0           # frames read from the CAN interface
    tx: int = 0           # frames written to the CAN interface
    tx_errors: int = 0    # failed writes
    dropped: int = 0      # frames read while no client was connected / could not be forwarded
    can_up: bool = False


def encode_status(st: BridgeStatus) -> bytes:
    return packet(T_STATUS, _STATUS.pack(st.rx, st.tx, st.tx_errors, st.dropped, 1 if st.can_up else 0))


def decode_status(payload: bytes) -> BridgeStatus:
    if len(payload) < _STATUS.size:
        raise ProtocolError("short STATUS packet")
    rx, tx, err, drop, up = _STATUS.unpack_from(payload)
    return BridgeStatus(rx, tx, err, drop, bool(up))


def encode_error(code: int, text: str) -> bytes:
    return packet(T_ERROR, struct.pack("<H", code) + text.encode("utf-8")[:MAX_PAYLOAD - 2])


def decode_error(payload: bytes) -> tuple[int, str]:
    if len(payload) < 2:
        raise ProtocolError("short ERROR packet")
    return struct.unpack_from("<H", payload)[0], payload[2:].decode("utf-8", "replace")


# ---- stream parser ----------------------------------------------------------------------------------------
class PacketParser:
    """Splits a TCP byte stream into (type, payload) packets; raises ProtocolError on garbage
    (the stream cannot be re-synchronised reliably, the caller drops the connection)."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self._buf += data
        out = []
        while len(self._buf) >= _HDR.size:
            magic, ptype, n = _HDR.unpack_from(self._buf)
            if magic != MAGIC:
                raise ProtocolError(f"bad magic 0x{magic:02X}")
            if n > MAX_PAYLOAD:
                raise ProtocolError(f"packet too long ({n})")
            if len(self._buf) < _HDR.size + n:
                break
            out.append((ptype, bytes(self._buf[_HDR.size:_HDR.size + n])))
            del self._buf[:_HDR.size + n]
        return out
