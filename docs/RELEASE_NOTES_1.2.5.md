# AutoSim Studio (ICAN) 1.2.5

**Download** (Windows 10 / 11, 64-bit): `AutoSim Studio_1.2.5_x64-setup.exe`. It installs over 1.2.x and keeps
settings, databases, rules, Test APIs and trace filters. The messages already in the Interactive generator go into a
group named *Signals*.

## New

- **Groups in the Interactive generator.**
  - Messages are kept in groups (e.g. *Powertrain*, *Body*), each in its own framed panel.
  - A group's header shows ▾ / ▸ to fold it, its name, and the number of messages and how many are Cyclic.
  - **+** adds messages and signals to that group.
  - **⋯** offers *Rename*, *Move up / down* and *Remove group*. Removing asks first, and warns when messages of
    the group are sending.
  - **Add group** (at the top, where *Add signals…* was, `Ctrl+Shift+A`) opens a dialog: the group name, then the
    messages and signals picked from the database.
  - *Collapse groups / Expand groups* folds all of them. The folds are kept.
  - Groups are saved with the settings, in projects and in signal boards.

## Fixed

- **Two windows on Windows.** Starting the app while it was already running opened a second window and a second
  backend. This happens for example when the installer starts it, and you then click its icon.
  - The single-instance check could not see a copy started with other rights, such as the one the installer starts.
  - Now the app first asks the running backend. If the app is already running, it brings that window to the front
    and exits.
  - Windows CI checks that the installed app shows one window, and still one after a second start.
- **Message ID hidden by the channel list** in the Interactive generator. The ID column fits an extended ID
  (`0x1FFFFFFF x`).
- **The orange "armed" label after *Cyclic* covered the *Send* button.** It is gone; the *Cyclic* box itself still
  turns orange while the measurement is stopped.

## Changed

- **No *N sent* counter in message headers.** The trace shows the count. The room goes to the message name.
- **Tables have one background colour again** (no alternating rows).
- **Bus trace and Test API keep their previous fonts and text colours.** The 1.2.4 change to one monospace font and
  darker text was tiring to read, so it was reverted.

See [1.2.4](RELEASE_NOTES_1.2.4.md) for the type sizes and the wider Interactive generator.
