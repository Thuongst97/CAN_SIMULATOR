// Board / build configuration. Every value can be overridden with a -D build flag in platformio.ini.
#pragma once
#include <Arduino.h>

// ---- firmware identity ------------------------------------------------------------------------
#define AUTOSIM_FW_MAJOR 1
#define AUTOSIM_FW_MINOR 0

// ---- MCP2518FD wiring --------------------------------------------------------------------------
//   classic ESP32 (VSPI):  MOSI 23  MISO 19  SCK 18  CS 5   INT 4
//   ESP32-S3 (FSPI):       MOSI 11  MISO 13  SCK 12  CS 10  INT 4
//   ESP32-C3 (FSPI):       MOSI 6   MISO 5   SCK 4   CS 7   INT 3
#if defined(CONFIG_IDF_TARGET_ESP32S3)
  #define AUTOSIM_DEF_MOSI 11
  #define AUTOSIM_DEF_MISO 13
  #define AUTOSIM_DEF_SCK  12
  #define AUTOSIM_DEF_CS   10
  #define AUTOSIM_DEF_INT  4
#elif defined(CONFIG_IDF_TARGET_ESP32C3)
  #define AUTOSIM_DEF_MOSI 6
  #define AUTOSIM_DEF_MISO 5
  #define AUTOSIM_DEF_SCK  4
  #define AUTOSIM_DEF_CS   7
  #define AUTOSIM_DEF_INT  3
#else
  #define AUTOSIM_DEF_MOSI 23
  #define AUTOSIM_DEF_MISO 19
  #define AUTOSIM_DEF_SCK  18
  #define AUTOSIM_DEF_CS   5
  #define AUTOSIM_DEF_INT  4
#endif

#ifndef MOSI_PIN
#define MOSI_PIN AUTOSIM_DEF_MOSI
#endif
#ifndef MISO_PIN
#define MISO_PIN AUTOSIM_DEF_MISO
#endif
#ifndef SCK_PIN
#define SCK_PIN AUTOSIM_DEF_SCK
#endif
#ifndef CS_PIN
#define CS_PIN AUTOSIM_DEF_CS
#endif
#ifndef INT_PIN                       // MCP2518FD INT output, falling edge = frame received / TX done
#define INT_PIN AUTOSIM_DEF_INT
#endif

// MCP2518FD crystal / oscillator frequency in MHz: 40 (default module) or 20.
// The driver clocks SPI at SYSCLK*2/5 (16 MHz with a 40 MHz crystal, 8 MHz with 20 MHz).
#ifndef OSC_FREQ
#define OSC_FREQ 40
#endif
static_assert(OSC_FREQ == 40 || OSC_FREQ == 20, "OSC_FREQ must be 40 or 20 (MHz)");

// ---- RX activity LED (blinks when CAN frames are received) -------------------------------------
#if defined(CONFIG_IDF_TARGET_ESP32S3)
  #define AUTOSIM_DEF_LED_PIN 48              // ESP32-S3-DevKitC-1 RGB LED (v1.1 boards: 38)
  #define AUTOSIM_DEF_LED_RGB 1
#elif defined(CONFIG_IDF_TARGET_ESP32C3)
  #define AUTOSIM_DEF_LED_PIN 8               // ESP32-C3-DevKitM-1 RGB LED
  #define AUTOSIM_DEF_LED_RGB 1
#else
  #define AUTOSIM_DEF_LED_PIN 2               // classic ESP32 DevKit blue LED
  #define AUTOSIM_DEF_LED_RGB 0
#endif
#ifndef LED_PIN                       // -1 disables the LED
#define LED_PIN AUTOSIM_DEF_LED_PIN
#endif
#ifndef LED_RGB                       // 1 = addressable WS2812 LED, 0 = plain GPIO LED
#define LED_RGB AUTOSIM_DEF_LED_RGB
#endif
#ifndef LED_ACTIVE_LOW                // plain LEDs wired to 3V3 light on LOW
#define LED_ACTIVE_LOW 0
#endif
#ifndef LED_RGB_LEVEL                 // RGB brightness 0..255
#define LED_RGB_LEVEL 40
#endif
#ifndef LED_ON_MS                     // each blink: lit this long ...
#define LED_ON_MS 20
#endif
#ifndef LED_OFF_MS                    // ... then dark at least this long (continuous traffic = steady blinking)
#define LED_OFF_MS 40
#endif
static_assert(!(LED_PIN == MOSI_PIN || LED_PIN == MISO_PIN || LED_PIN == SCK_PIN || LED_PIN == CS_PIN ||
                LED_PIN == INT_PIN), "LED_PIN collides with an MCP2518FD pin");

// ---- host link ---------------------------------------------------------------------------------
// Classic ESP32 boards talk through the USB-UART bridge on UART0 at AUTOSIM_UART_BAUD; the app
// default is 921600 (override both sides together: firmware -DAUTOSIM_UART_BAUD, app env AUTOSIM_BAUD).
// ESP32-S3 / C3 use the chip's native USB-CDC; the baud rate is irrelevant there.
#ifndef AUTOSIM_UART_BAUD
#define AUTOSIM_UART_BAUD 921600
#endif

// ---- node identity -------------------------------------------------------------------------------
// The name a node reports in HELLO_ACK. Default "ESP32-<last 3 MAC bytes>" (unique per board); the app can
// rename a node (stored in NVS). Define AUTOSIM_NODE_NAME to bake in a different default.
// #define AUTOSIM_NODE_NAME "BENCH-1"

// ---- FreeRTOS sizing -------------------------------------------------------------------------------
#define AUTOSIM_TX_QUEUE_LEN     128   // frames host -> CAN
#define AUTOSIM_RX_QUEUE_LEN     512   // frames CAN -> host (absorbs bursts while the serial link is busy)
#define AUTOSIM_CTL_QUEUE_LEN    8
#define AUTOSIM_OUT_QUEUE_LEN    32    // ACK / NACK / STATUS / ERROR / PONG ...
#define AUTOSIM_DRIVER_RX_FIFO   256   // ACAN2517FD software receive FIFO
#define AUTOSIM_DRIVER_TX_FIFO   64    // ACAN2517FD software transmit FIFO
#define AUTOSIM_LINK_STACK       6144
#define AUTOSIM_CAN_STACK        6144
#define AUTOSIM_LINK_PRIO        3
#define AUTOSIM_CAN_PRIO         5
#define AUTOSIM_WATCHDOG_MS      5000  // host silent this long while running -> CAN stopped
#define AUTOSIM_STATUS_PERIOD_MS 1000
