// ICanController for the MCP2518FD, built on the ACAN2517FD driver (pierremolinaro/ACAN2517FD).
//
// ACAN2517FD on ESP32 creates its own FreeRTOS task ("ACAN2517Handler") that is woken by the INT pin
// (falling edge) and moves received frames from the MCP2518FD RAM (SPI) into a software FIFO. The CAN
// task of this firmware drains that FIFO with receive() and pushes frames into the transmit side with
// tryToSend().
#pragma once
#include "autosim_node.h"

class McpCan : public autosim::ICanController {
 public:
  // Wiring, once before anything else. A non-empty `problem` makes every start() fail with that text, so a wrong
  // configuration reaches the app's log when it connects (the serial port cannot carry debug text).
  void setup(int sck, int mosi, int miso, int cs, int intPin, uint8_t oscMHz /* 0 = auto */, const char* problem, bool xstby = true);
  void configure(const autosim::CanConfig& cfg, uint8_t dataFactor) override;
  bool start(char* err, size_t cap) override;     // SPI.begin + ACAN2517FD::begin (requested mode)
  void stop() override;                           // ACAN2517FD::end (controller reset, INT detached)

  bool send(const autosim::CanFrame& f);          // false: driver TX FIFO full (retry)
  bool receive(autosim::CanFrame& out);           // false: nothing pending
  void readStatus(uint8_t& tec, uint8_t& rec, uint8_t& busFlags);   // from the TREC register
  uint32_t rxOverflows() const;
  uint32_t busDiag();                             // BDIAG1 register (latched bus error flags), 0 when stopped
  uint8_t oscMHz() const { return osc_; }         // crystal in use: 20 / 40, 0 until auto detection ran
  uint32_t measuredHz() const { return measuredHz_; }   // last auto detection result (0 = not measured)

 private:
  autosim::CanConfig cfg_;
  uint8_t factor_ = 4;
  bool begun_ = false;
  int sck_ = -1, mosi_ = -1, miso_ = -1;
  bool detectOscillator(char* err, size_t cap);
  uint8_t osc_ = 0;                               // 0: auto detect on the first start()
  uint32_t measuredHz_ = 0;
  bool xstby_ = true;
  const char* problem_ = "";
};
