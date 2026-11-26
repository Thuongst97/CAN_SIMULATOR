# AutoSim Studio (ICAN) 1.2.9

**Download** (Windows 10 / 11, 64-bit): `AutoSim Studio_1.2.9_x64-setup.exe`. It installs over 1.2.x and keeps
settings, databases, groups, rules, Test APIs and trace filters.

## Fixed

- **Two title bars after an update** (the Windows title bar *ICAN* above the app's own *ICAN · AutoSim Studio*,
  looking like two windows). Since 1.2.1 the app draws its own title bar. But the window-state plugin, which remembers
  the window's size and position, also remembered *"has a Windows title bar"* from 1.0 - 1.2.0 and put it back at
  every start. It now remembers size, position and maximized state only, and the app always turns the Windows title
  bar off. Fresh installs were not affected, which is why the earlier checks did not see it.
  - Windows CI now starts the installed app with a window state left by an old version and checks that there is no
    Windows title bar.
- **No *Windows Security Alert* at the first start.** Since 1.2.6 the backend listens on the network (port 6789); the
  installer now adds a Windows Firewall rule for it (*AutoSim Studio backend*, removed on uninstall). Other machines
  still need the access token.
- **A second start while the app is still starting** (e.g. the installer's *Run AutoSim Studio*, then a click on the
  icon) now always shows the running window instead of starting another copy.

See [1.2.8](RELEASE_NOTES_1.2.8.md) for selecting / copying in the Trace and the clearer Test API details.
