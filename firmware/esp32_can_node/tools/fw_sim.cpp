// Host simulator of the firmware: the REAL protocol + node logic (lib/autosim_proto) wired to a fake
// CAN controller that loops every transmitted frame back as a received frame. It speaks the wire
// protocol on stdin/stdout, so the Python app (or any script) can be tested against the firmware
// logic without hardware:     g++ -std=c++17 -I lib/autosim_proto tools/fw_sim.cpp lib/autosim_proto/*.cpp
//
// Test hooks (environment variables):
//   FW_SIM_NAME=<name>        initial node name            FW_SIM_FAIL_START=1   MCP2518FD "does not start"
//   FW_SIM_TX_LIMIT=<n>       TX queue "full" after n frames   FW_SIM_FAIL_NVS=1    name storage fails
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <chrono>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#include "autosim_node.h"

using namespace autosim;

static uint64_t nowUs() {
  using namespace std::chrono;
  static const auto t0 = steady_clock::now();
  return duration_cast<microseconds>(steady_clock::now() - t0).count();
}

struct StdoutSink : ISink {
  bool send(uint8_t type, uint8_t seq, const uint8_t* payload, size_t len) override {
    uint8_t buf[MAX_WIRE];
    const size_t n = encodePacket(type, seq, payload, len, buf, sizeof(buf));
    if (!n) return false;
    fwrite(buf, 1, n, stdout);
    fflush(stdout);
    return true;
  }
};

struct FakeCan : ICanController {
  CanConfig cfg;
  uint8_t factor = 1;
  bool failStart = false;
  void configure(const CanConfig& c, uint8_t f) override { cfg = c; factor = f; }
  bool start(char* err, size_t cap) override {
    if (failStart) { snprintf(err, cap, "SPI read-back error (check wiring)"); return false; }
    return true;
  }
  void stop() override {}
};

struct Names : INameStore {
  bool fail = false;
  bool save(const char*) override { return !fail; }
};

struct LoopbackTx : ITxPort {
  StdoutSink* sink = nullptr;
  Node* node = nullptr;
  long limit = -1;
  long accepted = 0;
  bool queue(const CanFrame& f) override {
    if (limit >= 0 && accepted >= limit) return false;
    accepted++;
    CanFrame rx = f;
    rx.ts_us = nowUs();
    uint8_t out[CAN_PAYLOAD_MAX];
    sink->send(MSG_RX_FRAME, 0, out, encodeCanPayload(rx, out));
    node->countTx();
    node->countRx();
    return true;
  }
};

// CONFIG/START/STOP are executed immediately (there is only one thread here).
struct DirectControl : IControlPort {
  Node* node = nullptr;
  bool submit(const ControlCmd& c) override { node->executeControl(c); return true; }
};

struct Feeder : IPacketHandler {
  Node* node = nullptr;
  void onPacket(const Packet& p) override { node->notifyHostActivity(uint32_t(nowUs() / 1000)); node->onPacket(p); }
  void onBadPacket(DecodeStatus why) override { node->onBadPacket(why); }
};

int main() {
#ifdef _WIN32
  _setmode(_fileno(stdin), _O_BINARY);
  _setmode(_fileno(stdout), _O_BINARY);
#endif
  StdoutSink sink;
  FakeCan can;
  Names names;
  LoopbackTx tx;
  DirectControl ctl;
  can.failStart = getenv("FW_SIM_FAIL_START") != nullptr;
  names.fail = getenv("FW_SIM_FAIL_NVS") != nullptr;
  if (const char* l = getenv("FW_SIM_TX_LIMIT")) tx.limit = atol(l);

  Node::Env env;
  env.sink = &sink; env.tx = &tx; env.ctl = &ctl; env.can = &can; env.names = &names;
  const char* name = getenv("FW_SIM_NAME");
  Node node(env, name ? name : "FW-SIM", 1, 0);
  tx.sink = &sink; tx.node = &node; ctl.node = &node;
  Feeder feeder;
  feeder.node = &node;
  StreamParser parser(feeder);

  uint8_t buf[512];
  size_t n;
  while ((n = fread(buf, 1, 1, stdin)) > 0) parser.feed(buf, n);       // byte-wise so replies stay interleaved
  return 0;
}
