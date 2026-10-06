# How to run AutoSim Studio

## 1. One-time setup
Install [uv](https://docs.astral.sh/uv/) (PowerShell):

```powershell
winget install astral-sh.uv
```

Then, in the project folder:

```powershell
uv sync
```

This installs Python 3.11 and all dependencies at the exact versions in `uv.lock`.

## 2. Start the app

```powershell
uv run python -m autosim
```

## 3. Layout
There is no menu bar: the **⚙ Settings** button in the top-right corner of the tab bar (shortcut `Ctrl+,`) opens a popup with everything that used to be in the File / View / Tools menus: theme, open/save files and projects, show/hide/pop-out panels, reset layout, stop all cyclic TX and quit.

Two separate tabs at the top of the window (the Log dock is shared at the bottom):

- **CAN** – *CAN connection* and *DBC explorer* docks on the left; in the centre a vertical splitter with *Signals* + *Signal plot* on top (35 %) and the *Bus trace* below (65 %). Drag the splitter handles to resize. The pop-out icon on a panel's header (or **⚙ Settings → Panels → Pop out**) moves it into its own window, e.g. on a second monitor; closing that window puts it back. **Settings → Panels → Reset layout** restores the default.
- **SOME/IP** – master-detail: a left sidebar with the *Automotive Ethernet* card (adapter, IPs, Start/Stop) and the *Services* explorer tree (services → methods / events); on the right the inspector for the selected service (Offer / Consume, status pills) with the tabs *Method Caller*, *Event Publisher*, *Provider Responses* and *SD Log Trace*. Clicking a method or event in the tree opens it in the matching tab.

**Theme:** *⚙ Settings → Appearance* → Dark (obsidian, default) / Light (clean slate / white) / Follow system. Status pills show OFFLINE (red) / connected (green). With no DBC loaded the CAN workspace shows an empty-state card: drag & drop a `.dbc` / `.arxml` onto it or click **Open DBC File**. It switches instantly and is remembered for the next start.

## 4. Quick try (no hardware needed)

**CAN**
1. **CAN** tab → *CAN FD node* card → port `SIM1` → indigo **Connect** (a simulated node that loops frames back). The same button turns into an outlined red **Disconnect** while the channel is open.
2. Open a database: click **Open DBC File** in the centre card (or drag & drop a `.dbc` onto it), or **⚙ Settings → Open database…**. Sample: `tests/data/sample.dbc`.
3. In *DBC explorer*, double-click a message → it appears in the **Signals** panel.
4. Move a slider, or choose a generator (sine, ramp…), then tick **Cyclic TX**.
5. Watch frames in the **Bus trace** panel. The toolbar has Pause/Resume, Clear, Export (CSV/ASC/BLF), Autoscroll, row Highlight (fading flash on updated IDs) and ID / name / channel / direction filters.

**SOME/IP**
1. **SOME/IP** tab → *Automotive Ethernet* → network adapter `Loopback (127.0.0.1)` → **Start SOME/IP**.
2. In the *Services* sidebar card: **Load Config** (sample: `tests/data/vsomeip.json`) and optionally **Load .proto** (`tests/data/sample.proto`), or **+ Add** a service by hand.
3. Select the service in the tree → **Offer Service**, then **Consume Service**. The consumer pill turns green (*Available*).
4. Use **Method Caller** and **Event Publisher** to send requests and publish/subscribe events. See **SD Log Trace** for discovery traffic.

## 5. With real hardware
- **ESP32 + MCP2518FD** (CAN tab): flash `firmware/esp32_can_node` (see its README), plug in the node, press **Scan**, select its COM port, set bit rates (nominal/data, FD, BRS, ISO) and **Connect**. For several boards repeat with another port and channel name; rename boards by double-clicking the *Node* cell. Data rate must be an integer multiple (1-10x) of the nominal rate.
- **RAD-MOON** (SOME/IP tab): choose its network adapter and IP, check the SD multicast address (default `224.0.0.1`, port `30490`; many AUTOSAR setups use `224.224.224.245`), press **Start SOME/IP**.

## 6. Configuring the CAN channel (bit rates etc.)
In the **CAN FD node** card choose, per board:

| Field | Meaning |
|---|---|
| **Nominal rate** | arbitration-phase bit rate (125 k / 250 k / 500 k / 1 M) |
| **Data rate** | data-phase rate for CAN FD frames. Only rates the MCP2518FD can reach are listed: an integer multiple (1-10x) of the nominal rate (500 k -> up to 5 M, 1 M -> up to 8 M) |
| **FD / BRS / ISO** | CAN FD on, bit-rate switching, ISO 11898-1:2015 CRC (untick for non-ISO / Bosch) |
| **Loop** | internal loopback: the board receives its own frames, no CAN bus needed (bench test) |

The settings are sent to the board when you press **Connect** (`CONFIG`, then `START`). To change them,
**Disconnect**, adjust, and **Connect** again. Every board/channel has its own settings.

If Connect fails with `HELLO: no answer`, the AutoSim firmware is not running on that port: see
`firmware/esp32_can_node/README.md` (flashing, the right build for your USB port, and a port probe tool).

## 7. Other commands

| Task | Command |
|---|---|
| Run tests | `uv run pytest` |
| Build the app folder | `.\scripts\build.ps1 -SkipInstaller` → `dist\AutoSimStudio\AutoSimStudio.exe` |
| Build the Windows installer | `winget install JRSoftware.InnoSetup`, then `.\scripts\build.ps1` |

Project settings can be saved and reopened via **⚙ Settings → Save project… / Open project…**.
