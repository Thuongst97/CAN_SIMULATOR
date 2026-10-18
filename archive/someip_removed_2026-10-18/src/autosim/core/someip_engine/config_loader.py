"""Load service descriptions: vsomeip JSON and (best effort) AUTOSAR ARXML."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MethodDef:
    name: str
    method_id: int
    request_type: str = ""            # payload spec, see payload.make_codec ("" = raw)
    response_type: str = ""
    fire_and_forget: bool = False


@dataclass
class EventDef:
    name: str
    event_id: int
    eventgroup_id: int
    payload_type: str = ""
    cyclic_ms: int | None = None


@dataclass
class ServiceDef:
    name: str
    service_id: int
    instance_id: int
    major: int = 1
    minor: int = 0
    methods: list[MethodDef] = field(default_factory=list)
    events: list[EventDef] = field(default_factory=list)
    port: int = 30501
    reliable: bool = False            # also offer/use TCP (methods and subscribed events)

    @property
    def eventgroups(self) -> list[int]:
        return sorted({e.eventgroup_id for e in self.events})


class ConfigError(Exception):
    pass


def _int(v, default: int | None = None) -> int:
    if v is None or v == "":
        if default is None:
            raise ConfigError("missing numeric value")
        return default
    if isinstance(v, int):
        return v
    try:
        return int(str(v), 0)           # accepts "0x1234" and "4660"
    except ValueError as e:
        raise ConfigError(f"bad number {v!r}") from e


def load_vsomeip_json(path: str | Path) -> list[ServiceDef]:
    """Parse the 'services' (provided) and 'clients' sections of a vsomeip config.

    vsomeip files only carry ids/ports/eventgroups, so methods are not available from them;
    add methods in the UI or use the optional non-standard "methods": [{"method","name"}]
    list per service (ignored by vsomeip itself)."""
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"{Path(path).name}: {e}") from e
    out: list[ServiceDef] = []
    for s in cfg.get("services", []):
        sid, iid = _int(s.get("service")), _int(s.get("instance"), 1)
        unrel, rel = s.get("unreliable"), s.get("reliable")
        if unrel is not None:
            port = _int(unrel)
        elif isinstance(rel, dict):
            port = _int(rel.get("port"), 30501)
        else:
            port = _int(rel, 30501)
        svc = ServiceDef(f"Service_{sid:04X}_{iid:04X}", sid, iid, port=port,
                         reliable=unrel is None and rel is not None)
        for eg in s.get("eventgroups", []):
            egid = _int(eg.get("eventgroup"))
            for ev_id in eg.get("events", []):
                eid = _int(ev_id)
                svc.events.append(EventDef(f"Event_{eid:04X}", eid, egid,
                                           cyclic_ms=_int(eg.get("cycle"), 0) or None))
        for md in s.get("methods", []):
            mid = _int(md.get("method"))
            svc.methods.append(MethodDef(md.get("name", f"Method_{mid:04X}"), mid))
        out.append(svc)
    if not out:
        raise ConfigError(f"{Path(path).name}: no 'services' found")
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def load_arxml_services(path: str | Path) -> list[ServiceDef]:
    """Best-effort AUTOSAR SOME/IP deployment reader: SOMEIP-SERVICE-INTERFACE-DEPLOYMENT with
    SERVICE-INTERFACE-ID, event/method deployments and event groups. Instance ids and ports
    are not read (defaults 1 / 30501) because they live in the ECU configuration."""
    try:
        root = ET.parse(str(path)).getroot()
    except (OSError, ET.ParseError) as e:
        raise ConfigError(f"{Path(path).name}: {e}") from e

    def child(el, name):
        return next((c for c in el if _local(c.tag) == name), None)

    def text(el, name, default=None):
        c = child(el, name)
        return c.text.strip() if c is not None and c.text else default

    out = []
    for dep in root.iter():
        if _local(dep.tag) != "SOMEIP-SERVICE-INTERFACE-DEPLOYMENT":
            continue
        major = next((el.text.strip() for el in dep.iter()
                      if _local(el.tag) == "MAJOR-VERSION" and el.text), None)
        svc = ServiceDef(text(dep, "SHORT-NAME", "Service"), _int(text(dep, "SERVICE-INTERFACE-ID")), 1,
                         major=_int(major, 1))
        ev_by_name: dict[str, int] = {}
        for el in dep.iter():
            ln = _local(el.tag)
            if ln == "SOMEIP-EVENT-DEPLOYMENT":
                ev_by_name[text(el, "SHORT-NAME", "")] = _int(text(el, "EVENT-ID"))
            elif ln == "SOMEIP-METHOD-DEPLOYMENT":
                svc.methods.append(MethodDef(text(el, "SHORT-NAME", "Method"), _int(text(el, "METHOD-ID"))))
        for el in dep.iter():
            if _local(el.tag) == "SOMEIP-EVENT-GROUP":
                egid = _int(text(el, "EVENT-GROUP-ID"))
                for ref in el.iter():
                    if _local(ref.tag) == "EVENT-REF" and ref.text:
                        name = ref.text.strip().rsplit("/", 1)[-1]
                        if name in ev_by_name:
                            svc.events.append(EventDef(name, ev_by_name[name], egid))
        out.append(svc)
    if not out:
        raise ConfigError(f"{Path(path).name}: no SOMEIP-SERVICE-INTERFACE-DEPLOYMENT found")
    return out


def load_services(path: str | Path) -> list[ServiceDef]:
    return load_arxml_services(path) if str(path).lower().endswith(".arxml") else load_vsomeip_json(path)
