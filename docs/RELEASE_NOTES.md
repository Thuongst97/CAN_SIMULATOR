# AutoSim Studio (ICAN) 1.2.3

**Download** (Windows 10 / 11, 64-bit): `AutoSim Studio_1.2.3_x64-setup.exe`. It installs over 1.2.x and keeps
settings, databases, rules, Test APIs and trace filters.

## Fixed

- **A ⋯ menu sometimes did not open.** Opening a menu while another one was open, for example the ⋯ of two
  messages one after the other, closed the new menu at once.
  - Cause: each menu left a one-shot "click anywhere closes the menu" listener, and that listener also caught the
    click that opened the next menu.
  - Now one listener closes the open menu on a press outside it, or on `Escape`.

## Changed

- **Removing a message from the Interactive generator**: the ⋯ menu is replaced by a trash button with a
  confirmation in place.
  - The first click turns it into a red **Remove?**; the second click removes the message.
  - A click elsewhere, `Escape` or 4 s without an answer cancel.

See [1.2.2](RELEASE_NOTES_1.2.2.md) for the readable taskbar icon and the aligned message rows.
