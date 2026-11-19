"""Test API (web mode): CAN signals published for test systems, read with GET and written with PUT over HTTP.

Each endpoint names one signal of one message:

    GET /api/v1/signals/VehicleSpeed      the newest value of that signal ON THE BUS (a frame received or sent on
                                          the endpoint's channel; any channel when it has none), with its age
    PUT /api/v1/signals/VehicleSpeed      {"value": 88} | {"text": "D"} | {"raw": 880} - AutoSim sends ONE frame of
                                          the message now (endpoints with access "getset" only)

The endpoints work on their own, apart from the Interactive generator: a PUT builds the frame from the message's
DBC initial values plus the value of every endpoint of the same message (each endpoint starts at its signal's
initial value and keeps the last value written), and sends it once. GET reads what is on the bus, whoever sent it.

Every call of a test system is kept in a call log (client, request, answer, frame sent) shown in the Test API tab.
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, fields

from autosim.core.can_engine.signal_codec import MessageState, signal_initial
from autosim.core.common import clock
from autosim.core.common.frame import CanFrame

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
LOG_MAX = 500
DETAIL_MAX = 8000                   # characters of request body / response kept per call (expanded log row)
ACCESS = ("get", "getset")


class ApiError(Exception):
    """A request a test system got wrong (or that cannot be done now), with its HTTP status."""

    def __init__(self, status: int, text: str) -> None:
        super().__init__(text)
        self.status = status


@dataclass
class Endpoint:
    name: str                       # URL name: /api/v1/signals/<name>
    message: str
    signal: str
    channel: str = ""               # "" = read on any channel, send on the message's default channel
    access: str = "get"             # "get" (read only) or "getset"
    stale_ms: int = 1000            # older than this: status "stale"
    value: float | None = None      # value a PUT sends; starts at the signal's DBC initial value
    gets: int = 0
    sets: int = 0
    last_call: float = 0.0          # wall-clock time of the last call (0 = never)

    def config(self) -> dict:
        return {k: getattr(self, k) for k in ("name", "message", "signal", "channel", "access", "stale_ms", "value")}


def _fmt_value(v: float) -> float | int:
    return int(v) if float(v).is_integer() else round(float(v), 9)


class TestApi:
    __test__ = False                # not a pytest class

    def __init__(self, session) -> None:
        self.s = session
        self.endpoints: dict[str, Endpoint] = {}
        self._latest: dict[tuple, object] = {}          # (channel, id, extended) -> newest TraceRow, Rx or Tx
        self._lock = threading.RLock()
        self.log: deque[dict] = deque(maxlen=LOG_MAX)
        self._new_log: list[dict] = []
        self._log_id = 0
        self._dirty = False
        self._last_save = 0.0

    # ---- bus (engine threads) ----------------------------------------------------------------------------------
    def on_rows(self, rows) -> None:
        with self._lock:
            for r in rows:
                f = r.frame
                self._latest[(f.channel, f.arbitration_id, bool(f.is_extended))] = r

    # ---- configuration -----------------------------------------------------------------------------------------
    def _message(self, name: str):
        m = self.s.ctx.can.registry.message_by_name(name)
        if m is None:
            raise ApiError(404, f"{name}: not in the databases in use")
        return m

    def _signal(self, m, name: str):
        s = next((x for x in m.signals if x.name == name), None)
        if s is None:
            raise ApiError(404, f"{m.name} has no signal {name}")
        return s

    def _unique_name(self, wanted: str, message: str, exclude: str = "") -> str:
        base = re.sub(r"[^A-Za-z0-9_.\-]", "_", wanted)[:64] or "signal"
        names = set(self.endpoints) - {exclude}
        if base not in names:
            return base
        alt = re.sub(r"[^A-Za-z0-9_.\-]", "_", f"{message}_{wanted}")[:64]
        if alt not in names:
            return alt
        n = 2
        while f"{alt}_{n}" in names:
            n += 1
        return f"{alt}_{n}"

    def _check_value(self, sig, v) -> float:
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ApiError(400, f"{sig.name}: not a number ({v!r})") from None
        if sig.minimum is not None and v < sig.minimum or sig.maximum is not None and v > sig.maximum:
            lo = "…" if sig.minimum is None else f"{sig.minimum:g}"
            hi = "…" if sig.maximum is None else f"{sig.maximum:g}"
            raise ApiError(400, f"{sig.name}: {v:g} is outside {lo} … {hi}" + (f" {sig.unit}" if sig.unit else ""))
        return v

    def add(self, message: str, signal: str, channel: str = "", name: str = "", access: str = "get",
            stale_ms: int = 1000, value=None) -> Endpoint:
        """New endpoint (value = the signal's DBC initial value unless given). The same signal on the same
        channel again returns the existing endpoint (double-click in the database tree)."""
        m = self._message(message)
        sig = self._signal(m, signal)
        channel = str(channel or "")
        for ep in self.endpoints.values():
            if ep.message == m.name and ep.signal == sig.name and ep.channel == channel and not name:
                return ep
        if name and not NAME_RE.match(name):
            raise ApiError(400, "API name: letters, digits, _ . - only (1 to 64)")
        if name and name in self.endpoints:
            raise ApiError(400, f"API name {name} is already used")
        if access not in ACCESS:
            raise ApiError(400, "access: get or getset")
        if value is None or value == "":
            value = signal_initial(sig) or 0.0
        ep = Endpoint(name or self._unique_name(sig.name, m.name), m.name, sig.name, channel, access,
                      max(0, int(stale_ms)), _fmt_value(self._check_value(sig, value)))
        with self._lock:
            self.endpoints[ep.name] = ep
        self.s.touch()
        return ep

    def update(self, name: str, **kw) -> Endpoint:
        ep = self.get(name)
        m = self._message(ep.message)
        sig = self._signal(m, ep.signal)
        new_name = str(kw.get("new_name") or "").strip()
        if new_name and new_name != ep.name:
            if not NAME_RE.match(new_name):
                raise ApiError(400, "API name: letters, digits, _ . - only (1 to 64)")
            if new_name in self.endpoints:
                raise ApiError(400, f"API name {new_name} is already used")
        if "access" in kw and kw["access"] not in ACCESS:
            raise ApiError(400, "access: get or getset")
        value = self._check_value(sig, kw["value"]) if kw.get("value") not in (None, "") else None
        with self._lock:
            if "channel" in kw:
                ep.channel = str(kw["channel"] or "")
            if "access" in kw:
                ep.access = kw["access"]
            if "stale_ms" in kw:
                ep.stale_ms = max(0, int(kw["stale_ms"]))
            if value is not None:
                ep.value = _fmt_value(value)
            if new_name and new_name != ep.name:
                self.endpoints = {(new_name if k == ep.name else k): v for k, v in self.endpoints.items()}
                ep.name = new_name
        self.s.touch()
        return ep

    def remove(self, name: str) -> None:
        with self._lock:
            if self.endpoints.pop(name, None) is None:
                raise ApiError(404, f"No API named {name}")
        self.s.touch()

    def get(self, name: str) -> Endpoint:
        ep = self.endpoints.get(name)
        if ep is None:
            raise ApiError(404, f"No API named {name}")
        return ep

    # ---- reading the bus ------------------------------------------------------------------------------------------
    def _newest(self, ep: Endpoint, m):
        ext = bool(m.is_extended_frame)
        with self._lock:
            if ep.channel:
                return self._latest.get((ep.channel, m.frame_id, ext))
            rows = [r for (_ch, i, x), r in self._latest.items() if i == m.frame_id and x == ext]
        return max(rows, key=lambda r: r.frame.timestamp) if rows else None

    def value_of(self, ep: Endpoint) -> dict:
        """What a GET answers (without counting it as a call)."""
        try:
            m = self._message(ep.message)
            sig = self._signal(m, ep.signal)
        except ApiError as e:
            return {"name": ep.name, "message": ep.message, "signal": ep.signal, "status": "error",
                    "error": str(e), "value": None}
        out = {"name": ep.name, "signal": sig.name, "message": m.name,
               "id": f"0x{m.frame_id:X}", "channel": ep.channel, "unit": sig.unit or "",
               "value": None, "raw": None, "text": None, "timestamp": None, "age_ms": None, "direction": None,
               "status": "no_data", "default": _fmt_value(signal_initial(sig) or 0.0), "set_value": ep.value,
               "access": ep.access}
        row = self._newest(ep, m)
        if row is None:
            return out
        f = row.frame
        try:
            vals = MessageState(m).decode(bytes(f.data))
        except Exception:  # noqa: BLE001  a payload the DBC cannot decode
            out["status"] = "undecodable"
            return out
        if sig.name not in vals:                       # inactive multiplexed signal
            out["status"] = "inactive"
            return out
        v = float(vals[sig.name])
        age = max(0.0, (clock.now() - f.timestamp) * 1000.0)
        raw = None if sig.is_float or not sig.scale else round((v - sig.offset) / sig.scale)
        text = sig.choices.get(round(v)) if sig.choices and v.is_integer() else None
        out.update({"value": _fmt_value(v), "raw": raw, "text": None if text is None else str(text),
                    "timestamp": round(f.timestamp, 6), "age_ms": round(age, 1), "channel": f.channel,
                    "direction": "rx" if f.is_rx else "tx",
                    "status": "stale" if ep.stale_ms and age > ep.stale_ms else "ok"})
        return out

    def live(self) -> list[dict]:
        out = []
        for ep in list(self.endpoints.values()):
            d = self.value_of(ep)
            d.update({"gets": ep.gets, "sets": ep.sets, "last_call": ep.last_call, "stale_ms": ep.stale_ms})
            out.append(d)
        return out

    # ---- calls of the test systems ----------------------------------------------------------------------------------
    def read(self, name: str) -> dict:
        ep = self.get(name)
        ep.gets += 1
        ep.last_call = time.time()
        return self.value_of(ep)

    def read_many(self, names: list[str] | None) -> dict:
        eps = [self.get(n) for n in names] if names else list(self.endpoints.values())
        now = time.time()
        for ep in eps:
            ep.gets += 1
            ep.last_call = now
        return {"signals": {ep.name: self.value_of(ep) for ep in eps}}

    def write(self, name: str, body: dict) -> dict:
        """Set the value and send one frame of the message now."""
        ep = self.get(name)
        if ep.access != "getset":
            raise ApiError(403, f"{ep.name} is read only (enable GET + SET in the Test API tab)")
        m = self._message(ep.message)
        sig = self._signal(m, ep.signal)
        if not isinstance(body, dict):
            raise ApiError(400, 'Expected a JSON object: {"value": …}, {"text": …} or {"raw": …}')
        if body.get("text") not in (None, ""):
            t = str(body["text"]).strip().lower()
            key = next((k for k, v in (sig.choices or {}).items() if str(v).lower() == t), None)
            if key is None:
                names = ", ".join(str(v) for v in (sig.choices or {}).values()) or "none"
                raise ApiError(400, f"{sig.name}: no value named {body['text']!r} (value table: {names})")
            v = float(key)
        elif body.get("raw") not in (None, ""):
            try:
                raw = int(str(body["raw"]), 0)
            except ValueError:
                raise ApiError(400, f"{sig.name}: raw must be an integer ({body['raw']!r})") from None
            v = raw * (sig.scale or 1) + (sig.offset or 0)
        elif "value" in body:
            v = body["value"]
        else:
            raise ApiError(400, 'Give {"value": …}, {"text": …} or {"raw": …}')
        v = self._check_value(sig, v)
        channel = ep.channel or self.s.default_channel(m.name)
        if not channel or channel not in self.s.ctx.can.bus.channels:
            raise ApiError(409, f"Channel {channel or '(none)'} is not open - connect it in Setup > Channels")
        with self._lock:
            ep.value = _fmt_value(v)
            state = MessageState(m)                     # DBC initial values ...
            for other in self.endpoints.values():        # ... plus every endpoint of this message
                if other.message == m.name and (other.channel or channel) == channel and other.value is not None:
                    try:
                        state.set_signal(other.signal, float(other.value))
                    except KeyError:
                        pass
            ep.sets += 1
            ep.last_call = time.time()
        fd = bool(m.is_fd) or m.length > 8
        frame = CanFrame(m.frame_id, state.encode(), bool(m.is_extended_frame), fd, fd)
        try:
            self.s.ctx.can.bus.send(channel, frame)
        except Exception as e:  # the adapter refused the frame
            raise ApiError(409, f"Send failed on {channel}: {e}") from e
        self._save_soon()
        text = sig.choices.get(round(v)) if sig.choices and float(v).is_integer() else None
        raw = None if sig.is_float or not sig.scale else round((v - sig.offset) / sig.scale)
        return {"name": ep.name, "signal": sig.name, "message": m.name, "id": f"0x{m.frame_id:X}", "channel": channel,
                "unit": sig.unit or "", "value": _fmt_value(v), "raw": raw, "text": None if text is None else str(text),
                "status": "sent", "access": ep.access,
                "sent": {"channel": channel, "id": f"0x{m.frame_id:X}", "data": bytes(frame.data).hex(" ").upper(),
                         "fd": fd}}

    # ---- call log -------------------------------------------------------------------------------------------------------
    def record(self, client: str, method: str, path: str, status: int, request: str, answer: str,
               sent: dict | None = None, response: str = "", headers: dict | None = None,
               duration_ms: float | None = None) -> None:
        """One call of a test system. `answer` is the one-line summary of the table; `request` (body) and
        `response` (JSON) are kept in full up to DETAIL_MAX characters for the expanded row."""
        with self._lock:
            self._log_id += 1
            e = {"id": self._log_id, "t": time.time(), "client": client, "method": method, "path": path,
                 "status": status, "request": request[:DETAIL_MAX], "answer": answer[:300], "sent": sent,
                 "response": response[:DETAIL_MAX], "headers": headers or {},
                 "ms": None if duration_ms is None else round(duration_ms, 2)}
            self.log.append(e)
            self._new_log.append(e)

    def drain_log(self) -> list[dict]:
        with self._lock:
            out, self._new_log = self._new_log, []
        return out

    def clear_log(self) -> None:
        with self._lock:
            self.log.clear()
            self._new_log.clear()

    # ---- persistence -------------------------------------------------------------------------------------------------
    def _save_soon(self) -> None:
        """PUTs can come many times per second: settings are written at most once per second."""
        if time.monotonic() - self._last_save > 1.0:
            self.flush()
        else:
            self._dirty = True

    def flush(self) -> None:
        self._dirty = False
        self._last_save = time.monotonic()
        self.s._save()

    def flush_if_dirty(self) -> None:
        if self._dirty:
            self.flush()

    def to_config(self) -> list[dict]:
        return [ep.config() for ep in self.endpoints.values()]

    def load_config(self, items, replace: bool = True) -> list[str]:
        """Endpoints from to_config() data; returns problems (unknown signals are kept, shown as errors)."""
        known = {f.name for f in fields(Endpoint)}
        problems = []
        new: dict[str, Endpoint] = {} if replace else dict(self.endpoints)
        for d in items or []:
            if not isinstance(d, dict) or not d.get("name") or not d.get("message") or not d.get("signal"):
                problems.append(f"Skipped an invalid API entry: {d!r}"[:200])
                continue
            ep = Endpoint(**{k: v for k, v in d.items() if k in known and k not in ("gets", "sets", "last_call")})
            if ep.access not in ACCESS:
                ep.access = "get"
            if self.s.ctx.can.registry.message_by_name(ep.message) is None:
                problems.append(f"API {ep.name}: {ep.message} is not in the databases in use")
            new[ep.name] = ep
        with self._lock:
            self.endpoints = new
        return problems

    def export(self) -> dict:
        return {"kind": "autosim-test-api", "version": 1, "apis": self.to_config()}

    def import_(self, data: dict, replace: bool = True) -> list[str]:
        if not isinstance(data, dict) or not isinstance(data.get("apis"), list):
            raise ApiError(400, "Not a Test API file (expected {\"apis\": [...]})")
        problems = self.load_config(data["apis"], replace)
        self.s.touch()
        return problems

    @staticmethod
    def as_dict(ep: Endpoint) -> dict:
        return asdict(ep)
