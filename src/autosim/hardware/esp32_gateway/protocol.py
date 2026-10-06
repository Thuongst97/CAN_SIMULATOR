"""Wire protocol between PC and ESP32 + MCP2518FD node (see docs/ESP32_PROTOCOL.md).

Packet on the wire:  COBS( type | seq | len_le16 | payload | crc16_le ) + 0x00
CRC16-CCITT-FALSE (poly 0x1021, init 0xFFFF) over type..payload.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from autosim.core.common.frame import CanFrame


class MsgType(IntEnum):
    # PC -> node
    HELLO = 0x01
    CONFIG = 0x02
    START = 0x03
    STOP = 0x04
    SET_NAME = 0x05
    TX_FRAME = 0x10
    TX_BATCH = 0x11
    PING = 0x20
    # node -> PC
    HELLO_ACK = 0x81
    ACK = 0x82
    NACK = 0x83
    RX_FRAME = 0x90
    TX_CONFIRM = 0x91
    STATUS = 0xA0
    PONG = 0xA1
    ERROR = 0xAF


class FrameFlags(IntEnum):
    EXTENDED = 0x01
    FD = 0x02
    BRS = 0x04
    ESI = 0x08
    RTR = 0x10


class ProtocolError(Exception):
    """Malformed packet (bad COBS, CRC mismatch, length error)."""


def crc16_ccitt(data: bytes, init: int = 0xFFFF) -> int:
    crc = init
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    out = bytearray([0])
    code_idx, code = 0, 1
    for b in data:
        if b == 0:
            out[code_idx] = code
            code_idx, code = len(out), 1
            out.append(0)
        else:
            out.append(b)
            code += 1
            if code == 0xFF:
                out[code_idx] = code
                code_idx, code = len(out), 1
                out.append(0)
    out[code_idx] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        code = data[i]
        if code == 0 or i + code > n:
            raise ProtocolError("bad COBS block")
        out += data[i + 1 : i + code]
        i += code
        if code < 0xFF and i < n:
            out.append(0)
    return bytes(out)


@dataclass(slots=True)
class Packet:
    type: MsgType
    seq: int
    payload: bytes = b""

    def encode(self) -> bytes:
        body = struct.pack("<BBH", self.type, self.seq & 0xFF, len(self.payload)) + self.payload
        body += struct.pack("<H", crc16_ccitt(body))
        return cobs_encode(body) + b"\x00"

    @classmethod
    def decode(cls, raw: bytes) -> "Packet":
        """`raw` is one delimiter-stripped COBS block."""
        body = cobs_decode(raw)
        if len(body) < 6:
            raise ProtocolError("packet too short")
        if crc16_ccitt(body[:-2]) != struct.unpack("<H", body[-2:])[0]:
            raise ProtocolError("CRC mismatch")
        mtype, seq, length = struct.unpack("<BBH", body[:4])
        payload = body[4:-2]
        if len(payload) != length:
            raise ProtocolError("length mismatch")
        return cls(MsgType(mtype), seq, payload)


# ---- CAN frame payload (TX_FRAME / RX_FRAME) -------------------------------
# u32 can_id | u8 flags | u8 dlc_len | u64 ts_us (RX only, 0 on TX) | data[len]

def encode_can_payload(f: CanFrame) -> bytes:
    flags = (
        (FrameFlags.EXTENDED if f.is_extended else 0)
        | (FrameFlags.FD if f.is_fd else 0)
        | (FrameFlags.BRS if f.bitrate_switch else 0)
    )
    return struct.pack("<IBBQ", f.arbitration_id, flags, len(f.data), 0) + bytes(f.data)


def decode_can_payload(p: bytes, channel: str) -> CanFrame:
    can_id, flags, n, ts_us = struct.unpack("<IBBQ", p[:14])
    return CanFrame(
        arbitration_id=can_id,
        data=bytes(p[14 : 14 + n]),
        is_extended=bool(flags & FrameFlags.EXTENDED),
        is_fd=bool(flags & FrameFlags.FD),
        bitrate_switch=bool(flags & FrameFlags.BRS),
        timestamp=ts_us / 1e6,
        channel=channel,
        is_rx=True,
    )


# ---- CONFIG / HELLO_ACK / STATUS payloads ----------------------------------
PROTO_VERSION = 1
CFG_FD, CFG_BRS, CFG_NON_ISO, CFG_LISTEN_ONLY, CFG_LOOPBACK, CFG_TX_CONFIRM = 1, 2, 4, 8, 16, 32


def encode_config(cfg) -> bytes:
    flags = ((CFG_FD if cfg.fd_enabled else 0) | (CFG_BRS if cfg.brs else 0)
             | (0 if cfg.iso_mode else CFG_NON_ISO) | (CFG_LISTEN_ONLY if cfg.listen_only else 0)
             | (CFG_LOOPBACK if cfg.extras.get("loopback") else 0)
             | (CFG_TX_CONFIRM if cfg.extras.get("tx_confirm") else 0))
    return struct.pack("<IIBBBB", cfg.nominal_bitrate, cfg.data_bitrate, flags,
                       round(cfg.sample_point_nominal * 100), round(cfg.sample_point_data * 100), 0)


def decode_config(p: bytes) -> dict:
    nom, data, flags, spn, spd, _ = struct.unpack("<IIBBBB", p[:12])
    return {"nominal": nom, "data": data, "flags": flags, "sp_nominal": spn, "sp_data": spd}


@dataclass(slots=True)
class NodeStatus:
    state: int = 0
    tec: int = 0
    rec: int = 0
    bus_flags: int = 0       # b0 error-passive, b1 bus-off
    rx_count: int = 0
    tx_count: int = 0
    err_count: int = 0
    dropped: int = 0

    _FMT = "<BBBBIIII"

    def encode(self) -> bytes:
        return struct.pack(self._FMT, self.state, self.tec, self.rec, self.bus_flags,
                           self.rx_count, self.tx_count, self.err_count, self.dropped)

    @classmethod
    def decode(cls, p: bytes) -> "NodeStatus":
        return cls(*struct.unpack(cls._FMT, p[:struct.calcsize(cls._FMT)]))


def encode_hello_ack(fw_major: int, fw_minor: int, caps: int, node_id: str) -> bytes:
    return struct.pack("<BBBB16s", PROTO_VERSION, fw_major, fw_minor, caps, node_id.encode()[:16])


def decode_hello_ack(p: bytes) -> dict:
    ver, maj, mnr, caps, nid = struct.unpack("<BBBB16s", p[:20])
    return {"proto": ver, "fw": f"{maj}.{mnr}", "caps": caps, "node_id": nid.rstrip(b"\0").decode(errors="replace")}


# ---- error codes (NACK / ERROR payloads), config validation, node names -----------------------
ERR_BAD_CRC, ERR_BAD_LEN, ERR_UNSUPPORTED, ERR_BAD_CONFIG, ERR_BUSY, ERR_MCP_FAULT, ERR_NOT_STARTED, ERR_STORAGE = range(1, 9)
ERROR_NAMES = {
    ERR_BAD_CRC: "bad CRC", ERR_BAD_LEN: "malformed frame / length", ERR_UNSUPPORTED: "unsupported",
    ERR_BAD_CONFIG: "bad configuration", ERR_BUSY: "busy / TX queue full", ERR_MCP_FAULT: "MCP2518FD fault",
    ERR_NOT_STARTED: "not started", ERR_STORAGE: "could not store the name in flash",
}


def validate_config(cfg) -> int:
    """Same rules as the firmware (validateConfig). Returns the data/nominal factor (1 for classic CAN)
    or raises ValueError with a readable reason. The MCP2518FD needs an integer factor of 1..10."""
    if not 10_000 <= cfg.nominal_bitrate <= 1_000_000:
        raise ValueError("nominal bit rate must be 10 kbit/s .. 1 Mbit/s")
    if not cfg.fd_enabled:
        return 1
    if cfg.data_bitrate < cfg.nominal_bitrate:
        raise ValueError("data bit rate must be >= nominal bit rate")
    if cfg.data_bitrate % cfg.nominal_bitrate:
        raise ValueError("data rate must be an integer multiple of the nominal rate")
    factor = cfg.data_bitrate // cfg.nominal_bitrate
    if factor > 10:
        raise ValueError(f"data/nominal ratio must be an integer from 1 to 10 "
                         f"(got {factor}: e.g. use 1M nominal for 8M data, or 500k/5M)")
    return factor


def valid_node_name(name: str) -> bool:
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", name))
