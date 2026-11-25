# AutoSim Studio (ICAN) 1.2.0

**Download** (Windows 10 / 11, 64-bit): `AutoSim Studio_1.2.0_x64-setup.exe`, the desktop app (Tauri). It contains
everything it needs, Python included. If WebView2 is missing, the installer downloads it.

The desktop app now has the features of the 1.0 desktop app (PySide6), except the plot.

## New

- **Measurement Start / Stop** (CANoe style), the first button of the left bar, above Setup, or `F9`:
  - **Stopped**: nothing is sent, answered or traced, and channels stay connected.
  - Messages switched to **Cyclic** while stopped are *armed*: their button is dashed, and they start sending with
    the measurement. *Send* is greyed out while stopped.
  - **Before Start**, the app warns when no channel is open (with *Open Setup* / *Start anyway*), when a channel is
    in error, and when messages switched to Cyclic have no open channel.
  - **When an adapter fails** while running, the app shows a message, or stops the measurement (Settings).
  - The status bar shows *Running* or *Stopped*.
  - The desktop app opens stopped. Web mode for test benches still starts running, so the Test API works without
    a click. Settings can change both.
- **Project and signal board files** (*Project* button on the left bar, `Ctrl+S` / `Ctrl+Shift+S`):
  - Open / Save project: databases, channels (open channels are reconnected on open), auto response rules and the
    Signals table.
  - Open / Save signal board: the Signals table, the auto response rules and the databases they need.
  - The files use the same format as the 1.0 desktop app, so they open in both. A board never starts sending by
    itself.
- **Settings** (`Ctrl+,`):
  - theme;
  - clear the trace on Start;
  - stop the measurement when an adapter fails;
  - start the measurement when the app opens;
  - highlight changed payloads;
  - the version and the data folder.
- **Command palette** (`Ctrl+K`, `Ctrl+Shift+P` or `F1`): all 32 commands, with filter-as-you-type, the arrow
  keys and Enter. New shortcuts:
  - `F9`: Start / Stop the measurement;
  - `Ctrl+O`: add a database;
  - `Ctrl+Shift+A`: add signals;
  - `Ctrl+Shift+R`: switch auto response on / off;
  - `Ctrl+F`: search the trace;
  - `Ctrl+Shift+T`: switch the theme.
- **New logo**: a simple ICAN tile.

## Since 1.1.0 (see `RELEASE_NOTES_1.1.0.md`)

- **Tauri desktop app**:
  - 60 frames/s in the trace, about 1 ms delay of a click while tracing;
  - loading a large database never freezes the window (`docs/PERFORMANCE.md`).
- **Trace filters like a logcat viewer**: Mark / Show only / Hide, by CAN ID or name, each one on / off.
- Windows engineering-tool look (Fluent), light and dark.
