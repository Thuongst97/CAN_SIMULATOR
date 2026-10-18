> **Note (2026-10-18):** SOME/IP was removed from the product; the SOME/IP parts of this plan are historical. The former code is in `archive/someip_removed_2026-10-18/`.

# AutoSim Studio – Detailed Implementation Plan

Source requirements: `PLAN.md`. Protocol: `docs/ESP32_PROTOCOL.md`.

## 1. Architecture

```
            ┌────────────────────── ui (PySide6) ──────────────────────┐
            │ panels  ←→  models (Qt)  ←→  presenters/EventBus bridge    │
            └───────────────▲───────────────────────▲───────────────────┘
                            │ EventBus (thread-safe)│
   ┌────────────────────────┴───┐         ┌─────────┴──────────────────┐
   │ core/can_engine            │         │ core/someip_engine         │
   │ DatabaseRegistry,          │         │ SomeIpEngine (asyncio      │
   │ MessageState, TxScheduler, │         │ thread), ProtoRegistry,    │
   │ RxDispatcher, BusManager   │         │ Provider/Consumer, codecs  │
   └──────────────┬─────────────┘         └─────────┬──────────────────┘
        CanBackend│                                 │ bind to NIC IP
   ┌──────────────┴────────────┐          ┌─────────┴──────────────────┐
   │ hardware/esp32_gateway    │          │ hardware/eth_interface     │
   │ protocol, SerialNode,     │          │ NIC list, multicast check  │
   │ NodeManager               │          │ (RAD-MOON)                 │
   └───────────────────────────┘          └────────────────────────────┘
```

Rules: `core/*` and `hardware/*` never import PySide6. Engines run on their own threads and publish via `EventBus`; the UI converts events to Qt signals (queued connection) and flushes in batches (≤30 Hz) so the GUI never blocks.

Threads: UI · CAN RX dispatcher · TX scheduler · one reader (+writer) per serial node · asyncio loop for SOME/IP.

## 2. Implementation status

| Milestone | State |
|---|---|
| M0 environment (uv, Python 3.11, ruff, pytest) | done |
| M1 protocol + hardware layer | **done** (SerialNode, NodeManager, Esp32Backend, in-process `SimNode`); firmware written (`firmware/esp32_can_node`, builds for ESP32 / S3 / C3, 22 host unit tests, cross-tested against the app); **not yet run on real hardware** |
| M2 CAN engine | done (DBC/ARXML via cantools, MessageState incl. multiplexing, TxScheduler, generators, RxDispatcher, CSV/ASC/BLF export) |
| M3 SOME/IP engine | done, **custom stack** (see decisions) – wire format, SD, UDP + TCP, provider/consumer, vsomeip JSON + best-effort ARXML, protobuf, primitive codec |
| M4 Qt shell | done (docks, menus, layout persistence, project save/load, dark theme) |
| M5 CAN panels | done (connection, DBC explorer, signal cards with sliders/enums/generators, trace with filters/export, plot) |
| M6 SOME/IP panels | done (services, method caller, publisher/subscriber, provider responses, SD log, dynamic protobuf form) |
| M7 packaging | PyInstaller build verified to launch; Inno Setup installer script present but not built on this machine |

27 automated tests (`uv run pytest`): protocol, CAN engine, hardware with the simulated node, SOME/IP over local UDP/TCP, and headless GUI flows.

### Decisions that deviate from PLAN.md
- **`someipy` is not used.** Version 2.x needs a Unix-domain-socket daemon (not workable on Windows) and is GPL-3. The engine is a custom asyncio implementation (`core/someip_engine/wire.py`, `sd.py`, `server.py`, `client.py`, `tcp.py`).
- The asyncio loop runs in a `QThread` (`ui/qt_loop.py`), injected into the engine so `core/` stays free of Qt.
- Default SD multicast group is `224.0.0.1:30490` (configurable; AUTOSAR setups often use `224.224.224.245`). All sockets bind to the NIC IP selected in the Connection panel.
- Timestamps use host receive time (see ESP32_PROTOCOL.md).

### Known limits / not done
- Hardware validation (real MCP2518FD / CAN bus timing and throughput, ISO/non-ISO, RAD-MOON, real multicast on the adapter).
- Two AutoSim instances on the *same* PC share SD port 30490 and cannot see each other's multicast; one engine hosting both roles works in-process.
- SD: no random initial delay / repetition phases, no multicast-eventgroup or IPv6/security options, no SOME/IP-TP.
- ARXML SOME/IP import is best effort (ids and event groups only; instance IDs and ports default).
- Repeated / map protobuf fields are edited as JSON text in the form.
- TX jitter on Windows has not been measured against the <1 ms goal; CAN TX rides over the serial link.
- The installer (`scripts/build.ps1`) needs Inno Setup, which is not installed here.

## 3. Milestones

Each milestone ends with something runnable and tests. Estimates for one developer.

### M0 – Environment & CI (0.5 d)
- Python 3.11 venv, `pip install -e .[dev]`, ruff + mypy + pytest running. Verify `someipy` version/API and whether Windows asyncio + multicast works (spike below).
- **Spike S1 (someipy): done – someipy 2.x is daemon-based and Unix-socket only, so a custom asyncio stack was built instead (see section 2).**

### M1 – Protocol & hardware layer (3 d)
1. `SerialNode`: reader thread with delimiter split → `Packet.decode`; writer queue; request/ACK with retry; heartbeat; state machine; clock offset from PING/PONG.
2. `NodeManager.scan()` using `serial.tools.list_ports` + HELLO probe.
3. `Esp32Backend` implementing `CanBackend`.
4. **Fake node**: `tests/fake_esp32.py` (pty/`loop://` pyserial URL) that speaks the protocol and loops frames back — enables hardware-free tests.
5. Firmware (`firmware/esp32_can_node`, ESP-IDF or Arduino + ACAN2517FD): implement the spec; 2 Mbaud, SPI 20 MHz, ISR-driven RX → ring buffer.
- Exit: PC sends FD frame (64 B, BRS) and receives loopback with timestamps; unplug/replug recovers.

### M2 – CAN engine (4 d)
1. `DatabaseRegistry.load/tree/message_by_id` (cantools; DBC + ARXML; multiplexed signals, value tables).
2. `MessageState`: defaults from DBC `initial` values, clamp, encode/decode. Unit-test against cantools reference vectors.
3. `TxScheduler`: heap of jobs, single thread, `perf_counter` with sleep-then-spin for ≤1 ms precision, drift-free reschedule, event trigger, signal change takes effect on next cycle. Measure jitter at 1 ms/10 ms with 50 messages.
4. `generators.py` + binding sampled at send time.
5. `RxDispatcher`: queue → DBC lookup → cycle-time per (channel,id) → batched publish.
6. `BusManager` multi-channel routing; `PythonCanBackend` (virtual bus) for dev.
7. `logger_io`: CSV, ASC, BLF export (python-can writers) and optional ASC/BLF replay.
- Exit: CLI script transmits DBC message with sine generator on two nodes and prints RX decode.

### M3 – SOME/IP engine (5 d)
1. `config_loader`: vsomeip JSON (services, instances, eventgroups, ports, reliable/unreliable) → `ServiceDef`; minimal ARXML SOME/IP deployment reader (service interfaces, event/method IDs) via lxml.
2. `ProtoRegistry`: compile `.proto` with `grpc_tools.protoc` to temp dir, import dynamically; `encode/decode` via dict↔message (`json_format`); `field_schema` including nested/repeated/enum/oneof.
3. `payload.py`: Raw (hex), Primitive (big-endian u8..u64, i*, f32/f64, bool, string w/ length, fixed arrays), Protobuf.
4. `SomeIpEngine`: asyncio thread; bind sockets to selected NIC IP; SD multicast join on that interface.
5. `ServiceProvider`: offer/stop-offer, eventgroup subscription handling, method handlers (echo/script/static response), `publish_event`, cyclic publisher.
6. `ServiceConsumer`: find, availability callbacks, subscribe/unsubscribe, `call_method` with timeout, request-ID tracking.
7. SD message log (offer/find/subscribe/ack) published on `someip.sd`.
- Exit: provider and consumer in two processes on RAD-MOON/loopback exchange a protobuf event and a method call; Wireshark shows valid SD.

### M4 – Qt shell (3 d)
`apply_theme` (qdarktheme + `dark.qss`), `MainWindow` with docks and QSettings layout restore, menu (project open/save, view toggles), `ProjectConfig` load/save, app bootstrap wiring services, log panel.

### M5 – CAN panels (5 d)
1. **Connection panel**: node table (port, status, bitrate/data rate/FD/BRS/ISO editors, Connect/Disconnect, stats from STATUS); NIC dropdown + IP/multicast/port fields (validates with `validate_multicast`).
2. **DBC explorer**: `DbcTreeModel` (Node › Message › Signal; columns start bit, length, byte order, factor, offset, min, max, unit); search box; drag message to Signal panel.
3. **Signal panel**: per-message card — TX enable, cycle ms, channel, FD/BRS, "send once"; per-signal slider/dial (range from DBC), spinbox, enum combo for value tables, generator picker (sine/ramp/square/toggle + params).
4. **Trace panel**: `TraceModel` (ring buffer 1 M rows, batched `beginInsertRows`), filter proxy (ID/name/node/channel, regex), pause/clear/autoscroll, cycle-time column, grouped "fixed" view option, export CSV/ASC/BLF.
5. Optional `PlotPanel` (pyqtgraph) for decoded signals.
- Exit: end-to-end demo with two ESP32 nodes (or fake nodes).

### M6 – SOME/IP panels (4 d)
1. Service tree (provided/consumed, availability state).
2. `ProtoForm`: widget factory from `field_schema` (spinboxes, checkboxes, enum combos, nested group boxes, repeated-field list editor) with raw-hex fallback tab and bidirectional sync.
3. **Method caller**: choose service/method, edit request, send, show response + latency + return code.
4. **Event publisher**: edit event/field, publish once or cyclic. **Subscriber view**: live decoded events table.
5. **SD log** table with filtering.
6. Server/client role toggles per service instance.

### M7 – Hardening & packaging (3 d)
Error dialogs/log, reconnect UX, performance pass (profile at 5 k frames/s RX), project file schema versioning, PyInstaller build, README/user guide.

Total ≈ 28 working days.

## 4. Testing strategy
- **Unit**: protocol (done), cobs/crc fuzz, `MessageState` encode/decode, generators, proto codec, vsomeip JSON parsing, filter proxy.
- **Integration (no hardware)**: fake ESP32 over `loop://`/pty; python-can virtual bus; SOME/IP loopback.
- **Performance targets**: RX ≥ 5 000 fps sustained with GUI responsive; TX jitter < 1 ms for 1 ms messages on scheduler thread; trace of 1 M rows without UI stall.
- **Hardware**: bench script with 2 nodes wired together, FD 500k/2M & 500k/5M, verify ISO vs non-ISO mismatch is detected (error frames).
- UI: `pytest-qt` smoke tests for each panel.

## 5. Risks / decisions
| Risk | Mitigation |
|---|---|
| `someipy` may lack features (TCP, SD subscribe nuances, Windows multicast binding) | Spike S1 in M0; fallback = thin own SOME/IP + SD implementation on `asyncio` sockets behind the same `ServiceProvider/Consumer` interface |
| Serial throughput: 2 Mbaud ≈ 200 KB/s; FD 64 B frames ≈ 80 B on wire → ~2 500 fps per node max | Use USB-CDC (full-speed 12 Mbit) on ESP32-S3; `TX_BATCH`; document limits |
| Windows timer resolution hurts TX jitter | `timeBeginPeriod(1)` + spin-wait tail; optionally move cyclic TX into the ESP32 (future `PERIODIC_ADD` message) |
| PySide6 UI flooding | Batch events, virtual models, ring buffer, decode only visible rows |
| RAD-MOON specifics (VLAN/media converter config) | Treat it as a plain NIC; no vendor SDK needed for L3 SOME/IP |
| ARXML variety | Support DBC first; ARXML via cantools for CAN, minimal reader for SOME/IP; expand on real samples |
| `ISO`/non-ISO selection | Expose in CONFIG b2; verify MCP2518FD `ISOCRCEN` bit behavior in firmware |

## 6. Next immediate steps
1. Install Python 3.11, create venv, `pip install -e .[dev]`, run `pytest`.
2. Run spike S1 (someipy loopback) and record findings here.
3. Start M1: `SerialNode` + fake node, then M2.
