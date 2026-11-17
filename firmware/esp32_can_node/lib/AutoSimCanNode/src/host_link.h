// Byte link to the PC: the board's Serial port - USB-UART bridge (UART0) or the chip's native USB-CDC
// (see autosim_limits.h for how it is chosen). Nothing else may print to it: debug text would corrupt the protocol.
#pragma once
#include <Arduino.h>

#include "autosim_limits.h"

class HostLink {
 public:
  void begin(uint32_t uartBaud) {
#if defined(AUTOSIM_LINK_USB_CDC)
    (void)uartBaud;
    Serial.setRxBufferSize(4096);
    Serial.begin();                         // native USB: speed is whatever USB negotiates
    Serial.setTxTimeoutMs(5);               // never stall the task when the PC is not reading
#else
    Serial.setRxBufferSize(4096);
    Serial.setTxBufferSize(4096);
    Serial.begin(uartBaud);
#endif
  }
  int available() { return Serial.available(); }
  size_t read(uint8_t* buf, size_t n) { return Serial.readBytes(buf, n); }   // only called when data is waiting
  size_t write(const uint8_t* buf, size_t n) { return Serial.write(buf, n); }
};
