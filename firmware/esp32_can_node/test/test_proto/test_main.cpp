// Host unit tests for lib/autosim_proto (run with:  pio test -e native).
#include <unity.h>

#include <string.h>
#include <string>
#include <vector>

#include "autosim_node.h"
#include "autosim_protocol.h"

using namespace autosim;

void setUp() {}
void tearDown() {}

// ------------------------------------------------------------------------------ primitives
static void test_crc16_check_value() {
  const uint8_t s[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
  TEST_ASSERT_EQUAL_HEX16(0x29B1, crc16(s, 9));          // CRC-16/CCITT-FALSE reference value
  TEST_ASSERT_EQUAL_HEX16(0xFFFF, crc16(nullptr, 0));
}

static void test_cobs_roundtrip_edge_sizes() {
  const size_t sizes[] = {0, 1, 2, 253, 254, 255, 256, 300, 508, 600};
  for (size_t n : sizes) {
    std::vector<uint8_t> in(n), enc(n + n / 254 + 2), dec(n + 1);
    for (size_t i = 0; i < n; i++) in[i] = (i % 7 == 0) ? 0 : uint8_t(i * 31 + 1);   // zeros sprinkled in
    const size_t e = cobsEncode(in.data(), n, enc.data());
    for (size_t i = 0; i < e; i++) TEST_ASSERT_NOT_EQUAL(0, enc[i]);                  // no zero on the wire
    size_t d = 0;
    TEST_ASSERT_TRUE(cobsDecode(enc.data(), e, dec.data(), dec.size(), d));
    TEST_ASSERT_EQUAL_UINT32(n, d);
    if (n) TEST_ASSERT_EQUAL_MEMORY(in.data(), dec.data(), n);     // (Unity refuses to compare 0 bytes)
  }
}

static void test_cobs_decode_rejects_garbage() {
  uint8_t out[16];
  size_t n = 0;
  const uint8_t zero_code[] = {0x00, 0x01};
  const uint8_t too_long[] = {0x09, 0x01, 0x02};          // claims 8 data bytes, has 2
  TEST_ASSERT_FALSE(cobsDecode(zero_code, 2, out, sizeof(out), n));
  TEST_ASSERT_FALSE(cobsDecode(too_long, 3, out, sizeof(out), n));
}

static void test_packet_roundtrip_and_wire_format() {
  const uint8_t payload[] = {0x00, 0x01, 0x02, 0x00};
  uint8_t wire[MAX_WIRE];
  const size_t n = encodePacket(MSG_TX_FRAME, 7, payload, sizeof(payload), wire, sizeof(wire));
  TEST_ASSERT_GREATER_THAN(0, n);
  TEST_ASSERT_EQUAL_UINT8(0x00, wire[n - 1]);              // delimiter
  for (size_t i = 0; i + 1 < n; i++) TEST_ASSERT_NOT_EQUAL(0, wire[i]);
  Packet p;
  TEST_ASSERT_TRUE(decodePacket(wire, n - 1, p) == DecodeStatus::Ok);
  TEST_ASSERT_EQUAL_UINT8(MSG_TX_FRAME, p.type);
  TEST_ASSERT_EQUAL_UINT8(7, p.seq);
  TEST_ASSERT_EQUAL_UINT16(4, p.len);
  TEST_ASSERT_EQUAL_MEMORY(payload, p.payload, 4);
}

static void test_packet_detects_corruption() {
  uint8_t wire[MAX_WIRE];
  const uint8_t payload[] = {1, 2, 3};
  const size_t n = encodePacket(MSG_PING, 1, payload, 3, wire, sizeof(wire));
  Packet p;
  uint8_t bad[MAX_WIRE];
  memcpy(bad, wire, n);
  bad[3] ^= 0x55;                                          // flip payload bits
  const DecodeStatus st = decodePacket(bad, n - 1, p);
  TEST_ASSERT_TRUE(st == DecodeStatus::BadCrc || st == DecodeStatus::BadCobs);
  TEST_ASSERT_TRUE(decodePacket(wire, 2, p) != DecodeStatus::Ok);                      // truncated
  TEST_ASSERT_EQUAL_UINT32(0, encodePacket(MSG_PING, 0, payload, MAX_PAYLOAD + 1, wire, sizeof(wire)));
}

struct Collect : IPacketHandler {
  std::vector<Packet> pkts;
  int bad = 0;
  void onPacket(const Packet& p) override { pkts.push_back(p); }
  void onBadPacket(DecodeStatus) override { bad++; }
};

static void test_stream_parser_resyncs_after_garbage() {
  Collect c;
  StreamParser sp(c);
  uint8_t a[MAX_WIRE], b[MAX_WIRE];
  const uint8_t pa[] = {0xAA}, pb[] = {0xBB, 0xCC};
  const size_t na = encodePacket(MSG_PING, 1, pa, 1, a, sizeof(a));
  const size_t nb = encodePacket(MSG_PING, 2, pb, 2, b, sizeof(b));
  const uint8_t junk[] = {0x05, 'g', 'a', 'r', 'b', 'a', 'g', 'e', 0x00, 0x00, 0x00};
  sp.feed(a, na);
  sp.feed(junk, sizeof(junk));                              // garbage + empty blocks in between
  sp.feed(b, nb);
  TEST_ASSERT_EQUAL_UINT32(2, c.pkts.size());
  TEST_ASSERT_EQUAL_UINT8(1, c.pkts[0].seq);
  TEST_ASSERT_EQUAL_UINT8(2, c.pkts[1].seq);
  TEST_ASSERT_EQUAL_INT(1, c.bad);
  // an oversized run without a delimiter must not overflow and must recover afterwards
  for (size_t i = 0; i < MAX_WIRE + 100; i++) sp.feed(0x11);
  sp.feed(0x00);
  sp.feed(a, na);
  TEST_ASSERT_EQUAL_UINT32(3, c.pkts.size());
  TEST_ASSERT_EQUAL_INT(2, c.bad);
}

// ------------------------------------------------------------------------------ payloads
static void test_can_payload_layout_and_roundtrip() {
  CanFrame f;
  f.id = 0x1ABCDE;
  f.flags = FLAG_EXT | FLAG_FD | FLAG_BRS;
  f.len = 12;
  f.ts_us = 0x0102030405060708ULL;
  for (int i = 0; i < 12; i++) f.data[i] = uint8_t(0xA0 + i);
  uint8_t p[CAN_PAYLOAD_MAX];
  const size_t n = encodeCanPayload(f, p);
  TEST_ASSERT_EQUAL_UINT32(CAN_HEADER_LEN + 12, n);
  const uint8_t expectHead[] = {0xDE, 0xBC, 0x1A, 0x00, 0x07, 12, 8, 7, 6, 5, 4, 3, 2, 1};   // all little endian
  TEST_ASSERT_EQUAL_MEMORY(expectHead, p, sizeof(expectHead));
  CanFrame g;
  TEST_ASSERT_TRUE(decodeCanPayload(p, n, g));
  TEST_ASSERT_EQUAL_HEX32(f.id, g.id);
  TEST_ASSERT_EQUAL_UINT8(f.flags, g.flags);
  TEST_ASSERT_TRUE(g.ts_us == f.ts_us);
  TEST_ASSERT_EQUAL_MEMORY(f.data, g.data, 12);
}

static void test_can_payload_rejects_bad_input_and_remote_has_no_data() {
  CanFrame g;
  uint8_t p[CAN_PAYLOAD_MAX] = {0};
  TEST_ASSERT_FALSE(decodeCanPayload(p, 10, g));            // shorter than the header
  p[5] = 65;
  TEST_ASSERT_FALSE(decodeCanPayload(p, CAN_HEADER_LEN + 65, g));
  p[5] = 8;
  TEST_ASSERT_FALSE(decodeCanPayload(p, CAN_HEADER_LEN + 4, g));    // data truncated
  p[4] = FLAG_RTR;
  TEST_ASSERT_TRUE(decodeCanPayload(p, CAN_HEADER_LEN, g));          // RTR: len is the DLC, no bytes follow
  CanFrame r;
  r.flags = FLAG_RTR;
  r.len = 4;
  TEST_ASSERT_EQUAL_UINT32(CAN_HEADER_LEN, encodeCanPayload(r, p));
}

static void test_fd_length_table() {
  const uint8_t in[] = {0, 1, 8, 9, 12, 13, 16, 17, 20, 21, 24, 25, 32, 33, 48, 49, 64, 65};
  const uint8_t out[] = {0, 1, 8, 12, 12, 16, 16, 20, 20, 24, 24, 32, 32, 48, 48, 64, 64, 0xFF};
  for (size_t i = 0; i < sizeof(in); i++) TEST_ASSERT_EQUAL_UINT8(out[i], nextValidFdLength(in[i]));
}

static void test_config_layout_and_validation_table() {
  CanConfig c;
  c.nominal = 500000;
  c.data = 2000000;
  c.flags = CFG_FD | CFG_BRS | CFG_NON_ISO;
  c.spNominal = 80;
  c.spData = 75;
  uint8_t b[CONFIG_LEN];
  encodeConfig(c, b);
  const uint8_t expect[] = {0x20, 0xA1, 0x07, 0x00, 0x80, 0x84, 0x1E, 0x00, 0x07, 80, 75, 0};
  TEST_ASSERT_EQUAL_MEMORY(expect, b, CONFIG_LEN);
  CanConfig d;
  TEST_ASSERT_TRUE(decodeConfig(b, CONFIG_LEN, d));
  TEST_ASSERT_TRUE(d.nonIso() && d.fd());
  TEST_ASSERT_FALSE(decodeConfig(b, CONFIG_LEN - 1, d));

  struct Row { uint32_t nom, data; uint8_t flags; bool ok; uint8_t factor; };
  const Row rows[] = {
      {500000, 2000000, CFG_FD, true, 4},   {500000, 5000000, CFG_FD, true, 10}, {1000000, 8000000, CFG_FD, true, 8},
      {125000, 1000000, CFG_FD, true, 8},   {500000, 500000, CFG_FD, true, 1},    {250000, 2000000, 0, true, 1},   // classic ignores data rate
      {500000, 8000000, CFG_FD, false, 0},  {500000, 2700000, CFG_FD, false, 0},  {500000, 250000, CFG_FD, false, 0},
      {2000000, 2000000, CFG_FD, false, 0}, {5000, 5000, 0, false, 0},
  };
  for (const Row& r : rows) {
    CanConfig x;
    x.nominal = r.nom; x.data = r.data; x.flags = r.flags;
    uint8_t factor = 0;
    const char* why = validateConfig(x, factor);
    TEST_ASSERT_EQUAL_MESSAGE(r.ok, why == nullptr, why ? why : "expected ok");
    if (r.ok) TEST_ASSERT_EQUAL_UINT8(r.factor, factor);
  }
}

static void test_status_and_hello_ack_layout() {
  NodeStatus s;
  s.state = 1; s.tec = 2; s.rec = 3; s.busFlags = 1; s.rx = 0x11223344; s.tx = 5; s.err = 6; s.dropped = 7;
  uint8_t b[STATUS_LEN];
  encodeStatus(s, b);
  const uint8_t expect[] = {1, 2, 3, 1, 0x44, 0x33, 0x22, 0x11, 5, 0, 0, 0, 6, 0, 0, 0, 7, 0, 0, 0};
  TEST_ASSERT_EQUAL_MEMORY(expect, b, STATUS_LEN);
  uint8_t h[HELLO_ACK_LEN];
  encodeHelloAck(1, 2, CAP_FD | CAP_HW_TIMESTAMP, "Left-Door-ECU-LONGNAME", h);
  TEST_ASSERT_EQUAL_UINT8(PROTO_VERSION, h[0]);
  TEST_ASSERT_EQUAL_UINT8(2, h[2]);
  TEST_ASSERT_EQUAL_UINT8(3, h[3]);
  TEST_ASSERT_EQUAL_MEMORY("Left-Door-ECU-L", h + 4, 15);    // truncated to 15 chars + NUL
  TEST_ASSERT_EQUAL_UINT8(0, h[19]);
}

static void test_node_name_validation() {
  TEST_ASSERT_TRUE(validNodeName((const uint8_t*)"Rear_ECU-2.a", 12));
  TEST_ASSERT_FALSE(validNodeName((const uint8_t*)"", 0));
  TEST_ASSERT_FALSE(validNodeName((const uint8_t*)"sixteen-chars-xx", 16));
  TEST_ASSERT_FALSE(validNodeName((const uint8_t*)"has space", 9));
  TEST_ASSERT_FALSE(validNodeName((const uint8_t*)"a/b", 3));
}

// ------------------------------------------------------------------------------ node logic
struct Sent { uint8_t type, seq; std::vector<uint8_t> payload; };
struct MockSink : ISink {
  std::vector<Sent> out;
  bool send(uint8_t t, uint8_t s, const uint8_t* p, size_t n) override { out.push_back({t, s, std::vector<uint8_t>(p, p + n)}); return true; }
  const Sent* last(uint8_t type) const { for (size_t i = out.size(); i-- > 0;) if (out[i].type == type) return &out[i]; return nullptr; }
};
struct MockTx : ITxPort {
  std::vector<CanFrame> frames;
  size_t cap = 100;
  bool queue(const CanFrame& f) override { if (frames.size() >= cap) return false; frames.push_back(f); return true; }
};
struct MockCtl : IControlPort {
  std::vector<ControlCmd> cmds;
  bool accept = true;
  bool submit(const ControlCmd& c) override { if (!accept) return false; cmds.push_back(c); return true; }
};
struct MockCan : ICanController {
  CanConfig cfg; uint8_t factor = 0; int starts = 0, stops = 0, configures = 0; bool fail = false;
  void configure(const CanConfig& c, uint8_t f) override { cfg = c; factor = f; configures++; }
  bool start(char* err, size_t cap) override { if (fail) { strncpy(err, "boom", cap); return false; } starts++; return true; }
  void stop() override { stops++; }
};
struct MockNames : INameStore {
  std::string saved; bool fail = false;
  bool save(const char* n) override { if (fail) return false; saved = n; return true; }
};

struct Rig {
  MockSink sink; MockTx tx; MockCtl ctl; MockCan can; MockNames names;
  Node node;
  Rig() : node(env(), "ESP32-ABCDEF", 1, 0) {}
  Node::Env env() { Node::Env e; e.sink = &sink; e.tx = &tx; e.ctl = &ctl; e.can = &can; e.names = &names; return e; }
  // deliver a host packet; control packets are executed immediately like the CAN task would
  void host(uint8_t type, uint8_t seq, const std::vector<uint8_t>& payload = {}) {
    Packet p; p.type = type; p.seq = seq; p.len = uint16_t(payload.size());
    if (!payload.empty()) memcpy(p.payload, payload.data(), payload.size());
    node.onPacket(p);
    while (!ctl.cmds.empty()) { ControlCmd c = ctl.cmds.front(); ctl.cmds.erase(ctl.cmds.begin()); node.executeControl(c); }
  }
  void hostFrame(uint8_t seq, uint32_t id, uint8_t flags, const std::vector<uint8_t>& data) {
    CanFrame f; f.id = id; f.flags = flags; f.len = uint8_t(data.size());
    if (!data.empty()) memcpy(f.data, data.data(), data.size());
    uint8_t buf[CAN_PAYLOAD_MAX];
    const size_t n = encodeCanPayload(f, buf);
    host(MSG_TX_FRAME, seq, std::vector<uint8_t>(buf, buf + n));
  }
  std::vector<uint8_t> cfgBytes(uint32_t nom, uint32_t data, uint8_t flags) {
    CanConfig c; c.nominal = nom; c.data = data; c.flags = flags;
    uint8_t b[CONFIG_LEN]; encodeConfig(c, b);
    return std::vector<uint8_t>(b, b + CONFIG_LEN);
  }
  const Sent* nackFor(uint8_t type) const {
    for (size_t i = sink.out.size(); i-- > 0;) if (sink.out[i].type == MSG_NACK && sink.out[i].payload[0] == type) return &sink.out[i];
    return nullptr;
  }
};

static void test_node_hello_ping_and_unknown() {
  Rig r;
  r.host(MSG_HELLO, 9, {1});
  const Sent* h = r.sink.last(MSG_HELLO_ACK);
  TEST_ASSERT_NOT_NULL(h);
  TEST_ASSERT_EQUAL_UINT8(9, h->seq);
  TEST_ASSERT_EQUAL_MEMORY("ESP32-ABCDEF", h->payload.data() + 4, 12);
  r.host(MSG_PING, 3, {1, 2, 3, 4, 99});
  const Sent* po = r.sink.last(MSG_PONG);
  TEST_ASSERT_NOT_NULL(po);
  TEST_ASSERT_EQUAL_UINT32(4, po->payload.size());
  r.host(0x77, 4);
  TEST_ASSERT_NOT_NULL(r.nackFor(0x77));
  TEST_ASSERT_EQUAL_UINT8(ERR_UNSUPPORTED, r.nackFor(0x77)->payload[1]);
}

static void test_node_set_name() {
  Rig r;
  r.host(MSG_SET_NAME, 1, {'B', 'E', 'N', 'C', 'H', '-', '1'});
  TEST_ASSERT_EQUAL_STRING("BENCH-1", r.names.saved.c_str());
  TEST_ASSERT_EQUAL_STRING("BENCH-1", r.node.name());
  TEST_ASSERT_EQUAL_UINT8(MSG_ACK, r.sink.out.back().type);
  r.host(MSG_SET_NAME, 2, {'b', 'a', 'd', ' ', 'n'});
  TEST_ASSERT_EQUAL_UINT8(ERR_BAD_CONFIG, r.nackFor(MSG_SET_NAME)->payload[1]);
  TEST_ASSERT_EQUAL_STRING("BENCH-1", r.node.name());       // unchanged
  r.names.fail = true;
  r.host(MSG_SET_NAME, 3, {'o', 'k'});
  TEST_ASSERT_EQUAL_UINT8(ERR_STORAGE, r.nackFor(MSG_SET_NAME)->payload[1]);
  TEST_ASSERT_EQUAL_STRING("BENCH-1", r.node.name());
}

static void test_node_config_start_stop_flow() {
  Rig r;
  r.host(MSG_CONFIG, 1, r.cfgBytes(500000, 5000000, CFG_FD | CFG_BRS));
  TEST_ASSERT_EQUAL_UINT8(MSG_ACK, r.sink.out.back().type);
  TEST_ASSERT_EQUAL_UINT8(10, r.can.factor);
  TEST_ASSERT_EQUAL_INT(0, r.can.starts);                    // CONFIG alone never touches the bus
  r.host(MSG_START, 2);
  TEST_ASSERT_EQUAL_INT(1, r.can.starts);
  TEST_ASSERT_TRUE(r.node.running());
  r.host(MSG_START, 3);                                      // idempotent
  TEST_ASSERT_EQUAL_INT(1, r.can.starts);
  r.host(MSG_CONFIG, 4, r.cfgBytes(250000, 1000000, CFG_FD));        // reconfigure while running -> BUSY
  TEST_ASSERT_EQUAL_UINT8(ERR_BUSY, r.nackFor(MSG_CONFIG)->payload[1]);
  r.host(MSG_STOP, 5);
  TEST_ASSERT_FALSE(r.node.running());
  TEST_ASSERT_EQUAL_INT(1, r.can.stops);
  r.host(MSG_CONFIG, 6, r.cfgBytes(250000, 1000000, CFG_FD));
  TEST_ASSERT_EQUAL_UINT8(MSG_ACK, r.sink.out.back().type);
  TEST_ASSERT_EQUAL_UINT8(4, r.can.factor);
}

static void test_node_rejects_invalid_config_with_reason() {
  Rig r;
  r.host(MSG_CONFIG, 1, r.cfgBytes(500000, 8000000, CFG_FD));
  const Sent* e = r.sink.last(MSG_ERROR);
  TEST_ASSERT_NOT_NULL(e);
  TEST_ASSERT_EQUAL_UINT8(ERR_BAD_CONFIG, e->payload[0]);
  TEST_ASSERT_NOT_NULL(strstr((const char*)e->payload.data() + 1, "ratio"));
  TEST_ASSERT_EQUAL_UINT8(ERR_BAD_CONFIG, r.nackFor(MSG_CONFIG)->payload[1]);
  TEST_ASSERT_EQUAL_INT(0, r.can.configures);
  r.host(MSG_CONFIG, 2, {1, 2, 3});                                    // truncated CONFIG
  TEST_ASSERT_EQUAL_UINT8(ERR_BAD_LEN, r.nackFor(MSG_CONFIG)->payload[1]);
}

static void test_node_start_failure_reports_reason() {
  Rig r;
  r.can.fail = true;
  r.host(MSG_START, 1);
  TEST_ASSERT_FALSE(r.node.running());
  TEST_ASSERT_EQUAL_UINT8(ERR_MCP_FAULT, r.nackFor(MSG_START)->payload[1]);
  TEST_ASSERT_EQUAL_STRING("boom", (const char*)r.sink.last(MSG_ERROR)->payload.data() + 1);
}

static void test_node_tx_gating_padding_and_limits() {
  Rig r;
  r.hostFrame(1, 0x100, 0, {1});                             // before START
  TEST_ASSERT_EQUAL_UINT8(ERR_NOT_STARTED, r.nackFor(MSG_TX_FRAME)->payload[1]);
  r.host(MSG_START, 2);
  r.hostFrame(3, 0x100, 0, {1, 2, 3});                       // ok, classic
  TEST_ASSERT_EQUAL_UINT32(1, r.tx.frames.size());
  r.hostFrame(4, 0x101, FLAG_FD | FLAG_BRS, std::vector<uint8_t>(10, 0xEE));   // FD 10 -> padded to 12
  TEST_ASSERT_EQUAL_UINT32(2, r.tx.frames.size());
  TEST_ASSERT_EQUAL_UINT8(12, r.tx.frames[1].len);
  TEST_ASSERT_EQUAL_UINT8(0xEE, r.tx.frames[1].data[9]);
  TEST_ASSERT_EQUAL_UINT8(0, r.tx.frames[1].data[10]);
  TEST_ASSERT_EQUAL_UINT8(0, r.tx.frames[1].data[11]);
  const size_t before = r.sink.out.size();
  r.hostFrame(5, 0x102, 0, std::vector<uint8_t>(9, 1));                          // classic with 9 bytes
  r.hostFrame(6, 0x800, 0, {1});                                                 // 11-bit id out of range
  r.hostFrame(7, 0x102, FLAG_FD, std::vector<uint8_t>(65, 1));                   // > 64 (decode rejects)
  TEST_ASSERT_EQUAL_UINT32(2, r.tx.frames.size());
  TEST_ASSERT_EQUAL_UINT32(before + 3, r.sink.out.size());
  r.tx.cap = 2;                                                                  // queue full
  r.hostFrame(8, 0x103, 0, {1});
  TEST_ASSERT_EQUAL_UINT8(ERR_BUSY, r.nackFor(MSG_TX_FRAME)->payload[1]);
  TEST_ASSERT_EQUAL_UINT32(1, r.node.droppedCount());
}

static void test_node_classic_mode_rejects_fd_frames() {
  Rig r;
  r.host(MSG_CONFIG, 1, r.cfgBytes(500000, 2000000, 0));                         // FD disabled
  r.host(MSG_START, 2);
  r.hostFrame(3, 0x10, FLAG_FD, {1});
  TEST_ASSERT_EQUAL_UINT8(ERR_UNSUPPORTED, r.nackFor(MSG_TX_FRAME)->payload[1]);
  TEST_ASSERT_EQUAL_UINT32(0, r.tx.frames.size());
}

static void test_node_tx_batch() {
  Rig r;
  r.host(MSG_START, 1);
  std::vector<uint8_t> batch = {3};
  for (int i = 0; i < 3; i++) {
    CanFrame f; f.id = 0x10 + i; f.len = 1; f.data[0] = uint8_t(i);
    uint8_t b[CAN_PAYLOAD_MAX];
    const size_t n = encodeCanPayload(f, b);
    batch.push_back(uint8_t(n));
    batch.insert(batch.end(), b, b + n);
  }
  r.host(MSG_TX_BATCH, 2, batch);
  TEST_ASSERT_EQUAL_UINT32(3, r.tx.frames.size());
  TEST_ASSERT_EQUAL_HEX32(0x12, r.tx.frames[2].id);
}

static void test_node_watchdog_stops_can_without_acking_the_host() {
  Rig r;
  r.host(MSG_START, 1);
  r.node.notifyHostActivity(1000);
  r.node.tickWatchdog(5500, 5000);                                               // 4.5 s of silence: still fine
  TEST_ASSERT_TRUE(r.node.running());
  const size_t acks = r.sink.out.size();
  r.node.tickWatchdog(6500, 5000);                                               // 5.5 s: stop requested
  TEST_ASSERT_EQUAL_UINT32(1, r.ctl.cmds.size());
  TEST_ASSERT_TRUE(r.ctl.cmds[0].internal);
  TEST_ASSERT_EQUAL_UINT8(MSG_ERROR, r.sink.out.back().type);                    // host is told why
  ControlCmd c = r.ctl.cmds[0];
  r.node.executeControl(c);
  TEST_ASSERT_FALSE(r.node.running());
  TEST_ASSERT_EQUAL_UINT32(acks + 1, r.sink.out.size());                         // only the ERROR, no ACK
  r.node.tickWatchdog(60000, 5000);                                              // nothing running: no more events
  TEST_ASSERT_EQUAL_UINT32(acks + 1, r.sink.out.size());
}

static void test_node_status_and_bad_packets() {
  Rig r;
  r.host(MSG_START, 1);
  r.node.countRx(); r.node.countRx(); r.node.countTx();
  r.node.emitStatus(3, 4, 0x01);
  const Sent* s = r.sink.last(MSG_STATUS);
  TEST_ASSERT_NOT_NULL(s);
  TEST_ASSERT_EQUAL_UINT8(1, s->payload[0]);                                     // running
  TEST_ASSERT_EQUAL_UINT8(3, s->payload[1]);
  TEST_ASSERT_EQUAL_UINT8(4, s->payload[2]);
  TEST_ASSERT_EQUAL_UINT8(2, s->payload[4]);                                     // rx = 2
  r.node.emitStatus(0, 0, 0x02);                                                 // bus-off => fault
  TEST_ASSERT_EQUAL_UINT8(2, r.sink.last(MSG_STATUS)->payload[0]);
  r.node.onBadPacket(DecodeStatus::BadCrc);
  TEST_ASSERT_EQUAL_UINT8(ERR_BAD_CRC, r.sink.last(MSG_ERROR)->payload[0]);
  TEST_ASSERT_EQUAL_UINT32(1, r.node.errorCount());
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_crc16_check_value);
  RUN_TEST(test_cobs_roundtrip_edge_sizes);
  RUN_TEST(test_cobs_decode_rejects_garbage);
  RUN_TEST(test_packet_roundtrip_and_wire_format);
  RUN_TEST(test_packet_detects_corruption);
  RUN_TEST(test_stream_parser_resyncs_after_garbage);
  RUN_TEST(test_can_payload_layout_and_roundtrip);
  RUN_TEST(test_can_payload_rejects_bad_input_and_remote_has_no_data);
  RUN_TEST(test_fd_length_table);
  RUN_TEST(test_config_layout_and_validation_table);
  RUN_TEST(test_status_and_hello_ack_layout);
  RUN_TEST(test_node_name_validation);
  RUN_TEST(test_node_hello_ping_and_unknown);
  RUN_TEST(test_node_set_name);
  RUN_TEST(test_node_config_start_stop_flow);
  RUN_TEST(test_node_rejects_invalid_config_with_reason);
  RUN_TEST(test_node_start_failure_reports_reason);
  RUN_TEST(test_node_tx_gating_padding_and_limits);
  RUN_TEST(test_node_classic_mode_rejects_fd_frames);
  RUN_TEST(test_node_tx_batch);
  RUN_TEST(test_node_watchdog_stops_can_without_acking_the_host);
  RUN_TEST(test_node_status_and_bad_packets);
  return UNITY_END();
}
