# AutoSim Studio

CAN / CAN FD simulator and analyzer (Python 3.11+, PySide6). Requirements: [PLAN.md](PLAN.md).
Design and milestones: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md). Serial protocol of the simulated nodes: [docs/ESP32_PROTOCOL.md](docs/ESP32_PROTOCOL.md).
CANable 2.0 adapters run the open-source canable2-fw (slcan); setup / update guide: [docs/CANABLE_FIRMWARE.md](docs/CANABLE_FIRMWARE.md).

```
src/autosim/
  core/common/          frame types, EventBus, project config
  core/can_engine/      DBC, signal codec, TX scheduler, RX dispatcher, bus manager, generators, log export
  hardware/esp32_gateway/  node manager (port scan, CANable detection), AutoSim serial protocol used by the
                           simulated SIM nodes, CanBackend adapter
  hardware/slcan/          CANable 2.0 USB-CAN FD adapters (slcan): codec, node, backend
  ui/                   main window shell (app bar, sidebar, docks, status bar, command palette), panels,
                        Qt models, design tokens + theme (Segoe UI), app icon (ui/assets, tools/ui/make_app_icon.py)
firmware/canable2_stm32/   CANable 2.0 (STM32G431) slcan firmware: official image, source, flash script
tests/
```

## New machine / migration
Clone the repository, run `scripts\setup.cmd -All` (installs everything and runs the tests), then `scripts\run.cmd`.
`scripts\build.cmd` builds the Windows installer; `scripts\build_firmware.cmd` / `scripts\flash_canable.cmd` handle the
CANable firmware. Details: [docs/MIGRATION.md](docs/MIGRATION.md).

## Development (dependencies managed by [uv](https://docs.astral.sh/uv/))
Migrating to a new machine: install uv, clone, then

```
uv sync          # installs Python 3.11 (.python-version) + exact versions from uv.lock
uv run pytest
uv run autosim   # or: uv run python -m autosim
```
Add a dependency: `uv add <pkg>` (runtime) / `uv add --dev <pkg>`; commit `pyproject.toml` and `uv.lock`.

## Windows installer
Needs Inno Setup 6 (`winget install JRSoftware.InnoSetup`):
```
.\scripts\build.ps1                  # PyInstaller one-folder build + installer -> dist\installer\AutoSimStudio-Setup-<version>.exe
.\scripts\build.ps1 -SkipInstaller   # app folder only: dist\AutoSimStudio\
```
Version comes from `pyproject.toml`. Files: `packaging/autosim.spec`, `packaging/installer.iss`.

## Trying it without hardware
In the sidebar (*Channels*) pick port **SIM1** (built-in simulated node that loops frames back), press Connect, load a DBC, double-click a message and enable *Cyclic TX*.

SOME/IP support and the ESP32 firmware were removed (2026-10-18); the former code is in git history (commit `fc1f393`).
