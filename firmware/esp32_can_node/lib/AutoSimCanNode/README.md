# AutoSimCanNode: Arduino library (ESP32-S3 + MCP2518FD)

This library turns an ESP32-S3 (or ESP32 classic, ESP32-C3) with an MCP2518FD module into **one CAN / CAN FD channel
of AutoSim Studio**. Install the library once; the main sketch is only a few lines, and the pins are set right in
`setup()`.

```cpp
#include <AutoSimCanNode.h>

void setup() {
  autosim::BoardConfig cfg = autosim::defaultBoardConfig();   // defaults for the chip being built
  // cfg.cs = 9;  cfg.ledPin = 38;                             // change only what differs on your board
  autosim::startCanNode(cfg);
}

void loop() { vTaskDelay(portMAX_DELAY); }                    // the node runs in its own FreeRTOS task
```

## 1. Installation (once)

1. **Board package**: *File → Preferences → Additional boards manager URLs*, add
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`. Then *Tools → Board → Boards Manager*,
   install **esp32 by Espressif Systems** version **3.x**.
2. **AutoSimCanNode library**: *Sketch → Include Library → **Add .ZIP Library…***, choose the file
   `AutoSimCanNode-<version>.zip`.
3. **Nothing else to install.** The MCP2518FD driver (ACAN2517FD 2.1.16 by Pierre Molinaro, MIT licence, see
   `LICENSE-ACAN2517FD.txt`) is bundled in the library. A separately installed ACAN2517FD does no harm: the sketch
   always uses the bundled one.

The ZIP file is in the repository: `firmware/esp32_can_node/release/AutoSimCanNode-<version>.zip`. On GitHub, open
that file and click **Download raw file**. For a new version, just *Add .ZIP Library* again with the new file.
Developers rebuild the ZIP with `python firmware/esp32_can_node/tools/make_arduino_zip.py`.

## 2. Open the example and flash it

1. *File → Examples → AutoSimCanNode → **AutoSimCanNode***, then *File → Save As* to keep your own copy of the sketch.
2. *Tools*:

| Menu | S3 **"UART"** port (recommended) | Native **"USB"** port |
|---|---|---|
| Board | ESP32S3 Dev Module | ESP32S3 Dev Module |
| USB CDC On Boot | **Disabled** | **Enabled** |
| USB Mode | default | Hardware CDC and JTAG |
| Port to choose in AutoSim | the USB-UART chip's COM port, 921600 baud | COM port `303A:1001` |

3. **Upload**. If the board does not enter flashing mode: hold BOOT, press RESET, release BOOT and upload again.
   After flashing, press RESET.
4. **Close the Serial Monitor** before Connect in AutoSim. Do not use `Serial.print` in the sketch: Serial carries
   the binary protocol to AutoSim.

## 3. Wiring ESP32-S3 ↔ MCP2518FD (defaults)

| MCP2518FD | ESP32-S3 | `BoardConfig` |
|---|---|---|
| SCK | GPIO 12 | `cfg.sck` |
| SI (MOSI) | GPIO 11 | `cfg.mosi` |
| SO (MISO) | GPIO 13 | `cfg.miso` |
| CS | GPIO 10 | `cfg.cs` |
| INT | GPIO 4 | `cfg.intPin` |
| VCC | **3V3** (the ESP32 does not tolerate 5 V) | |
| GND | GND | |

Other settings:
- `cfg.oscMHz`: the module's crystal. Default `0` = **measured** at every Connect (since 1.4), so 20 MHz modules
  (crystal marked `H20.00`) and 40 MHz modules both run at the right bit rate. Set `20` / `40` only to fix it. With
  20 MHz use FD up to 500k/2M; 5M is unreliable and 8M is not possible.
- `cfg.ledPin`: activity LED. RGB LED colours: **blue** = frame received from the bus, **green** = frame sent on
  AutoSim's request, **red** = could not send (bus-off, no ACK…). RGB on GPIO 48; the DevKitC-1 v1.1 board uses 38;
  `-1` turns it off.
- `cfg.uartBaud`: only for the UART port; must equal the app's `AUTOSIM_BAUD` environment variable (default 921600).
- `cfg.xstby` (default `true`): the MCP2518FD uses its INT0 / GPIO0 pin to pull the transceiver's STBY low, so the
  transceiver is active. Many modules wire STBY to that pin; without it the transceiver stays in standby and reports
  `nominal BIT0` / `no ACK`. Turn it off only if you use INT0 for something else.
- `cfg.nodeName`: the node name shown in AutoSim; empty means `ESP32-<MAC>`.

Full wiring guide (transceiver, 120 Ω, common ground, TDC, meaning of the bus errors):
`docs/ESP32_MCP2518FD_WIRING.md` in the repository.

With a wrong configuration (two signals on one pin, a pin that does not exist, a crystal other than 20 / 40 MHz) the
board still runs and the error shows in **AutoSim's Log** at Connect, e.g. `MCP2518FD fault - configuration: CS and
INT both use GPIO 4`, because the serial port cannot be used for debug output. An LED set to an SPI pin is turned off
automatically.

## 4. Connecting to AutoSim

1. *Setup → Channels → Scan*, choose the COM port from the table in section 2. The first time, tick **Loop** (a test
   without a bus).
2. Choose the bit rates. The data rate must be an integer multiple (1–10) of the nominal rate, e.g. 500k/2M or
   500k/5M.
3. **Connect**. The channel table shows `ESP32-xxxxxx · running`.

On `HELLO: no answer`: wrong port (check the USB CDC On Boot menu), RESET not pressed after flashing, or the Serial
Monitor still holds the port.

## 5. For developers

This folder is the **only source** of the firmware; PlatformIO (`firmware/esp32_can_node`, `pio run`) and the
Arduino IDE share it. The protocol and node logic (`autosim_protocol.*`, `autosim_node.*`, `rx_blink.h`) do not
depend on the hardware and have unit tests on the PC (`pio test -e native`). The ESP32-specific parts
(`AutoSimCanNode.cpp`, `can_mcp2518fd.cpp`) are only built for ESP32 targets.
