# AutoSim Studio Web – web mode

The simulator in a browser. The PC with the CAN adapters runs a small server; any browser (on the same PC or on
another PC of the network) shows the same desktops as the app: **Setup** (Channels | Database), **Simulation**
(Interactive generator, Auto response, Trace) and **Analysis** (Bus trace). The Signal plot is not part of web mode.

Everything the browser does goes through an HTTP / JSON API and one WebSocket (below), so scripts and test benches
can drive the simulator the same way.

## 1. Start it

| How | Command |
|---|---|
| Installed (Windows) | Start menu → **AutoSim Studio Web** (this PC only) or **AutoSim Studio Web (network access)** |
| From source | `uv run --extra web autosim-web` or `uv run --extra web python -m autosim.web` |

A console window shows the address and the browser opens on it (`--no-browser` to skip). Close the window or press
`Ctrl+C` to stop.

| Option | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Address to listen on. `0.0.0.0`: every network interface (other PCs can connect) |
| `--port` | `8765` | TCP port |
| `--data-dir` | `~/.autosim-web` | Settings (`settings.json`) and uploaded databases (`databases/`) |
| `--token` | random when the host is not loopback | Access token |
| `--no-token` | off | No token even on a network address (trusted lab network only) |
| `--dbc FILE` | – | Database to add at start (repeatable) |
| `--no-browser` | off | Do not open the browser |

**Security.** On `127.0.0.1` only this PC can connect. With `--host 0.0.0.0` anyone who can reach the port could
send frames on the real bus, so a token is required: the printed address contains `?token=…`, opening it once stores
a cookie in that browser. API clients send the header `X-AutoSim-Token: <token>`. Allow the port in the Windows
firewall when other PCs must connect.

## 2. What it can do

Same behaviour as the desktop windows, on the same engine:

- **Channels**: Scan, ESP32 / CANable / PCAN / simulated nodes / **VIRTUAL CAN** (Android bridge), rates limited
  to what the adapter supports, Connect, Connect all, Disconnect, rename (ESP32), live state and counters.
- **Database**: upload `.dbc` / `.arxml` / `.kcd` / `.sym` (button or drag and drop) or add a file by its path on the
  server, tree (nodes, Tx messages, messages, signals), search, properties, Channel choice per database (All
  channels / one channel / Not used), reload, remove.
- **Interactive generator**: Add signals dialog, channel, cycle, FD / BRS, Cyclic, Send, raw / physical / symbolic
  values, generators (sine, ramp, square, toggle) with their parameters, live values 5 times per second.
- **Auto response**: the full rule editor, on / off, hits, duplicate, delete, import / export JSON.
- **Trace**: fixed view (one row per message, expandable to decoded signals, highlight on change) and chronological
  view (last 100 000 frames in the browser, virtual scrolling), filters (the name filter works as in the app: `Speed|Gear`, `ADRV_*`, `!CHECKSUM`; the 5 recent filters are kept in the browser), pause, clear, export CSV / ASC / BLF of the
  last 200 000 frames kept by the server.
- Log, status bar (bus load, frames/s, errors), light / dark theme, `Ctrl+1…3`, `Ctrl+J`, `Ctrl+L`, `Space`.

Settings (databases, rules, generator messages, last form) are saved in `--data-dir` and restored at the next start.
Channels are not reopened automatically.

## 3. API

JSON in, JSON out. A problem for the user answers **400** `{"error": "text"}`; a missing token **401**.

### State and channels

| Method | Path | Body / query | Answer |
|---|---|---|---|
| GET | `/api/state` | – | `ports`, `channels`, `form`, `databases`, `cards`, `auto_response`, `version`, … |
| POST | `/api/scan` | – | state, with the ports scanned again |
| GET | `/api/rates` | `?port=SIM1&nominal=500000` | `{"rates": [data rates the adapter accepts]}` |
| POST | `/api/connect` | `{"port", "channel"?, "nominal", "data", "fd", "brs", "iso", "loop"}` | `{"channel": "CAN1"}` |
| POST | `/api/connect_all` | rates / mode as above | `{"opened": [...], "failed": [...]}` |
| POST | `/api/disconnect` | `{"channel"}` | `{"ok": true}` |
| POST | `/api/disconnect_all` | – | `{"closed": n}` |
| POST | `/api/rename` | `{"channel", "name"}` | ESP32 nodes only |

### Databases

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/databases` | – | every database with nodes, messages, signals (start bit, factor, range, unit, value table, …) |
| POST | `/api/databases/upload` | multipart, field `file` (repeatable) | saved in `<data-dir>/databases/` and used on all channels |
| POST | `/api/databases/add` | `{"path"}` | a file on the server PC |
| POST | `/api/databases/use` | `{"path", "channel"}` | `"*"` all channels, `"CAN1"`, or `null` = not used |
| POST | `/api/databases/reload` · `/remove` | `{"path"}` | |
| POST | `/api/decode` | `{"channel", "id", "data": "hex"}` | `{"message", "signals": [{name, value, unit, text}]}` |

### Interactive generator

| Method | Path | Body |
|---|---|---|
| POST | `/api/tx/add` | `{"message"}` or `{"messages": [...]}`, optional `"channel"` → `{"ids": [...]}` |
| POST | `/api/tx/update` | `{"id", "channel"?, "cycle"?, "fd"?, "brs"?, "enabled"?}` (`enabled` = cyclic on / off) |
| POST | `/api/tx/send` | `{"id"}` – send once |
| POST | `/api/tx/signal` | `{"id", "name", "value"}` (physical) or `{"id", "name", "raw"}` → `{"value"}` (clamped) |
| POST | `/api/tx/generator` | `{"id", "name", "kind": "sine"\|"ramp"\|"square"\|"toggle"\|null, "params"?: [p1, p2, p3]}` |
| POST | `/api/tx/remove` | `{"id"}` |
| POST | `/api/tx/stop_all` | – |

### Auto response and trace

| Method | Path | Body |
|---|---|---|
| GET | `/api/rules` | – → `{"enabled", "rules": [rule + when / condition / response text, hits, errors]}` |
| PUT | `/api/rules` | `{"rules": [...], "enabled"?}` → `{"problems": [...]}` (same fields as the desktop rule editor and the JSON export) |
| POST | `/api/rules/enabled` | `{"enabled"}` |
| POST | `/api/rules/reset` | – reset hit counters |
| POST | `/api/trace/pause` | `{"paused"}` |
| POST | `/api/trace/clear` | – |
| GET | `/api/trace/export?format=csv\|asc\|blf` | file download |

### WebSocket `/ws`

Server → client messages (JSON):

| `type` | When | Content |
|---|---|---|
| `hello` | on connect | `rows` (last 2 000 frames), `logs`, `status`, `version` |
| `update` | up to 25 times per second | `rows` (new frames), `dropped`, `logs`, `nodes` (channel states), `version` (changed → GET `/api/state`) |
| `values` | 5 times per second while a message is cyclic | `cards`: `{id: {count, values}}` |
| `status` | every second | per channel `load` (%), `fps`, `state`, `errors`; `hits` of the rules; `trace` size |
| `clear` | trace cleared | – |

A frame row is `[time s, channel, rx (1) / tx (0), id, extended, name, fd, brs, "hex data", cycle ms, changed]`.

### Example (Python)

```python
import requests
B = "http://127.0.0.1:8765"
requests.post(f"{B}/api/connect", json={"port": "SIM1"})
requests.post(f"{B}/api/databases/add", json={"path": r"C:\dbc\vehicle.dbc"})
cid = requests.post(f"{B}/api/tx/add", json={"message": "EngineData"}).json()["ids"][0]
requests.post(f"{B}/api/tx/signal", json={"id": cid, "name": "Speed", "value": 88})
requests.post(f"{B}/api/tx/update", json={"id": cid, "cycle": 10, "enabled": True})
```

## 4. Build and install

`scripts\build.cmd` (Windows) builds both programs with PyInstaller and one installer with Inno Setup:
`dist\installer\AutoSimStudio-Setup-<version>.exe`. The installer offers **Desktop app and web mode** (default),
**Desktop app only** or **Web mode only**; web mode goes to `<install dir>\web\AutoSimWeb.exe` (about 36 MB, no Qt).

The GitHub Actions workflow `.github/workflows/build-windows.yml` does the same on a Windows runner for every push
to `main` / `web-mode` (and on demand): download the installer from the run's **Artifacts**; a `v*` tag also
publishes it as a release.
