# UI refactor – step 1: design tokens and theme

Screenshots in this folder: `before_*` / `after_*` × `dark` / `light` × `100` / `125` / `150` (% Windows scaling,
emulated with `QT_SCALE_FACTOR`) × `can` / `someip`. Re-create with `tools/ui/snapshot.py`.

## What changed

| | before | after |
|---|---|---|
| Colour source | two hand-written palettes (`themes.py`) + 9 hard-coded colours in models, plot, icons, badges | one token set (`styles/tokens.py`), same keys in both themes; models / plot / badges read tokens |
| Accent | indigo (dark), **blue** (light) | indigo in both (`ACCENT_STRONG #4F46E5`) |
| Fonts | `Inter` requested but not installed → Segoe UI; 9.5 pt | Inter + JetBrains Mono bundled (OFL), body 13 px, integer px scale, real 500/600/700 weights |
| Tabular figures | no | Inter `tnum` on (Qt 6.11 `QFont.setFeature`), survives stylesheet size changes (tested) |
| Payload bytes / IDs in trace | orange / cyan | neutral `TEXT_PRIMARY` |
| Direction column | only Tx coloured (light blue) | Rx cyan / Tx orange (`RX` / `TX`), colour-blind check in tests |
| Offline status dot | red | muted grey (red = errors only) |
| Focus ring | `* { outline: none }`, 1 px translucent border on inputs only (≈2.2 : 1) | 2 px solid accent on buttons, inputs, checkboxes, radio, slider handle, tool buttons; views get an accent border |
| Buttons and focus | take focus on click | keyboard (Tab) focus only, so the ring marks keyboard navigation and a click no longer pulls focus out of a field |
| WCAG AA (same checks on both palettes) | **7 failures dark, 9 light** (focus ring 2.1–2.3 : 1, light success 3.4 : 1, dark accent text 3.9 : 1 …) | **0 failures** – `tests/test_theme_contrast.py` (77 checks + byte highlight + Rx/Tx colour-blind distance) |

Tightest passing pairs: light ERROR on surface-2 4.62 : 1, light WARNING on surface-2 4.71 : 1, dark ACCENT on
surface-2 4.72 : 1. TEXT_DISABLED is exempt (2.6 : 1 dark, 2.3 : 1 light).

## Files

| File | Change |
|---|---|
| `src/autosim/ui/styles/tokens.py` | **new** – colour tokens (dark / light), spacing, radii, type scale, control heights, motion |
| `src/autosim/ui/styles/themes.py` | template rewritten on tokens; px sizes; focus rules; `DARK` / `LIGHT` are now the token dicts |
| `src/autosim/ui/theme.py` | bundled font loading, `ui_font()` with `tnum`, `mono_font()`, `token()`, colour helpers + badge from tokens, button focus policy |
| `src/autosim/ui/assets/fonts/` | **new** – Inter Regular/Medium/SemiBold/Bold, JetBrains Mono Regular/Medium/Bold, both OFL licences (2.5 MB) |
| `src/autosim/ui/models/trace_model.py`, `fixed_trace_model.py` | colours from tokens, built once per theme (not per `data()` call); Rx coloured too |
| `src/autosim/ui/panels/plot_panel.py` | background / axes / series colours from tokens; series re-coloured on theme switch |
| `src/autosim/ui/widgets.py` | `CONTROL_HEIGHT` and badge font from tokens; glow colour from accent |
| `tests/test_theme_contrast.py` | **new** – WCAG AA checks |
| `tests/test_gui.py` | theme tests assert against tokens instead of old hex values; new font / tnum / focus test |
| `tools/ui/snapshot.py`, `tools/ui/perf_trace.py` | **new** – screenshot and performance tools used for every step |

## Behaviour risks

* **Buttons no longer take focus on mouse click** (Tab still reaches them). Typing after clicking a button
  continues in the field that had focus. Space/Enter still activate the focused button.
* Light theme accent changed from blue to indigo (approved).
* Trace colours changed (neutral payload / IDs, coloured direction) – anyone used to "orange = data" will notice.
* Body text is 13 px Inter instead of ~12.7 px Segoe UI fallback: Inter is wider, so some fixed-width areas
  show less text (connection table: the Rx / Tx / Err columns now need a horizontal scroll at the default dock
  width – addressed by the channel cards in step 3).
* Fonts are loaded from `autosim/ui/assets/fonts`; PyInstaller already bundles package data
  (`collect_data_files("autosim")`).
* No protocol, driver or data-model code changed.

## Scaling and minimum window size

| scale | before (min size hint) | after |
|---|---|---|
| 100 % | 1415 × 1058 | 1446 × 1049 |
| 125 % | 1405 × 1061 | 1464 × 1049 |
| 150 % | 1405 × 1061 | 1464 × 1049 |

**The window cannot be made 1366 × 768 – before or after.** Cause: the SOME/IP tab's content needs
1217 × 832 (the tabs share the window, so it forces the CAN tab too). The CAN tab alone needs about 870 px
height (connection 362 + DBC 178 + log 122 + chrome). Fixing this is part of step 2 (shell: scrollable sidebar,
SOME/IP inspector layout) and will be a test (`minimumSizeHint() <= 1366x768` at all three scales).
Other things visible at all scales, already present before: SOME/IP sidebar buttons clipped ("oad Confi"),
"Fire &forget" shows a stray underscore (mnemonic), adapter combo text truncated.

## Performance (two real CANables, 500 k / 2 M FD+BRS, 32-byte frames, 10 ms cycle)

Probe: `tools/ui/perf_trace.py` – 15 s per trace mode after warm-up. `trace_fps` = repaints of the visible
trace per second, `lag` = how late a 10 ms timer fires (input responsiveness).

**Your scenario – 1 message per channel (400 trace rows/s):**

| | fixed: fps | fixed: lag mean / p95 | chronological: fps | chronological: lag mean / p95 |
|---|---|---|---|---|
| before | 28.9 | 0.9 / 4 ms | 26.0 | **26.5 / 36 ms** |
| after step 1 | 28.4 | 1.1 / 6 ms | 26.6 | 26.3 / 35 ms |

**Stress – 10 messages per channel (4 000 rows/s), 3 runs each:**

| | fixed: fps | fixed: lag mean | chronological: fps | chronological: lag mean |
|---|---|---|---|---|
| before | 7.5 / 7.7 / 7.8 | 123 / 118 / 118 ms | 16.0 / 15.9 / 15.6 | 50 / 48 / 51 ms |
| after step 1 | 8.3 / 7.6 / 7.7 | 111 / 121 / 119 ms | 15.3 / 15.7 / 15.2 | 53 / 50 / 52 ms |

* Step 1 has **no measurable effect** (differences are within run-to-run spread).
* Process CPU from psutil is **not reliable** here (3–28 % for identical runs), so it is not used for comparisons;
  step 7 will add per-thread CPU sampling to the probe.
* Existing problems found (targets for step 7, trace): the chronological view delays input by ~26 ms even at
  400 rows/s; at 4 000 rows/s the fixed view repaints at only ~8 fps with ~120 ms input lag. No frames were lost
  (Rx = Tx on both adapters in every run).

## Not changed – needs your OK (outside UI scope)

* DBC text encoding: units like `º/s` show as `Âº/s` (trace, Hyundai DBC: bytes `C2 BA`). The DBC file is UTF-8 but the loader
  reads it as cp1252 (`core/can_engine/database.py`). Fix: try UTF-8 first, fall back to cp1252. This touches the
  database loader, so I have not changed it.
