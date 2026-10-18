# Review round 2 – changes

Screenshots: `after_{dark,light}_100_can.png` (1366 × 768) + close-ups `after_*_{signals,trace,plot,appbar}.png`,
`trace_toolbar_zoom_{before,after}.png` (the review-1 build), `font_candidates.png`, `app_icon_sheet.png`,
`exe_icon_extracted.png`.

## Trace toolbar buttons stuck to the top
Root cause: QDockWidget sizes its title area from the header's *size hint* (24 px), while the header is 36 px tall –
every panel's first 12 px were hidden under its header (trace buttons, the Signals title area, the plot toolbar).
The header now reports its real height; panel toolbars use 32 px items with equal margins. The Qt toolbar
separator (drawn by the stylesheet as a grey box – the "unlabelled square") is replaced by a 1 px divider.
Tests: content starts below the 36 px header in every panel; trace buttons centred (equal top / bottom gap).

## Font
Your choice from `font_candidates.png`: **Segoe UI** for the whole app (Windows system font – nothing bundled; the
Inter files were moved to `archive/inter_font_removed_2026-10-18/`). Sizes: 13 px for all text, 14 px semibold
headers, 18 px app title. A test walks every visible widget (plus the plot axes and trace cells) and fails on any
other family or size. Note: GUI tests run headless; the test setup now gives Qt's offscreen platform the Windows
fonts folder so Segoe UI resolves there exactly as in the app.

## Signals panel
1. **No value changes by scrolling.** App-wide: the mouse wheel over a spin box, slider or drop-down never changes
   it – the wheel scrolls the panel underneath (also over the cycle-time box, port / rate drop-downs …).
2. **Collapsible signals, collapsed by default.** Row: ▸ name | current value + unit (and value-table name) | raw |
   generator status. Expanded: slider + value input, *Value table* ("—" if none), *Generator* + parameters, and the
   signal layout (start bit, length, byte order, signedness, factor, offset, range). *Expand all / Collapse all* per
   message. The cycle box is wide enough for "60000 ms".
3. **Font sizes** – see Font (one family, three sizes, enforced by a test).

## App icon
`src/autosim/ui/assets/app_icon.svg`: the CAN differential signal – CAN_H (indigo) rises and CAN_L (cyan) falls on
dominant bits, meeting at the recessive level – on a graphite tile; reads as bits "1 0", legible down to 16 px on
light and dark taskbars. `tools/ui/make_app_icon.py` renders PNGs (16–256) and `packaging/autosim.ico`. Used for the
window / taskbar (own AppUserModelID so Windows shows it instead of Python's), the exe and the installer.

## Tests / performance
215 passed, 1 skipped with both CANables (one transient "cannot open COM port" on the first hardware test right
after the previous test released the adapter – passed on the immediate re-run; Windows port re-open timing).
Performance (single runs): fixed trace 26.7 fps / 0.8 ms lag at 400 rows/s, 9.2 fps at 4 000 rows/s; chronological
27.5 fps / 8 ms and 22.9 fps / 27 ms – unchanged or slightly better than round 1.
