# AutoSim Studio (ICAN) 1.2.6

**Download** (Windows 10 / 11, 64-bit): `AutoSim Studio_1.2.6_x64-setup.exe`. It installs over 1.2.x and keeps
settings, databases, groups, rules, Test APIs and trace filters.

## Changed

- **Port 6789, reachable from the network.** The app's backend (Test API, web UI) now listens on port **6789**
  (was 8765) on every network interface (`0.0.0.0`), so test systems on other PCs can use it.
  - This PC: `http://127.0.0.1:6789`, no token, as before.
  - Other machines: `http://<this PC's IP>:6789` with the header `X-AutoSim-Token: <token>`. The *Test API* desktop
    shows the token and copies it; it stays the same between starts (`token.txt` in the app data folder).
  - Update test scripts that use port 8765.
  - At the first start, Windows Firewall may ask whether *AutoSimWeb* may use the network: allow it for other
    machines to connect.
  - Web mode (`autosim-web`) also uses port 6789 by default. New option `--trust-local`: requests from this PC need
    no token, other machines still do.
- **Theme above Settings** in the left bar: *Settings* is now the last button.
- **Test API table redesigned.**
  - A **No** column numbers the APIs.
  - A click on a row opens a short list below it: URL, message (ID, length, cycle, sender, channel), signal (bits,
    scaling, range), value table, the value now, the value a PUT sends, and the curl commands with *Copy*.
  - Larger *URL* and *curl* buttons.
  - The remove button is the trash icon of the Interactive generator and asks in a dialog first.
  - A simpler *Add / Edit API* dialog: one column; *GET / GET + SET* as two buttons; *PUT sends* only shows for
    GET + SET, with the range as a hint and the named values in a list.
- **Bold headers**: panel titles and table column headers (Trace, Test API, Analysis, Simulation).

## Fixed

- **Cyclic was lost when the app restarted.** The messages of the Interactive generator came back with *Cyclic*
  off. It is kept now: a restored Cyclic message is armed and sends once its channel is open and the measurement
  is started (*Start*, `F9`).

See [1.2.5](RELEASE_NOTES_1.2.5.md) for groups in the Interactive generator and the two-window fix.
