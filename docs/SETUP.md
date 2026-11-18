# AutoSim Studio – Setup

From zero to a running simulation. Pick the parts you need:

1. [Install the app (Windows)](#1-install-the-app-windows)
2. [Run from source / development environment](#2-run-from-source--development-environment)
3. [First run without hardware](#3-first-run-without-hardware)
4. [ESP32-S3 + MCP2518FD node](#4-esp32-s3--mcp2518fd-node)
5. [CANable 2.0 adapter](#5-canable-20-adapter)
6. [Android head unit bridge](#6-android-head-unit-bridge)
7. [Troubleshooting](#7-troubleshooting)

## 1. Install the app (Windows)

1. Run `AutoSimStudio-Setup-<version>.exe`. It installs into `C:\Program Files\AutoSim Studio` and adds a Start menu
   entry (desktop icon optional).
2. Start **AutoSim Studio**. Nothing else is needed: Python and Qt are bundled.

To build the installer yourself, see [Deployment §3.1](DEPLOYMENT.md#31-desktop-application).

## 2. Run from source / development environment

Requirements: Git, and [uv](https://docs.astral.sh/uv/) (it installs Python 3.11 itself).

**Windows, one command** (installs uv, Python and dependencies, then runs the tests):

```powershell
git clone <repository> CAN_SIMULATOR
cd CAN_SIMULATOR
scripts\setup.cmd              # add -Installer to build installers, -Firmware for the CANable toolchain, -All for both
scripts\run.cmd
```

**Any OS, manually:**

```bash
# install uv: https://docs.astral.sh/uv/getting-started/installation/  (Windows: winget install astral-sh.uv)
git clone <repository> CAN_SIMULATOR && cd CAN_SIMULATOR
uv sync                 # Python 3.11 + exact dependency versions from uv.lock
uv run pytest           # optional: about 300 tests; headless: QT_QPA_PLATFORM=offscreen
uv run autosim          # or: uv run python -m autosim
```

Linux notes: your user needs access to serial ports (`sudo usermod -aG dialout $USER`, then log in again). Qt needs
the usual X11 / Wayland libraries (on Debian / Ubuntu: `libxkbcommon-x11-0 libxcb-cursor0`).

## 3. First run without hardware

1. **Setup** desktop (`Ctrl+1`) → *Channels*: port **SIM1** → **Connect**. A simulated node loops every frame back.
2. Same desktop, *Database* window → **Add database…** → `tests/data/sample.dbc` (or drop the file on the window).
3. **Simulation** desktop (`Ctrl+2`) → **Add signals…** → tick **EngineData** → **Add**. Tick **Cyclic**; set
   *Speed*'s generator to **Sine**.
4. **Analysis** desktop (`Ctrl+3`): the trace shows Tx and Rx. In *Signal plot* → **Add signals…** → *Speed*.

Real-world databases to explore: `tests/data/real_dbc/` (Hyundai CAN FD, Tesla Model 3, VW MQB, Toyota; from
comma.ai opendbc, MIT).

## 4. ESP32-S3 + MCP2518FD node

**Hardware**: an ESP32-S3 DevKit and an MCP2518FD module with a CAN FD transceiver (e.g. ATA6563 or TJA1051), 120 Ω
termination, and a 5 V supply for the transceiver.

| MCP2518FD | ESP32-S3 (default) |
|---|---|
| SCK | GPIO 12 |
| SI (MOSI) | GPIO 11 |
| SO (MISO) | GPIO 13 |
| CS | GPIO 10 |
| INT | GPIO 4 |
| VCC (logic) | 3V3 |
| Transceiver VCC | 5 V (common GND) |

Full wiring, other boards and pictures: [ESP32_MCP2518FD_WIRING](ESP32_MCP2518FD_WIRING.md).

**Flash with the Arduino IDE**

1. *Boards Manager*: install **esp32 by Espressif Systems** 3.x.
2. *Sketch → Include Library → Add .ZIP Library…* →
   `firmware/esp32_can_node/release/AutoSimCanNode-1.5.0.zip` (the MCP2518FD driver is included).
3. *File → Examples → AutoSimCanNode → AutoSimCanNode*. Change only what differs on your board in `setup()` (pins,
   LED). The crystal (20 or 40 MHz) is detected automatically.
4. Board **ESP32S3 Dev Module**. Use the **UART** USB port with *USB CDC On Boot: Disabled* (921 600 Bd), or the
   native **USB** port with *Enabled*. Upload, then press RESET.

**Or with PlatformIO**: `cd firmware/esp32_can_node && pio run -e esp32s3_uart -t upload`.

**Connect in AutoSim**: *Setup → Channels* → **Scan** → pick the board's COM port → set the bit rates → **Connect**.
The node table shows `ESP32-xxxxxx · running`. Tick **Loop** first to test without a bus.

## 5. CANable 2.0 adapter

1. Plug it in; Windows uses its built-in USB CDC driver.
2. *Setup → Channels* → **Scan**: it appears as *CANable 2.0 · slcan* and is preselected.
3. Nominal rate as needed; data rate **2 or 5 Mbit/s** (the firmware's only FD rates); keep **ISO**; **Connect**.

**Recommended firmware** (more tolerant bit timing next to crystal-clocked nodes such as the ESP32):

```powershell
scripts\setup.cmd -Firmware            # once: dfu-util, Arm toolchain, make
# hold BOOT while plugging the CANable in, then:
scripts\flash_canable.cmd -Bin firmware\canable2_stm32\bin\canable2-b158aa7-autosim1.bin
uv run python tools/canable_info.py    # check the version
```

Details: [CANABLE_FIRMWARE](CANABLE_FIRMWARE.md).

**Two adapters on one bus** (e.g. an ESP32 node and a CANable): connect CANH–CANH and CANL–CANL, put 120 Ω at
both ends, use the **same bit rates** on both channels, then **Connect all**.

## 5b. PEAK PCAN adapter

1. Install **PEAK's device driver** (free, includes the PCAN-Basic API):
   <https://www.peak-system.com/support/downloads/drivers/driver-packages/>. AutoSim does not install it. When it
   is missing, *Hardware* / *Help → Download PEAK driver (PCAN)…* opens that page; the Log says so when a PEAK
   adapter is plugged in, and Connect explains it. On Linux, install PEAK's `pcan` driver with PCAN-Basic for Linux.
2. Plug the adapter in, *Setup → Channels* → **Scan**: each channel appears as `PCAN_USBBUS1`, `PCAN_USBBUS2`, …
   with its device name (e.g. *PCAN-USB FD · PEAK*). A PCAN adapter is preselected like a CANable.
3. Choose the rates and **Connect**:
   - PCAN-USB (no FD): classic CAN only; the form switches FD off.
   - FD models: data rate 1, 2, 4, 5 or 8 Mbit/s. Sample points 80 % (nominal) / 75 % (data) are computed by
     AutoSim. ISO CAN FD (non-ISO is a setting in PEAK's tools).
4. Close PCAN-View or any other program using the same channel first: a channel can be opened by one application.

Errors (error passive, bus-off, unplugged adapter, error frames from a bit-rate mismatch) appear in the Log with the
channel name. After bus-off the driver restarts the controller by itself.

## 6. Android head unit bridge

**Try it on the PC first** (no head unit):

```bash
uv run python -m autosim.hardware.bridge.sim_bridge --demo
```

Then in AutoSim connect port `bridge://127.0.0.1:29536`. The demo plays a VHAL that sends requests.

**On a head unit** (userdebug / eng build, adb root):

```powershell
scripts\android_bridge.cmd check                 # environment report (kernel CAN, vcan, VHAL, SELinux)
scripts\android_bridge.cmd build                 # NDK build (ANDROID_NDK_HOME)
scripts\android_bridge.cmd deploy -RestartVhal   # vcan in place of the VHAL's interface + bridge + adb forward
```

In AutoSim: *Setup → Channels* → port `bridge://127.0.0.1:29536` (or `bridge://<head unit IP>:29536` over
Ethernet) → **Connect**. Then add rules in **Simulation → Auto response**. Undo with
`scripts\android_bridge.cmd restore`.

Background and boot-time integration: [ANDROID_BRIDGE](ANDROID_BRIDGE.md),
[ANDROID_ENV_CHECKLIST](ANDROID_ENV_CHECKLIST.md), [ANDROID_CAN_LAYERS](ANDROID_CAN_LAYERS.md).

## 7. Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| `HELLO: no answer` from an ESP32 | Wrong USB port for the *USB CDC On Boot* setting, firmware not running (press RESET), Serial Monitor still open, or different baud (`AUTOSIM_BAUD`) |
| ESP32 sends, the other node reports STUFF / FORM / CRC errors | Bit rate mismatch: same rates on both channels; firmware below 1.4 with a 20 MHz crystal (update) |
| `no ACK` / `nominal BIT0` | Transceiver not powered (5 V) or in standby, CANH / CANL swapped, no termination, or the other node not open |
| Only the first frame goes out, nothing is received | INT wire not connected (firmware 1.5 warns `INT never fired` and keeps working by polling) |
| *PEAK driver not installed* (Log / Connect) | Install PEAK's driver (*Help → Download PEAK driver*), then **Scan** |
| PCAN `… in use by another application` | Close PCAN-View / CANoe / another AutoSim using that channel |
| PCAN `error passive` / `bus-off` | Nobody acknowledges (lone adapter, other node closed), bit rate mismatch or missing termination |
| CANable reports frames not acknowledged | A lone adapter: nobody acknowledges; connect a second node and terminate the bus |
| Bridge stays in ERROR | `adb forward` missing (`scripts\android_bridge.cmd forward`), bridge not running (`status`, `log`), or a firewall when using Ethernet |
| The window looks wrong or does not move / resize | Start with `AUTOSIM_NATIVE_TITLEBAR=1` to use the OS title bar |
| A window is missing | *View → Reset layout*, or *View* → tick the window |

More: the Log window (rail → **Log**, `Ctrl+J`) explains errors with hints.
