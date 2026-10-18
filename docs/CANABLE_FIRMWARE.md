# CANable 2.0 firmware – what runs on the adapters, and how to set up / update it

## Who wrote what

| Part | Author | Where |
|---|---|---|
| Firmware on the CANable 2.0 boards (STM32G4) | **open source**: *canable2-fw* by Openlight Labs (Ethan Zonca) – not written in this project | <https://github.com/normaldotcom/canable2-fw> |
| PC side: slcan driver, CAN FD frame encoding, error register, multi-adapter handling | this project | `src/autosim/hardware/slcan/` |
| Local copy of the CANable firmware: official image, matching source, flash script | upstream files, packaged here | `firmware/canable2_stm32/` |
| (removed) ESP32 + MCP2518FD firmware written for this project | this project | git history, commit `fc1f393` (`firmware/esp32_can_node`) |

AutoSim talks to the CANable over its USB virtual COM port using the **slcan** text protocol (`O`, `C`, `S0`–`S9`,
`Y2`/`Y5`, `t`/`T`/`d`/`D`/`b`/`B` frames, `V` version, `E` error register). Any CANable 2.0 running *canable2-fw*
(slcan) works; no firmware change is needed for AutoSim.

## Your two boards (as read by AutoSim)

| | COM6 | COM7 |
|---|---|---|
| USB ID | `16D0:117E` | `16D0:117E` |
| USB serial | `2070337B594E` | `2063308F484E` |
| Firmware (`V`) | `16e7497-dirty github.com/normaldotcom/canable2.git` | same |
| MCU | STM32G431 (canable2-fw targets `STM32G431CBTx`; check the chip marking on clone boards) | same |

* `16e7497` is the upstream commit of **2021-10-18** ("Add minimal printf implementation…").
* `-dirty` means the binary was built from a modified source tree – here by the board maker. Upstream builds of that
  commit enumerate as USB `AD50:60C4`; your boards report `16D0:117E`, the USB ID upstream adopted in July 2022, so
  the maker most likely applied that change.
* With this firmware every AutoSim hardware test passes (both directions, 500k/2M … 1M/5M, all CAN FD lengths,
  1000-frame bursts, real DBCs).

Check any board yourself (close AutoSim Studio first – it keeps the COM ports open):

```bash
uv run python tools/canable_info.py
```

## Available firmware builds

| Build | Date | USB ID | Protocol | Works with AutoSim |
|---|---|---|---|---|
| `16e7497-dirty` (your boards, maker build) | 2021-10 base | `16D0:117E` | slcan + CAN FD | yes (tested) |
| **`b158aa7` "slcan with FD support"** – latest upstream, offered by the canable.io web updater | 2022-07-07 | `16D0:117E` | slcan + CAN FD | yes (same command set) |
| upstream builds up to `16e7497` | 2021 | `AD50:60C4` | slcan + CAN FD | yes (AutoSim recognises this ID too) |
| candleLight `ba6b1dd` (web updater's other option) | 2021-07 | gs_usb (no COM port) | native CAN interface (Linux SocketCAN / candle API) | **no** – AutoSim needs slcan |

Changes from `16e7497` to `b158aa7` upstream: the USB ID change (already in your maker build) and a printf library
update. **There is no functional reason to update your boards today.** Update if a board misbehaves, if it was
flashed with candleLight, or to get a known clean upstream build.

## Before you update

1. Close AutoSim Studio (or anything else using the COM port).
2. Run `uv run python tools/canable_info.py` and note the COM port, serial number and firmware of each board.
3. Update **one board at a time** (unplug the other) so you never flash the wrong one.
4. Optional: back up the current firmware (method B, step 3).

Flashing cannot brick the board: the STM32 bootloader lives in ROM (system memory) and is not touched by
programming flash. If anything goes wrong, enter boot mode again and flash again.

## Step 1 – put the board in boot (DFU) mode

1. Unplug the board.
2. **Hold the BOOT button** (on some clone boards: a jumper or two pads labelled BOOT / B0 – bridge them) and plug
   the USB cable in. Release after about a second.
3. The blue LED is dimly lit. Windows *Device Manager* now shows **STM32 BOOTLOADER** (USB `0483:DF11`, under
   *Universal Serial Bus devices*) and the board's COM port is gone.

If no STM32 BOOTLOADER appears: the button was not held while plugging in, or the cable is charge-only.

## Step 2 – flash (choose one method)

### Method A – web updater (simplest)

1. Open <https://canable.io/updater/canable2.html> in **Chrome or Edge** (WebUSB; Firefox does not work).
2. With the board in boot mode, select **"slcan with FD support (b158aa7 7/6/22)"** – not candlelight.
3. Press **Connect and Update**, pick *STM32 BOOTLOADER* in the browser dialog and wait for the success message.
4. Unplug and plug the board back in **without** holding BOOT.

Nothing in the browser dialog on Windows? The page links a driver fix utility
(`canable.io/utilities/ImpulseRC_Driver_Fixer.exe`); alternatively install the **WinUSB** driver for
*STM32 BOOTLOADER* with [Zadig](https://zadig.akeo.ie/) (*Options → List all devices*). Only change the driver of the
*STM32 BOOTLOADER* entry – never of other devices.

### Method B – dfu-util (command line, works offline)

1. Install [dfu-util](https://dfu-util.sourceforge.net/) (Windows binaries on its release page) and give
   *STM32 BOOTLOADER* the WinUSB driver with Zadig (see above).
2. The firmware image is in the project: `firmware/canable2_stm32/bin/canable2-b158aa7.bin` (checksum in
   `bin/SHA256SUMS`; same file as <https://canable.io/builds/canable2/slcan/canable2-b158aa7.bin>).
   **Shortcut:** `scripts\flash_canable.cmd` does steps 3-4 (checksum, backup, flash; dfu-util from
   `scripts\setup.cmd -Firmware`).
3. With the board in boot mode:

```bash
dfu-util -l
```

   lists the device (`0483:df11`). Optional backup of the current firmware (STM32G431: 128 KB flash):

```bash
dfu-util -d 0483:df11 -a 0 -s 0x08000000:131072 -U canable2-backup-<serial>.bin
```

   Flash (the same command the upstream Makefile uses; `:leave` starts the new firmware afterwards):

```bash
dfu-util -w -d 0483:df11 -c 1 -i 0 -a 0 -s 0x08000000:leave -D canable2-b158aa7.bin
```

4. If the board stays in boot mode, unplug and replug it without holding BOOT.

### Method C – STM32CubeProgrammer (ST's GUI)

1. Install STM32CubeProgrammer from st.com (it brings its own DFU driver).
2. Board in boot mode → select **USB** → *Connect*.
3. *Erasing & Programming*: file = the `.bin`, start address **0x08000000** → *Start Programming*.
4. *Disconnect*, unplug, replug without BOOT.

If CubeProgrammer does not see the board after Zadig was used, reinstall the DFU driver that ships in the
CubeProgrammer installation's `Drivers` folder.

## Building the firmware from source (optional)

The matching source (commit `b158aa7`) is in `firmware/canable2_stm32/source/` – build it there with
`make GIT_VERSION=b158aa7 GIT_REMOTE=github.com/normaldotcom/canable2-fw.git` (not a git checkout). For the latest
upstream state:

```bash
git clone https://github.com/normaldotcom/canable2-fw
```

* Toolchain: Arm GNU toolchain (`arm-none-eabi-gcc`) and `make` on the PATH (on Windows MSYS2 or WSL is easiest).
* `make` builds `build/canable2-<git version>.bin` (the version string comes from `git describe`; it gets `-dirty`
  when the source tree has local changes – exactly what your boards report).
* `make flash` runs the dfu-util command above (it uses `sudo`; on Windows run the dfu-util line yourself).
* License: the firmware sources are **GPL-3.0** (see `LICENSE.md` in the repository). Using the adapters with
  AutoSim is not affected; if you distribute modified firmware, its source must be made available.

## Step 3 – verify

1. `uv run python tools/canable_info.py` → each board shows its serial number and the new firmware version
   (e.g. `b158aa7 …`), error register `0x00`.
2. AutoSim Studio → *Channels* → **Scan**: the port shows *CANable 2.0 · slcan*; **Connect** works.
3. With two adapters wired CANH–CANH, CANL–CANL and terminated (120 Ω each end), run the hardware tests
   (PowerShell):

```powershell
$env:AUTOSIM_HW = "1"; uv run pytest tests/test_hw_canable.py -v
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| No *STM32 BOOTLOADER* in Device Manager | BOOT not held while plugging in; charge-only cable; try another USB port |
| Browser dialog / `dfu-util -l` shows nothing | WinUSB driver missing: driver fix utility or Zadig for *STM32 BOOTLOADER* |
| `LIBUSB_ERROR_ACCESS` / device busy | another program (browser tab, CubeProgrammer) holds the bootloader – close it |
| Board still in boot mode after flashing | unplug and replug without BOOT |
| AutoSim: "no slcan answer to 'V'" | the board runs candleLight (gs_usb) or another firmware – flash the slcan build |
| AutoSim does not list the board as a CANable | unknown USB ID: run `tools/canable_info.py`; AutoSim knows `16D0:117E` and `AD50:60C4` |
| COM port number changed | normal after reflashing – press **Scan** |
| Error register shows old flags | the firmware latches its error flags until the board is re-plugged (normal) |

## LEDs (canable2-fw)

Blue blinks on every received frame, green on transmit; a dim blue LED in boot mode.

## Sources

* Firmware repository and history: <https://github.com/normaldotcom/canable2-fw> (commits `16e7497`, `39e7ecc`,
  `b158aa7`; Makefile flash command; `LICENSE.md`)
* CANable 2.0 web updater: <https://canable.io/updater/canable2.html>
* dfu-util: <https://dfu-util.sourceforge.net/> · Zadig: <https://zadig.akeo.ie/>
