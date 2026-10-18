"""Persistent application/project settings (JSON on disk)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class NodeSetting:
    port: str
    channel_name: str
    nominal_bitrate: int = 500_000
    data_bitrate: int = 2_000_000
    fd: bool = True
    brs: bool = True
    iso: bool = True
    loopback: bool = False         # MCP2518FD internal loopback (bench test without a bus)
    autoconnect: bool = False      # was connected when the project was saved: reconnect on open


@dataclass
class ProjectConfig:
    dbc_paths: list[str] = field(default_factory=list)
    nodes: list[NodeSetting] = field(default_factory=list)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "ProjectConfig":
        d = json.loads(path.read_text(encoding="utf-8"))

        def pick(klass, data: dict):
            names = {f.name for f in fields(klass)}      # ignore unknown keys (newer files)
            return klass(**{k: v for k, v in data.items() if k in names})

        # keys of older project files (e.g. the removed SOME/IP settings) are ignored
        return cls(dbc_paths=list(d.get("dbc_paths", [])),
                   nodes=[pick(NodeSetting, n) for n in d.get("nodes", [])])
