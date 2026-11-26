# AutoSim Studio (ICAN) 1.2.3

**Download** (Windows 10 / 11, 64-bit): `AutoSim Studio_1.2.3_x64-setup.exe`. It installs over 1.2.x and keeps
settings, databases, rules, Test APIs and trace filters.

## Fixed

- **Clicking a message in the Bus trace did not always open its signals.**
  - The fixed view repaints about 10 times a second, to flash changed payloads. A click is lost when its row is
    repainted between the press and the release.
  - A message now opens / closes on the press, anywhere on its row, not only on the ▸.
- **A right-click (or ⋯) menu did not close when clicking elsewhere, and sometimes did not open.**
  - Cause: menus waited for a *click* to close; on the live trace that click was lost. A leftover one-shot listener
    also closed the next menu at once.
  - Now a press outside the open menu closes it, and so does `Escape`. Opening one menu while another is open works.

## Changed

- **Removing a message from the Interactive generator**: the ⋯ menu is replaced by a trash button.
  - It opens a confirmation dialog: *Remove EngineData (0x100) from the Interactive generator?*, with *Cancel* /
    *Remove*. It warns when the message is sending cyclically.
  - *Cancel* has the focus, so `Enter` or `Escape` keep the message.

See [1.2.2](RELEASE_NOTES_1.2.2.md) for the readable taskbar icon and the aligned message rows.
