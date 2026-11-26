# AutoSim Studio (ICAN) 1.2.2

**Download** (Windows 10 / 11, 64-bit): `AutoSim Studio_1.2.2_x64-setup.exe`. It installs over 1.2.x and keeps
settings, databases, rules, Test APIs and trace filters.

## Improved

- **The app icon is readable in the taskbar.**
  - Each size from 16 to 256 px is drawn on its own (`tools/ui/make_ican_icon.py`).
  - The blue tile fills the icon, with no transparent margin, so it looks as large as other apps' icons.
  - **ICAN** is in a bold condensed font as wide as the tile, so the whole word shows at 24, 32 and 48 px.
- **The Interactive generator's message rows line up.** Every message header has the same columns: name, ID,
  channel, cycle, FD, BRS, sent, Cyclic, Send. A long name is cut, with a tooltip showing all of it. The header
  stays in place when the signal table scrolls sideways.

See [1.2.1](RELEASE_NOTES_1.2.1.md) for the previous changes:

- the app's own title bar;
- database loading progress;
- the Cyclic checkbox;
- the left bar order;
- fresh UI files after an update.
