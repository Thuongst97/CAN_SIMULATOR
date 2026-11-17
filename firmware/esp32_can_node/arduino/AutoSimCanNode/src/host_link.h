// Byte link to the PC: UART0 (USB-UART bridge) on classic ESP32 boards, native USB-CDC on S3 / C3.
// Nothing else may print to this port: debug text would corrupt the binary protocol.
#pragma once
#include <Arduino.h>

#include "board_config.h"

class HostLink {
 public:
  void begin() {
#if defined(AUTOSIM_LINK_USB_CDC)
    Serial.setRxBufferSize(4096);
    Serial.begin();                         // native USB: speed is whatever USB negotiates
    Serial.setTxTimeoutMs(5);               // never stall the task when the PC is not reading
#else
    Serial.setRxBufferSize(4096);
    Serial.setTxBufferSize(4096);
    Serial.begin(AUTOSIM_UART_BAUD);
#endif
  }
  int available() { return Serial.available(); }
  size_t read(uint8_t* buf, size_t n) { return Serial.readBytes(buf, n); }   // only called when data is waiting
  size_t write(const uint8_t* buf, size_t n) { return Serial.write(buf, n); }
};
