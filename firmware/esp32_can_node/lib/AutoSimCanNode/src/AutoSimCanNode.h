// AutoSim CAN FD node - Arduino library for ESP32 (classic, S3, C3) + MCP2518FD.
//
// Turns the board into one CAN / CAN FD channel of AutoSim Studio (protocol: docs/ESP32_PROTOCOL.md).
// Minimal sketch:
//
//     #include <AutoSimCanNode.h>
//     void setup() {
//       autosim::BoardConfig cfg = autosim::defaultBoardConfig();   // pins for the chip being built for
//       // cfg.cs = 9;  cfg.oscMHz = 20;  cfg.ledPin = 38;  ...      // change what differs on your board
//       autosim::startCanNode(cfg);
//     }
//     void loop() { vTaskDelay(portMAX_DELAY); }                    // the node runs in its own FreeRTOS tasks
//
// The host link is the board's Serial port: the USB-UART bridge ("USB CDC On Boot: Disabled", uartBaud) or the
// chip's native USB ("USB CDC On Boot: Enabled"). Nothing else may print to Serial: it carries a binary protocol.
#pragma once
#include <Arduino.h>

#define AUTOSIM_FW_MAJOR 1
#define AUTOSIM_FW_MINOR 1

namespace autosim {

struct BoardConfig {
  // MCP2518FD wiring (GPIO numbers)
  int sck = -1;
  int mosi = -1;
  int miso = -1;
  int cs = -1;
  int intPin = -1;              // MCP2518FD nINT, falling edge
  uint8_t oscMHz = 40;          // MCP2518FD crystal: 40 or 20
  // RX activity LED: blinks when CAN frames are received. ledPin -1 = no LED.
  int ledPin = -1;
  bool ledRgb = false;          // addressable WS2812 LED (S3 / C3 DevKits) instead of a plain GPIO LED
  bool ledActiveLow = false;    // plain LED wired to 3V3
  uint8_t ledLevel = 40;        // RGB brightness 0..255
  uint16_t ledOnMs = 20;        // each blink lit this long ...
  uint16_t ledOffMs = 40;       // ... then dark at least this long
  // Host link speed when the board talks through its USB-UART bridge; must match the app (AUTOSIM_BAUD).
  uint32_t uartBaud = 921600;
  // Name reported to the app; nullptr = the name stored by the app (rename) or "ESP32-<last 3 MAC bytes>".
  const char* nodeName = nullptr;
};

// Defaults for the chip the sketch is compiled for:
//   classic ESP32 DevKit : SCK 18, MOSI 23, MISO 19, CS 5,  INT 4, blue LED GPIO 2
//   ESP32-S3-DevKitC-1   : SCK 12, MOSI 11, MISO 13, CS 10, INT 4, RGB LED GPIO 48 (v1.1 boards: 38)
//   ESP32-C3-DevKitM-1   : SCK 4,  MOSI 6,  MISO 5,  CS 7,  INT 3, RGB LED GPIO 8
BoardConfig defaultBoardConfig();

// Opens the host link and starts the node's tasks; the CAN controller itself starts when the app connects.
// Returns false only if it was already started or the tasks could not be created. A wiring / configuration
// problem (pin conflict, unsupported crystal, MCP2518FD not answering) is reported to the app when it connects,
// in its log, because the serial port cannot be used for debug text.
bool startCanNode(const BoardConfig& cfg);

// Text of the configuration problem found by startCanNode(), or "" (also sent to the app on connect).
const char* canNodeConfigProblem();

}  // namespace autosim
