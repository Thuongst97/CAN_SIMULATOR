// AutoSim CAN FD node: ESP32-S3 (or ESP32 / ESP32-C3) + MCP2518FD as a CAN / CAN FD channel of AutoSim Studio.
//
// Board: "ESP32S3 Dev Module". Pick the USB port the app will use with Tools > "USB CDC On Boot":
//   Disabled = the "UART" port (USB-UART bridge, 921600 baud)   Enabled = the native "USB" port
// Nothing else to install: the MCP2518FD driver (ACAN2517FD, MIT) is bundled. Wiring: README.md of this library.
// Do not print anything to Serial: it carries the binary protocol to the app.
#include <AutoSimCanNode.h>

void setup() {
  autosim::BoardConfig cfg = autosim::defaultBoardConfig();

  // ESP32-S3 defaults - change only what differs on your board:
  // cfg.sck = 12;            // MCP2518FD SCK
  // cfg.mosi = 11;           // MCP2518FD SI
  // cfg.miso = 13;           // MCP2518FD SO
  // cfg.cs = 10;             // MCP2518FD CS
  // cfg.intPin = 4;          // MCP2518FD INT
  // cfg.oscMHz = 40;         // MCP2518FD crystal: 40 or 20
  // cfg.ledPin = 48;         // RGB LED (DevKitC-1 v1.1: 38, none: -1)
  // cfg.uartBaud = 921600;   // only for the "UART" port; same value as the app (AUTOSIM_BAUD)
  // cfg.nodeName = "BENCH-1";  // otherwise ESP32-<MAC>; the app can rename the node

  autosim::startCanNode(cfg);
}

void loop() {
  vTaskDelay(portMAX_DELAY);  // the node runs in its own FreeRTOS tasks
}
