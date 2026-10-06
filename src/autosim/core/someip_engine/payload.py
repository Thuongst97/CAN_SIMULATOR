"""Payload codecs: raw hex, SOME/IP primitives (big endian) and Protobuf.

Spec strings (used in ServiceDef method/event types and by the UI):
    ""                       raw bytes (hex text)
    "prim:u8,u16,f32,string" primitive list; values are given as a comma separated list
    "proto:pkg.Message"      protobuf message; values are a dict
"""
from __future__ import annotations

import json
import struct
from abc import ABC, abstractmethod

from autosim.core.someip_engine.proto_loader import ProtoRegistry


class PayloadError(ValueError):
    pass


class PayloadCodec(ABC):
    @abstractmethod
    def encode(self, values) -> bytes: ...

    @abstractmethod
    def decode(self, data: bytes): ...


def hex_to_bytes(text: str) -> bytes:
    cleaned = text.replace("0x", " ").replace(",", " ").replace("-", " ")
    try:
        return bytes.fromhex("".join(cleaned.split()))
    except ValueError as e:
        raise PayloadError(f"invalid hex: {e}") from e


class RawCodec(PayloadCodec):
    def encode(self, values) -> bytes:
        return values if isinstance(values, (bytes, bytearray)) else hex_to_bytes(str(values))

    def decode(self, data: bytes) -> str:
        return data.hex(" ").upper()


_FMT = {"u8": ">B", "i8": ">b", "u16": ">H", "i16": ">h", "u32": ">I", "i32": ">i",
        "u64": ">Q", "i64": ">q", "f32": ">f", "f64": ">d", "bool": ">B"}


class PrimitiveCodec(PayloadCodec):
    """Sequence of fixed-size primitives plus length-prefixed strings (u32 length + UTF-8,
    big endian, as in SOME/IP dynamic-length strings without BOM/terminator)."""

    def __init__(self, types: list[str]) -> None:
        bad = [t for t in types if t not in _FMT and t != "string"]
        if bad:
            raise PayloadError(f"unknown primitive type(s): {', '.join(bad)}")
        self.types = types

    def encode(self, values) -> bytes:
        if isinstance(values, str):
            values = [v.strip() for v in values.split(",")] if values.strip() else []
        if len(values) != len(self.types):
            raise PayloadError(f"expected {len(self.types)} value(s), got {len(values)}")
        out = b""
        for t, v in zip(self.types, values):
            try:
                if t == "string":
                    b = str(v).encode("utf-8")
                    out += struct.pack(">I", len(b)) + b
                elif t == "bool":
                    out += struct.pack(">B", 1 if str(v).lower() in ("1", "true", "yes") else 0)
                elif t.startswith("f"):
                    out += struct.pack(_FMT[t], float(v))
                else:
                    out += struct.pack(_FMT[t], int(str(v), 0))
            except (struct.error, ValueError) as e:
                raise PayloadError(f"cannot encode {v!r} as {t}: {e}") from e
        return out

    def decode(self, data: bytes) -> list:
        out, i = [], 0
        try:
            for t in self.types:
                if t == "string":
                    n = struct.unpack_from(">I", data, i)[0]
                    out.append(data[i + 4:i + 4 + n].decode("utf-8", "replace"))
                    i += 4 + n
                else:
                    v = struct.unpack_from(_FMT[t], data, i)[0]
                    out.append(bool(v) if t == "bool" else v)
                    i += struct.calcsize(_FMT[t])
        except struct.error as e:
            raise PayloadError(f"payload too short for {self.types}: {e}") from e
        return out


class ProtobufCodec(PayloadCodec):
    def __init__(self, registry: ProtoRegistry, message: str) -> None:
        self.registry, self.message = registry, message

    def encode(self, values) -> bytes:
        if isinstance(values, str):
            try:
                values = json.loads(values or "{}")
            except json.JSONDecodeError as e:
                raise PayloadError(f"invalid JSON: {e}") from e
        return self.registry.encode(self.message, values)

    def decode(self, data: bytes) -> dict:
        return self.registry.decode(self.message, data)


def make_codec(spec: str, registry: ProtoRegistry | None = None) -> PayloadCodec:
    spec = (spec or "").strip()
    if not spec:
        return RawCodec()
    kind, _, rest = spec.partition(":")
    if kind == "prim":
        return PrimitiveCodec([t.strip() for t in rest.split(",") if t.strip()])
    if kind == "proto":
        if registry is None:
            raise PayloadError("protobuf codec needs a ProtoRegistry")
        return ProtobufCodec(registry, rest.strip())
    raise PayloadError(f"unknown payload spec {spec!r}")
