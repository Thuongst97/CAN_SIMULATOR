# AutoSim Studio – architecture and how it works

AutoSim Studio is a Windows desktop tool that **simulates CAN / CAN FD nodes and analyses the bus**: it loads a DBC
database, transmits messages with signal values you set (or that generators drive), receives every frame on the bus,
decodes it and shows it in a trace and a plot. Real buses are reached through **CANable 2.0** USB-CAN FD adapters;
built-in **simulated nodes** (SIM1, SIM2) allow working without hardware.

Contents: [1 System context](#1-system-context) · [2 System design](#2-system-design) ·
[3 Component diagram](#3-component-diagram) · [4 Communication diagram](#4-communication-diagram) ·
[5 Sequence diagrams](#5-sequence-diagrams) · [6 How it works](#6-how-it-works) ·
[7 Build, test, deploy](#7-build-test-and-deploy) · [8 Extending](#8-extending-the-tool)

Diagrams are [Mermaid](https://mermaid.js.org/) – GitHub and VS Code render them.

---

## 1. System context

```mermaid
flowchart LR
    user(["Engineer"])
    dbc[("DBC / ARXML<br/>database")]
    subgraph PC["Windows PC"]
        app["AutoSim Studio<br/>(Python 3.11, PySide6)"]
    end
    subgraph HW["Hardware"]
        c1["CANable 2.0 #1<br/>STM32G431 · canable2-fw (slcan)"]
        c2["CANable 2.0 #2"]
    end
    bus{{"CAN / CAN FD bus<br/>(CANH / CANL, 120 Ω each end)"}}
    ecu["ECUs / other nodes<br/>(optional)"]

    user -- "configure, send, observe" --> app
    dbc -- "messages + signals" --> app
    app <-- "USB CDC (virtual COM port)<br/>slcan text protocol" --> c1
    app <-- "USB CDC" --> c2
    c1 <--> bus
    c2 <--> bus
    ecu <--> bus
```

| External element | Role |
|---|---|
| Engineer | loads databases, opens channels, sets signal values, starts cyclic transmission, reads trace / plot |
| DBC / ARXML file | defines frames (ID, length, FD) and signals (bit layout, factor, offset, range, unit, value tables) |
| CANable 2.0 | USB-CAN FD adapter; its open-source firmware (`firmware/canable2_stm32/`) speaks **slcan** over a virtual COM port |
| CAN bus | 500 k / 2 M (or other) CAN FD bus; two CANables on one bus can talk to each other (loop test) |

---

## 2. System design

### 2.1 Layers

```mermaid
flowchart TB
    subgraph L1["UI layer · src/autosim/ui (PySide6 / Qt Widgets)"]
        direction LR
        shell["Shell: main window, app bar, sidebar,<br/>dock panels, status bar, command palette"]
        panels["Panels: Channels, Database, Signals,<br/>Signal plot, Bus trace, Log"]
        theme["Design tokens + theme"]
    end
    subgraph L2["Core layer · src/autosim/core (pure Python, no Qt)"]
        direction LR
        engine["CAN engine: database, signal codec,<br/>TX scheduler, RX dispatcher, bus manager"]
        common["Common: frames, EventBus, config, clock"]
    end
    subgraph L3["Hardware layer · src/autosim/hardware"]
        direction LR
        slcan["slcan: CANable node + backend"]
        gw["Node manager, simulated nodes,<br/>AutoSim serial protocol"]
    end
    subgraph L4["Adapter firmware · firmware/canable2_stm32"]
        fw["canable2-fw (STM32G431, FDCAN)"]
    end
    L1 -- "calls (GUI thread)" --> L2
    L2 -- "EventBus callbacks → Qt signals (queued)" --> L1
    L2 -- "CanBackend interface" --> L3
    L3 -- "USB serial: slcan" --> L4
```

**Design rules**

* **The core never imports Qt.** Engines run on their own threads and publish results on a thread-safe `EventBus`;
  the UI's `QtBridge` turns each topic into a Qt signal, which Qt delivers *queued* on the GUI thread. The engine can
  therefore be tested (and scripted) without a GUI.
* **One interface per adapter type.** Everything that carries frames implements `CanBackend`
  (`open(cfg) / send(frame) / close() / set_rx_callback(cb)`); the bus manager does not know whether a channel is a
  CANable, a simulated node or a python-can interface.
* **One channel per adapter**, any number of channels in parallel (CAN1, CAN2, …), each on its own threads.
* **The GUI never blocks on hardware:** sends are a short serial write; receives arrive through a queue and are
  pushed to the GUI in batches (~30 per second).

### 2.2 Thread model

| Thread | Owner | Work |
|---|---|---|
| GUI (main) | Qt | widgets, models, timers (trace highlight 120 ms, signal panel 150 ms, plot 50 ms, status 1 s) |
| `can-tx` | `TxScheduler` | heap of due transmissions; sleeps, then spins the last ~2 ms for precise cycle times; encodes and sends |
| `can-rx` | `RxDispatcher` | takes frames from `BusManager.frame_q`, decodes / annotates them, publishes `TraceRow` batches every 33 ms |
| `slcan-rx-<ch>` | `SlcanNode` (one per CANable) | reads the COM port, splits slcan lines, delivers frames and the error register |
| `slcan-status-<ch>` | `SlcanNode` | every second: polls the error register (`E`), publishes node status |
| `sim-node`, `sim-node-status` | `SimNode` (per simulated channel) | firmware-like simulated node behind an in-memory pipe |
| `<name>-<ch>` | `SerialNode` | reader / writer of the AutoSim serial protocol (simulated nodes) |

Shared state is protected by locks (`TxScheduler._cv`, `MessageState._lock`, `RxDispatcher._lock`,
`DatabaseRegistry._lock`); the frame queue (`queue.Queue`, 200 000 entries) decouples the adapters from decoding.

### 2.3 Data model

```mermaid
classDiagram
    class CanFrame {
        arbitration_id: int
        data: bytes
        is_extended: bool
        is_fd: bool
        bitrate_switch: bool
        timestamp: float
        channel: str
        is_rx: bool
    }
    class CanChannelConfig {
        nominal_bitrate: int
        data_bitrate: int
        fd_enabled: bool
        brs: bool
        iso_mode: bool
        listen_only: bool
        extras: dict
    }
    class TraceRow {
        frame: CanFrame
        name: str
        cycle_ms: float
    }
    class TxJob {
        channel: str
        message
        state: MessageState
        cycle_ms: int
        fd: bool
        brs: bool
        enabled: bool
        bindings: signal → Generator
        count: int
    }
    class MessageState {
        values: signal → physical value
        set_signal(name, v)
        encode() bytes
    }
    class NodeSetting {
        port, channel_name
        nominal / data bitrate
        fd, brs, iso, loopback
        autoconnect
    }
    class ProjectConfig {
        dbc_paths: list
        nodes: list~NodeSetting~
        save(path) / load(path)
    }
    TraceRow --> CanFrame
    TxJob --> MessageState
    ProjectConfig --> NodeSetting
```

---

## 3. Component diagram

```mermaid
flowchart LR
    subgraph UI["ui"]
        MW["MainWindow<br/>main_window.py"]
        BR["QtBridge<br/>bridge.py"]
        CP["CanConnectionPanel<br/>panels/connection_panel.py"]
        DB["DbcExplorer<br/>panels/dbc_explorer.py"]
        SP["SignalPanel / MessageCard / SignalRow<br/>panels/signal_panel.py"]
        PP["PlotPanel (pyqtgraph)<br/>panels/plot_panel.py"]
        TP["TracePanel<br/>panels/trace_panel.py"]
        TM["TraceModel · FixedTraceModel<br/>models/"]
        LG["LogPanel"]
        SH["Shell: AppBar, Sidebar, Panel docks,<br/>AppStatusBar, CommandRegistry, BusLoadMeter"]
        TH["ThemeManager · tokens · themes<br/>theme.py, styles/"]
    end
    subgraph CORE["core"]
        CTX["AppContext<br/>context.py"]
        EB["EventBus<br/>common/events.py"]
        ENG["CanEngine<br/>can_engine/engine.py"]
        REG["DatabaseRegistry<br/>(cantools)"]
        TX["TxScheduler"]
        RX["RxDispatcher"]
        BM["BusManager<br/>CanBackend registry"]
        GEN["Generators<br/>sine · ramp · square · toggle"]
        LIO["logger_io<br/>CSV · ASC · BLF export"]
    end
    subgraph HWL["hardware"]
        NM["NodeManager<br/>port scan · kind detection"]
        SN["SlcanNode + SlcanBackend<br/>slcan/node.py"]
        SPR["slcan protocol<br/>encode · decode · LineSplitter"]
        SIM["SimNode + SerialNode + Esp32Backend<br/>(simulated channels)"]
    end
    FW["CANable 2.0<br/>canable2-fw"]

    MW --> CP & DB & SP & PP & TP & LG & SH
    MW --> TH
    CTX --> EB & ENG & NM
    ENG --> REG & TX & RX & BM
    TX --> GEN
    TX -- "send(channel, frame)" --> BM
    BM -- "frame_q (RX + TX)" --> RX
    RX -- "CAN_RX batches" --> EB
    NM -- "NODE_STATE, LOG" --> EB
    ENG -- "CAN_DB_LOADED, LOG" --> EB
    EB --> BR
    BR -- "canBatch" --> TP & SH
    BR -- "nodeState" --> CP & SH
    BR -- "dbLoaded / log" --> DB & SP & PP & LG
    TP --> TM
    TP -- "export" --> LIO
    CP -- "create_backend" --> NM
    CP -- "open_channel / close_channel" --> ENG
    SP -- "add_job · set_signal · bind_generator" --> ENG
    DB -- "load_database" --> ENG
    PP -- "watch / snapshot" --> RX
    NM --> SN & SIM
    BM -. "CanBackend" .-> SN & SIM
    SN --> SPR
    SN <-- "USB CDC · slcan" --> FW
```

| Component | Responsibility | Key code |
|---|---|---|
| `AppContext` | creates and owns the event bus, CAN engine, node manager, project config | `context.py` |
| `CanEngine` | facade for the UI: load databases, open / close channels, create TX jobs, set signals, bind generators | `core/can_engine/engine.py` |
| `DatabaseRegistry` | loads DBC / ARXML / KCD / SYM with cantools, id → message lookup per channel, decode | `database.py` |
| `MessageState` | current physical signal values of one TX message; clamps to the DBC range; encodes the payload | `signal_codec.py` |
| `TxScheduler` | cyclic and one-shot transmission, generator evaluation, precise timing | `tx_scheduler.py` |
| `BusManager` | channel name → backend; routes `send`; puts every RX **and** TX frame into `frame_q` | `bus_manager.py` |
| `RxDispatcher` | decodes / annotates frames (name, cycle time), feeds plot buffers, publishes trace batches | `rx_dispatcher.py` |
| `EventBus` / `QtBridge` | thread-safe pub/sub; topics `CAN_RX`, `NODE_STATE`, `CAN_DB_LOADED`, `LOG` → Qt signals | `events.py`, `ui/bridge.py` |
| `NodeManager` | lists COM ports, recognises CANables by USB ID, builds the right node + backend, publishes node state | `hardware/esp32_gateway/manager.py` |
| `SlcanNode` / `SlcanBackend` | slcan session with one CANable: version, configuration, open, frames, error register | `hardware/slcan/` |
| Simulated nodes | `SimNode` behind an in-memory pipe, spoken to by `SerialNode` with the AutoSim protocol | `hardware/esp32_gateway/` |
| Panels | Channels, Database, Signals, Plot, Trace, Log – see section 6 | `ui/panels/` |
| Shell | app bar, sidebar, docks with versioned layout, status bar, bus-load estimate, commands / shortcuts | `ui/shell/` |
| Theme | design tokens (dark / light), stylesheet, fonts (Segoe UI), icons | `ui/theme.py`, `ui/styles/` |

---

## 4. Communication diagram

How the objects collaborate when CAN1 transmits a message cyclically and CAN2 (on the same bus) receives it.
Numbers give the order; `a/b` are concurrent branches.

```mermaid
flowchart TB
    U(["Engineer"])
    SR["SignalRow /<br/>MessageCard"]
    E["CanEngine"]
    T["TxScheduler<br/>(can-tx)"]
    B["BusManager"]
    N1["SlcanNode CAN1"]
    A1["CANable #1"]
    A2["CANable #2"]
    N2["SlcanNode CAN2<br/>(slcan-rx)"]
    Q[("frame_q")]
    R["RxDispatcher<br/>(can-rx)"]
    EB["EventBus"]
    QB["QtBridge"]
    TR["TracePanel +<br/>trace models"]
    SB["Status bar /<br/>BusLoadMeter"]

    U -- "1: set value, tick Cyclic TX" --> SR
    SR -- "2: set_signal() / tx.start(job)" --> E
    E -- "3: schedule job" --> T
    T -- "4: encode → send(CAN1, frame)" --> B
    B -- "5: backend.send()" --> N1
    N1 -- "6: 'b04A20…' (slcan line)" --> A1
    B -- "7a: TX copy" --> Q
    A1 -- "7b: CAN FD frame on the bus" --> A2
    A2 -- "8: 'b04A20…' over USB" --> N2
    N2 -- "9: on_rx(frame) → _on_rx" --> B
    B -- "10: RX frame" --> Q
    Q -- "11: get()" --> R
    R -- "12: publish CAN_RX [TraceRow…] (every 33 ms)" --> EB
    EB -- "13: canBatch (queued to GUI thread)" --> QB
    QB -- "14a: on_batch()" --> TR
    QB -- "14b: add_rows()" --> SB
```

---

## 5. Sequence diagrams

### 5.1 Application start-up

```mermaid
sequenceDiagram
    autonumber
    participant M as app.main()
    participant TH as ThemeManager
    participant C as AppContext
    participant E as CanEngine
    participant W as MainWindow
    participant S as QSettings (registry)
    M->>TH: apply_theme(saved mode)
    Note right of TH: palette, Segoe UI font,<br/>stylesheet, input policy filter
    M->>C: AppContext()
    C->>E: CanEngine(EventBus)
    M->>C: start()
    C->>E: start(): can-tx and can-rx threads
    M->>W: MainWindow(ctx)
    W->>W: build panels, dock workspace, app bar,<br/>status bar, commands / shortcuts
    W->>S: read layout/version
    alt same LAYOUT_VERSION
        W->>W: restoreState(main + dock workspace)
    else missing or other version
        W->>W: reset_layout() (default docks)
    end
    W->>S: last/port, last/dbc
    W-->>E: load last databases (deferred)
    W->>W: log resolved fonts, validate layout on show
    M->>W: show(), app.exec()
```

### 5.2 Connecting a CANable

```mermaid
sequenceDiagram
    autonumber
    actor U as Engineer
    participant CP as CanConnectionPanel
    participant NM as NodeManager
    participant E as CanEngine / BusManager
    participant N as SlcanNode
    participant A as CANable (firmware)
    participant SP as SignalPanel
    U->>CP: Scan
    CP->>NM: scan()
    NM-->>CP: ports + kind (USB 16D0:117E / AD50:60C4 = CANable)
    U->>CP: Connect (or Connect all)
    CP->>NM: create_backend(port, "CAN1")
    NM->>N: SlcanNode(port), SlcanBackend(node)
    CP->>E: open_channel("CAN1", backend, cfg)
    E->>N: backend.open(cfg)
    N->>A: open COM port, start slcan-rx thread
    N->>A: C (close any old session)
    N->>A: V
    A-->>N: "b158aa7 github.com/…"  (firmware version)
    N->>A: E
    A-->>N: error register (latched baseline)
    N->>N: configure: S6 Y2 M0 A1 (500 k / 2 M, normal, auto-retransmit)
    N->>A: C, S6, Y2, M0, A1, O
    N->>N: start slcan-status thread, state RUNNING
    N-->>NM: on_state / on_status
    NM-->>CP: NODE_STATE via EventBus → QtBridge
    CP->>CP: table row, status pill, propose next free port
    CP-->>SP: channelsChanged([CAN1, …])
    SP->>SP: messages without a channel take CAN1
```

### 5.3 Cyclic transmission and reception (two adapters on one bus)

```mermaid
sequenceDiagram
    autonumber
    participant T as TxScheduler (can-tx)
    participant MS as MessageState
    participant B as BusManager
    participant N1 as SlcanNode CAN1
    participant A1 as CANable #1
    participant A2 as CANable #2
    participant N2 as SlcanNode CAN2 (slcan-rx)
    participant Q as frame_q
    participant R as RxDispatcher (can-rx)
    participant UI as GUI thread (trace, status)
    loop every cycle_ms (heap, sleep, then spin the last ~2 ms)
        T->>MS: generators → set_signal(), encode()
        MS-->>T: payload (padded to the DBC length)
        T->>B: send("CAN1", CanFrame FD+BRS)
        B->>N1: backend.send(frame)
        N1->>A1: "b04A20…\r" (slcan, zero-padded to an FD length)
        B->>Q: TX copy (is_rx=False, timestamp)
        A1-)A2: CAN FD frame on the bus (ACK by #2)
        A2->>N2: "b04A20…\r" over USB
        N2->>B: on_rx(frame) → channel CAN2, is_rx=True, timestamp
        B->>Q: RX frame
    end
    loop continuously
        R->>Q: get(timeout 33 ms)
        R->>R: decode (DBC), cycle time per id, plot buffers
    end
    R->>UI: every 33 ms: CAN_RX [TraceRow…] (EventBus → QtBridge, queued)
    UI->>UI: chronological + fixed trace models, bus load, frames/s
```

### 5.4 Editing a signal and using a generator

```mermaid
sequenceDiagram
    autonumber
    actor U as Engineer
    participant SR as SignalRow (GUI)
    participant E as CanEngine
    participant J as TxJob / MessageState
    participant T as TxScheduler (can-tx)
    U->>SR: expand the signal, move slider / type a value
    SR->>E: set_signal(job, name, value)
    E->>J: set_signal: clamp to the DBC range, store (lock)
    J-->>SR: stored value
    SR->>SR: summary, raw value, value-table entry
    Note over T,J: the next cycle encodes the new value
    U->>SR: Generator = Ramp (low, high, period)
    SR->>E: bind_generator(job, name, Ramp)
    E->>T: bindings[name] = generator
    loop each transmission
        T->>J: value = generator(t), set_signal
    end
    loop every 150 ms (panel timer)
        SR->>J: get_signal(name)
        SR->>SR: show the live value (read-only while driven)
    end
```

### 5.5 Adapter error monitoring

```mermaid
sequenceDiagram
    autonumber
    participant ST as SlcanNode (slcan-status)
    participant A as CANable
    participant RD as SlcanNode (slcan-rx)
    participant NM as NodeManager
    participant UI as GUI (channels, app bar, status bar, log)
    loop every second
        ST->>A: E
        A-->>RD: "CANable Error Register: 04"
        RD->>RD: new bits = register & ~already-reported
        alt new error bits
            RD->>NM: on_error(code, "CAN TX failed …")
            NM->>UI: LOG (EventBus) → Log panel
        end
        ST->>NM: on_status(rx / tx / errors)
        NM->>UI: NODE_STATE → table row, status pill ("1 warning"), Err counter
    end
    Note over A: the firmware latches error flags until the adapter is re-plugged,<br/>the baseline read at connect stops old flags being reported as new
```

---

## 6. How it works

### 6.1 Databases and decoding

* `DatabaseRegistry.load()` reads DBC / ARXML / KCD / SYM with **cantools** (non-strict). Real-world DBCs that declare
  ids above 0x7FF without the extended flag are repaired on the fly (treated as 29-bit ids; impossible ids skipped) and
  reported in the Log.
* Databases can be global (`*`) or bound to a channel; `message_by_id(channel, id)` looks in the channel's databases
  first. `decode(frame)` pads short payloads and returns physical values (factor / offset applied).
* The Database panel shows node → message → signal; double-click (or *Add to signals*) creates a TX message.

### 6.2 Transmission

* A **TX job** = message + `MessageState` (current physical values, initialised from the DBC) + cycle time + FD / BRS
  flags + generator bindings. Each section in the Signals panel owns one job; the same message can be sent on several
  channels (one section per channel).
* `MessageState.set_signal()` clamps to the DBC range and stores under a lock; `encode()` packs all signals
  (cantools, padding) – so the GUI and the TX thread never see a half-updated payload.
* `TxScheduler` keeps a heap of `(due time, job)`. Its thread sleeps until ~2 ms before the due time and then spins,
  giving sub-millisecond cycle timing without busy-waiting the whole time; when it falls behind it re-bases instead of
  bursting. Generators (sine, ramp, square, toggle) are evaluated just before encoding. *Send once* calls the same
  path immediately.
* A send error stops that job and is logged; closing a channel stops its jobs.

### 6.3 Reception, decoding and the trace

* Adapters deliver frames on their reader threads into `BusManager._on_rx`, which stamps channel / direction / time
  and puts them into `frame_q`; transmitted frames are put there too, so the trace shows both directions.
* `RxDispatcher` empties the queue on its own thread: cycle time per (channel, id, direction), decoding, samples for
  watched plot signals – and publishes **batches every 33 ms** (≈30 Hz), so thousands of frames per second cost the
  GUI ~30 updates per second.
* The **Bus trace** has two models fed by the same batches:
  * *Fixed* (default): one row per (channel, direction, id), updated in place, with expandable signal rows decoded
    lazily when painted;
  * *Chronological*: a ring buffer of every frame (also the source of CSV / ASC / BLF export).
  Filters (id, name, channel, direction) and a fading highlight for updated rows run on the GUI thread.
* The **plot** asks `RxDispatcher.snapshot()` for watched signals every 50 ms and redraws the time window.

### 6.4 CANable adapters (slcan)

* Recognised by USB ID (`16D0:117E` current firmware, `AD50:60C4` older builds). One `SlcanNode` per adapter.
* Session: `C` (close), `V` (version), `E` (baseline error register), then on start `C`, `S0–S9` (nominal rate),
  `Y2`/`Y5` (data rate 2 / 5 Mbit/s), `M0` (normal), `A1` (auto retransmit), `O` (open).
* Frames: `t/T` classic, `d/D` FD without BRS, `b/B` FD with BRS; the length nibble is the CAN FD DLC code, payloads
  are zero-padded to the next valid FD length (12, 16, 20, 24, 32, 48, 64).
* The firmware sends no command acknowledgements; the error register (`E`) is polled every second and decoded
  (TX failed, RX FIFO overflow, TX buffer full, USB buffers full …). Flags are latched by the firmware until re-plug,
  so only *new* bits are reported.
* *Loop* mode is emulated on the PC (the firmware has no loopback): frames are echoed to the app, nothing goes on the
  bus.
* Firmware details, update and recovery: [CANABLE_FIRMWARE.md](CANABLE_FIRMWARE.md).

### 6.5 Simulated nodes

`SIM1` / `SIM2` create a `SimNode` behind an in-memory pipe and a `SerialNode` speaking the AutoSim binary protocol
(COBS framing, CRC-16; [ESP32_PROTOCOL.md](ESP32_PROTOCOL.md)). The simulated node loops transmitted frames back, so
the whole app can be used and tested without hardware.

### 6.6 Multiple channels

Each open adapter is a channel (CAN1, CAN2, …) with its own backend and threads; all of them feed the one frame
queue. *Connect all* opens every free CANable with the form's bit rates; a port can be open only once. Messages added
before a channel existed pick up the first free channel as soon as one is connected.

### 6.7 User interface

* **Shell:** app bar (status pill, Connect / Disconnect all, command palette, View menu, theme), collapsible sidebar
  (Channels, Database), dock panels (Signals, Signal plot, Bus trace, Log) whose layout is saved with a version
  number, status bar (estimated bus load and frames/s per channel, error counter, trace size, recording, elapsed).
* **Signals panel:** messages and signal rows collapse (collapsed by default) so many fit in view; details hold the
  slider, value input (unit inline), raw value, value table and generator. The mouse wheel never changes values.
* **Bus load** is estimated from each frame's length, format and the channel's bit rates (ISO CAN FD frame layout,
  ~10 % stuff bits) – labelled "est.".
* **Theme:** design tokens for a graphite dark and a light theme (WCAG AA contrast, tested); one font (Segoe UI) at
  13 / 14 / 18 px. A theme switch takes ~0.1 s (stylesheet cleared and re-applied, repaints frozen).
* **Keyboard:** `Ctrl+K` palette, `Space` pause trace (never while typing), `Ctrl+F/L/E` trace search / clear /
  export, `Ctrl+B` sidebar, `Ctrl+J` log, `Ctrl+O` load DBC, `Ctrl+S` save project.

### 6.8 Persistence

| What | Where |
|---|---|
| Theme, window / dock layout (versioned), sidebar sections, last port, last databases | `QSettings` → registry `HKCU\Software\AutoSim\AutoSimStudio` |
| Project: databases + open channels with bit rates (reconnected on open) | JSON file chosen by the user (`ProjectConfig`) |
| Trace export | CSV / Vector ASC / Vector BLF (python-can writers) |

### 6.9 Performance (measured, two CANables, 32-byte FD frames)

| Load | Fixed trace | Chronological trace |
|---|---|---|
| 400 frames/s (1 message per channel, 10 ms) | ~27 repaints/s, input delay < 1 ms | ~27 repaints/s, ~8 ms |
| 4 000 frames/s (10 messages per channel) | ~9 repaints/s, ~100 ms | ~23 repaints/s, ~27 ms |

Hardware throughput: a 1 000-frame burst of 16-byte FD frames at 500 k / 5 M arrives complete and in order
(~2 800 frames/s). Probe: `tools/ui/perf_trace.py`.

---

## 7. Build, test and deploy

```mermaid
flowchart LR
    src["Sources + uv.lock<br/>(git)"] --> setup["scripts\setup.cmd<br/>uv sync --frozen"]
    setup --> tests["pytest<br/>(offscreen Qt)"]
    setup --> run["scripts\run.cmd"]
    setup --> build["scripts\build.cmd"]
    build --> pyi["PyInstaller one-folder<br/>dist\AutoSimStudio\"]
    pyi --> inno["Inno Setup<br/>AutoSimStudio-Setup-x.y.z.exe"]
    fwsrc["firmware\canable2_stm32\source"] --> fwb["scripts\build_firmware.cmd<br/>(arm-none-eabi-gcc, make)"]
    fwbin["firmware\canable2_stm32\bin"] --> flash["scripts\flash_canable.cmd<br/>(dfu-util)"]
    fwb --> flash
```

* **Dependencies:** `uv` with `uv.lock` (exact versions); Python 3.11 from `.python-version`.
* **Tests** (`tests/`, ~200): engine and codec, slcan protocol against a software model of the CANable firmware
  (`tests/fake_canable.py`), GUI tests (pytest-qt, offscreen), WCAG contrast, shell / shortcuts / layout, and
  **hardware tests** with real CANables (`AUTOSIM_HW=1`: both directions, bit-rate matrix, all FD lengths, bursts,
  real production DBCs bit-exact, the full GUI with two adapters).
* **Installer:** PyInstaller one-folder app + Inno Setup; app icon from `src/autosim/ui/assets/app_icon.svg`.
* **Moving to another machine:** clone and run `scripts\setup.cmd` – [MIGRATION.md](MIGRATION.md).

---

## 8. Extending the tool

* **Another adapter type:** implement a node with `connect / configure / start / send_frame / close` and the
  `on_rx / on_state / on_status / on_error` callbacks, wrap it in a `CanBackend`, recognise it in
  `NodeManager.kind_of()` / `create_backend()` (e.g. by USB ID in `hardware/slcan/__init__.py`). The engine and UI
  need no change.
* **Another panel:** create the widget, add it as a dock in `MainWindow._build_can_tab()` (`ws.add_panel(...)`),
  subscribe to `QtBridge` signals, and bump `LAYOUT_VERSION` so saved layouts are rebuilt.
* **Another generator:** add a class with `value(t)` in `core/can_engine/generators.py` and register it in
  `make_generator()` / `PARAM_LABELS`; it appears in the Signals panel's generator list after adding it to `GEN_ITEMS`.
* **New event topic:** add the name in `core/common/events.py`, publish it from the engine, add a signal in
  `QtBridge`.
