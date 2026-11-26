# AutoSim Studio (ICAN) 1.2.9

**Download** (Windows 10 / 11, 64-bit): `AutoSim Studio_1.2.9_x64-setup.exe`. It installs over 1.2.x and keeps
settings, databases, groups, rules, Test APIs and trace filters.

## Fixed

- **Two windows after installing and starting the app.** Two causes:
  - **Windows Firewall.** Since 1.2.6 the backend listens on the network (port 6789), and Windows showed a
    *Windows Security Alert* window next to the app at the first start. The installer now adds a firewall rule for
    the backend (*AutoSim Studio backend*, removed on uninstall), so Windows asks nothing. Other machines still need
    the access token.
  - **A second copy started while the first was still starting**, e.g. the installer's *Run AutoSim Studio* and
    then a click on the icon. The installer starts the app as administrator, where the usual single-instance check
    cannot see it, and its backend needs a few seconds before it answers. A second copy now always recognises the
    running one, waits for it, brings its window to the front and exits.
  - Windows CI checks both: the firewall rule after installing, and two starts at once give one window.

See [1.2.8](RELEASE_NOTES_1.2.8.md) for selecting / copying in the Trace and the clearer Test API details.
