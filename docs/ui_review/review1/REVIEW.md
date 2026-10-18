# Review round 1 (after step 2) – changes

Screenshots (dark, 1366 × 768): `before_dark_100_can.png` → `after_dark_100_can.png`, close-ups
`after_dark_100_{signals,trace,plot,appbar}.png`; light theme for comparison: `after_light_100_*`.

## 1. Graphite dark theme

New dark tokens as specified (surface-0 #1B1D22 … surface-3 #30333B, input-bg #1B1D22 recessed, border #33363E,
border-strong #41454F, row-alt #24262C, selection = accent-soft on surface-1). Re-tuned: TEXT_PRIMARY #E6E8EC,
TEXT_SECONDARY #B4B8C4, TEXT_MUTED #9EA2B0, TEXT_DISABLED #656978, ACCENT / focus ring #8C91FA (indigo). No pure
black or pure grey left (the one hard-coded near-black icon fill was removed). Plot background = surface-1, axes /
grid from the border tokens. Light theme unchanged (it gained only an INPUT_BG token with its old input colour).

Lowest contrast ratio per token (`tests/test_theme_contrast.py`, 91 checks, all pass):

| token | dark | light |
|---|---|---|
| TEXT_PRIMARY | 9.50 (on selection) | 14.49 (selection) |
| TEXT_SECONDARY | 6.37 (surface-3) | 6.74 (surface-3) |
| TEXT_MUTED | 4.96 (surface-3) | 5.10 (surface-3) |
| ACCENT | 5.16 (surface-2) | 6.01 (surface-2) |
| SUCCESS | 8.24 (surface-2) | 4.63 (surface-0) |
| WARNING | 9.38 (surface-2) | 4.55 (surface-0) |
| ERROR | 5.19 (surface-2) | 4.62 (surface-2) |
| INFO | 5.65 (surface-2) | 4.94 (surface-2) |
| RX | 7.95 (surface-2) | 5.12 (surface-2) |
| TX | 6.35 (surface-2) | 4.95 (surface-2) |
| ON_ACCENT (buttons) | 5.43 (accent hover) | 6.29 (accent) |
| FOCUS_RING (needs 3:1) | 4.54 (surface-3) | 5.59 (surface-3) |

## 2. Signals panel

One surface: the nested group box is gone; each message is a flat section (title 14 px, ID · bytes · CAN FD,
two control rows) separated by a 1 px rule; 12 px padding under the dock title. Signal grid with column headers:

    Signal | slider | Value (unit inside the box) | Raw | Value table | Generator

* Sliders: every bounded signal – the DBC range, or (when the DBC has none, e.g. `[0|0]`) the range of the raw
  bits from length / signedness / factor / offset. Only floats without a range have no slider.
* Raw value next to the physical value (tooltip: hex and the formula).
* **The bare "none" combo was the signal generator, not a value table.** It is now the labelled *Generator*
  column ("Off", Sine, Ramp, Square, Toggle); its parameters appear in a row under the signal while active.
  *Value table* is a separate column: a combo of named values, "—" when the current value has no name, and a "—"
  cell when the signal has no table.
* The panel's minimum width fits all columns (the plot gives way); sliders are the first thing hidden if it is
  made narrower; vertical scrolling only.
* Core code unchanged: I first thought the codec clamped `[0|0]` signals to 0, but cantools already reads `[0|0]`
  as "no range"; the change I made was reverted.

## 3. Panel titles

All panel and section titles: same colour (TEXT_PRIMARY), same font (Inter 14 px semibold). Focus is shown only by
a 2 px accent underline on the panel header. The Bus trace keeps a neutral 2 px rule so the accent means focus.

## 4. Signal plot / reopening panels

The app-bar **View** menu lists every panel (tick to show / hide), *Show all panels* and *Reset layout*; the command
palette has *Show panel: Signal plot* (etc.), *Show all panels* and *Reset layout*. At startup a restored layout is
checked: a docked panel squeezed below 40 px or a floating one outside every screen is restored and logged.
Likely cause of the missing plot: before the settings fix, test runs saved their layouts (with panels hidden) into
your registry settings – those keys were removed; and layout version 3 (CAN only) starts from the default layout.

## 5. Trace wording

"Cycle [ms]" column 104 px (header fits, tested) · ID filter placeholder "ID (hex)" (example in its tooltip) ·
"All channels" · the two icon-only buttons now read **Follow** and **Highlight**, with tooltips (they have no
shortcut; both are in the command palette).

## 6. Fonts

Bundled Inter is registered and resolved (logged at every start, e.g. "Fonts: UI text resolves to 'Inter' Regular
13 px, headers 'Inter' SemiBold 14 px; bundled families registered: Inter (4 files …)" – a fallback would add a
WARNING). The condensed look you saw matches **Segoe UI** (see `font_probe_before.png`): the installer in `dist/`
had been built before the fonts were added. Rebuilt; the new installer contains the four Inter files.
Per your request: **one family and one size everywhere** – 13 px Inter for all text (labels, inputs, tables, trace
cells, IDs, payload, status bar, log), headers 14 px semibold, app title 18 px. JetBrains Mono was removed (digits
still line up: Inter's tabular figures; hex letters A–F are not fixed-width).

## 7. Disconnect all

Ghost button (secondary text, neutral border); red fill only on hover / press; always asks for confirmation (and
mentions running cyclic TX).

## 8. Later steps (confirmed)

* Step 3 – channel cards: Err badge (the Err = 2 case) with details and the "re-plug the adapter" hint; the ISO
  checkbox explained (CANable firmware is ISO-only: shown checked + disabled with a tooltip and an inline hint);
  full Node IDs (serial numbers) without truncation; the channel table with its horizontal scrollbar goes away.
* Step 8 – log: severity colours / icons, filtering, copy, wrapped long lines (+ the TODO for a structured level).

## SOME/IP removed

UI: no tabs (the app bar is its own bar), no Ethernet card, no SOME/IP settings / commands. Code: the SOME/IP
engine, Ethernet NIC helpers, panels (SOME/IP, payload editor, proto form), the asyncio-QThread helper, the SOME/IP
tests and test data were **moved to `archive/someip_removed_2026-10-18/`** (17 Python files). A permanent delete was
blocked by the session's safety rules because the folder has no version control – delete the `archive/` folder
yourself to finish. Also removed: SOME/IP events / bridge signals / project-file fields (old project files still
open; the SOME/IP keys are ignored), the `protobuf` and `grpcio-tools` dependencies (uv.lock updated) and their
installer entries; `psutil` moved to the dev group (only the perf tool uses it). Docs updated.

## Tests and performance

212 passed, 1 skipped (incl. both real CANables). New tests: signals grid / sliders / raw / value table /
generator labels, signal range from raw bits, trace wording, ghost Disconnect all, font log, CAN-only workspace.

Performance (both CANables, single runs): fixed trace unchanged (28 fps, 0.6 ms lag at 400 rows/s; 8.7 fps at
4 000 rows/s). The chronological trace improved – lag 26 → 8 ms at 400 rows/s, 61 → 33 ms and 14 → 21 fps at
4 000 rows/s – most likely because the SOME/IP engine's event-loop thread no longer runs. Step 7 remains the place
for the stress case.
