// AutoSim CAN FD node: ESP32 + MCP2518FD <-> PC bridge (library entry point: AutoSimCanNode.h).
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
// autosim_protocol / autosim_node and are unit tested on the PC; this file only wires them to the hardware.
//
// Multi-node setups: every board reports a unique name (default ESP32-<MAC>); the app can rename it.
// The on-board LED blinks blue on received frames, green on sent frames, red when a frame cannot be sent.
#if defined(ARDUINO_ARCH_ESP32)                    // hardware binding: not part of the native (PC) unit tests
#include "AutoSimCanNode.h"

#include "autosim_limits.h"
#include "autosim_node.h"
#include "can_mcp2518fd.h"
#include "host_link.h"
#include "name_store.h"
#include "rtos_ports.h"
#include "status_led.h"

using namespace autosim;

static Queues q;
static HostLink hostLink;
static McpCan mcp;
static NvsNames names;
static char nodeName[16];
static Node* node = nullptr;
static StatusLed led;                 // blue RX, green TX, red TX failed

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
      led.tick(millis());                                // let a blink in progress finish
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
        led.onTx(millis());
        havePending = false;
      } else if (millis() - pendingSince > 50) {
        node->countDropped();
        node->countError();
        led.onTxFailed(millis());
        havePending = false;
      } else {
        vTaskDelay(1);
      }
    }

    // bus -> host
    CanFrame r;
    const uint32_t now = millis();
    for (int k = 0; k < 64 && mcp.receive(r); k++) {
      r.ts_us = uint64_t(esp_timer_get_time());
      led.onRx(now);
      if (xQueueSend(q.rx, &r, 0) == pdTRUE) node->countRx();
      else node->countDropped();                           // serial link slower than the bus: counted in STATUS
    }

    led.tick(now);

    // 1 Hz STATUS
    if (millis() - lastStatus >= AUTOSIM_STATUS_PERIOD_MS) {
      lastStatus = millis();
      uint8_t tec, rec, flags;
      mcp.readStatus(tec, rec, flags);
      node->emitStatus(tec, rec, flags);
      node->onBusDiag(mcp.busDiag());                      // names new bus errors (BIT0/BIT1/CRC/ACK ...) once
    }
  }
}

// -------------------------------------------------------------------------------------------------
namespace autosim {

BoardConfig defaultBoardConfig() {
  BoardConfig c;
#if defined(CONFIG_IDF_TARGET_ESP32S3)
  c.sck = 12; c.mosi = 11; c.miso = 13; c.cs = 10; c.intPin = 4;
  c.ledPin = 48; c.ledRgb = true;                 // ESP32-S3-DevKitC-1 v1.0 (v1.1: 38)
#elif defined(CONFIG_IDF_TARGET_ESP32C3)
  c.sck = 4; c.mosi = 6; c.miso = 5; c.cs = 7; c.intPin = 3;
  c.ledPin = 8; c.ledRgb = true;                  // ESP32-C3-DevKitM-1
#else
  c.sck = 18; c.mosi = 23; c.miso = 19; c.cs = 5; c.intPin = 4;
  c.ledPin = 2; c.ledRgb = false;                 // classic ESP32 DevKit blue LED
#endif
  return c;
}

static char gProblem[96] = "";

const char* canNodeConfigProblem() { return gProblem; }

// Wiring checks that would otherwise show up as a silent "no answer from MCP2518FD".
static void checkConfig(BoardConfig& c) {
  gProblem[0] = 0;
  const int pins[] = {c.sck, c.mosi, c.miso, c.cs, c.intPin};
  const char* names[] = {"SCK", "MOSI", "MISO", "CS", "INT"};
  for (int i = 0; i < 5 && !gProblem[0]; i++) {
    if (pins[i] < 0 || !GPIO_IS_VALID_GPIO(pins[i]))
      snprintf(gProblem, sizeof(gProblem), "%s pin %d is not a GPIO of this chip", names[i], pins[i]);
    for (int j = 0; j < i && !gProblem[0]; j++)
      if (pins[i] == pins[j]) snprintf(gProblem, sizeof(gProblem), "%s and %s both use GPIO %d", names[j], names[i], pins[i]);
  }
  if (!gProblem[0] && c.oscMHz != 40 && c.oscMHz != 20)
    snprintf(gProblem, sizeof(gProblem), "oscMHz must be 40 or 20 (MCP2518FD crystal), not %u", unsigned(c.oscMHz));
  for (int p : pins)                               // an LED on an MCP2518FD pin would garble SPI: drop the LED
    if (c.ledPin == p) c.ledPin = -1;
}

bool startCanNode(const BoardConfig& user) {
  static bool started = false;
  if (started) return false;
  BoardConfig cfg = user;
  checkConfig(cfg);

  hostLink.begin(cfg.uartBaud);
  led.begin(cfg.ledPin, cfg.ledRgb, cfg.ledActiveLow, cfg.ledLevel, cfg.ledOnMs, cfg.ledOffMs);
  mcp.setup(cfg.sck, cfg.mosi, cfg.miso, cfg.cs, cfg.intPin, cfg.oscMHz, gProblem, cfg.xstby);
  q.create();
  if (!q.ok()) return false;
  NvsNames::load(nodeName, cfg.nodeName);

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
  if (xTaskCreatePinnedToCore(linkTask, "autosim-link", AUTOSIM_LINK_STACK, nullptr, AUTOSIM_LINK_PRIO, nullptr,
                              linkCore) != pdPASS ||
      xTaskCreatePinnedToCore(canTask, "autosim-can", AUTOSIM_CAN_STACK, nullptr, AUTOSIM_CAN_PRIO, nullptr,
                              canCore) != pdPASS)
    return false;
  started = true;
  return true;
}

}  // namespace autosim

#endif  // ARDUINO_ARCH_ESP32
