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
The window is the CAN workspace (SOME/IP was removed):

- **App bar** (top): sidebar toggle, *AutoSim Studio*, channel status pill (click it to see the channels),
  **Connect all / Disconnect all**, command palette (`Ctrl+K`), **View** menu (show / hide panels, *Show all
  panels*, *Reset layout*), light / dark toggle, **⚙ Settings** (`Ctrl+,`: theme, files, projects, panels).
- **Sidebar** (left, `Ctrl+B`): *Channels* (adapters, bit rates, open channels) and *Database* (DBC / ARXML tree).
- **Panels**: *Signals* and *Signal plot* on top, *Bus trace* below with the most space. Drag a panel header to
  move or tab it, double-click it (or its pop-out button) to float it e.g. on a second monitor; closing a floating
  panel puts it back. A hidden panel comes back from the **View** menu, the command palette (*Show panel: …*) or
  *Reset layout*. The layout is remembered between runs.
- **Log** (bottom, `Ctrl+J`) and the **status bar**: estimated bus load and frames/s per channel, error counter,
  trace size, Recording / Paused, elapsed time.

**Keyboard:** `Ctrl+K` palette · `Space` pause / resume the trace (not while typing in a field) · `Ctrl+F` trace
search · `Ctrl+L` clear · `Ctrl+E` export · `Ctrl+O` load DBC · `Ctrl+S` save project · `Ctrl+Shift+T` theme.

**Theme:** graphite dark (default) or light, switched from the app bar or *⚙ Settings → Appearance*; remembered.
All text uses **Segoe UI** (13 px, headers 14 px, app title 18 px); the Log shows at startup which font was
resolved. The mouse wheel never changes a value (spin boxes, sliders, drop-downs): it always scrolls the panel.

## 4. Quick try (no hardware needed)

1. Sidebar → *Channels* → port `SIM1` → indigo **Connect** (a simulated node that loops frames back). The same button turns into an outlined red **Disconnect** while the channel is open.
2. Open a database: click **Open DBC File** in the centre card (or drag & drop a `.dbc` onto it), or **⚙ Settings → Open database…**. Sample: `tests/data/sample.dbc`.
3. In *DBC explorer*, double-click a message → it appears in the **Signals** panel.
4. The **Signals** panel is a table (like CANoe's interactive generator): the message row holds channel, cycle,
   **TX** (cyclic on/off), **Send**, the sent count and the live payload bytes (FD / BRS in its ⋯ menu); below it
   one row per signal - *Raw*, *Physical*, *Unit*, *Symbolic* (value table) and *Generator*. Click a cell (or F2)
   to edit it. Expand a signal (▸, Enter or double-click its name) for the slider, generator parameters and its
   bit layout. Collapse a message to its row; *Collapse messages* in the panel header folds all of them.
5. Watch frames in the **Bus trace** panel. By default it is a **fixed trace**: one row per message (per channel and direction) that is updated in place - time, data, cycle time and a frame counter - and expandable (▸) into one row per signal showing its latest decoded value with unit and value-table text. Untick **Fixed** in its toolbar for the chronological view (one row per frame). The toolbar also has Pause/Resume, Clear, Export (CSV/ASC/BLF, always every frame chronologically), Autoscroll, row Highlight (fading flash on updated messages) and ID / name / channel / direction filters.


## 5. With real hardware
- **CANable 2.0 USB-CAN FD adapter** (the small USB-C "CAN FD Bus Analyzer V2.0" boards, USB `16D0:117E`, slcan
  firmware): plug it in, press **Scan** - it is recognised automatically (the card shows *CANable 2.0 · slcan*
  and is preselected). Pick nominal rate and a data rate of **2 or 5 Mbit/s** (the only data rates its firmware
  has), keep **ISO** (its firmware is ISO-only) and **Connect**. Each adapter is one channel and is identified by
  its USB serial number in the *Node* column.
  **Several adapters in parallel:** press **Connect all** to open every detected adapter at once (`CAN1`,
  `CAN2`, ... with the form's bit rates), or connect one at a time - after each Connect the form moves on to
  the next free port and channel name. All channels send and receive at the same time. A port can only be open
  once; selecting an open port (or its row in the table) shows its channel and turns the button into
  Disconnect. In the **Signals** panel each message card has a *Channel* selector and **+ Channel** adds the
  same message for another channel, so e.g. CAN1 and CAN2 can each send their own messages cyclically.
  The CANable's blue LED blinks on every received frame and the green one on transmit (built into its
  firmware).
  Wire CANH-CANH, CANL-CANL and terminate the bus (120 Ω at each end). A lone adapter cannot send: nobody
  acknowledges its frames, which the log reports as *frames are not being acknowledged*. Note that this firmware
  latches its error flags until the adapter is re-plugged.
  Firmware: the boards run the open-source canable2-fw (slcan) - check the version with
  `uv run python tools/canable_info.py`; to update or recover a board see
  [CANABLE_FIRMWARE.md](CANABLE_FIRMWARE.md).
  Hardware tests: one adapter: `$env:AUTOSIM_HW = "1"; uv run pytest tests/test_hw_canable_single.py -v -s`
  (bit-rate matrix, real production DBCs bit-exact to the adapter, connect cycles, GUI); two adapters on one
  bus: `uv run pytest tests/test_hw_canable.py -v -s` (receive in both directions, all FD lengths, 1000-frame
  burst, and the full app with the Hyundai CAN FD DBC: Connect all, CAN1 and CAN2 sending different FD+BRS
  messages every 10 ms to each other, decoded in the fixed trace).
- **Real-world DBCs** for trying the app: `tests/data/real_dbc/` (from comma.ai opendbc, MIT): Hyundai CAN FD,
  Tesla Model 3, VW MQB, Toyota. DBCs with ids above 0x7FF but no extended flag load as 29-bit ids; entries whose
  id cannot exist on a bus are skipped - both are reported in the Log.
- **Several CANables:** use **Connect all**, or connect them one after the other (the form proposes the next free port and channel name); rename a channel's node by double-clicking the *Node* cell.

## 6. Configuring the CAN channel (bit rates etc.)
In the sidebar's *Channels* section choose, per adapter:

| Field | Meaning |
|---|---|
| **Nominal rate** | arbitration-phase bit rate (125 k / 250 k / 500 k / 1 M) |
| **Data rate** | data-phase rate for CAN FD frames: 2 or 5 Mbit/s on a CANable (its firmware presets); the simulated nodes accept an integer multiple (1-10x) of the nominal rate |
| **FD / BRS / ISO** | CAN FD on, bit-rate switching, ISO 11898-1:2015 CRC (untick for non-ISO / Bosch) |
| **Loop** | loopback: the frames are echoed back to the app (CANable: by the PC, nothing is sent on the bus) |

The settings are sent to the adapter when you press **Connect**. To change them, **Disconnect**, adjust, and
**Connect** again. Every adapter / channel has its own settings.

If a CANable is not recognised or does not answer, see [CANABLE_FIRMWARE.md](CANABLE_FIRMWARE.md)
(firmware check with `uv run python tools/canable_info.py`, update / recovery).

## 7. Other commands

| Task | Command |
|---|---|
| Run tests | `uv run pytest` |
| Build the app folder | `.\scripts\build.ps1 -SkipInstaller` → `dist\AutoSimStudio\AutoSimStudio.exe` |
| Build the Windows installer | `winget install JRSoftware.InnoSetup`, then `.\scripts\build.ps1` |

Project settings can be saved and reopened via **⚙ Settings → Save project… / Open project…**.
