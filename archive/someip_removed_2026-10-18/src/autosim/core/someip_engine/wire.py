"""SOME/IP header and SOME/IP-SD message (de)serialisation (IPv4, UDP)."""
from __future__ import annotations

import socket
import struct
from dataclasses import dataclass, field
from enum import IntEnum


class MsgType(IntEnum):
    REQUEST = 0x00
    REQUEST_NO_RETURN = 0x01
    NOTIFICATION = 0x02
    RESPONSE = 0x80
    ERROR = 0x81


class ReturnCode(IntEnum):
    OK = 0x00
    NOT_OK = 0x01
    UNKNOWN_SERVICE = 0x02
    UNKNOWN_METHOD = 0x03
    NOT_READY = 0x04
    NOT_REACHABLE = 0x05
    TIMEOUT = 0x06
    WRONG_PROTOCOL_VERSION = 0x07
    WRONG_INTERFACE_VERSION = 0x08
    MALFORMED_MESSAGE = 0x09


SD_SERVICE_ID = 0xFFFF
SD_METHOD_ID = 0x8100
PROTO_VER = 0x01
UDP, TCP = 0x11, 0x06
ANY_INSTANCE = 0xFFFF

_HDR = struct.Struct(">HHIHHBBBB")
HEADER_LEN = _HDR.size   # 16


class WireError(ValueError):
    pass


@dataclass
class SomeIpMessage:
    service_id: int
    method_id: int
    client_id: int = 0
    session_id: int = 1
    msg_type: int = MsgType.REQUEST
    return_code: int = ReturnCode.OK
    payload: bytes = b""
    iface_ver: int = 1
    proto_ver: int = PROTO_VER

    def pack(self) -> bytes:
        return _HDR.pack(self.service_id, self.method_id, 8 + len(self.payload), self.client_id,
                         self.session_id, self.proto_ver, self.iface_ver, self.msg_type,
                         self.return_code) + self.payload

    @classmethod
    def unpack(cls, data: bytes) -> "SomeIpMessage":
        if len(data) < HEADER_LEN:
            raise WireError("short SOME/IP message")
        sid, mid, length, cid, sess, pv, iv, mt, rc = _HDR.unpack_from(data)
        if length < 8 or length - 8 > len(data) - HEADER_LEN:
            raise WireError("bad SOME/IP length")
        return cls(sid, mid, cid, sess, mt, rc, bytes(data[HEADER_LEN:HEADER_LEN + length - 8]), iv, pv)


# --------------------------------------------------------------------- SD
class SdType(IntEnum):
    FIND = 0x00
    OFFER = 0x01
    SUBSCRIBE = 0x06
    SUBSCRIBE_ACK = 0x07


@dataclass(frozen=True)
class IPv4Option:
    ip: str
    port: int
    proto: int = UDP
    kind: int = 0x04          # 0x04 unicast, 0x14 multicast, 0x24 SD endpoint


@dataclass
class SdEntry:
    type: int
    service_id: int
    instance_id: int
    major: int = 1
    ttl: int = 3
    minor: int = 0                    # service entries
    eventgroup_id: int = 0            # eventgroup entries
    counter: int = 0
    options: list[IPv4Option] = field(default_factory=list)

    @property
    def is_eventgroup(self) -> bool:
        return self.type in (SdType.SUBSCRIBE, SdType.SUBSCRIBE_ACK)

    def describe(self) -> str:
        try:
            t = SdType(self.type).name
        except ValueError:
            t = f"0x{self.type:02X}"
        s = f"{t} svc=0x{self.service_id:04X} inst=0x{self.instance_id:04X} v{self.major} ttl={self.ttl}"
        if self.is_eventgroup:
            s += f" eg=0x{self.eventgroup_id:04X}"
        if self.options:
            s += " " + ",".join(f"{o.ip}:{o.port}/{'udp' if o.proto == UDP else 'tcp'}" for o in self.options)
        return s


_ENTRY = struct.Struct(">BBBBHHII")
_OPT = struct.Struct(">HBB4sBBH")


def pack_sd(entries: list[SdEntry], session_id: int, flags: int = 0xC0) -> bytes:
    opts_blob = b""
    entries_blob = b""
    n_opts = 0
    for e in entries:
        idx = n_opts
        for o in e.options:
            opts_blob += _OPT.pack(9, o.kind, 0, socket.inet_aton(o.ip), 0, o.proto, o.port)
            n_opts += 1
        last = ((e.counter & 0xF) << 16 | e.eventgroup_id) if e.is_eventgroup else e.minor
        entries_blob += _ENTRY.pack(e.type, idx if e.options else 0, 0, len(e.options) << 4,
                                    e.service_id, e.instance_id, (e.major << 24) | (e.ttl & 0xFFFFFF), last)
    body = struct.pack(">B3xI", flags, len(entries_blob)) + entries_blob + struct.pack(">I", len(opts_blob)) + opts_blob
    return SomeIpMessage(SD_SERVICE_ID, SD_METHOD_ID, 0, session_id, MsgType.NOTIFICATION, 0, body).pack()


def is_sd(msg: SomeIpMessage) -> bool:
    return msg.service_id == SD_SERVICE_ID and msg.method_id == SD_METHOD_ID


def unpack_sd(msg: SomeIpMessage) -> list[SdEntry]:
    p = msg.payload
    try:
        elen = struct.unpack_from(">I", p, 4)[0]
        entries_raw = p[8:8 + elen]
        olen = struct.unpack_from(">I", p, 8 + elen)[0]
        opts_raw = p[12 + elen:12 + elen + olen]
        if len(entries_raw) != elen or len(opts_raw) != olen:
            raise WireError("truncated SD message")
        options: list[IPv4Option | None] = []
        i = 0
        while i + 4 <= len(opts_raw):
            length, kind, _ = struct.unpack_from(">HBB", opts_raw, i)
            body = opts_raw[i + 4:i + 3 + length]
            if kind in (0x04, 0x14, 0x24) and length == 9:
                port = struct.unpack(">H", body[6:8])[0]
                options.append(IPv4Option(socket.inet_ntoa(body[0:4]), port, body[5], kind))
            else:
                options.append(None)
            i += 3 + length
        entries = []
        for j in range(0, len(entries_raw) - _ENTRY.size + 1, _ENTRY.size):
            t, i1, i2, nn, sid, iid, mt, last = _ENTRY.unpack_from(entries_raw, j)
            opts = [o for o in options[i1:i1 + (nn >> 4)] + options[i2:i2 + (nn & 0xF)] if o]
            e = SdEntry(t, sid, iid, mt >> 24, mt & 0xFFFFFF, options=opts)
            if e.is_eventgroup:
                e.eventgroup_id, e.counter = last & 0xFFFF, (last >> 16) & 0xF
            else:
                e.minor = last
            entries.append(e)
        return entries
    except struct.error as ex:
        raise WireError(f"malformed SD message: {ex}") from ex
