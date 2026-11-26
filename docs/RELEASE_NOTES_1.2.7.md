# AutoSim Studio (ICAN) 1.2.7

**Download** (Windows 10 / 11, 64-bit): `AutoSim Studio_1.2.7_x64-setup.exe`. It installs over 1.2.x and keeps
settings, databases, groups, rules, Test APIs and trace filters.

## Changed

- **Collapse all / Expand all in each group** of the Interactive generator: hides or shows the signals of every
  message in that group (the messages stay visible).
- **Messages are indented under their group** (their names and signals), so they read as the group's children; the
  backgrounds stay as they were.
- **Bold headers**: panel titles and table column headers (Trace, Test API, Analysis, Simulation).
- **Test API details are simpler.** A click on a row opens a short list: URL; message (ID, length, cycle, sender,
  channel); signal (bits, scaling, range); value table; the value now, its age and the calls; the value a PUT sends;
  then the curl commands with *Copy*.
- **A simpler Add / Edit API dialog**: one column. *GET / GET + SET* are two buttons. *PUT sends* shows only for
  GET + SET, with the signal's range as a hint and the named values in a list.

See [1.2.6](RELEASE_NOTES_1.2.6.md) for port 6789 on the network, the Test API table and the Cyclic fix.
