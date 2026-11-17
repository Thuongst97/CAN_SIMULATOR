// On-board LED that blinks when CAN frames are received (rx_blink.h has the timing).
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

  // CAN task: a frame was received.
  void onRx(uint32_t now) {
    if (blink_.pulse(now)) set(true);
  }

  // CAN task: once per loop.
  void tick(uint32_t now) {
    const int change = blink_.tick(now);
    if (change) set(change > 0);
  }

 private:
  void set(bool on) {
    if (pin_ < 0) return;
    if (rgb_) {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
      rgbLedWrite(pin_, 0, 0, on ? level_ : 0);          // blue, like the CANable's RX LED
#else
      neopixelWrite(pin_, 0, 0, on ? level_ : 0);        // Arduino core 2.x
#endif
    } else {
      digitalWrite(pin_, (on != activeLow_) ? HIGH : LOW);
    }
  }
  int pin_ = -1;
  bool rgb_ = false, activeLow_ = false;
  uint8_t level_ = 40;
  RxBlink blink_;
};

}  // namespace autosim
