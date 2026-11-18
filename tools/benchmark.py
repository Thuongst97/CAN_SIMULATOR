"""AutoSim Studio performance benchmark (no hardware needed).

    uv run python tools/benchmark.py            # all benchmarks, about 3 minutes
    uv run python tools/benchmark.py --quick    # shorter runs

Measures the software limits of the app on this machine:
  1. TX scheduler: frames/s and signals/s it can generate, cycle-time jitter at 1 / 5 / 10 ms cycles.
  2. Full TX path through a channel (BusManager -> ESP32 serial protocol -> simulated node -> RX back).
  3. RX path: frames/s the RX dispatcher can annotate and decode (the trace's input).
  4. Trace / log: rows per second into the GUI trace models, memory per row, export speed.
  5. Auto response: latency from a received frame to the response frame being sent.

The hardware links have their own, usually lower, limits (USB-UART 921600 baud, CAN bus load, slcan over USB CDC),
see docs/BENCHMARK.md.
"""
from __future__ import annotations

import argparse
import gc
import os
import platform
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autosim.core.can_engine.auto_response import AutoResponder, ResponseRule   # noqa: E402
from autosim.core.can_engine.bus_manager import BusManager, CanBackend          # noqa: E402
from autosim.core.can_engine.database import DatabaseRegistry                   # noqa: E402
from autosim.core.can_engine.generators import Sine                             # noqa: E402
from autosim.core.can_engine.rx_dispatcher import RxDispatcher, TraceRow        # noqa: E402
from autosim.core.can_engine.tx_scheduler import TxScheduler                    # noqa: E402
from autosim.core.common import clock                                           # noqa: E402
from autosim.core.common.events import EventBus                                 # noqa: E402
from autosim.core.common.frame import CanChannelConfig, CanFrame                # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


def report(section: str, name: str, value: str) -> None:
    RESULTS.append((section, name, value))
    print(f"  {name:<58} {value}", flush=True)


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


# ---- test database ------------------------------------------------------------------------------------
def make_dbc(path: Path, n_classic: int = 500, n_fd: int = 100) -> None:
    """n_classic 8-byte messages with 8 signals (8 bit each) and n_fd 64-byte CAN FD messages with 32 signals
    (16 bit each)."""
    lines = ['VERSION ""', "", "NS_ :", "", "BS_:", "", "BU_: SIM", ""]
    for i in range(n_classic):
        lines.append(f"BO_ {0x100 + i} M{i}: 8 SIM")
        for s in range(8):
            lines.append(f' SG_ S{i}_{s} : {s * 8}|8@1+ (1,0) [0|255] "" SIM')
        lines.append("")
    for i in range(n_fd):
        lines.append(f"BO_ {0x600 + i} FD{i}: 64 SIM")
        for s in range(32):
            lines.append(f' SG_ F{i}_{s} : {s * 16}|16@1+ (0.1,0) [0|6553.5] "" SIM')
        lines.append("")
    lines += ['BA_DEF_ BO_ "VFrameFormat" ENUM "StandardCAN","ExtendedCAN","reserved","reserved","reserved",'
              '"reserved","reserved","reserved","reserved","reserved","reserved","reserved","reserved","reserved",'
              '"StandardCAN_FD","ExtendedCAN_FD";',
              'BA_DEF_DEF_ "VFrameFormat" "StandardCAN";']
    lines += [f'BA_ "VFrameFormat" BO_ {0x600 + i} 14;' for i in range(n_fd)]
    path.write_text("\n".join(lines) + "\n")


class NullBackend(CanBackend):
    """Accepts every frame immediately: measures the app, not a link."""
    name = "null"

    def __init__(self) -> None:
        self.count = 0
        self.times: dict[int, list[float]] = {}
        self.record = False

    def open(self, cfg) -> None: ...

    def close(self) -> None: ...

    def set_rx_callback(self, cb) -> None: ...

    def send(self, frame: CanFrame) -> None:
        self.count += 1
        if self.record:
            self.times.setdefault(frame.arbitration_id, []).append(time.perf_counter())


# ---- 1. TX scheduler -----------------------------------------------------------------------------------
def bench_tx_scheduler(reg: DatabaseRegistry, dur: float) -> None:
    print("\n1. TX scheduler (cyclic messages -> app, no link limit)")
    msgs = [m for m in reg.messages() if m.name.startswith("M")]
    fd_msgs = [m for m in reg.messages() if m.name.startswith("FD")]

    def run(messages, cycle_ms: int, generators: bool, record: bool = False):
        be = NullBackend()
        be.record = record
        bus = BusManager()
        bus.add_channel("CAN1", be)
        tx = TxScheduler(bus.send)
        tx.start_thread()
        jobs = []
        for m in messages:
            j = tx.add_job("CAN1", m, cycle_ms)
            if generators:
                for s in m.signals:
                    tx.bind_generator(j, s.name, Sine(50, 1.0, 100))
            jobs.append(j)
        drain = threading.Event()

        def drainer():                    # the RX dispatcher's job in the app: keep frame_q empty
            q = bus.frame_q
            while not drain.is_set():
                try:
                    q.get(timeout=0.05)
                except Exception:
                    pass
        th = threading.Thread(target=drainer, daemon=True)
        th.start()
        for j in jobs:
            tx.start(j)
        time.sleep(0.3)                   # settle
        c0, t0 = be.count, time.perf_counter()
        be.times.clear()
        time.sleep(dur)
        c1, t1 = be.count, time.perf_counter()
        for j in jobs:
            tx.stop(j)
        tx.stop_thread()
        drain.set()
        th.join()
        return (c1 - c0) / (t1 - t0), be.times

    # jitter of a single message
    for cyc in (1, 5, 10):
        rate, times = run(msgs[:1], cyc, False, record=True)
        ts = next(iter(times.values()))
        d = [(b - a) * 1000 for a, b in zip(ts, ts[1:])]
        report("TX", f"1 message @ {cyc} ms: period mean / p99 / max [ms]",
               f"{statistics.mean(d):.3f} / {pct(d, 99):.3f} / {max(d):.3f}")
    # many messages at 1 ms with generators on every signal
    best = (0, 0)
    for n in (10, 50, 100, 200, 300, 500):
        rate, times = run(msgs[:n], 1, True, record=True)
        expected = n * 1000.0
        sig = rate * 8
        late = [((b - a) * 1000) for ts in times.values() for a, b in zip(ts, ts[1:])]
        report("TX", f"{n:>3} msgs x 1 ms, 8 signals each, all with generators",
               f"{rate:9.0f} frames/s ({100 * rate / expected:5.1f} %), {sig:9.0f} signals/s, "
               f"period p99 {pct(late, 99):.2f} ms")
        if rate >= 0.97 * expected:
            best = (n, rate)
    report("TX", "largest 1 ms set kept on time (>= 97 %)", f"{best[0]} messages = {best[1]:.0f} frames/s, "
           f"{best[1] * 8:.0f} signals/s")
    # saturation: everything at 1 ms
    for gens in (False, True):
        rate, _ = run(msgs, 1, gens)
        report("TX", f"saturation: 500 msgs x 1 ms, generators {'on' if gens else 'off'}",
               f"{rate:9.0f} frames/s, {rate * 8:9.0f} signals/s")
    rate, _ = run(fd_msgs, 1, True)
    report("TX", "saturation: 100 CAN FD 64-byte msgs x 1 ms (32 signals, generators)",
           f"{rate:9.0f} frames/s, {rate * 32:9.0f} signals/s")


# ---- 2. Full TX path through the ESP32 protocol to a simulated node ------------------------------------
def bench_full_path(reg: DatabaseRegistry, dur: float) -> None:
    print("\n2. Full path: scheduler -> ESP32 serial protocol -> simulated node (loopback) -> RX -> trace queue")
    from autosim.hardware.esp32_gateway.manager import NodeManager
    msgs = [m for m in reg.messages() if m.name.startswith("M")]
    for n in (10, 50, 100, 200):
        mgr = NodeManager()
        backend = mgr.create_backend("SIM1", "CAN1")
        bus = BusManager()
        bus.add_channel("CAN1", backend, CanChannelConfig())        # the simulated node loops TX back as RX
        tx = TxScheduler(bus.send)
        tx.start_thread()
        rx = [0]
        tx_n = [0]
        stop = threading.Event()

        def drainer():
            while not stop.is_set():
                try:
                    f = bus.frame_q.get(timeout=0.05)
                    if f.is_rx:
                        rx[0] += 1
                    else:
                        tx_n[0] += 1
                except Exception:
                    pass
        th = threading.Thread(target=drainer, daemon=True)
        th.start()
        jobs = [tx.add_job("CAN1", m, 1) for m in msgs[:n]]
        for j in jobs:
            tx.start(j)
        time.sleep(0.5)
        r0, t0c, t0 = rx[0], tx_n[0], time.perf_counter()
        time.sleep(dur)
        r1, t1c, t1 = rx[0], tx_n[0], time.perf_counter()
        for j in jobs:
            tx.stop(j)
        tx.stop_thread()
        stop.set()
        th.join()
        bus.close_all()
        for ch in list(mgr.nodes):
            mgr.release(ch)
        el = t1 - t0
        report("PATH", f"{n:>3} msgs x 1 ms through SIM1 (expected {n * 1000} frames/s)",
               f"TX {(t1c - t0c) / el:8.0f} /s, RX back {(r1 - r0) / el:8.0f} /s")


def bench_protocols(n: int) -> None:
    print("\n2b. Link protocols: app-side cost per frame, bytes on the wire, link and bus limits")
    from autosim.hardware.esp32_gateway import protocol as P
    from autosim.hardware.slcan import protocol as S
    for label, length, fd in (("classic 8 B", 8, False), ("CAN FD 64 B", 64, True)):
        f = CanFrame(0x123, bytes(range(length)), False, fd, fd, 0.0, "CAN1", False)
        pk = P.Packet(P.MsgType.TX_FRAME, 1, P.encode_can_payload(f)).encode()
        t0 = time.perf_counter()
        for _ in range(n):
            P.Packet(P.MsgType.TX_FRAME, 1, P.encode_can_payload(f)).encode()
        enc = n / (time.perf_counter() - t0)
        block = pk.rstrip(b"\0")
        t0 = time.perf_counter()
        for _ in range(n):
            P.decode_can_payload(P.Packet.decode(block).payload, "CAN1")
        dec = n / (time.perf_counter() - t0)
        wire = len(pk) + 0
        report("LINK", f"ESP32 protocol {label}: encode / decode per second",
               f"{enc:8.0f} / {dec:8.0f} frames/s, {wire} bytes per frame on the wire")
        report("LINK", f"  USB-UART 921600 baud carries ({label})", f"{92160 / wire:8.0f} frames/s per direction")
        line = S.encode_frame(f)
        t0 = time.perf_counter()
        for _ in range(n):
            S.encode_frame(f)
        enc = n / (time.perf_counter() - t0)
        txt = line.decode().strip()
        t0 = time.perf_counter()
        for _ in range(n):
            S.decode_frame(txt)
        dec = n / (time.perf_counter() - t0)
        report("LINK", f"slcan (CANable) {label}: encode / decode per second",
               f"{enc:8.0f} / {dec:8.0f} frames/s, {len(line)} bytes per frame")

    def frame_us(nominal, data, length, fd, brs):
        """Worst-case-ish CAN frame duration (standard ID, stuffing ~10 %, 3-bit IFS)."""
        if not fd:
            bits = 1 + 11 + 1 + 1 + 1 + 4 + 8 * length + 15 + 1 + 1 + 1 + 7 + 3
            return bits * 1.1 / nominal * 1e6
        crc = 17 if length <= 16 else 21
        arb = 1 + 11 + 1 + 1 + 1 + 1                     # SOF, ID, RRS, IDE, FDF, res
        ctl_data = 1 + 4 + 8 * length + 4 + crc          # ESI, DLC, data, stuff count, CRC
        tail = 1 + 1 + 1 + 7 + 3                         # CRC delim, ACK, ACK delim, EOF, IFS
        rate = data if brs else nominal
        return (arb * 1.1 + tail) / nominal * 1e6 + ctl_data * 1.1 / rate * 1e6
    for label, nom, dat, ln, fd in (("500k classic 8 B", 500e3, 500e3, 8, False),
                                     ("1M classic 8 B", 1e6, 1e6, 8, False),
                                     ("500k/2M FD 64 B", 500e3, 2e6, 64, True),
                                     ("500k/5M FD 64 B", 500e3, 5e6, 64, True),
                                     ("1M/8M FD 64 B", 1e6, 8e6, 64, True)):
        us = frame_us(nom, dat, ln, fd, True)
        report("BUS", f"CAN bus {label}: one frame / 100 % bus load", f"{us:6.0f} µs -> {1e6 / us:6.0f} frames/s")


# ---- 3. RX dispatcher ---------------------------------------------------------------------------------
def bench_rx(reg: DatabaseRegistry, n: int) -> None:
    print("\n3. RX path: frames annotated + decoded by the RX dispatcher (feeds the trace and the plot)")
    msgs = [m for m in reg.messages() if m.name.startswith("M")][:50]
    for watched in (0, 5):
        bus = BusManager()
        ev = EventBus()
        rxd = RxDispatcher(reg, bus, ev)
        for i in range(watched):
            rxd.watch(msgs[i].name, msgs[i].signals[0].name)
        frames = [CanFrame(msgs[i % 50].frame_id, bytes(range(8)), False, False, False, 0.0, "CAN1", True)
                  for i in range(n)]
        for k, f in enumerate(frames):
            f.timestamp = k * 1e-4
        for f in frames:
            bus.frame_q.put_nowait(f)
        t0 = time.perf_counter()
        rxd.start()
        while rxd.rx_count < n:
            time.sleep(0.005)
        el = time.perf_counter() - t0
        rxd.stop()
        report("RX", f"RX dispatcher, {watched} plotted signals",
               f"{n / el:9.0f} frames/s ({8 * n / el:9.0f} signals decoded/s)")
    report("RX", "frame queue between adapters and the dispatcher", "200 000 frames (then new frames are dropped)")


# ---- 4. Trace models (GUI) -------------------------------------------------------------------------------
def bench_trace(reg: DatabaseRegistry, n: int) -> None:
    print("\n4. Trace / log (GUI models, offscreen)")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from autosim.ui.models.fixed_trace_model import FixedTraceModel
    from autosim.ui.models.trace_model import TraceModel
    msgs = [m for m in reg.messages() if m.name.startswith("M")][:50]

    def rows(count):
        out = []
        for i in range(count):
            f = CanFrame(msgs[i % 50].frame_id, bytes([i & 0xFF] * 8), False, False, False, i * 1e-4, "CAN1",
                         True)
            out.append(TraceRow(f, msgs[i % 50].name, 1.0, True))
        return out

    data = rows(n)
    model = TraceModel()
    t0 = time.perf_counter()
    for i in range(0, n, 2000):                   # batches as the dispatcher sends them
        model.add_batch(data[i:i + 2000])
        app.processEvents()
    el = time.perf_counter() - t0
    report("TRACE", "chronological trace: rows added per second", f"{n / el:9.0f} rows/s")
    report("TRACE", "chronological trace capacity (ring buffer)", f"{model.max_rows:,} rows (oldest dropped)")
    fixed = FixedTraceModel(reg)
    t0 = time.perf_counter()
    for i in range(0, n, 2000):
        fixed.add_batch(data[i:i + 2000])
        app.processEvents()
    el = time.perf_counter() - t0
    report("TRACE", "fixed trace (one row per message): frames per second", f"{n / el:9.0f} frames/s")
    del model, fixed
    gc.collect()
    # memory per row
    tracemalloc.start()
    m2 = TraceModel()
    big = rows(200_000)
    for i in range(0, len(big), 5000):
        m2.add_batch(big[i:i + 5000])
    cur, _peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    report("TRACE", "memory for a full 200 000-row trace", f"{cur / 1e6:6.0f} MB ({cur / 200_000:.0f} bytes/row)")
    # export
    from autosim.core.can_engine import logger_io
    rows_exp = m2.rows()
    with tempfile.TemporaryDirectory() as d:
        for ext in ("csv", "asc", "blf"):
            p = Path(d) / f"t.{ext}"
            t0 = time.perf_counter()
            k = logger_io.export_by_extension(rows_exp, p)
            el = time.perf_counter() - t0
            report("TRACE", f"export {k:,} rows to .{ext}",
                   f"{el:5.2f} s ({k / el:9.0f} rows/s, {p.stat().st_size / 1e6:5.1f} MB)")
    del m2, big, rows_exp
    gc.collect()


# ---- 5. Auto response latency ------------------------------------------------------------------------
def bench_auto_response(reg: DatabaseRegistry, n: int) -> None:
    print("\n5. Auto response (received frame -> response sent)")

    class Echo(NullBackend):
        def __init__(self) -> None:
            super().__init__()
            self.sent_at: list[float] = []

        def send(self, frame) -> None:
            self.sent_at.append(time.perf_counter())

    be = Echo()
    bus = BusManager()
    bus.add_channel("CAN1", be)
    resp = AutoResponder(reg, bus.send, log=lambda t: None)
    resp.enabled = True
    resp.set_rules([ResponseRule(name="r", rx_id=0x21, match="raw", op="==", value="10", raw_offset=0,
                                 tx_id=0x27, tx_dlc=8, tx_value="rx", tx_offset=0)])
    bus.add_rx_listener(resp.on_frame)
    resp.start()
    lat = []
    for i in range(n):
        f = CanFrame(0x21, bytes([10, 0, 0, 0, 0, 0, 0, 0]), False, False, False, 0.0, "", True)
        k = len(be.sent_at)
        t0 = time.perf_counter()
        bus._on_rx("CAN1", f)
        end = t0 + 1.0
        while len(be.sent_at) == k and time.perf_counter() < end:
            time.sleep(0)                 # yield the GIL (a busy loop would add Python's 5 ms switch interval)
        if len(be.sent_at) == k:
            raise RuntimeError("no response (rule switched off by the loop guard?)")
        lat.append((be.sent_at[-1] - t0) * 1e6)
        try:
            while True:
                bus.frame_q.get_nowait()
        except Exception:
            pass
        time.sleep(0.002)                 # 500 triggers/s: below the 1000/s loop guard
    resp.stop()
    report("AUTO", "latency in the app, delay 0 ms: median / p99 / max [µs]",
           f"{statistics.median(lat):.0f} / {pct(lat, 99):.0f} / {max(lat):.0f}")
    report("AUTO", "loop guard: a rule firing more than this is switched off", "1 000 responses/s per rule")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    dur = 1.0 if a.quick else 2.0
    print(f"AutoSim Studio benchmark - {platform.system()} {platform.release()}, Python "
          f"{platform.python_version()}, {os.cpu_count()} logical CPUs, {platform.processor() or platform.machine()}")
    with tempfile.TemporaryDirectory() as d:
        dbc = Path(d) / "bench.dbc"
        make_dbc(dbc)
        reg = DatabaseRegistry()
        reg.load(dbc)
        bench_tx_scheduler(reg, dur)
        bench_full_path(reg, dur)
        bench_protocols(20_000)
        bench_rx(reg, 50_000 if a.quick else 200_000)
        bench_trace(reg, 50_000 if a.quick else 200_000)
        bench_auto_response(reg, 300 if a.quick else 2000)
    _ = clock


if __name__ == "__main__":
    main()
