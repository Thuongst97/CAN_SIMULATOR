# CANable 2.0 firmware (STM32G431) – slcan with CAN FD

Firmware for the CANable 2.0 USB-CAN FD adapters used by AutoSim Studio. It is the **open-source canable2-fw by
Openlight Labs** (not written in this project), kept here so the adapters can be set up, updated or recovered
offline. Full guide (boot mode, drivers, web updater, troubleshooting): [docs/CANABLE_FIRMWARE.md](../../docs/CANABLE_FIRMWARE.md).

| Folder / file | Content |
|---|---|
| `bin/canable2-b158aa7.bin` | official prebuilt image "slcan with FD support (b158aa7 7/6/22)" from canable.io (26 428 bytes) |
| `bin/SHA256SUMS` | checksum of the image (`40a913e6…10122`) |
| `flash.ps1` | flash with dfu-util: checksum check → backup of the current firmware → write → start |
| `backup/` | backups made by `flash.ps1` (one file per run) |
| `source/` | the matching source, upstream commit `b158aa7` (see `source/UPSTREAM.md`) |

## Quick update (one board at a time)

1. Close AutoSim Studio. Optional: `uv run python tools/canable_info.py` (notes firmware + serial number).
2. Hold **BOOT** while plugging the board in → Device Manager shows **STM32 BOOTLOADER** (USB `0483:DF11`).
3. Flash:

```bat
scripts\flash_canable.cmd
```

4. Unplug and replug **without** BOOT, then `uv run python tools/canable_info.py` → firmware `b158aa7 …`.

Needs dfu-util (`scripts\setup.cmd -Firmware` puts it in `.tools\`) and, once per machine, the WinUSB driver for
*STM32 BOOTLOADER* ([Zadig](https://zadig.akeo.ie/)). Without installing anything: the web updater
<https://canable.io/updater/canable2.html> (Chrome / Edge) flashes the same build.

## Your boards today

Both adapters run `16e7497-dirty` – upstream commit 16e7497 (2021-10-18) built by the board maker with the newer USB
ID `16D0:117E`. It works with AutoSim (all hardware tests pass); updating to `b158aa7` is optional.

## Building from source

Needs the Arm GNU toolchain (`arm-none-eabi-gcc`) and `make` (MSYS2 or WSL on Windows). This copy is not a git
checkout, so pass the version string the Makefile would otherwise take from `git describe`:

```bash
cd firmware/canable2_stm32/source
make GIT_VERSION=b158aa7 GIT_REMOTE=github.com/normaldotcom/canable2-fw.git
```

Output: `build/canable2-b158aa7.bin` – flash it with `scripts\flash_canable.cmd -Built`. On Windows,
`scripts\build_firmware.cmd` runs this make command with the right tools on PATH. A modified build should get its own
version (e.g. `GIT_VERSION=b158aa7-mine`) so `tools/canable_info.py` and AutoSim show which firmware a board runs.

## License

The firmware sources are **GPL-3.0** (`source/LICENSE.md`; the ST HAL / CMSIS / USB device library files carry their
own licences in their headers). AutoSim only talks to the adapter over its serial port and contains none of this
code. If you distribute modified firmware, make its source available under the GPL.
