# AutoSim Studio 0.2.0

**Download:** `AutoSimStudio-Setup-0.2.0.exe` below (Windows 10 / 11, 64-bit, about 38 MB). The installer offers
*Desktop app and web mode* (default), *Desktop app only* or *Web mode only*. Nothing else to install: Python and Qt
are included.

## New

- **Web mode**: the simulator in any browser, also from other PCs on the network (token protected): Channels,
  Database, Interactive generator, Auto response, Bus trace. Start menu → *AutoSim Studio Web*. It comes with an
  HTTP / WebSocket API to drive the simulator from scripts and test benches. See `docs/WEB_MODE.md`.
- **PEAK PCAN adapters** (PCAN-USB, PCAN-USB FD, PCAN-USB Pro FD …) through PEAK's free PCAN-Basic driver: scan,
  classic CAN and CAN FD, bit timing computed from the rates, error passive / bus-off / unplugged reported in the
  Log. *Help → Download PEAK driver* links to the driver.
- **VIRTUAL CAN**: the Android head unit bridge port has a readable name (`bridge://<IP>:29536` still works for
  Ethernet).
- **Setup desktop** holds Channels (40 %) and the Database explorer (60 %) side by side; each database has one
  Channel choice: All channels / a channel / Not used.
- `tools/benchmark.py` and `docs/BENCHMARK.md`: measured limits (about 30 000 frames/s on time with generators,
  1 ms cycles, 200 000-frame trace, 0.12 ms auto response latency).

## Changed

- **Signal plot removed**: decoded values are in the Bus trace (expand a message ▸). The desktop app is much smaller:
  about 89 MB installed instead of over 250 MB.
- The Database desktop is merged into Setup; desktops are now `Ctrl+1` Setup, `Ctrl+2` Simulation, `Ctrl+3`
  Analysis. The command palette stays on `Ctrl+K` (no rail button).
- The window layout is reset once after updating (new layout version).

## Fixed

- An occasional crash when detaching a window and docking it back.
- Clipped text in the empty "No database" card.
- Checked but disabled check boxes (ISO on CANable / PCAN) showed as empty.
- Flaky slcan tests on loaded machines.

## Checked

362 automated tests (engine, adapters with simulated hardware, GUI, web API, browser end to end) on Linux and the
engine / web tests on Windows. PCAN support was tested against a model of the PCAN-Basic library; please report how
it behaves with real PEAK hardware.
