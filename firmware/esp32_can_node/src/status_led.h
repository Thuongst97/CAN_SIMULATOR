// On-board LED that blinks when CAN frames are received (see lib/autosim_proto/rx_blink.h for the timing).
//
//   classic ESP32 DevKit   : blue LED on GPIO2 (plain, active high)
//   ESP32-S3-DevKitC-1     : addressable RGB LED (WS2812) on GPIO48  (some v1.1 boards: -DLED_PIN=38)
//   ESP32-C3-DevKitM-1     : addressable RGB LED on GPIO8
// Override with -DLED_PIN=<gpio> / -DLED_RGB=0|1 / -DLED_ACTIVE_LOW=1, or disable with -DLED_PIN=-1.
#pragma once
#include <Arduino.h>

#include "board_config.h"
#include "rx_blink.h"

namespace autosim {

class StatusLed {
 public:
  void begin() {
#if LED_PIN >= 0
  #if !LED_RGB
    pinMode(LED_PIN, OUTPUT);
  #endif
    set(false);
#endif
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
#if LED_PIN >= 0
  #if LED_RGB
    neopixelWrite(LED_PIN, 0, 0, on ? LED_RGB_LEVEL : 0);       // blue, like the CANable's RX LED
  #else
    digitalWrite(LED_PIN, (on != bool(LED_ACTIVE_LOW)) ? HIGH : LOW);
  #endif
#else
    (void)on;
#endif
  }
  RxBlink blink_{LED_ON_MS, LED_OFF_MS};
};

}  // namespace autosim
