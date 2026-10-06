"""Compile .proto files at runtime and expose message descriptors for the dynamic UI."""
from __future__ import annotations

import tempfile
from pathlib import Path

from google.protobuf import descriptor_pb2, descriptor_pool, json_format, message_factory
from google.protobuf.descriptor import FieldDescriptor as FD

_KIND = {
    FD.TYPE_DOUBLE: "float", FD.TYPE_FLOAT: "float",
    FD.TYPE_INT64: "int", FD.TYPE_UINT64: "int", FD.TYPE_INT32: "int", FD.TYPE_FIXED64: "int",
    FD.TYPE_FIXED32: "int", FD.TYPE_UINT32: "int", FD.TYPE_SFIXED32: "int", FD.TYPE_SFIXED64: "int",
    FD.TYPE_SINT32: "int", FD.TYPE_SINT64: "int",
    FD.TYPE_BOOL: "bool", FD.TYPE_STRING: "string", FD.TYPE_BYTES: "bytes",
    FD.TYPE_ENUM: "enum", FD.TYPE_MESSAGE: "message", FD.TYPE_GROUP: "message",
}


def _is_repeated(f) -> bool:
    r = getattr(f, "is_repeated", None)       # protobuf >= 6; `label` was removed in 7
    return bool(r) if r is not None else f.label == FD.LABEL_REPEATED


class ProtoError(Exception):
    pass


class ProtoRegistry:
    """load(path) compiles a .proto with grpc_tools.protoc into a FileDescriptorSet and loads it
    into a private descriptor pool (no generated modules, no sys.path tricks)."""

    def __init__(self) -> None:
        self._pool = descriptor_pool.DescriptorPool()
        self._messages: dict[str, object] = {}
        self.paths: list[str] = []

    def load(self, path: str | Path) -> list[str]:
        """-> full names of the top-level messages defined in the file."""
        from grpc_tools import protoc
        import importlib.resources as res
        path = Path(path).resolve()
        wkt = str(res.files("grpc_tools") / "_proto")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "set.pb"
            rc = protoc.main(["protoc", f"-I{path.parent}", f"-I{wkt}", "--include_imports",
                              f"--descriptor_set_out={out}", str(path)])
            if rc != 0 or not out.exists():
                raise ProtoError(f"protoc failed for {path.name} (see console for details)")
            fds = descriptor_pb2.FileDescriptorSet.FromString(out.read_bytes())
        names = []
        for fdp in fds.file:
            try:
                self._pool.Add(fdp)
            except TypeError:
                self._pool.AddSerializedFile(fdp.SerializeToString())
        target = path.name
        for fdp in fds.file:
            fd = self._pool.FindFileByName(fdp.name)
            for name, md in fd.message_types_by_name.items():
                self._messages[md.full_name] = md
                if fdp.name == target:
                    names.append(md.full_name)
        self.paths.append(str(path))
        return names

    def messages(self) -> list[str]:
        return sorted(self._messages)

    def _cls(self, name: str):
        md = self._messages.get(name)
        if md is None:
            raise ProtoError(f"unknown protobuf message {name!r}")
        return message_factory.GetMessageClass(md)

    def encode(self, message: str, values: dict) -> bytes:
        try:
            return json_format.ParseDict(values, self._cls(message)()).SerializeToString()
        except json_format.ParseError as e:
            raise ProtoError(str(e)) from e

    def decode(self, message: str, data: bytes) -> dict:
        msg = self._cls(message)()
        try:
            msg.ParseFromString(data)
        except Exception as e:
            raise ProtoError(f"cannot parse as {message}: {e}") from e
        try:
            return json_format.MessageToDict(msg, always_print_fields_with_no_presence=True,
                                             preserving_proto_field_name=True)
        except TypeError:   # older protobuf
            return json_format.MessageToDict(msg, including_default_value_fields=True,
                                             preserving_proto_field_name=True)

    def field_schema(self, message: str, _depth: int = 0) -> list[dict]:
        """[{name, kind(int|float|bool|string|bytes|enum|message), repeated, enum_values, fields}]"""
        md = self._messages.get(message)
        if md is None:
            raise ProtoError(f"unknown protobuf message {message!r}")
        out = []
        for f in md.fields:
            kind = _KIND.get(f.type, "string")
            item = {"name": f.name, "kind": kind, "repeated": _is_repeated(f),
                    "enum_values": [], "fields": []}
            if kind == "enum":
                item["enum_values"] = [v.name for v in f.enum_type.values]
            elif kind == "message" and f.message_type.GetOptions().map_entry:
                item["kind"], item["repeated"] = "string", False   # map: edit as JSON text
                item["json"] = True
            elif kind == "message" and _depth < 6:
                item["fields"] = self.field_schema(f.message_type.full_name, _depth + 1)
            out.append(item)
        return out
