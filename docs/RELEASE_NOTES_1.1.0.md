# AutoSim Studio 1.1.0

**Download** (Windows 10 / 11, 64-bit): `AutoSim Studio_1.1.0_x64-setup.exe`. This is the new desktop app (Tauri).
It contains everything it needs, Python included. WebView2 is part of Windows 10 / 11; if it is missing, the
installer downloads it.

## New desktop app (Tauri), much smoother

The window now shows the web UI in a native window, and the CAN engine runs in its own background process. The UI
no longer waits for the engine.

Measured with a 1 500-message / 24 000-signal database and 10 000 trace rows per second (details in
`docs/PERFORMANCE.md`):

| | 1.0 (PySide6) | 1.1 (Tauri) |
|---|---|---|
| Loading the database | window frozen 2.6–3.3 s | never frozen more than 0.1 s |
| Trace on screen | 7–19 frames/s | 60 frames/s |
| Delay of a click / key while tracing | 70–140 ms on average, up to 0.5 s | about 1 ms, at most 25 ms |

The app starts its backend on 127.0.0.1:8765, so the Test API address does not change, and stops it on exit.
Add database, imports and exports use the Windows Open and Save dialogs. Only one copy runs at a time, and the
window size and position are kept.

## New

- **Trace filters like a logcat viewer** (Bus trace → *Filters*):
  - Each filter matches CAN IDs or ranges (`118, 3E9, 100-1FF`) or message / signal names (`Engine|Speed`,
    `DI_*`, `!CHECKSUM`).
  - It does one of three things: **Mark** (colours the rows), **Show only** or **Hide**.
  - Each filter has its own on / off box, and one switch turns them all off.
  - Right-click a trace row to add a filter for it. Export / import the filters as JSON.
  - The filters apply to the Bus trace and to the trace in Simulation.
- **New look**: a Windows engineering-tool style (Fluent): a blue accent, flat panels, data grids with column
  lines, and the status bar in the accent colour. Light and dark themes.
- **New icon: ICAN.**

## Faster

- **CAN engine**, for SIM nodes and the ESP32 gateway: the frame CRC and COBS run about 80 times faster, and the
  frame queues are C queues. The trace now gets every frame at 10 000 rows/s, against 1 600–2 700 before.
- **Large databases in the web UI**:
  - The database list is no longer downloaded again on every click, and it is sent compressed.
  - The tree is rebuilt only when a database changes.
  - The search waits for a pause in the typing and shows only the matching signals.
- **Generator and trace**:
  - A change to one message redraws only that message, not all of them.
  - The fixed trace view builds only the visible rows.
  - Panels out of view cost nothing.

## Fixed

- The *Signal* column of the Interactive generator and of the Test API table was squeezed to one letter in a
  narrow window.

## Not yet in the new app

These are planned for the next versions:

- measurement *Start / Stop*;
- signal boards and project files;
- the plot;
- settings;
- the command palette.

Until then, the 1.0.0 desktop app (PySide6) has them. Settings, databases, auto response rules and Test APIs of
web mode are kept.
