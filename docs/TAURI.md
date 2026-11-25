# AutoSim Studio desktop app (Tauri)

The Tauri app replaces the PySide6 window with the web UI shown in a native window (WebView2 on Windows,
WebKitGTK on Linux). The CAN engine, adapters and Test API do not change: they run in the Python backend
`AutoSimWeb`, the same program as web mode.

```
┌──────────────── AutoSim Studio (Tauri, Rust) ────────────────┐
│ window: splash → http://127.0.0.1:<port>/ (web UI)           │
│ native Open / Save dialogs (window.__TAURI__)                │
│ starts / stops ─────────────┐                                │
└─────────────────────────────┼────────────────────────────────┘
                              ▼
          AutoSimWeb --host 127.0.0.1 --port 8765 --exit-with-pid <app pid>
          (aiohttp + CAN engine + Test API; data in the app data folder)
```

## What the app does

- It uses port **8765** (the usual Test API address for test systems), or a free port if 8765 is taken.
- It starts the backend on 127.0.0.1 only, with no console window. The backend writes its output to
  `backend.log` in the app data folder:
  - Windows: `%APPDATA%\com.autosim.studio`
  - Linux: `~/.local/share/com.autosim.studio`
- A splash page shows until the backend answers, then the window opens the UI. If the backend fails, the splash
  shows the error.
- Closing the window stops the backend. If the app is killed, the backend stops by itself (`--exit-with-pid`).
- Only one copy runs at a time: starting a second copy brings the first window to the front. The window size and
  position are remembered.
- Add database, imports and exports use the system's native Open and Save dialogs.

## Build

Requirements:

- uv;
- Node 22;
- Rust (stable);
- on Linux, also the WebKitGTK 4.1 dev packages (`libwebkit2gtk-4.1-dev`, `librsvg2-dev`, `libayatana-appindicator3-dev`).

```
uv sync --group build --extra web
uv run --group build python -m PyInstaller packaging/autosim_web.spec --noconfirm   # → dist/AutoSimWeb/
cd tauri
npm ci
npx tauri build                  # Windows: NSIS installer; Linux: .deb
```

Output: `tauri/src-tauri/target/release/bundle/` (`nsis/AutoSim Studio_<ver>_x64-setup.exe`, or
`deb/AutoSim Studio_<ver>_amd64.deb`). The installer contains the backend, so nothing else needs to be installed.
On Windows the installer downloads WebView2 if it is missing; it is already part of Windows 10 and 11.

CI: `.github/workflows/build-tauri.yml` builds the Windows installer and checks that the app starts its backend
and stops it on exit. It runs on a push to the `tauri` branch, or by hand. Get the result from the run's
artifact "AutoSimStudio-Tauri-Setup".

## Develop

Run the backend from source so that Python and web UI changes need no rebuild:

```
cd tauri
AUTOSIM_BACKEND="uv run --extra web python -m autosim.web" npx tauri dev
```

On Windows (PowerShell), set the variable first: `$env:AUTOSIM_BACKEND = "..."`.

Without `AUTOSIM_BACKEND`, the app looks for `backend/AutoSimWeb[.exe]` (the bundled resource), then for
`dist/AutoSimWeb/` in the repository. Reload the window (Ctrl+R) to pick up web UI changes.
