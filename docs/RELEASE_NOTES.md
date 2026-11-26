# AutoSim Studio (ICAN) 1.2.8

**Download** (Windows 10 / 11, 64-bit): `AutoSim Studio_1.2.8_x64-setup.exe`. It installs over 1.2.x and keeps
settings, databases, groups, rules, Test APIs and trace filters.

## Fixed

- **Text in the Trace can be selected and copied again.** Drag over rows to select, `Ctrl+C` copies them (tab
  separated, ready for Excel). The trace no longer repaints while text is selected in it or the mouse is held down,
  so the selection stays; a plain click on a message row still opens / closes it.
- **No blinking text cursor** after clicking labels, names, headers or buttons (in Simulation and everywhere). Fields,
  traces, logs, the Test API details, properties and dialog text stay selectable.

## Changed

- **Clearer Test API details.** A click on a row opens:
  - the URL on its own line, with *Copy*;
  - property boxes like the database explorer's *Properties*: **API** (access, value now, status, age, the value a
    PUT sends, stale time, calls), **Message** (name, ID, frame, DLC, cycle time, senders, channel) and **Signal**
    (start bit, length, byte order, signed, factor, offset, minimum, maximum, unit, initial);
  - the **Value table** as a list (Raw | Value), the value now on the bus highlighted;
  - the curl commands, with *Copy*.

See [1.2.7](RELEASE_NOTES_1.2.7.md) for Collapse all in groups, bold headers and the simpler Add / Edit API dialog.
