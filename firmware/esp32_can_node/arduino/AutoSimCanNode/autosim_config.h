// AutoSim CAN FD node - your settings for the Arduino IDE build.
// Uncomment and change what differs from the defaults. Defaults per chip are in src/board_config.h.
// This file is created once by tools/make_arduino_sketch.py and never overwritten.
#pragma once

// ---- MCP2518FD wiring (defaults: classic ESP32 SCK 18, MOSI 23, MISO 19, CS 5, INT 4;
//      ESP32-S3 12 / 11 / 13 / 10 / 4; ESP32-C3 4 / 6 / 5 / 7 / 3) -----------------------------------
// #define SCK_PIN  18
// #define MOSI_PIN 23
// #define MISO_PIN 19
// #define CS_PIN   5
// #define INT_PIN  4

// ---- MCP2518FD crystal in MHz: 40 (most modules) or 20 ----------------------------------------------
// #define OSC_FREQ 40

// ---- RX LED: -1 = off; LED_RGB 1 for an addressable (WS2812) LED -----------------------------------
// #define LED_PIN 2
// #define LED_RGB 0

// ---- UART speed when the board is used through its USB-UART bridge ("USB CDC On Boot: Disabled").
//      Must match the app (environment variable AUTOSIM_BAUD, default 921600) ----------------------------
// #define AUTOSIM_UART_BAUD 921600

// ---- Default node name (otherwise ESP32-<last 3 MAC bytes>; the app can rename it) ------------------
// #define AUTOSIM_NODE_NAME "BENCH-1"
