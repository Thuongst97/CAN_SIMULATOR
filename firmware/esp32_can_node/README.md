# AutoSim CAN FD node firmware (ESP32 + MCP2518FD)

Turns an ESP32 + MCP2518FD board into a CAN / CAN FD interface for AutoSim Studio. One board = one CAN
channel; connect as many boards as you have USB ports (one COM port each).

```
PC ──USB-UART / USB-CDC── ESP32 ──SPI + INT── MCP2518FD ── CAN transceiver ── bus
```

## Wiring (defaults, override with `-D` flags)

| Signal | classic ESP32 | ESP32-S3 | ESP32-C3 | MCP2518FD pin |
|---|---|---|---|---|
| `MOSI_PIN` | GPIO 23 | 11 | 6 | SDI |
| `MISO_PIN` | GPIO 19 | 13 | 5 | SDO |
| `SCK_PIN`  | GPIO 18 | 12 | 4 | SCK |
| `CS_PIN`   | GPIO 5  | 10 | 7 | nCS |
| `INT_PIN`  | GPIO 4  | 4  | 3 | nINT (active low) |

3V3 and GND shared; the module needs a CAN transceiver (usually on the MCP2518FD board) and a 120 Ω
termination at each end of the bus. `OSC_FREQ` is the MCP2518FD crystal in MHz (`40` default, `20` also
supported): `build_flags = -DOSC_FREQ=20`. The driver clocks SPI at SYSCLK × 2/5 (16 MHz with 40 MHz).

## Build and flash

Needs [PlatformIO](https://platformio.org) 6.1+ (`uvx --from platformio pio ...` works without installing).

**Pick the environment that matches your board AND the USB port you will use for the app.** The firmware talks
on only one link, so a build for the wrong port flashes fine but never answers (`HELLO: no answer`).

| Board / port | environment | app port |
|---|---|---|
| classic ESP32 dev board (USB-UART bridge) | `esp32dev` | the bridge's COM port, 921600 baud |
| ESP32-S3, **UART** port (CH343 / CP210x, VID:PID like `1A86:55D3`) | `esp32s3_uart` | that COM port, 921600 baud |
| ESP32-S3, native **USB** port (VID:PID `303A:1001`) | `esp32s3` | the native-USB COM port |
| ESP32-C3 (native USB) | `esp32c3` | the native-USB COM port |

```bash
cd firmware/esp32_can_node
pio run -e esp32s3_uart -t upload --upload-port COM4      # example: S3 board on its UART port
pio run -e esp32dev -t upload                              # classic ESP32 dev board
pio test -e native                # 22 host unit tests of the protocol + node logic (no hardware)
```

Flashing replaces whatever program the board had (e.g. the factory LED demo). If the upload cannot start, hold
the **BOOT** button, tap **RESET**, release BOOT, and retry. Afterwards press RESET once.

### Is the firmware really on the board?

```bash
uv run python firmware/esp32_can_node/tools/probe_port.py COM4
```

prints, per baud rate, whether the AutoSim firmware answered (`AutoSim firmware found: {...}`), whether some
other firmware is running (e.g. the ESP32 boot log), or only line noise. A factory-fresh board shows its demo's
boot log at 115200 baud.

All three targets build cleanly (esp32dev: 23 % flash / 7 % static RAM). **The firmware has not yet been run on
real hardware** - see "Verification" below.

## How it works

| | core | job |
|---|---|---|
| **link task** | 0 | reads the serial stream, parses packets, answers HELLO / PING / SET_NAME, validates TX frames and queues them, writes RX frames + ACK / NACK / STATUS to the PC |
| **CAN task** | 1 | starts / stops / configures the MCP2518FD, sends queued frames (`tryToSend`), drains received frames (`receive`), emits STATUS once per second |
| **ACAN2517Handler** | any | created by the ACAN2517FD library: woken by the INT pin (falling edge) ISR, moves frames from the MCP2518FD RAM (SPI) into the driver's 256-frame software FIFO |

FreeRTOS queues: `txQueue` (128 frames, link → CAN), `rxQueue` (512 frames, CAN → link), `ctlQueue`
(CONFIG / START / STOP, link → CAN, so the controller is only ever touched from the CAN task) and `outQueue`
(small replies from either task). Frames that cannot be sent are retried for 50 ms, then dropped and counted;
frames that do not fit `rxQueue` (serial link slower than the bus) are dropped and counted in STATUS.

Source layout: `lib/autosim_proto` is hardware independent (protocol, COBS/CRC, node state machine) and is
what the unit tests and `tools/fw_sim.cpp` exercise; `src/` binds it to FreeRTOS, the MCP2518FD
(`can_mcp2518fd.cpp`), the serial port (`host_link.h`) and NVS (`name_store.h`).

## Protocol (see `docs/ESP32_PROTOCOL.md`)

The firmware speaks the app's v1 protocol (COBS + CRC16), not a bare `0xAA ... 0x55` frame: COBS gives
unambiguous resynchronisation and CRC16 catches corruption. The commands map one to one:

| Your name | Message | Notes |
|---|---|---|
| CONNECT | `START` (0x03) | initialise the MCP2518FD and enter the requested mode |
| DISCONNECT | `STOP` (0x04) | `end()`: controller reset, INT detached |
| CONFIG_BITRATE | `CONFIG` (0x02) | nominal + data rate, FD / BRS / non-ISO / listen-only / loopback flags |
| CAN_TX | `TX_FRAME` (0x10), `TX_BATCH` (0x11) | flags: bit0 extended, bit1 FD, bit2 BRS, bit3 ESI, **bit4 RTR** |
| CAN_RX | `RX_FRAME` (0x90) | with a node microsecond timestamp |

Extras: `HELLO` / `HELLO_ACK` (node id, firmware version), `PING` / `PONG` heartbeat, `STATUS` (TEC/REC, bus
state, counters) each second, `ERROR` text, `SET_NAME`. If the host sends nothing for 5 s while running, the
node stops the CAN controller (watchdog) and reports it.

Rules enforced by the node (with the reason sent back as an `ERROR` message before the NACK):
* data rate must be an integer multiple of the nominal rate, ratio 1-10 (MCP2518FD): 500k/5M and 1M/8M are
  valid, 500k/8M is not
* classic CAN frames carry at most 8 bytes; FD frames are zero-padded to the next valid length (12, 16, 20,
  24, 32, 48, 64); remote frames are classic only; ids must fit 11 / 29 bits
* TX before `START` -> `NOT_STARTED`; queue full -> `BUSY`; FD frame while configured for classic CAN -> `UNSUPPORTED`
* `CONFIG` while running -> `BUSY` (send `STOP` first)

Bandwidth: a 64-byte FD frame is about 90 bytes on the link, so 921600 baud UART carries roughly 1000 FD or
3000 classic frames per second; ESP32-S3 / C3 native USB removes that limit. Faster USB-UART bridges
(CP2102N, CH340, FT232) can run higher: change `AUTOSIM_UART_BAUD` in the firmware build flags *and* set the
`AUTOSIM_BAUD` environment variable for the app.

## Several ESP32 nodes (several CAN channels)

1. Flash every board with the same firmware. Each reports a unique default name `ESP32-<last 3 MAC bytes>`.
2. Plug them all in. In the app (CAN tab) pick a port, give it a channel name (`CAN1`, `CAN2` ...), set the
   bit rates and press **Connect**; repeat for the next board. Every channel has its own bit rates, FD / BRS /
   ISO mode and appears separately in the trace (`Channel` column) and as the TX channel of each message.
3. Give boards recognisable names: double-click the **Node** cell (or right-click -> *Rename node...*). The name
   (1-15 chars of `A-Z a-z 0-9 _ . -`) is stored in the board's flash, so it survives power cycles and COM port
   renumbering. Two boards with the same name are highlighted and logged.
4. **Settings -> Save project** stores every connected channel (port, name, bit rates, mode); opening the
   project reconnects them (a missing board is logged, the others still connect).

Bench test without a bus: tick **Loop** in the CAN card before connecting (MCP2518FD internal loopback: the board
receives its own frames), or wire two boards to each other with 120 Ω termination.

## Verification

* `pio test -e native`: 22 unit tests (CRC check value, COBS edge sizes, stream resync, payload layouts,
  config rules, node state machine, watchdog).
* `tests/test_firmware.py` in the app repo builds `tools/fw_sim.cpp` (the real protocol + node logic behind a
  fake loopback controller) and drives it with the app's actual `SerialNode` over pipes: handshake, naming,
  config errors, FD padding, NACK reasons, batches, garbage, three simultaneous nodes. This proves the wire
  format matches between the Python host and the C++ firmware.
* All three PlatformIO targets compile without warnings.
* **Not verified**: anything that needs the real MCP2518FD / CAN bus (bit timing on the wire, ISR behaviour,
  throughput at 5-8 Mbit/s, error counters). Please bench-test with two nodes first.

## Troubleshooting `Connect failed ... HELLO: no answer`

| What the probe / symptom shows | Cause and fix |
|---|---|
| boot log / another firmware's text at 115200 | AutoSim firmware not flashed yet: flash it (see above) |
| only line noise at 921600 (`0x00` / `0x80` bytes) | the board sends at a different baud rate: not our firmware, or a build with a different `AUTOSIM_UART_BAUD` (set the same value in the app with the `AUTOSIM_BAUD` environment variable) |
| silence at every baud | wrong port (S3/C3 boards often have two USB ports: UART bridge vs native USB), a build for the other port, charge-only cable, or the board is in download mode (press RESET) |
| works once, then not after re-plugging | the COM number changed: press **Scan** again |

## Known limits

* `TX_CONFIRM` (transmit echo) is not implemented; sample-point fields in `CONFIG` are accepted but the driver
  chooses its own bit timing.
* Receive timestamps in the app use host receive time; the node's microsecond counter is sent but unused.
* The MCP2518FD handler task of the ACAN2517FD library is not pinned to a core.
* No acceptance filters (all frames received), no bus-off auto recovery (STATUS shows bus-off; send STOP/START).
