# UI refactor – step 2: shell layout

Screenshots: `before_*` (= end of step 1) and `after_*` × dark / light × 100 / 125 / 150 % × can / someip.
All `after_*` shots are taken at a **1366 × 768** window (at 150 % the screen only offers ~1280 × 720 logical
pixels, so that shot is 1283 × 707 – the realistic 150 % case).

## What changed

| | before | after |
|---|---|---|
| Top bar | tabs + gear only | **app bar**: sidebar toggle, *AutoSim Studio*, CAN / SOME/IP tabs, global status pill ("2 channels · 1 warning", click → channels), quick **Connect all / Disconnect all**, command palette, **Layout menu**, theme toggle (sun / moon), settings |
| CAN connection + DBC explorer | two movable docks, each with its own minimum height | one collapsible **sidebar** (Ctrl+B, 160 ms animation) with foldable *Channels* / *Database* sections in a scroll area |
| Signals / Plot / Trace | splitter "cards" (pop-out only) | **QDockWidgets** with a custom title bar = the panel-header component (title, pop-out, hide); drag to move / tab, double-click to float; closing a floating panel re-docks it. Bus trace has the largest area by default (≈65 % height, full main width) and a 2 px accent rule; the panel holding keyboard focus gets a tinted header with an accent title |
| Layout persistence | unversioned `saveState` | **versioned** (`LAYOUT_VERSION = 2`): a layout saved by another version is discarded, the default layout used and a log line written. Sidebar section state, last port and last databases are also remembered (databases reload on start if the files still exist) |
| Reset layout | settings popup | Layout menu, command palette ("Reset layout") and settings popup |
| Log | fixed dock | collapsible panel (status-bar *Log* button, Ctrl+J) with the same header |
| Status bar | "N frames/s total M" | per channel **bus load % (est.)** and **frames/s**, error counter (amber when > 0), trace size (messages · frames), **Recording / Paused**, **elapsed** time (reset by Clear) |
| Command palette | – | **Ctrl+K**: every command, filtered as you type, shortcut shown, unavailable ones greyed with a reason |
| Shortcuts | Ctrl+, only | Ctrl+K palette · **Space** pause/resume trace · Ctrl+F trace search · Ctrl+L clear · Ctrl+E export · Ctrl+B sidebar · Ctrl+J log · Ctrl+O load DBC · Ctrl+S save project · Ctrl+1 / Ctrl+2 tabs · Ctrl+Shift+T theme · Ctrl+, settings. Shown in tooltips. |
| Minimum window size | **1446 × 1049** (did not fit 1366 × 768) | **1186 × 607** (100 %), 1205 × 607 (125 / 150 %) |

**Single-key shortcuts (Space):** never fire while a text input, spin box, combo box or text editor has focus,
nor on a focused button / checkbox / slider (Space belongs to them), nor in dialogs or other windows; they work
in the main window and in its popped-out panels, and Space only acts while the CAN tab is shown. Tested.

**Minimum size fix:** the SOME/IP tab was the cause (its content needed 1217 × 832). Its inspector header is now
two rows (title + status pills | actions) and its sidebar and tab pages scroll instead of imposing their full
height. The CAN sidebar scrolls too; its default width follows the connection form so nothing is clipped.

## Files

| File | Change |
|---|---|
| `src/autosim/ui/main_window.py` | rewritten shell: dock workspace without fixed centre, app bar wiring, commands, status, versioned layout, last port / DBC |
| `src/autosim/ui/shell/` | **new**: `panels.py` (dock + header), `sidebar.py`, `app_bar.py`, `status_bar.py`, `commands.py` (registry, palette, single-key filter), `bus_load.py` (estimator) |
| `src/autosim/ui/settings_store.py` | **new**: `app_settings()` – see "Settings store" below |
| `src/autosim/ui/styles/themes.py` | rules for app bar, panel header (primary / focused), sidebar, status segments, palette |
| `src/autosim/ui/styles/tokens.py` | dark `ACCENT` #7277F7 → **#7C81F8** (the focused-panel title failed 4.5 : 1 on surface-3) |
| `src/autosim/ui/icons.py` | new icons: sidebar, layout, sun, moon, close, chevrons, command, warning |
| `src/autosim/ui/panels/someip_panel.py` | two-row header, scrolling sidebar and tab pages, "Fire && forget" label fix |
| `src/autosim/ui/panels/connection_panel.py` | `disconnect_all()` |
| `src/autosim/ui/settings_dialog.py` | panel list from the new registry; *Pop out* disabled for the sidebar with an explanation |
| `src/autosim/ui/widgets.py` | `scrolling()` helper |
| `src/autosim/ui/models/*.py`, `shell/*.py` | theme callbacks connected as bound slots (see below) |
| `src/autosim/app.py` | theme read through `app_settings()` |
| `tests/test_shell.py` | **new** (13 tests): 1366 × 768 fit, Space rules, shortcuts / tooltips, palette, versioned restore + fallback, reset from menu and palette, sidebar, status pill / bar, quick disconnect confirmation, last port / DBC, focus highlight, bus-load maths |
| `tests/test_gui.py`, `test_hw_canable*.py`, `test_theme_contrast.py` | layout tests updated to docks; settings isolated; 3 new contrast checks |

## Behaviour risks

* **The old saved window layout is discarded once** (version 1 → 2): the first start shows the default layout.
* **Last databases reload automatically on start** and the last port is preselected (new, as requested).
* The *Channels* / *Database* areas are no longer separate floating docks; they are sidebar sections (fold,
  collapse, resize the sidebar). Signals, Plot, Trace and Log remain dockable / floatable.
* **Space** now pauses / resumes the trace when the focus is on the trace or other non-input widgets.
* *Disconnect all* in the app bar asks for confirmation only while cyclic TX is running.
* Bus load is an **estimate** (frame layout + bit rates, ~10 % stuff bits) – labelled "est.".
* The error figure is the adapters' error counter; the CANable firmware does not deliver error frames.
* No protocol, driver or data-model code changed.

## Fixed along the way (found while testing)

* **Tests were writing to your real settings.** `QSettings("AutoSim", "AutoSimStudio")` always uses the
  registry and ignores the test redirection, so test runs overwrote `theme` and (today) wrote layout,
  `last/port` and `last/dbc` – the app would have auto-loaded test databases. All settings now go through
  `app_settings()` (registry for the app, temporary INI files in tests). I removed the `last/*` and `layout/*`
  keys the test runs had written; `theme` (now `dark`) is the only key left.
* Theme-change callbacks were connected through lambdas, which outlive their widgets (a crash when the theme
  changed after a window closed). They are bound slots now.

## Performance (two real CANables, 32-byte FD+BRS, 10 ms cycle; `tools/ui/perf_trace.py`, 15 s per mode)

| | fixed: fps | fixed: lag mean / p95 | chronological: fps | chronological: lag mean / p95 |
|---|---|---|---|---|
| 1 msg/channel (400 rows/s), before | 28.6 | 0.8 / 3 ms | 24.4 | 28.5 / 38 ms |
| 1 msg/channel, after step 2 | 27.8 | 0.7 / 3 ms | 26.2 | 26.1 / 34 ms |
| 10 msg/channel (4 000 rows/s), before (2 runs) | 7.0 / 6.0 | 133 / 159 ms | 13.0 / 12.8 | 66 / 67 ms |
| 10 msg/channel, after step 2 (2 runs) | 6.6 / 8.1 | 142 / 114 ms | 13.9 / 14.1 | 61 / 61 ms |

No measurable change (within run-to-run spread). The bus-load estimator adds one small calculation per frame.
The stress numbers (and the ~26 ms chronological lag) remain the step 7 target. No frames lost in any run.

## Still open

* DBC text encoding (`Âº/s`) – waiting for your OK to touch `core/can_engine/database.py`.
* At 1366 × 768 the Database tree gets little height while *Channels* is expanded; the channel cards (step 3)
  make that section much shorter.
