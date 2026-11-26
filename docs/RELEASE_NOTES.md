# AutoSim Studio (ICAN) 1.2.1

**Download** (Windows 10 / 11, 64-bit): `AutoSim Studio_1.2.1_x64-setup.exe`. It installs over 1.2.0 and keeps
settings, databases, rules, Test APIs and trace filters.

## Improved

- **Loading a database shows its progress.** The Database window shows the file, the step and the time so far, with
  a progress bar. The status bar shows the same:
  - by path: *reading and checking*, then *building the message tree*;
  - upload from a browser: the upload percentage, then the same steps.

  The buttons wait meanwhile, and a toast at the end gives the time taken. Opening a project or signal board that
  loads databases shows the same.
- **The title bar has the app's colours.** The window now has its own title bar (ICAN, minimise / maximise /
  close, drag to move, double-click to maximise) instead of the Windows one, whose colour did not match the app.
- **Cyclic is a checkbox** in each message of the Interactive generator, like the 1.0 desktop app. While the
  measurement is stopped, a ticked box shows **armed**.
- **Left bar:**
  - *Project* is at the top, above *Start / Stop*.
  - The large logo is gone.
  - The *Commands* button is gone; the palette stays on `Ctrl+K`, `Ctrl+Shift+P` and `F1`.

## Fixed

- **The UI could look old after an update.** The browser / WebView kept the previous `app.js` / `app.css` in its
  cache. They are now checked on every start, and their address carries the version.
- **`uv run autosim` opened the old PySide6 app.** From the sources, it now opens ICAN (the new UI) in an app
  window, with the measurement stopped. The 1.0 app is `uv run autosim-qt`.
