// On-board activity LED (rx_blink.h has the timing). RGB LEDs show what happened:
//   blue  = a CAN frame was received from the bus
//   green = a frame from the app was handed to the MCP2518FD for transmission
//   red   = a frame from the app could not be transmitted (controller TX queue full: bus-off, no ACK ...)
// A plain LED blinks for all three.
//   classic ESP32 DevKit : blue LED on GPIO2 (plain, active high)
//   ESP32-S3-DevKitC-1   : addressable RGB LED (WS2812) on GPIO48 (some v1.1 boards: 38)
//   ESP32-C3-DevKitM-1   : addressable RGB LED on GPIO8
#pragma once
#include <Arduino.h>

#include "rx_blink.h"

namespace autosim {

class StatusLed {
 public:
  void begin(int pin, bool rgb, bool activeLow, uint8_t level, uint16_t onMs, uint16_t offMs) {
    pin_ = pin;
    rgb_ = rgb;
    activeLow_ = activeLow;
    level_ = level;
    blink_ = RxBlink(onMs, offMs);
    if (pin_ < 0) return;
    if (!rgb_) pinMode(pin_, OUTPUT);
    set(false);
  }

  enum Color : uint8_t { Blue, Green, Red };

  // CAN task events
  void onRx(uint32_t now) { event(now, Blue); }
  void onTx(uint32_t now) { event(now, Green); }
  void onTxFailed(uint32_t now) { event(now, Red); }

  // CAN task: once per loop.
  void tick(uint32_t now) {
    const int change = blink_.tick(now);
    if (change > 0) color_ = next_;                    // a remembered event starts its blink now
    if (change) set(change > 0);
  }

 private:
  void set(bool on) {
    if (pin_ < 0) return;
    if (rgb_) {
      const uint8_t l = on ? level_ : 0;
      const uint8_t r = color_ == Red ? l : 0, g = color_ == Green ? l : 0, b = color_ == Blue ? l : 0;
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
      rgbLedWrite(pin_, r, g, b);
#else
      neopixelWrite(pin_, r, g, b);                      // Arduino core 2.x
#endif
    } else {
      digitalWrite(pin_, (on != activeLow_) ? HIGH : LOW);
    }
  }
  void event(uint32_t now, Color c) {
    next_ = c;                                          // a busy LED shows the latest event on its next blink
    if (blink_.pulse(now)) {
      color_ = c;
      set(true);
    }
  }

  int pin_ = -1;
  Color color_ = Blue, next_ = Blue;
  bool rgb_ = false, activeLow_ = false;
  uint8_t level_ = 40;
  RxBlink blink_;
};

}  // namespace autosim
