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
  void configure(const autosim::CanConfig& cfg, uint8_t dataFactor) override;
  bool start(char* err, size_t cap) override;     // SPI.begin + ACAN2517FD::begin (requested mode)
  void stop() override;                           // ACAN2517FD::end (controller reset, INT detached)

  bool send(const autosim::CanFrame& f);          // false: driver TX FIFO full (retry)
  bool receive(autosim::CanFrame& out);           // false: nothing pending
  void readStatus(uint8_t& tec, uint8_t& rec, uint8_t& busFlags);   // from the TREC register
  uint32_t rxOverflows() const;

 private:
  autosim::CanConfig cfg_;
  uint8_t factor_ = 4;
  bool begun_ = false;
};
