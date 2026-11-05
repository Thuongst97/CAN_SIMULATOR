"""Auto response: answer received frames with frames of their own, by rules ("when 0x21 carries 10, send 0x27
with 10; when it carries 20, send 0x27 with 100").

A rule = trigger + response:

* trigger: CAN id (+ channel, or any channel) and a condition on the payload - any payload, a DBC signal
  compared with a value, or a raw integer read from the bytes (offset / size / byte order).
* response: CAN id (+ channel, default: the channel the trigger came in on), FD / BRS, a delay, and the payload
  either as raw bytes (hex template + an optional integer written at an offset) or as DBC signal values (the
  rest of the message keeps its initial values). Values are numbers or small expressions of ``rx``, the value
  the condition read: ``rx``, ``rx * 10``, ``rx + 1``.

``AutoResponder`` listens to every received frame (BusManager RX listener, on the adapter's reader thread),
evaluates the rules indexed by id (cheap), and hands matches to its own scheduler thread which sends them after
their delay (0 ms = immediately). A rule that fires more than ``max_rate`` times per second is switched off: it
is almost certainly answering its own frames (loopback) or another responder in a loop.
"""
from __future__ import annotations

import ast
import heapq
import itertools
import math
import operator
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields

from autosim.core.can_engine.signal_codec import MessageState
from autosim.core.common.frame import CanFrame

MATCH_ANY, MATCH_SIGNAL, MATCH_RAW = "any", "signal", "raw"
TX_RAW, TX_SIGNAL = "raw", "signal"
OPS: dict[str, Callable[[float, float], bool]] = {
    "==": lambda a, b: math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6),
    "!=": lambda a, b: not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6),
    "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}
FD_LENGTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)
RAW_SIZES = (1, 2, 3, 4, 8)


class RuleError(ValueError):
    """A rule is incomplete or inconsistent (message text is meant for the user)."""


# ---- value expressions ------------------------------------------------------------------------------------
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}


def compile_value(text: str) -> Callable[[float | None], float]:
    """'10', '0x64', 'rx', 'rx*10+1' -> f(rx). Only numbers, rx, + - * / // % and parentheses are allowed."""
    src = str(text).strip()
    if not src:
        raise RuleError("empty value")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        raise RuleError(f"invalid value {src!r} (use a number or an expression of rx, e.g. rx*10)") from None
    uses_rx = False

    def check(n):
        nonlocal uses_rx
        if isinstance(n, ast.Expression):
            return check(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return
        if isinstance(n, ast.Name) and n.id == "rx":
            uses_rx = True
            return
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            check(n.left)
            check(n.right)
            return
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            check(n.operand)
            return
        raise RuleError(f"invalid value {src!r}: only numbers, rx and + - * / // % are allowed")
    check(tree)

    def ev(n, rx):
        if isinstance(n, ast.Expression):
            return ev(n.body, rx)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            if rx is None:
                raise RuleError("'rx' needs a condition that reads a value (signal or raw)")
            return rx
        if isinstance(n, ast.BinOp):
            return _BIN[type(n.op)](ev(n.left, rx), ev(n.right, rx))
        v = ev(n.operand, rx)
        return -v if isinstance(n.op, ast.USub) else v

    def fn(rx: float | None = None) -> float:
        try:
            return ev(tree, rx)
        except ZeroDivisionError:
            raise RuleError(f"{src!r}: division by zero") from None
    fn.uses_rx = uses_rx                                   # type: ignore[attr-defined]
    fn.source = src                                        # type: ignore[attr-defined]
    return fn


def parse_int(text) -> int:
    """'10', '0x0A', '0b1010', ' 33 ' -> int."""
    if isinstance(text, int):
        return text
    try:
        return int(str(text).strip(), 0)
    except ValueError:
        raise RuleError(f"{text!r} is not an integer (decimal, 0x.. hex or 0b.. binary)") from None


def parse_hex(text: str) -> bytes:
    """'64 00 0A', '64000A', '64:00:0a' -> bytes."""
    s = "".join(ch for ch in str(text) if ch not in " :,-_\t")
    if s.lower().startswith("0x"):
        s = s[2:]
    if len(s) % 2:
        raise RuleError(f"hex data {text!r} has an odd number of digits")
    try:
        return bytes.fromhex(s)
    except ValueError:
        raise RuleError(f"{text!r} is not hex data (e.g. 0A 00 FF)") from None


def read_raw(data: bytes, offset: int, size: int, order: str, signed: bool = False) -> int | None:
    if offset < 0 or offset + size > len(data):
        return None
    return int.from_bytes(data[offset:offset + size], "big" if order == "big" else "little", signed=signed)


def write_raw(buf: bytearray, offset: int, size: int, order: str, value: int) -> None:
    if offset < 0 or offset + size > len(buf):
        raise RuleError(f"value at byte {offset} ({size} byte{'s' if size > 1 else ''}) does not fit a "
                        f"{len(buf)}-byte payload")
    value &= (1 << (8 * size)) - 1                         # negative -> two's complement
    buf[offset:offset + size] = value.to_bytes(size, "big" if order == "big" else "little")


def valid_length(n: int, fd: bool) -> int:
    if not fd:
        if n > 8:
            raise RuleError(f"{n} bytes need CAN FD (classic CAN carries at most 8)")
        return n
    for v in FD_LENGTHS:
        if v >= n:
            return v
    raise RuleError(f"{n} bytes: CAN FD carries at most 64")


# ---- rule -------------------------------------------------------------------------------------------------
@dataclass(eq=False)
class ResponseRule:
    name: str = ""
    enabled: bool = True
    # trigger
    rx_channel: str = ""               # "" = any channel
    rx_id: int = 0
    rx_extended: bool = False
    match: str = MATCH_ANY             # any | signal | raw
    signal: str = ""                   # match == signal: DBC signal of the rx message
    op: str = "=="
    value: str = "0"                   # compared value (number)
    raw_offset: int = 0                # match == raw: unsigned integer read at this byte offset
    raw_size: int = 1
    raw_order: str = "little"          # little (Intel) | big (Motorola)
    # response
    tx_channel: str = ""               # "" = the channel the trigger came in on
    tx_id: int = 0
    tx_extended: bool = False
    tx_fd: bool = False
    tx_brs: bool = False
    tx_mode: str = TX_RAW              # raw | signal
    tx_dlc: int = 8                    # raw: payload length in bytes
    tx_data: str = ""                  # raw: base payload (hex), zero-padded to tx_dlc
    tx_value: str = ""                 # raw: optional value / expression written at tx_offset ("" = none)
    tx_offset: int = 0
    tx_size: int = 1
    tx_order: str = "little"
    tx_signals: dict[str, str] = field(default_factory=dict)   # signal mode: name -> value / expression
    delay_ms: int = 0

    # ---- persistence ----------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ResponseRule:
        names = {f.name for f in fields(cls)}
        r = cls(**{k: v for k, v in d.items() if k in names})
        r.tx_signals = {str(k): str(v) for k, v in (r.tx_signals or {}).items()}
        r.value, r.tx_value = str(r.value), str(r.tx_value)
        return r

    def copy(self) -> ResponseRule:
        return ResponseRule.from_dict(self.to_dict())

    # ---- text for tables ----------------------------------------------------------------------------
    def condition_text(self) -> str:
        if self.match == MATCH_SIGNAL:
            return f"{self.signal} {self.op} {self.value}"
        if self.match == MATCH_RAW:
            span = f"byte {self.raw_offset}" if self.raw_size == 1 else \
                f"bytes {self.raw_offset}..{self.raw_offset + self.raw_size - 1}" + \
                (" BE" if self.raw_order == "big" else " LE")
            return f"{span} {self.op} {self.value}"
        return "any data"

    def response_text(self) -> str:
        if self.tx_mode == TX_SIGNAL:
            return ", ".join(f"{k} = {v}" for k, v in self.tx_signals.items()) or "initial values"
        parts = []
        if self.tx_value.strip():
            parts.append(f"byte {self.tx_offset}" + (f"+{self.tx_size}" if self.tx_size > 1 else "")
                         + f" = {self.tx_value}")
        if self.tx_data.strip():
            parts.append(f"[{self.tx_data.strip()}]")
        return "  ".join(parts) or f"{self.tx_dlc} zero bytes"


def id_text(can_id: int, extended: bool) -> str:
    return f"0x{can_id:08X}" if extended else f"0x{can_id:03X}"


@dataclass
class _Compiled:
    rule: ResponseRule
    cmp: Callable[[float, float], bool] | None
    ref: float
    tx_value: Callable | None
    tx_signals: dict[str, Callable]
    base: bytes


@dataclass
class RuleStats:
    hits: int = 0                     # responses sent
    errors: int = 0
    last_error: str = ""
    last_fired: float = 0.0
    disabled_reason: str = ""


class AutoResponder:
    """Rules engine. Thread model: on_frame() on the reader threads, sends on the 'auto-response' thread."""

    def __init__(self, registry, send: Callable[[str, CanFrame], None],
                 log: Callable[[str], None] = lambda s: None, max_rate: int = 1000) -> None:
        self.registry, self._send, self._log, self.max_rate = registry, send, log, max_rate
        self.enabled = False
        self._rules: list[ResponseRule] = []
        self._index: dict[tuple[int, bool], list[_Compiled]] = {}
        self.stats: dict[int, RuleStats] = {}             # id(rule) -> stats
        self._window: dict[int, tuple[float, int]] = {}
        self._lock = threading.Lock()
        self._cv = threading.Condition()
        self._heap: list[tuple[float, int, _Compiled, CanFrame, float | None]] = []
        self._seq = itertools.count()
        self._thread: threading.Thread | None = None
        self._running = False
        self.on_change: Callable[[], None] = lambda: None   # a rule was switched off by the engine

    # ---- rules ----------------------------------------------------------------------------------------
    @property
    def rules(self) -> list[ResponseRule]:
        with self._lock:
            return list(self._rules)

    def set_rules(self, rules: list[ResponseRule]) -> list[str]:
        """Replace every rule. Invalid enabled rules are kept but not active; returns their problems."""
        problems, index = [], {}
        for i, r in enumerate(rules):
            if not r.enabled:
                continue
            try:
                c = self.compile(r)
            except RuleError as e:
                problems.append(f"rule {i + 1} ({r.name or id_text(r.rx_id, r.rx_extended)}): {e}")
                continue
            index.setdefault((r.rx_id, r.rx_extended), []).append(c)
        with self._lock:
            self._rules = list(rules)
            self._index = index
            keep = {id(r) for r in rules}
            self.stats = {k: v for k, v in self.stats.items() if k in keep}
            for r in rules:
                self.stats.setdefault(id(r), RuleStats())
        return problems

    def stats_of(self, rule: ResponseRule) -> RuleStats:
        with self._lock:
            return self.stats.setdefault(id(rule), RuleStats())

    def reset_stats(self) -> None:
        with self._lock:
            for k in self.stats:
                self.stats[k] = RuleStats()

    def compile(self, r: ResponseRule) -> _Compiled:
        """Validate a rule; RuleError explains what is wrong."""
        if not 0 <= r.rx_id <= (0x1FFFFFFF if r.rx_extended else 0x7FF):
            raise RuleError(f"trigger id {r.rx_id:#x} does not fit {'29' if r.rx_extended else '11'} bits")
        if not 0 <= r.tx_id <= (0x1FFFFFFF if r.tx_extended else 0x7FF):
            raise RuleError(f"response id {r.tx_id:#x} does not fit {'29' if r.tx_extended else '11'} bits")
        if r.delay_ms < 0:
            raise RuleError("delay must be >= 0 ms")
        cmp, ref, reads = None, 0.0, False
        if r.match in (MATCH_SIGNAL, MATCH_RAW):
            if r.op not in OPS:
                raise RuleError(f"unknown operator {r.op!r}")
            cmp = OPS[r.op]
            try:
                ref = float(parse_int(r.value)) if r.match == MATCH_RAW else float(r.value)
            except (RuleError, ValueError):
                raise RuleError(f"compared value {r.value!r} is not a number") from None
            reads = True
            if r.match == MATCH_SIGNAL and not r.signal:
                raise RuleError("choose the signal the condition reads")
            if r.match == MATCH_RAW and (r.raw_size not in RAW_SIZES or r.raw_offset < 0 or r.raw_offset > 63):
                raise RuleError("raw condition: offset 0..63, size 1, 2, 3, 4 or 8 bytes")
        elif r.match != MATCH_ANY:
            raise RuleError(f"unknown condition type {r.match!r}")
        tx_value, tx_signals, base = None, {}, b""
        if r.tx_mode == TX_RAW:
            base = parse_hex(r.tx_data) if r.tx_data.strip() else b""
            n = max(r.tx_dlc, len(base))
            valid_length(n, r.tx_fd)
            if r.tx_value.strip():
                tx_value = compile_value(r.tx_value)
                if r.tx_size not in RAW_SIZES:
                    raise RuleError("response value size must be 1, 2, 3, 4 or 8 bytes")
                if r.tx_offset < 0 or r.tx_offset + r.tx_size > valid_length(n, r.tx_fd):
                    raise RuleError(f"response value at byte {r.tx_offset} does not fit the {n}-byte payload")
        elif r.tx_mode == TX_SIGNAL:
            tx_signals = {k: compile_value(v) for k, v in r.tx_signals.items() if str(v).strip()}
        else:
            raise RuleError(f"unknown response type {r.tx_mode!r}")
        if not reads and (getattr(tx_value, "uses_rx", False) or any(f.uses_rx for f in tx_signals.values())):
            raise RuleError("the response uses 'rx' but the condition reads no value (choose signal or raw)")
        return _Compiled(r, cmp, ref, tx_value, tx_signals, base)

    # ---- matching (reader threads) --------------------------------------------------------------------
    def on_frame(self, frame: CanFrame) -> None:
        if not self.enabled or not frame.is_rx:
            return
        cands = self._index.get((frame.arbitration_id, frame.is_extended))
        if not cands:
            return
        decoded = None
        for c in cands:
            r = c.rule
            if r.rx_channel and r.rx_channel != frame.channel:
                continue
            rx_val: float | None = None
            if r.match == MATCH_SIGNAL:
                if decoded is None:
                    decoded = self._decode(frame) or {}
                if r.signal not in decoded:
                    self._error(r, f"signal {r.signal} not decodable from 0x{frame.arbitration_id:X} "
                                   f"(DBC loaded?)", once=True)
                    continue
                rx_val = float(decoded[r.signal])
            elif r.match == MATCH_RAW:
                v = read_raw(frame.data, r.raw_offset, r.raw_size, r.raw_order)
                if v is None:
                    continue                               # payload too short for this condition: no match
                rx_val = float(v)
            if c.cmp is not None and not c.cmp(rx_val, c.ref):
                continue
            if not self._rate_ok(r):
                continue
            due = time.perf_counter() + r.delay_ms / 1000.0
            with self._cv:
                heapq.heappush(self._heap, (due, next(self._seq), c, frame, rx_val))
                self._cv.notify()

    def _decode(self, frame: CanFrame) -> dict | None:
        d = self.registry.decode(frame) if self.registry is not None else None
        return d[1] if d else None

    def _rate_ok(self, r: ResponseRule) -> bool:
        now = time.monotonic()
        key = id(r)
        t0, n = self._window.get(key, (now, 0))
        if now - t0 >= 1.0:
            t0, n = now, 0
        n += 1
        self._window[key] = (t0, n)
        if n <= self.max_rate:
            return True
        if r.enabled:
            r.enabled = False
            why = (f"fired more than {self.max_rate} times in one second - switched off (is it answering its "
                   f"own frames, e.g. on a loopback channel?)")
            self.stats_of(r).disabled_reason = why
            self._log(f"Auto response: rule {r.name or id_text(r.rx_id, r.rx_extended)} {why}")
            self.set_rules(self.rules)
            self.on_change()
        return False

    def _error(self, r: ResponseRule, text: str, once: bool = False) -> None:
        st = self.stats_of(r)
        st.errors += 1
        if once and st.last_error == text:
            return
        st.last_error = text
        self._log(f"Auto response ({r.name or id_text(r.rx_id, r.rx_extended)}): {text}")

    # ---- building + sending (scheduler thread) --------------------------------------------------------
    def build(self, c: _Compiled, trigger: CanFrame, rx_val: float | None) -> tuple[str, CanFrame]:
        r = c.rule
        channel = r.tx_channel or trigger.channel
        if r.tx_mode == TX_SIGNAL:
            msg = self.registry.message_by_id(channel, r.tx_id) if self.registry is not None else None
            if msg is None:
                raise RuleError(f"response id {id_text(r.tx_id, r.tx_extended)} is not in a loaded database "
                                f"(signal values need the DBC)")
            st = MessageState(msg)
            for name, fn in c.tx_signals.items():
                try:
                    st.set_signal(name, fn(rx_val))
                except KeyError:
                    raise RuleError(f"{msg.name} has no signal {name}") from None
            data = st.encode()
            fd = r.tx_fd or bool(msg.is_fd) or len(data) > 8
            if fd:
                data = data + bytes(valid_length(len(data), True) - len(data))
            return channel, CanFrame(r.tx_id, data, r.tx_extended, fd, fd and r.tx_brs)
        n = valid_length(max(r.tx_dlc, len(c.base)), r.tx_fd)
        buf = bytearray(c.base + bytes(n - len(c.base)))
        if c.tx_value is not None:
            v = c.tx_value(rx_val)
            write_raw(buf, r.tx_offset, r.tx_size, r.tx_order, round(v))
        return channel, CanFrame(r.tx_id, bytes(buf), r.tx_extended, r.tx_fd, r.tx_fd and r.tx_brs)

    def _fire(self, c: _Compiled, trigger: CanFrame, rx_val: float | None) -> None:
        r = c.rule
        try:
            channel, frame = self.build(c, trigger, rx_val)
            self._send(channel, frame)
        except Exception as e:  # noqa: BLE001  RuleError, unknown channel (KeyError), adapter NodeError
            self._error(r, f"{e}", once=True)
            return
        st = self.stats_of(r)
        st.hits += 1
        st.last_fired = time.time()
        if st.last_error:
            st.last_error = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="auto-response", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cv:
            self._running = False
            self._heap.clear()
            self._cv.notify()
        if self._thread:
            self._thread.join(2)
        self._thread = None

    def _run(self) -> None:
        pc = time.perf_counter
        while True:
            with self._cv:
                if not self._running:
                    return
                if not self._heap:
                    self._cv.wait(0.5)
                    continue
                delay = self._heap[0][0] - pc()
                if delay > 0.002:
                    self._cv.wait(delay - 0.0015)
                    continue
                due, _s, c, frame, rx_val = self._heap[0]
            while pc() < due:                               # sub-ms precision for short delays
                time.sleep(0)
            with self._cv:
                if not self._heap or self._heap[0][2] is not c or self._heap[0][3] is not frame:
                    continue
                heapq.heappop(self._heap)
            if self.enabled and c.rule.enabled:
                self._fire(c, frame, rx_val)

    # ---- persistence ----------------------------------------------------------------------------------
    def to_config(self) -> dict:
        return {"enabled": self.enabled, "rules": [r.to_dict() for r in self.rules]}

    def load_config(self, d: dict | None) -> list[str]:
        d = d or {}
        problems = self.set_rules([ResponseRule.from_dict(x) for x in d.get("rules", [])])
        self.enabled = bool(d.get("enabled", False))
        return problems
