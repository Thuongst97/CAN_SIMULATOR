"""Web mode session: the whole simulator state behind the browser UI, without Qt.

One `WebSession` owns an `AppContext` (engine, adapters) and keeps what the desktop windows keep:

    channels      open channels and their settings (Setup > Channels)
    library       database files of the configuration, each used on all channels, one channel or not used
    cards         messages of the Interactive generator: channel, cycle, FD / BRS, signal values, generators
    rules         auto response rules (AutoResponder) and whether auto response is on
    trace         the last TRACE_ROWS frames (for clients that connect later and for Export)
    logs, nodes   the Log and the state of every adapter

Engine threads publish frames, log lines and node states on the EventBus; the session collects them under a lock
and `drain()` hands them to the web server, which pushes them to every browser 25 times per second. Every method
that changes the configuration bumps `version` (clients re-read the state) and saves `settings.json`.

Errors meant for the user raise `WebError` (HTTP 400 with the message).
"""
from __future__ import annotations

import json
import re
import tempfile
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

from autosim.context import AppContext
from autosim.core.can_engine.auto_response import ResponseRule, id_text
from autosim.core.can_engine.database import ANY, DatabaseError, DatabaseRegistry
from autosim.core.can_engine.signal_codec import signal_initial
from autosim.core.can_engine.generators import PARAM_LABELS, make_generator
from autosim.core.can_engine.rx_dispatcher import TraceRow
from autosim.core.common import events
from autosim.core.common.frame import CanChannelConfig, CanFrame
from autosim.hardware.bridge.node import VIRTUAL_CAN as BRIDGE_URL
from autosim.hardware.bridge.node import is_bridge_port
from autosim.ui.shell.bus_load import BusLoadMeter
from autosim.web.test_api import TestApi

TRACE_ROWS = 200_000               # frames kept for late clients and Export (as the desktop trace)
LOG_LINES = 1000
SIM_PORTS = ("SIM1", "SIM2")
from autosim import __version__ as APP_VERSION
# Settings dialog. measure_on_start: None = the default of the program (web mode: running, desktop app: stopped).
DEFAULT_GROUP = "Signals"                # the group of messages added without one (and of older files)
DEFAULT_PREFS = {"clear_on_start": True, "stop_on_failure": False, "measure_on_start": None}
NOMINAL = (125_000, 250_000, 500_000, 1_000_000)
DATA = (1_000_000, 2_000_000, 4_000_000, 5_000_000, 8_000_000)
UNBOUNDED = 1e12


class WebError(Exception):
    """A problem to show to the user (bad input, adapter refused, file not readable …)."""


@dataclass
class DbEntry:
    path: str
    active: bool = True
    channel: str = ANY

    @property
    def name(self) -> str:
        return Path(self.path).name


@dataclass
class Card:
    """One message of the Interactive generator."""
    id: int
    message: str
    channel: str
    cycle: int
    fd: bool
    brs: bool
    enabled: bool = False
    values: dict[str, float] = field(default_factory=dict)          # kept while the channel is closed
    gens: dict[str, tuple[str, list[float]]] = field(default_factory=dict)
    job: object = None
    group: str = ""                                                  # the group panel it is shown in


def encode_row(r: TraceRow) -> list:
    """Compact trace row for the browser:
    [time, channel, rx, id, extended, name, fd, brs, hex data, cycle ms | None, payload changed]."""
    f = r.frame
    return [round(f.timestamp, 6), f.channel, 1 if f.is_rx else 0, f.arbitration_id, 1 if f.is_extended else 0,
            r.name, 1 if f.is_fd else 0, 1 if f.bitrate_switch else 0, bytes(f.data).hex(),
            None if r.cycle_ms is None else round(r.cycle_ms, 2), 1 if r.changed else 0]


def _num(v, name: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise WebError(f"{name}: not a number ({v!r})") from None


class WebSession:
    def __init__(self, data_dir: str | Path, ctx: AppContext | None = None, save: bool = True) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_dir = self.data_dir / "databases"
        self._save_enabled = save
        self.ctx = ctx or AppContext()
        self.ctx.start()
        self.version = 0
        self.db_version = 0                  # changes only when the databases change: /api/databases is large
        self._lock = threading.RLock()
        self.channels: dict[str, dict] = {}                  # channel -> settings used to open it
        self.nodes: dict[str, dict] = {}                     # channel -> last NODE_STATE payload
        self.library: list[DbEntry] = []
        self._parsed: dict[str, object] = {}
        self.cards: dict[int, Card] = {}
        self.groups: list[str] = []                          # Interactive generator groups, in display order
        self._next_card = 1
        self.form: dict = {"port": "SIM1", "nominal": 500_000, "data": 2_000_000, "fd": True, "brs": True,
                           "iso": True, "loop": False}
        self.ports: list[dict] = []
        self.trace: deque[TraceRow] = deque(maxlen=TRACE_ROWS)
        self.logs: deque[tuple[float, str]] = deque(maxlen=LOG_LINES)
        self.paused = False
        # app settings (Settings dialog), kept in settings.json
        self.prefs: dict = dict(DEFAULT_PREFS)
        self._rows: list[TraceRow] = []
        self._new_logs: list[tuple[float, str]] = []
        self._nodes_changed: set[str] = set()
        self.load_meter = BusLoadMeter(self._rates)
        self.api = TestApi(self)                             # Test API tab: signals published to test systems
        self.ctx.can.responder.on_change = self.touch       # a rule switched off by the loop guard
        self.ctx.events.subscribe(events.MEASUREMENT, lambda _on: self.touch(save=False))   # Start / Stop: re-read
        ev = self.ctx.events
        ev.subscribe(events.CAN_RX, self._on_rows)
        ev.subscribe(events.LOG, self._on_log)
        ev.subscribe(events.NODE_STATE, self._on_node)
        self._restore()
        self.scan()

    # ---- engine events (engine threads) -------------------------------------------------------------
    def _on_rows(self, rows: list[TraceRow]) -> None:
        self.api.on_rows(rows)                               # newest value per message, even while paused
        with self._lock:
            self.load_meter.add_rows(rows)
            if self.paused:
                return
            self.trace.extend(rows)
            self._rows.extend(rows)

    def _on_log(self, text: str) -> None:
        with self._lock:
            item = (time.time(), str(text))
            self.logs.append(item)
            self._new_logs.append(item)

    def _on_node(self, st: dict) -> None:
        with self._lock:
            if st["channel"] in self.ctx.nodes.nodes:
                self.nodes[st["channel"]] = {k: st[k] for k in ("channel", "port", "state", "rx", "tx", "errors")} \
                    | {"node": (st.get("info") or {}).get("node_id", "")}
                self._nodes_changed.add(st["channel"])

    def log(self, text: str) -> None:
        self.ctx.events.publish(events.LOG, text)

    def touch(self, save: bool = True) -> None:
        """The configuration changed: clients re-read the state; settings are saved."""
        with self._lock:
            self.version += 1
        if save:
            self._save()

    # ---- measurement (Start / Stop, like CANoe) ------------------------------------------------------------
    @property
    def measuring(self) -> bool:
        return self.ctx.can.measuring

    def set_measuring(self, on: bool) -> None:
        """Start: optionally clears the trace first (Settings). Stop: nothing is sent, answered or logged; cyclic
        messages switched on stay armed and start again with the measurement."""
        on = bool(on)
        if on and not self.measuring and self.prefs.get("clear_on_start"):
            self.clear_trace()
        self.ctx.can.set_measuring(on)
        self.log("Measurement started" if on else "Measurement stopped")

    def set_prefs(self, **kw) -> dict:
        for k, v in kw.items():
            if k not in DEFAULT_PREFS:
                raise WebError(f"Unknown setting {k}")
            self.prefs[k] = None if v is None else bool(v)
        self.touch()
        return dict(self.prefs)

    def drain(self, max_rows: int = 5000) -> dict:
        """What happened since the last call: {rows, dropped, logs, nodes, version}. At most `max_rows` rows are
        sent (the newest); `dropped` counts the others (still in the trace for Export)."""
        with self._lock:
            rows, self._rows = self._rows, []
            logs, self._new_logs = self._new_logs, []
            nodes = [self.nodes[c] for c in self._nodes_changed if c in self.nodes]
            self._nodes_changed.clear()
            version = self.version
        dropped = max(0, len(rows) - max_rows)
        return {"rows": [encode_row(r) for r in rows[dropped:]], "dropped": dropped,
                "logs": [list(x) for x in logs], "nodes": nodes, "version": version}

    def recent(self, n: int = 2000) -> list[list]:
        with self._lock:
            rows = list(self.trace)[-n:]
        return [encode_row(r) for r in rows]

    def status(self) -> dict:
        """Once per second: bus load and frames/s per channel, errors, trace size, live generator values."""
        self.api.flush_if_dirty()                           # values written by Test API calls
        with self._lock:
            load = self.load_meter.snapshot()
            nodes = dict(self.nodes)
            n = len(self.trace)
        chans = []
        for ch in self.ctx.can.bus.channels:
            pct, fps = load.get(ch, (0.0, 0.0))
            st = nodes.get(ch, {})
            chans.append({"channel": ch, "load": round(pct, 1), "fps": round(fps), "state": st.get("state", ""),
                          "errors": st.get("errors", 0)})
        r = self.ctx.can.responder
        return {"channels": chans, "errors": sum(c["errors"] for c in chans), "trace": n, "paused": self.paused,
                "cards": self.card_values(), "hits": [r.stats_of(x).hits for x in r.rules]}

    def status_light(self) -> dict:
        """status() without consuming the bus-load interval (for a client that just connected)."""
        with self._lock:
            nodes = dict(self.nodes)
            n = len(self.trace)
        chans = [{"channel": ch, "load": 0.0, "fps": 0, "state": nodes.get(ch, {}).get("state", ""),
                  "errors": nodes.get(ch, {}).get("errors", 0)} for ch in self.ctx.can.bus.channels]
        return {"channels": chans, "errors": sum(c["errors"] for c in chans), "trace": n, "paused": self.paused,
                "cards": self.card_values()}

    def _rates(self, channel: str):
        s = self.channels.get(channel)
        if s is None:
            return None
        return s["nominal"], (s["data"] if s["fd"] else s["nominal"])

    # ---- channels -------------------------------------------------------------------------------------
    def scan(self) -> list[dict]:
        """Adapters (ESP32 / CANable / PCAN), then the simulated nodes and the Android bridge."""
        ports = []
        try:
            for p in self.ctx.nodes.scan():
                ports.append({"port": p["port"], "kind": p["kind"], "label": p.get("label", ""),
                              "hwid": p.get("hwid", ""), "fd": bool(p.get("supports_fd", True))})
        except Exception as e:  # noqa: BLE001
            self.log(f"Port scan failed: {e}")
        ports += [{"port": s, "kind": "sim", "label": "simulated node", "hwid": "", "fd": True} for s in SIM_PORTS]
        ports.append({"port": BRIDGE_URL, "kind": "bridge", "label": "Android head unit · vcan over TCP (adb)", "hwid": "",
                      "fd": True})
        self.ports = ports
        return ports

    def port_kind(self, port: str) -> str:
        p = next((x for x in self.ports if x["port"].upper() == port.upper()), None)
        if p:
            return p["kind"]
        if is_bridge_port(port):
            return "bridge"
        return "sim" if port.upper().startswith("SIM") else "autosim"

    def data_rates(self, port: str, nominal: int) -> list[int]:
        """Data rates the adapter on `port` accepts with this nominal rate (as the desktop form offers)."""
        kind = self.port_kind(port)
        if kind == "slcan":
            return [2_000_000, 5_000_000]
        if kind == "pcan":
            from autosim.hardware.pcan.timing import TimingError, fd_timing
            out = []
            for v in (nominal, *DATA):
                if v >= nominal and v not in out:
                    try:
                        fd_timing(nominal, v)
                        out.append(v)
                    except TimingError:
                        pass
            return out
        out = [v for v in DATA if v >= nominal and v % nominal == 0 and v // nominal <= 10]
        return out if nominal in out else [nominal, *out]

    def next_channel_name(self) -> str:
        n = 1
        while f"CAN{n}" in self.ctx.can.bus.channels:
            n += 1
        return f"CAN{n}"

    def port_owner(self, port: str) -> str | None:
        if port.upper().startswith("SIM"):
            return None
        return next((ch for ch, s in self.channels.items() if s["port"].upper() == port.upper()), None)

    def connect(self, port: str, channel: str = "", nominal: int = 500_000, data: int = 2_000_000,
                fd: bool = True, brs: bool = True, iso: bool = True, loop: bool = False) -> str:
        port, channel = str(port).strip(), (str(channel).strip() or self.next_channel_name())
        if not port:
            raise WebError("Select a port")
        if channel in self.ctx.can.bus.channels:
            raise WebError(f"Channel {channel} is already open")
        owner = self.port_owner(port)
        if owner:
            raise WebError(f"{port} is already open as channel {owner}")
        nominal, data = int(nominal), int(data)
        kind = self.port_kind(port)
        if kind in ("slcan", "pcan"):
            iso = True
        cfg = CanChannelConfig(nominal, data if fd else nominal, bool(fd), bool(brs and fd), bool(iso),
                               extras={"loopback": bool(loop)})
        try:
            backend = self.ctx.nodes.create_backend(port, channel)
            self.ctx.can.open_channel(channel, backend, cfg)
        except Exception as e:
            self.ctx.nodes.release(channel)
            with self._lock:
                self.nodes.pop(channel, None)
            raise WebError(f"{port} ({channel}): {e}") from e
        self.channels[channel] = {"port": port, "nominal": nominal, "data": data, "fd": bool(fd),
                                  "brs": bool(brs and fd), "iso": bool(iso), "loop": bool(loop)}
        self.form = {"port": port, "nominal": nominal, "data": data, "fd": bool(fd), "brs": bool(brs),
                     "iso": bool(iso), "loop": bool(loop)}
        for c in self.cards.values():                          # messages waiting for this channel get their job
            if c.channel == channel:
                self._ensure_job(c)
        self.touch()
        return channel

    def free_ports(self) -> list[str]:
        """Detected USB adapters not open yet (never the simulated nodes, never a motherboard COM port)."""
        out = []
        for p in self.ports:
            usb = p["kind"] in ("slcan", "pcan") or "USB" in p["hwid"]
            if usb and p["kind"] not in ("sim", "bridge") and self.port_owner(p["port"]) is None:
                out.append(p["port"])
        return out

    def connect_all(self, **settings) -> dict:
        opened, failed = [], []
        settings.pop("port", None)
        settings.pop("channel", None)
        for port in self.free_ports():
            try:
                opened.append(self.connect(port, "", **settings))
            except WebError as e:
                failed.append(f"{port}: {e}")
        if not opened and not failed:
            raise WebError("No free adapter found - plug one in and press Scan")
        for f in failed:
            self.log(f"Connect all: {f}")
        return {"opened": opened, "failed": failed}

    def disconnect(self, channel: str) -> None:
        if channel not in self.ctx.can.bus.channels:
            raise WebError(f"Channel {channel} is not open")
        for c in self.cards.values():
            if c.channel == channel and c.job is not None:
                c.values = dict(c.job.state.values)
                c.enabled = False
                c.job = None                                   # close_channel removes the scheduler job
        self.ctx.can.close_channel(channel)
        self.ctx.nodes.release(channel)
        self.channels.pop(channel, None)
        with self._lock:
            self.nodes.pop(channel, None)
        self.touch()

    def disconnect_all(self) -> int:
        chans = list(self.ctx.can.bus.channels)
        for ch in chans:
            self.disconnect(ch)
        return len(chans)

    def rename(self, channel: str, name: str) -> None:
        try:
            self.ctx.nodes.rename(channel, name.strip())
        except Exception as e:
            raise WebError(f"Rename failed: {e}") from e
        self.touch()

    # ---- databases ------------------------------------------------------------------------------------
    def entry(self, path: str) -> DbEntry | None:
        return next((e for e in self.library if DatabaseRegistry._same(e.path, path)), None)

    def database(self, path: str):
        reg = self.ctx.can.registry
        for ch, paths in reg._paths.items():
            for p, db in zip(paths, reg._dbs[ch]):
                if DatabaseRegistry._same(p, path):
                    return db
        key = str(Path(path).resolve())
        if key not in self._parsed:
            try:
                self._parsed[key] = DatabaseRegistry().load(path)
            except DatabaseError:
                self._parsed[key] = None
        return self._parsed[key]

    def add_database(self, path: str, channel: str = ANY) -> DbEntry:
        path = str(Path(path).expanduser())
        if not Path(path).is_file():
            raise WebError(f"{path}: file not found")
        e = self.entry(path)
        if e is None:
            e = DbEntry(path, False, channel)
            self._load(e, channel)
            self.library.append(e)
        else:
            self._parsed.pop(str(Path(path).resolve()), None)
            self._load(e, e.channel)
        self.db_version += 1                                # clients re-read /api/databases (large)
        self.touch()
        return e

    def upload_database(self, filename: str, content: bytes) -> DbEntry:
        name = re.sub(r"[^\w.\- ]", "_", Path(filename).name).strip() or "database.dbc"
        if Path(name).suffix.lower() not in (".dbc", ".arxml", ".kcd", ".sym"):
            raise WebError(f"{name}: not a CAN database (.dbc, .arxml, .kcd, .sym)")
        self.db_dir.mkdir(parents=True, exist_ok=True)
        target = self.db_dir / name
        target.write_bytes(content)
        return self.add_database(str(target))

    def _load(self, e: DbEntry, channel: str) -> None:
        try:
            self.ctx.can.load_database(e.path, channel)
        except DatabaseError as ex:
            raise WebError(str(ex)) from ex
        e.active, e.channel = True, channel
        self._parsed.pop(str(Path(e.path).resolve()), None)

    def _unload(self, e: DbEntry) -> None:
        if self.ctx.can.registry.is_loaded(e.path):
            removed = self.ctx.can.unload_database(e.path)
            gone = {id(j) for j in removed}
            for cid, c in list(self.cards.items()):
                if c.job is not None and id(c.job) in gone:
                    del self.cards[cid]
        e.active = False

    def use_database(self, path: str, channel: str | None) -> None:
        """channel: '*' (all channels), a channel name, or None / '' = not used."""
        e = self.entry(path)
        if e is None:
            raise WebError(f"{Path(path).name} is not in the configuration")
        if not channel:
            if e.active:
                self._unload(e)
        else:
            if e.active:
                self._unload(e)
            self._load(e, channel)
        self.db_version += 1                                # clients re-read /api/databases (large)
        self.touch()

    def reload_database(self, path: str) -> None:
        e = self.entry(path)
        if e is None:
            raise WebError(f"{Path(path).name} is not in the configuration")
        self._parsed.pop(str(Path(path).resolve()), None)
        if e.active:
            self._load(e, e.channel)
        self.db_version += 1                                # clients re-read /api/databases (large)
        self.touch()

    def remove_database(self, path: str) -> None:
        e = self.entry(path)
        if e is None:
            raise WebError(f"{Path(path).name} is not in the configuration")
        if e.active:
            self._unload(e)
        self.library.remove(e)
        self._parsed.pop(str(Path(path).resolve()), None)
        self.db_version += 1                                # clients re-read /api/databases (large)
        self.touch()

    def databases(self) -> list[dict]:
        """Every database with its nodes, messages and signals (Database window)."""
        out = []
        for e in self.library:
            db = self.database(e.path)
            item = {"path": e.path, "name": e.name, "active": e.active, "channel": e.channel,
                    "readable": db is not None, "nodes": [], "messages": []}
            if db is not None:
                by_sender: dict[str, list[str]] = {n.name: [] for n in db.nodes}
                for m in db.messages:
                    for s in (m.senders or []):
                        by_sender.setdefault(s, []).append(m.name)
                item["nodes"] = [{"name": n, "tx": sorted(v)} for n, v in sorted(by_sender.items())]
                item["messages"] = [self.message_info(m) for m in sorted(db.messages, key=lambda m: m.name)]
            out.append(item)
        return out

    @staticmethod
    def message_info(m) -> dict:
        return {"name": m.name, "id": m.frame_id, "extended": bool(m.is_extended_frame), "fd": bool(m.is_fd),
                "length": m.length, "cycle": m.cycle_time, "senders": list(m.senders or []),
                "comment": str(m.comment or ""),
                "signals": [WebSession.signal_info(s) for s in m.signals]}

    @staticmethod
    def signal_info(s) -> dict:
        def num(v):
            return None if v is None else float(v)
        return {"name": s.name, "start": s.start, "length": s.length,
                "order": "Intel" if s.byte_order == "little_endian" else "Motorola", "signed": bool(s.is_signed),
                "factor": num(s.scale), "offset": num(s.offset), "min": num(s.minimum), "max": num(s.maximum),
                "unit": s.unit or "", "initial": signal_initial(s),
                "choices": {str(int(k)): str(v) for k, v in s.choices.items()} if s.choices else {},
                "comment": str(s.comment or "")}

    def decode(self, channel: str, can_id: int, data_hex: str) -> dict:
        """Signals of a frame (trace rows expanded in the browser)."""
        f = CanFrame(int(can_id), bytes.fromhex(data_hex), False, False, False, 0.0, channel, True)
        m = self.ctx.can.registry.message_by_id(channel, int(can_id))
        if m is None:
            return {"message": "", "signals": []}
        dec = self.ctx.can.registry.decode(f)
        vals = dec[1] if dec else {}
        sigs = []
        for s in m.signals:
            if s.name not in vals:
                continue
            v = float(vals[s.name])
            choice = s.choices.get(int(v)) if s.choices and float(v).is_integer() else None
            sigs.append({"name": s.name, "value": v, "unit": s.unit or "", "text": "" if choice is None else str(choice)})
        return {"message": m.name, "signals": sigs}

    # ---- interactive generator ----------------------------------------------------------------------------
    def _ensure_job(self, c: Card):
        if c.job is not None and c.job in self.ctx.can.tx.jobs():
            return c.job
        if c.channel not in self.ctx.can.bus.channels:
            c.job = None
            return None
        msg = self.ctx.can.registry.message_by_name(c.message)
        if msg is None:
            return None
        job = self.ctx.can.tx.add_job(c.channel, msg, c.cycle or None, fd=c.fd, brs=c.brs)
        for k, v in c.values.items():
            try:
                job.state.set_signal(k, v)
            except KeyError:
                pass
        c.job = job
        for sig, (kind, params) in c.gens.items():
            self._bind(c, sig, kind, params)
        if c.enabled:
            self.ctx.can.tx.start(job)
        return job

    def _bind(self, c: Card, sig: str, kind: str, params: list[float]) -> None:
        if c.job is not None:
            self.ctx.can.tx.bind_generator(c.job, sig, make_generator(kind, *params) if kind else None)

    def _card(self, cid) -> Card:
        c = self.cards.get(int(cid))
        if c is None:
            raise WebError(f"Message {cid} is not in the generator")
        return c

    def default_channel(self, message: str) -> str:
        path = self.ctx.can.registry.path_of(self.ctx.can.registry.message_by_name(message))
        e = self.entry(path) if path else None
        if e and e.channel != ANY:
            return e.channel
        chans = list(self.ctx.can.bus.channels)
        return chans[0] if chans else ""

    # ---- groups of the Interactive generator ----------------------------------------------------------------------
    def _group(self, name: str | None) -> str:
        """The group a new message goes to: the one asked for (made if new), else the first one, else 'Signals'."""
        name = str(name or "").strip()
        if not name:
            name = self.groups[0] if self.groups else DEFAULT_GROUP
        if name not in self.groups:
            self.groups.append(name)
        return name

    def add_group(self, name: str, messages: list[str] | None = None, channel: str = "") -> list[int]:
        name = str(name or "").strip()
        if not name:
            raise WebError("Group name: required")
        if name in self.groups:
            raise WebError(f"A group named {name} already exists")
        self.groups.append(name)
        ids = [self.add_message(m, channel, name) for m in messages or []]
        self.touch()
        return ids

    def rename_group(self, name: str, new: str) -> None:
        new = str(new or "").strip()
        if name not in self.groups:
            raise WebError(f"No group {name}")
        if not new:
            raise WebError("Group name: required")
        if new != name and new in self.groups:
            raise WebError(f"A group named {new} already exists")
        self.groups[self.groups.index(name)] = new
        for c in self.cards.values():
            if c.group == name:
                c.group = new
        self.touch()

    def remove_group(self, name: str) -> int:
        """The group and its messages (their cyclic transmission stops)."""
        if name not in self.groups:
            raise WebError(f"No group {name}")
        gone = [cid for cid, c in self.cards.items() if c.group == name]
        for cid in gone:
            c = self.cards.pop(cid)
            if c.job is not None:
                self.ctx.can.tx.remove_job(c.job)
        self.groups.remove(name)
        self.touch()
        return len(gone)

    def move_group(self, name: str, step: int) -> None:
        if name not in self.groups:
            raise WebError(f"No group {name}")
        i = self.groups.index(name)
        j = max(0, min(len(self.groups) - 1, i + int(step)))
        self.groups.insert(j, self.groups.pop(i))
        self.touch()

    def group_list(self) -> list[dict]:
        for c in self.cards.values():                         # every message is in a listed group
            if not c.group:
                c.group = self._group("")
            elif c.group not in self.groups:
                self.groups.append(c.group)
        return [{"name": g, "cards": [c.id for c in self.cards.values() if c.group == g]} for g in self.groups]

    def add_message(self, message: str, channel: str = "", group: str = "") -> int:
        msg = self.ctx.can.registry.message_by_name(message)
        if msg is None:
            raise WebError(f"{message}: not in the databases in use")
        if any(c.message == message for c in self.cards.values()):        # one entry per message: moved if asked
            c = next(c for c in self.cards.values() if c.message == message)
            if group and c.group != group:
                c.group = self._group(group)
                self.touch()
            return c.id
        fd = bool(msg.is_fd) or msg.length > 8
        c = Card(self._next_card, message, channel or self.default_channel(message), int(msg.cycle_time or 100),
                 fd, fd, group=self._group(group))
        self._next_card += 1
        self.cards[c.id] = c
        self._ensure_job(c)
        self.touch()
        return c.id

    def update_card(self, cid, **kw) -> None:
        c = self._card(cid)
        old_channel = c.channel
        if kw.get("group"):
            c.group = self._group(kw["group"])
        if "channel" in kw and kw["channel"] != c.channel:
            if c.job is not None:
                c.values = dict(c.job.state.values)
                self.ctx.can.tx.remove_job(c.job)
                c.job = None
            c.channel = str(kw["channel"] or "")
        if "cycle" in kw:
            cyc = int(_num(kw["cycle"], "Cycle"))
            if not 1 <= cyc <= 60000:
                raise WebError("Cycle: 1 to 60000 ms")
            c.cycle = cyc
        for k in ("fd", "brs"):
            if k in kw:
                setattr(c, k, bool(kw[k]))
        msg = self.ctx.can.registry.message_by_name(c.message)
        if msg is not None and msg.length > 8:
            c.fd = True                                        # a payload above 8 bytes is CAN FD only
        c.brs = c.brs and c.fd
        job = self._ensure_job(c)
        if job is not None:
            job.fd, job.brs = c.fd, c.brs
            if "cycle" in kw:
                self.ctx.can.tx.set_cycle(job, c.cycle)
        if "enabled" in kw:
            on = bool(kw["enabled"])
            if on and job is None:
                raise WebError(f"{c.message}: channel {c.channel or '(none)'} is not open")
            c.enabled = on
            if job is not None:
                (self.ctx.can.tx.start if on else self.ctx.can.tx.stop)(job)
        if c.channel != old_channel and c.enabled and c.job is None:
            c.enabled = False
        self.touch()

    def send_once(self, cid) -> None:
        c = self._card(cid)
        job = self._ensure_job(c)
        if job is None:
            raise WebError(f"{c.message}: channel {c.channel or '(none)'} is not open")
        try:
            self.ctx.can.tx.trigger(job)
        except Exception as e:
            raise WebError(f"Send failed: {e}") from e

    def set_signal(self, cid, name: str, value=None, raw=None) -> float:
        c = self._card(cid)
        msg = self.ctx.can.registry.message_by_name(c.message)
        sig = next((s for s in (msg.signals if msg else []) if s.name == name), None)
        if sig is None:
            raise WebError(f"{c.message} has no signal {name}")
        if raw is not None:
            value = _num(raw, "Raw") * (sig.scale or 1) + (sig.offset or 0)
        v = _num(value, name)
        job = self._ensure_job(c)
        if job is not None:
            v = job.state.set_signal(name, v)
        else:
            from autosim.core.can_engine.signal_codec import MessageState
            v = MessageState._clamp(sig, v)
        c.values[name] = v
        self._save()
        return v

    def set_generator(self, cid, name: str, kind: str | None, params: list | None = None) -> None:
        c = self._card(cid)
        msg = self.ctx.can.registry.message_by_name(c.message)
        sig = next((s for s in (msg.signals if msg else []) if s.name == name), None)
        if sig is None:
            raise WebError(f"{c.message} has no signal {name}")
        if not kind or kind == "none":
            c.gens.pop(name, None)
            self._bind(c, name, "", [])
            self.touch()
            return
        if kind not in PARAM_LABELS:
            raise WebError(f"Unknown generator {kind}")
        if params is None:
            lo = sig.minimum if sig.minimum is not None else 0.0
            hi = sig.maximum if sig.maximum is not None and abs(sig.maximum) < UNBOUNDED else 100.0
            params = {"sine": [(hi - lo) / 2, 1.0, (hi + lo) / 2], "ramp": [lo, hi, 5.0],
                      "square": [lo, hi, 1.0], "toggle": [lo, hi, 1.0]}[kind]
        params = [_num(p, "Parameter") for p in params][:3]
        if len(params) != 3:
            raise WebError("A generator takes 3 parameters")
        c.gens[name] = (kind, params)
        self._ensure_job(c)
        self._bind(c, name, kind, params)
        self.touch()

    # ---- signal board and project files: the formats of the desktop app (main_window.py, config.py) -------------
    BOARD_KIND = "autosim-signal-board"

    def _signals_config(self) -> list[dict]:
        out = []
        for c in self.cards.values():
            values = dict(c.job.state.values) if c.job is not None else dict(c.values)
            out.append({"message": c.message, "channel": c.channel, "cycle": c.cycle, "fd": c.fd, "brs": c.brs,
                        "open": True, "values": values, "generators": {k: [g[0], list(g[1])] for k, g in c.gens.items()},
                        "group": c.group})
        return out

    def _active_paths(self) -> list[str]:
        return [e.path for e in self.library if e.active]

    def board_config(self) -> dict:
        """Signals table + auto response rules + the databases they need (desktop: File > Save signal board)."""
        return {"kind": self.BOARD_KIND, "version": 1, "databases": self._active_paths(), "groups": list(self.groups),
                "signals": self._signals_config(), "auto_response": self.ctx.can.responder.to_config()}

    def project_config(self) -> dict:
        """Desktop project file (ProjectConfig): databases, channels (open ones reconnect on open), rules, signals."""
        if self.channels:
            nodes = [{"port": d["port"], "channel_name": ch, "nominal_bitrate": d["nominal"], "data_bitrate": d["data"],
                      "fd": d["fd"], "brs": d["brs"], "iso": d["iso"], "loopback": d["loop"], "autoconnect": True}
                     for ch, d in self.channels.items()]
        else:
            f = self.form
            nodes = [{"port": f["port"], "channel_name": self.next_channel_name(), "nominal_bitrate": f["nominal"],
                      "data_bitrate": f["data"], "fd": f["fd"], "brs": f["brs"], "iso": f["iso"], "loopback": f["loop"],
                      "autoconnect": False}]
        return {"dbc_paths": self._active_paths(), "nodes": nodes, "auto_response": self.ctx.can.responder.to_config(),
                "signals": self._signals_config(), "groups": list(self.groups)}

    def _ensure_databases(self, paths: list) -> list[str]:
        """Load the databases a file needs; returns those that cannot be loaded."""
        missing, have = [], {str(Path(e.path).resolve()) for e in self.library if e.active}
        for p in paths or []:
            p = str(p)
            if str(Path(p).expanduser().resolve()) in have:
                continue
            e = self.entry(p)
            try:
                if e is not None and not e.active:
                    self.use_database(e.path, ANY)
                else:
                    self.add_database(p)
            except WebError:
                missing.append(p)
        return missing

    def _load_signals(self, items: list, groups: list | None = None) -> list[str]:
        """Replace the generator by `items` (to_config format); returns the messages the databases do not have."""
        for c in list(self.cards.values()):
            if c.job is not None:
                self.ctx.can.tx.remove_job(c.job)
        self.cards.clear()
        self.groups = [str(g) for g in (groups or []) if str(g).strip()]
        gone = []
        for d in items or []:
            if not isinstance(d, dict):
                continue
            name = str(d.get("message", ""))
            msg = self.ctx.can.registry.message_by_name(name)
            if msg is None:
                gone.append(name)
                continue
            fd = bool(d.get("fd", msg.is_fd or msg.length > 8)) or msg.length > 8
            c = Card(self._next_card, name, str(d.get("channel") or "") or self.default_channel(name),
                     int(d.get("cycle") or msg.cycle_time or 100), fd, bool(d.get("brs", fd)) and fd,
                     values={k: float(v) for k, v in (d.get("values") or {}).items() if isinstance(v, (int, float))},
                     gens={k: (g[0], list(g[1])) for k, g in (d.get("generators") or d.get("gens") or {}).items()
                           if isinstance(g, (list, tuple)) and len(g) == 2 and g[0]},
                     group=self._group(d.get("group")))
            self._next_card += 1
            self.cards[c.id] = c
            self._ensure_job(c)                     # never starts sending by itself: Cyclic stays off
        return gone

    def load_board(self, d: dict) -> dict:
        if not isinstance(d, dict) or not isinstance(d.get("signals", []), list):
            raise WebError("Not a signal board file")
        missing = self._ensure_databases(d.get("databases"))
        gone = self._load_signals(d.get("signals") or [], d.get("groups"))
        problems = self.ctx.can.responder.load_config(d["auto_response"]) if isinstance(d.get("auto_response"), dict) and d["auto_response"] else []
        self.touch()
        return self._report("Signal board", missing, gone, problems)

    def load_project(self, d: dict) -> dict:
        if not isinstance(d, dict) or not any(k in d for k in ("dbc_paths", "nodes", "signals")):
            raise WebError("Not a project file")
        missing = self._ensure_databases(d.get("dbc_paths"))
        failed = []
        nodes = [n for n in d.get("nodes") or [] if isinstance(n, dict) and n.get("port")]
        for n in nodes:
            args = dict(port=n["port"], channel=n.get("channel_name", ""), nominal=n.get("nominal_bitrate", 500_000),
                        data=n.get("data_bitrate", 2_000_000), fd=n.get("fd", True), brs=n.get("brs", True),
                        iso=n.get("iso", True), loop=n.get("loopback", False))
            if n.get("autoconnect") and args["channel"] not in self.ctx.can.bus.channels:
                try:
                    self.connect(**args)
                except WebError as e:
                    failed.append(str(e))
        if nodes:
            n = nodes[0]
            self.form = {"port": n["port"], "nominal": n.get("nominal_bitrate", 500_000),
                         "data": n.get("data_bitrate", 2_000_000), "fd": n.get("fd", True), "brs": n.get("brs", True),
                         "iso": n.get("iso", True), "loop": n.get("loopback", False)}
        problems = self.ctx.can.responder.load_config(d["auto_response"]) if d.get("auto_response") else []
        gone = self._load_signals(d["signals"], d.get("groups")) if d.get("signals") else []
        self.touch()
        rep = self._report("Project", missing, gone, problems)
        rep["failed"] = failed
        for f in failed:
            self.log(f"Project: {f}")
        return rep

    def _report(self, what: str, missing: list, gone: list, problems: list) -> dict:
        if missing:
            self.log(f"{what}: could not load " + ", ".join(missing))
        if gone:
            self.log("Signals: messages not in the databases: " + ", ".join(gone))
        for p in problems or []:
            self.log(f"Auto response: {p}")
        return {"missing": missing, "gone": gone, "problems": list(problems or []), "cards": len(self.cards)}

    def remove_card(self, cid) -> None:
        c = self.cards.pop(int(cid), None)
        if c is not None and c.job is not None:
            self.ctx.can.tx.remove_job(c.job)
        self.touch()

    def stop_all(self) -> int:
        n = 0
        for c in self.cards.values():
            if c.enabled:
                n += 1
                c.enabled = False
                if c.job is not None:
                    self.ctx.can.tx.stop(c.job)
        self.touch()
        return n

    def card_values(self) -> dict:
        """Live values of the generator messages (generators change them on the TX thread)."""
        out = {}
        for c in self.cards.values():
            if c.job is not None:
                out[str(c.id)] = {"count": c.job.count, "values": dict(c.job.state.values)}
        return out

    def card_list(self) -> list[dict]:
        out = []
        for c in self.cards.values():
            msg = self.ctx.can.registry.message_by_name(c.message)
            values = dict(c.job.state.values) if c.job is not None else dict(c.values)
            sigs = []
            for s in (msg.signals if msg else []):
                info = self.signal_info(s)
                info["value"] = values.get(s.name, 0.0)
                g = c.gens.get(s.name)
                info["gen"] = {"kind": g[0], "params": g[1]} if g else None
                sigs.append(info)
            out.append({"id": c.id, "message": c.message, "can_id": msg.frame_id if msg else 0,
                        "extended": bool(msg.is_extended_frame) if msg else False,
                        "length": msg.length if msg else 0, "channel": c.channel, "cycle": c.cycle, "fd": c.fd,
                        "brs": c.brs, "enabled": c.enabled, "ready": c.job is not None,
                        "count": c.job.count if c.job is not None else 0, "group": c.group, "signals": sigs})
        return out

    # ---- auto response ---------------------------------------------------------------------------------------
    def rules(self) -> dict:
        r = self.ctx.can.responder
        out = []
        for rule in r.rules:
            st = r.stats_of(rule)
            d = rule.to_dict()
            d.update({"when": id_text(rule.rx_id, rule.rx_extended), "condition": rule.condition_text(),
                      "response": f"{id_text(rule.tx_id, rule.tx_extended)}  {rule.response_text()}",
                      "hits": st.hits, "errors": st.errors, "last_error": st.last_error,
                      "disabled_reason": st.disabled_reason})
            out.append(d)
        return {"enabled": r.enabled, "rules": out}

    def set_rules(self, rules: list[dict], enabled: bool | None = None) -> list[str]:
        try:
            parsed = [ResponseRule.from_dict(d) for d in rules]
        except (TypeError, ValueError) as e:
            raise WebError(f"Invalid rule: {e}") from e
        problems = self.ctx.can.responder.set_rules(parsed)
        if enabled is not None:
            self.ctx.can.responder.enabled = bool(enabled)
        self.touch()
        return problems

    def set_auto_response(self, on: bool) -> None:
        self.ctx.can.responder.enabled = bool(on)
        self.log(f"Auto response {'on' if on else 'off'}")
        self.touch()

    def reset_hits(self) -> None:
        self.ctx.can.responder.reset_stats()
        self.touch()

    # ---- trace ------------------------------------------------------------------------------------------------
    def clear_trace(self) -> None:
        with self._lock:
            self.trace.clear()
            self._rows.clear()

    def set_paused(self, on: bool) -> None:
        with self._lock:
            self.paused = bool(on)

    def export(self, fmt: str) -> tuple[bytes, str]:
        fmt = fmt.lower().lstrip(".")
        if fmt not in ("csv", "asc", "blf"):
            raise WebError("Export format: csv, asc or blf")
        from autosim.core.can_engine.logger_io import export_by_extension
        with self._lock:
            rows = list(self.trace)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / f"trace.{fmt}"
            export_by_extension(rows, p)
            return p.read_bytes(), f"autosim_trace_{time.strftime('%Y%m%d_%H%M%S')}.{fmt}"

    # ---- state for the browser ----------------------------------------------------------------------------------
    def state(self) -> dict:
        with self._lock:
            nodes = dict(self.nodes)
        chans = []
        for ch in self.ctx.can.bus.channels:
            s = self.channels.get(ch, {})
            n = nodes.get(ch, {})
            chans.append({"channel": ch, **s, "node": n.get("node", ""), "state": n.get("state", ""),
                          "rx": n.get("rx", 0), "tx": n.get("tx", 0), "errors": n.get("errors", 0)})
        return {"version": self.version, "db_version": self.db_version, "measuring": self.measuring,
                "app_version": APP_VERSION, "data_dir": str(self.data_dir),
                "prefs": dict(self.prefs), "ports": self.ports, "form": self.form, "channels": chans,
                "nominal_rates": list(NOMINAL),
                "databases": [{"path": e.path, "name": e.name, "active": e.active, "channel": e.channel}
                              for e in self.library],
                "cards": self.card_list(), "groups": self.group_list(), "auto_response": self.rules(), "paused": self.paused,
                "generators": {k: list(v) for k, v in PARAM_LABELS.items()},
                "test_api": self.api.to_config()}

    # ---- persistence ----------------------------------------------------------------------------------------------
    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    def _save(self) -> None:
        if not self._save_enabled:
            return
        cards = []
        for c in self.cards.values():
            values = dict(c.job.state.values) if c.job is not None else dict(c.values)
            cards.append({"message": c.message, "channel": c.channel, "cycle": c.cycle, "fd": c.fd, "brs": c.brs,
                          "values": values, "gens": {k: [g[0], g[1]] for k, g in c.gens.items()}, "group": c.group})
        data = {"version": 1, "form": self.form, "prefs": self.prefs, "databases": [asdict(e) for e in self.library],
                "auto_response": self.ctx.can.responder.to_config(), "cards": cards, "groups": list(self.groups),
                "test_api": self.api.to_config()}
        try:
            tmp = self.settings_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.settings_path)
        except OSError as e:
            self.log(f"Settings not saved: {e}")

    def _restore(self) -> None:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._save_enabled, saving = False, self._save_enabled
        try:
            self.form.update(data.get("form") or {})
            self.prefs.update({k: v for k, v in (data.get("prefs") or {}).items() if k in DEFAULT_PREFS})
            missing = []
            for d in data.get("databases", []):
                e = DbEntry(d["path"], False, d.get("channel", ANY))
                if not Path(e.path).is_file():
                    missing.append(e.name)
                    continue
                self.library.append(e)
                if d.get("active", True):
                    try:
                        self._load(e, e.channel)
                    except WebError as ex:
                        self.log(str(ex))
            if missing:
                self.log("Databases not found: " + ", ".join(missing))
            problems = self.ctx.can.responder.load_config(data.get("auto_response"))
            for p in problems:
                self.log(f"Auto response: {p}")
            for p in self.api.load_config(data.get("test_api") or []):
                self.log(p)
            self.groups = [str(g) for g in data.get("groups") or [] if str(g).strip()]
            for cd in data.get("cards", []):
                if self.ctx.can.registry.message_by_name(cd["message"]) is None:
                    continue
                c = Card(self._next_card, cd["message"], cd.get("channel", ""), int(cd.get("cycle", 100)),
                         bool(cd.get("fd")), bool(cd.get("brs")), values=dict(cd.get("values") or {}),
                         gens={k: (g[0], list(g[1])) for k, g in (cd.get("gens") or {}).items()},
                         group=str(cd.get("group") or ""))
                self._next_card += 1
                self.cards[c.id] = c
        finally:
            self._save_enabled = saving

    def shutdown(self) -> None:
        self._save()
        for c in self.cards.values():
            if c.job is not None:
                self.ctx.can.tx.stop(c.job)
        self.ctx.shutdown()
