# Moving AutoSim Studio to another machine

The project is a git repository (`origin` = <https://github.com/Thuongst97/CAN_SIMULATOR>). Everything needed is
tracked: sources, `uv.lock` (exact Python package versions), the CANable firmware (`firmware/canable2_stm32/`) and the
scripts below. Environments, builds and caches are not tracked (`.gitignore`) and are recreated on the new machine.

## Scripts (`scripts\`, double-click or run from a terminal)

Each `.cmd` runs the matching `.ps1` with `-ExecutionPolicy Bypass`, so the PowerShell execution policy does not have
to be changed. Arguments are passed through.

| Script | What it does |
|---|---|
| `setup.cmd` | installs **uv** if missing, creates `.venv` with Python 3.11 and the exact packages of `uv.lock`, runs the tests |
| `setup.cmd -Installer` | + **Inno Setup 6** and the PyInstaller group (to build the Windows installer) |
| `setup.cmd -Firmware` | + **dfu-util** (unpacked into `.tools\`, PATH unchanged), **Arm GNU toolchain**, **make** |
| `setup.cmd -All` | all of the above |
| `setup.cmd -Check` | reports what is installed, changes nothing |
| `run.cmd` | starts AutoSim Studio from the sources |
| `build.cmd` | builds the app (PyInstaller) and the installer `dist\installer\AutoSimStudio-Setup-<version>.exe`; `-SkipInstaller` for the app folder only |
| `build_firmware.cmd` | builds the CANable firmware from `firmware\canable2_stm32\source` (`-Version`, `-Clean`) |
| `flash_canable.cmd` | flashes a CANable in boot mode: official image, `-Built` (your build) or `-Bin <file>`; backup first unless `-NoBackup` |

Programs are installed with **winget** (Windows 10 1809+ / 11). Without winget, uv comes from its official installer;
install the others by hand: Inno Setup <https://jrsoftware.org/isdl.php>, Arm GNU toolchain
<https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads>, make (MSYS2 or ezwinports). **Git for Windows**
is needed anyway for the clone; the firmware Makefile also uses its `sh`, `sed`, `mkdir`, `rm`.

## 1. On the old machine: commit and push

```bat
git add -A
git commit -m "Describe the change"
git push
```

`git status` must be clean afterwards. Line endings are normalised by `.gitattributes` (the `.cmd` / `.ps1` scripts
are checked out with Windows line endings on every machine).

## 2. On the new machine

1. Install **Git for Windows** (<https://git-scm.com/download/win>) and clone:

```bat
git clone https://github.com/Thuongst97/CAN_SIMULATOR.git
cd CAN_SIMULATOR
```

2. Run the setup – app only, or with installer / firmware tools:

```bat
scripts\setup.cmd -All
```

3. Start the app:

```bat
scripts\run.cmd
```

4. Plug in the CANables: Windows installs its built-in USB serial (CDC) driver automatically; no driver download is
   needed. In the app: *Channels* → **Scan** → **Connect all**.

Later updates on either machine: `git pull`, then `scripts\setup.cmd` again if `uv.lock` changed.

Settings (theme, layout, last port / database) are stored per Windows user (registry,
`HKCU\Software\AutoSim\AutoSimStudio`) and are not moved; the app starts with the defaults.

Without network access to GitHub: `git bundle create autosim.bundle --all` on the old machine, copy the file, then
`git clone autosim.bundle CAN_SIMULATOR` on the new one.

## 3. Optional

* Windows installer for machines without the sources: `scripts\build.cmd` → give them
  `dist\installer\AutoSimStudio-Setup-<version>.exe` (it contains Python and everything else).
* Hardware tests with two CANables wired together (PowerShell):

```powershell
$env:AUTOSIM_HW = "1"; uv run pytest tests/test_hw_canable.py -v
```

* Firmware: check the boards with `uv run python tools/canable_info.py`; update / recover them with
  `scripts\flash_canable.cmd` (one-time per machine: WinUSB driver for *STM32 BOOTLOADER* with Zadig) – see
  [CANABLE_FIRMWARE.md](CANABLE_FIRMWARE.md).

## Removed parts

SOME/IP, the ESP32 + MCP2518FD firmware and the bundled Inter font were removed from the project. They remain in git
history at commit `fc1f393`, for example `git checkout fc1f393 -- firmware/esp32_can_node` restores the ESP32
firmware. The local `archive\` folder (if present) is not tracked and can be deleted.

## Verified

A clean copy of the tracked files → `setup.cmd`: environment created from `uv.lock`, 184 tests passed (the hardware
tests are skipped without `AUTOSIM_HW=1`).
