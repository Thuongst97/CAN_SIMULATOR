# AutoSim Studio

CAN / CAN FD simulator and analyzer (Python 3.11+, PySide6). Requirements: [PLAN.md](PLAN.md).
Design and milestones: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md). Serial protocol: [docs/ESP32_PROTOCOL.md](docs/ESP32_PROTOCOL.md).

```
src/autosim/
  core/common/          frame types, EventBus, project config
  core/can_engine/      DBC, signal codec, TX scheduler, RX dispatcher, bus manager, generators, log export
  hardware/esp32_gateway/  wire protocol (implemented), serial node, node manager, CanBackend adapter
  hardware/slcan/          CANable 2.0 USB-CAN FD adapters (slcan): codec, node, backend
  ui/                   main window shell (app bar, sidebar, docks, status bar, command palette), panels,
                        Qt models, design tokens + theme (bundled Inter font)
firmware/esp32_can_node/   ESP32 + MCP2518FD firmware (PlatformIO, see its README)
tests/
```

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

SOME/IP support was removed (2026-10-18); its former code is kept in `archive/someip_removed_2026-10-18/` (not part of the app, tests or installer).
