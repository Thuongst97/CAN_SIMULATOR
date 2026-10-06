// AutoSim CAN FD node: ESP32 + MCP2518FD <-> PC bridge.
//
//                  ┌────────────── core 0 ──────────────┐        ┌──────────────── core 1 ────────────────┐
//   PC <-- UART/USB-CDC -->  link task                    txQueue  CAN task  <-- SPI + INT (falling edge) --> MCP2518FD
//                  │ parse packets, answer HELLO/PING/   ───────►  │ start/stop/configure the controller      │
//                  │ SET_NAME, validate + queue TX frames ctlQueue │ transmit queued frames (tryToSend)       │
//                  │ send RX frames, ACK/NACK, STATUS     ◄─────── │ drain received frames (receive)          │
//                  └───────────────────────────────────  rxQueue   └──────────────────────────────────────────┘
//                                                        outQueue (ACK / NACK / STATUS / ERROR from either task)
//
// Wire protocol: docs/ESP32_PROTOCOL.md (COBS framed, CRC16). Protocol and command logic live in
// lib/autosim_proto and are unit tested on the PC; this file only wires them to the hardware.
//
// Multi-node setups: every board reports a unique name (default ESP32-<MAC>); the app can rename it.
#include <Arduino.h>

#include "autosim_node.h"
#include "board_config.h"
#include "can_mcp2518fd.h"
#include "host_link.h"
#include "name_store.h"
#include "rtos_ports.h"

using namespace autosim;

static Queues q;
static HostLink hostLink;
static McpCan mcp;
static NvsNames names;
static char nodeName[16];
static Node* node = nullptr;

// -------------------------------------------------------------------------------------------------
// link task: PC <-> queues
// -------------------------------------------------------------------------------------------------
namespace {
struct LinkHandler : IPacketHandler {
  void onPacket(const Packet& p) override {
    node->notifyHostActivity(millis());           // any valid packet keeps the watchdog quiet
    node->onPacket(p);
  }
  void onBadPacket(DecodeStatus why) override { node->onBadPacket(why); }
};

// Coalesces small packets into one write() to keep the UART / USB overhead low.
struct TxBuffer {
  uint8_t buf[640];
  size_t n = 0;
  void flush() {
    if (n) { hostLink.write(buf, n); n = 0; }
  }
  void packet(uint8_t type, uint8_t seq, const uint8_t* payload, size_t len) {
    uint8_t tmp[MAX_WIRE];
    const size_t w = encodePacket(type, seq, payload, len, tmp, sizeof(tmp));
    if (!w) return;
    if (n + w > sizeof(buf)) flush();
    memcpy(buf + n, tmp, w);
    n += w;
  }
};
}  // namespace

static void linkTask(void*) {
  static LinkHandler handler;
  static StreamParser parser(handler);
  static TxBuffer out;
  uint8_t rxbuf[128];
  uint8_t rxSeq = 0;

  for (;;) {
    bool busy = false;

    // PC -> node
    int avail = hostLink.available();
    while (avail > 0) {
      const size_t n = hostLink.read(rxbuf, avail > int(sizeof(rxbuf)) ? sizeof(rxbuf) : size_t(avail));
      parser.feed(rxbuf, n);
      busy = true;
      avail = hostLink.available();
      if (!n) break;
    }

    // node -> PC: control replies first (low latency ACKs), then CAN frames
    OutMsg m;
    while (xQueueReceive(q.out, &m, 0) == pdTRUE) {
      out.packet(m.type, m.seq, m.payload, m.len);
      busy = true;
    }
    CanFrame f;
    uint8_t payload[CAN_PAYLOAD_MAX];
    for (int k = 0; k < 32 && xQueueReceive(q.rx, &f, 0) == pdTRUE; k++) {
      out.packet(MSG_RX_FRAME, rxSeq++, payload, encodeCanPayload(f, payload));
      busy = true;
    }
    out.flush();

    node->tickWatchdog(millis(), AUTOSIM_WATCHDOG_MS);
    if (!busy) vTaskDelay(1);
  }
}

// -------------------------------------------------------------------------------------------------
// CAN task: queues <-> MCP2518FD
// -------------------------------------------------------------------------------------------------
static void canTask(void*) {
  CanFrame pending;
  bool havePending = false;
  uint32_t pendingSince = 0, lastStatus = 0;

  for (;;) {
    ControlCmd c;
    // While idle this call is the task's sleep point (5 ms); while running it never waits.
    TickType_t wait = node->running() ? 0 : pdMS_TO_TICKS(5);
    while (xQueueReceive(q.ctl, &c, wait) == pdTRUE) {
      node->executeControl(c);
      wait = 0;
    }

    if (!node->running()) {
      if (havePending) havePending = false;
      xQueueReset(q.tx);                                   // frames queued before STOP are discarded
      continue;
    }

    // host -> bus. A frame the driver cannot take yet is retried; after 50 ms it is dropped.
    if (!havePending && xQueueReceive(q.tx, &pending, pdMS_TO_TICKS(1)) == pdTRUE) {
      havePending = true;
      pendingSince = millis();
    }
    if (havePending) {
      if (mcp.send(pending)) {
        node->countTx();
        havePending = false;
      } else if (millis() - pendingSince > 50) {
        node->countDropped();
        node->countError();
        havePending = false;
      } else {
        vTaskDelay(1);
      }
    }

    // bus -> host
    CanFrame r;
    for (int k = 0; k < 64 && mcp.receive(r); k++) {
      r.ts_us = uint64_t(esp_timer_get_time());
      if (xQueueSend(q.rx, &r, 0) == pdTRUE) node->countRx();
      else node->countDropped();                           // serial link slower than the bus: counted in STATUS
    }

    // 1 Hz STATUS
    if (millis() - lastStatus >= AUTOSIM_STATUS_PERIOD_MS) {
      lastStatus = millis();
      uint8_t tec, rec, flags;
      mcp.readStatus(tec, rec, flags);
      node->emitStatus(tec, rec, flags);
    }
  }
}

// -------------------------------------------------------------------------------------------------
void setup() {
  hostLink.begin();
  q.create();
  NvsNames::load(nodeName);

  static QueueSink sink(q.out);
  static QueueTxPort txPort(q.tx);
  static QueueControlPort ctlPort(q.ctl);
  Node::Env env;
  env.sink = &sink;
  env.tx = &txPort;
  env.ctl = &ctlPort;
  env.can = &mcp;
  env.names = &names;
  static Node theNode(env, nodeName, AUTOSIM_FW_MAJOR, AUTOSIM_FW_MINOR);
  node = &theNode;

#if CONFIG_FREERTOS_UNICORE                                // ESP32-C3: single core, both tasks share it
  const BaseType_t linkCore = 0, canCore = 0;
#else
  const BaseType_t linkCore = 0, canCore = 1;
#endif
  xTaskCreatePinnedToCore(linkTask, "link", AUTOSIM_LINK_STACK, nullptr, AUTOSIM_LINK_PRIO, nullptr, linkCore);
  xTaskCreatePinnedToCore(canTask, "can", AUTOSIM_CAN_STACK, nullptr, AUTOSIM_CAN_PRIO, nullptr, canCore);
}

void loop() { vTaskDelay(portMAX_DELAY); }                 // everything runs in the two tasks
