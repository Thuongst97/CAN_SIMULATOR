// FreeRTOS queue implementations of the node's ports (see lib/autosim_proto/autosim_node.h).
//
//   link task (core 0)  --txQueue-->   CAN task (core 1)      frames for the bus
//   link task (core 0)  --ctlQueue-->  CAN task (core 1)      CONFIG / START / STOP
//   CAN task  (core 1)  --rxQueue-->   link task (core 0)     frames from the bus
//   either task         --outQueue-->  link task (core 0)     ACK / NACK / STATUS / ERROR / PONG / HELLO_ACK
#pragma once
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <string.h>

#include "autosim_node.h"
#include "board_config.h"

struct OutMsg {                         // a small control packet waiting to be sent to the host
  uint8_t type;
  uint8_t seq;
  uint8_t len;
  uint8_t payload[96];
};

struct Queues {
  QueueHandle_t tx = nullptr;           // autosim::CanFrame
  QueueHandle_t rx = nullptr;           // autosim::CanFrame
  QueueHandle_t ctl = nullptr;          // autosim::ControlCmd
  QueueHandle_t out = nullptr;          // OutMsg

  void create() {
    tx = xQueueCreate(AUTOSIM_TX_QUEUE_LEN, sizeof(autosim::CanFrame));
    rx = xQueueCreate(AUTOSIM_RX_QUEUE_LEN, sizeof(autosim::CanFrame));
    ctl = xQueueCreate(AUTOSIM_CTL_QUEUE_LEN, sizeof(autosim::ControlCmd));
    out = xQueueCreate(AUTOSIM_OUT_QUEUE_LEN, sizeof(OutMsg));
  }
  bool ok() const { return tx && rx && ctl && out; }
};

struct QueueSink : autosim::ISink {
  QueueHandle_t out;
  explicit QueueSink(QueueHandle_t q) : out(q) {}
  bool send(uint8_t type, uint8_t seq, const uint8_t* payload, size_t len) override {
    OutMsg m;
    if (len > sizeof(m.payload)) return false;
    m.type = type;
    m.seq = seq;
    m.len = uint8_t(len);
    if (len) memcpy(m.payload, payload, len);
    return xQueueSend(out, &m, pdMS_TO_TICKS(5)) == pdTRUE;    // brief wait so ACKs are not lost to a burst
  }
};

struct QueueTxPort : autosim::ITxPort {
  QueueHandle_t q;
  explicit QueueTxPort(QueueHandle_t h) : q(h) {}
  bool queue(const autosim::CanFrame& f) override { return xQueueSend(q, &f, 0) == pdTRUE; }
};

struct QueueControlPort : autosim::IControlPort {
  QueueHandle_t q;
  explicit QueueControlPort(QueueHandle_t h) : q(h) {}
  bool submit(const autosim::ControlCmd& c) override { return xQueueSend(q, &c, 0) == pdTRUE; }
};
