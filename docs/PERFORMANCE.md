# Performance: PySide6 desktop vs. web UI (Tauri app)

Measured on 2026-11-25. The tools are in `tools/perf/`, so anyone can run the measurement again.

## Workload

- **Database**: `make_big_dbc.py` writes a synthetic CAN FD database of 1 500 messages and 24 000 signals (64-byte
  frames, value tables). It stands for a large vehicle database.
- **Traffic**: N messages of that database, sent every 10 ms on the simulated node SIM1. Each frame gives a Tx and
  an Rx row, so the load is N × 200 trace rows per second:
  - 50 messages: 10 000 rows/s;
  - 200 messages: 40 000 rows/s.
- **Measured** per view (Bus trace *fixed*, Bus trace *chronological*, Simulation):
  - frames painted per second;
  - **lag**: how late a 10 ms timer fires on the UI thread. This is what the user feels as "it lags" (clicks,
    typing, scrolling);
  - trace rows delivered per second;
  - CPU.
- **Setup**:
  - Linux, 4 cores, headless.
  - Qt runs with the offscreen platform.
  - The web UI runs in Chromium, which is the engine of WebView2, the engine the Tauri app uses on Windows.

```
python tools/perf/make_big_dbc.py big.dbc
QT_QPA_PLATFORM=offscreen python tools/perf/bench_qt.py --dbc big.dbc --messages 50
python -m autosim.web --port 8897 --no-browser --no-token &     # then, with that process id:
node tools/perf/bench_web.js http://127.0.0.1:8897/ <pid> big.dbc 50
```

## Results

### Loading the 1 500-message database

| | PySide6 desktop | Web UI / Tauri |
|---|---|---|
| UI frozen | **2.6–3.3 s** (the window does not answer) | **never more than 0.1 s** (the database is parsed in the backend) |
| Database tree shown | 2.6–3.3 s | 1.8–2.7 s |
| Open the Messages folder (1 500 rows) | – | 0.1 s |
| Search a signal present in every message (24 000 signals) | – | 0.24 s (was 0.78 s) |

### Trace at 50 messages × 10 ms (10 000 rows/s)

| View | PySide6: fps | PySide6: lag mean / max | Web: fps | Web: lag mean / max |
|---|---|---|---|---|
| Bus trace, fixed | 7 | **138 / 255 ms** | **60** | **0.6 / 22 ms** |
| Bus trace, chronological | 7 | **126 / 507 ms** | **60** | **1.3 / 23 ms** |
| Simulation (generator + trace) | 19 | **68 / 372 ms** | **60** | **0.6 / 17 ms** |

Rows delivered: 9 600–9 800 per second in both. CPU:

- PySide6: 104 % of a core. The UI and the CAN engine share one Python process and its GIL.
- Web: the page uses 20–28 % and the backend 64–76 %, in two separate processes.

### Trace at 200 messages × 10 ms (40 000 rows/s requested)

| View | PySide6: fps | PySide6: lag mean / max | PySide6: rows/s | Web: fps | Web: lag mean / max | Web: rows/s |
|---|---|---|---|---|---|---|
| Bus trace, fixed | 2 | 421 / 543 ms | 1 300 | **60** | **1.1 / 22 ms** | 11 700 |
| Bus trace, chronological | 4 | 271 / 340 ms | 1 500 | **60** | **0.6 / 26 ms** | 11 100 |
| Simulation | 7 | 293 / 373 ms | 1 500 | **60** | **0.5 / 22 ms** | 11 200 |

The PySide6 numbers at 200 messages are from the run before the engine queue change (see below). In both apps the
backend is then at 100 % of a core and cannot send all 40 000 rows/s. The web UI stays fluid. The PySide6 UI
takes the GIL away from the engine, so it gets 8 times fewer rows and still lags.

## Why the PySide6 app lags

1. **One process, one GIL.**
   - The CAN engine threads and the Qt UI thread take turns on the Python GIL.
   - With traffic, each UI event waits for the engine threads: 70–400 ms per event.
   - Qt cannot paint faster than Python lets it.
   - The web UI paints in the browser's own process, so the engine never blocks a click.
2. **Loading a database runs on the UI thread.** cantools parses the file there, so the window is frozen for 3 s
   with a large DBC.

## What was made faster (it helps both apps where noted)

| Change | Where | Effect |
|---|---|---|
| CRC-16 via `binascii.crc_hqx` (C) instead of a bit loop in Python; COBS by blocks | ESP32 gateway protocol: SIM nodes and the real gateway, both apps | 47 → 0.6 µs per frame; rows delivered at 50 msgs: 1 600–2 700 → 9 800 /s |
| `queue.SimpleQueue` (C) for the engine's frame queue and the SIM pipes | engine, both apps | less lock contention between the 5 engine threads; 200 msgs: 3 300 → 11 000 rows/s |
| `/api/databases` (5 MB for this DBC) is fetched again only when a database changes (`db_version`), and gzipped | web | before, every click (e.g. Cyclic) downloaded 5 MB again |
| The database tree is rebuilt only when the databases change; the search waits for a pause in the typing and shows only the matching signals (first 300 messages) | web | search 0.78 → 0.24 s, no UI freeze |
| The generator updates card by card: one changed message rebuilds one card, not all of them | web | adding / switching 200 messages: 48 → 27 s in a script; one click no longer rebuilds 3 200 rows |
| The fixed trace view is virtual: only the visible rows are built, as in the chronological view | web | 200 msgs: 20 → 60 fps, lag 41 → 1 ms |
| CSS containment for the trace and the generator; message cards out of view are not rendered | web | Simulation view: main thread 62 → 22 %, 47 → 60 fps |

## Still to improve

- **Backend throughput.** Above roughly 11 000 rows/s, the Python engine is the limit in both apps. The next steps
  would be decoding in batches and moving the SIM node to a separate process.
- **Many changes at once.** Adding 200 messages and switching each one on by script makes 400 state refreshes in
  the browser. Batch endpoints, for example "switch all on", would make this instant.
