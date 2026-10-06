// AutoSim node wire protocol (v1). Hardware independent: no Arduino / FreeRTOS includes, so it is
// unit-tested on the PC and cross-checked byte-for-byte against the Python implementation in
// src/autosim/hardware/esp32_gateway/protocol.py.  Spec: docs/ESP32_PROTOCOL.md
//
//   on the wire:  COBS( type | seq | len_le16 | payload[len] | crc16_le ) 0x00
//                 CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over  type..payload
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace autosim {

constexpr uint8_t  PROTO_VERSION = 1;
constexpr size_t   MAX_PAYLOAD = 512;
constexpr size_t   PACKET_OVERHEAD = 6;                       // type, seq, len(2) ... crc(2)
constexpr size_t   MAX_BODY = MAX_PAYLOAD + PACKET_OVERHEAD;  // bytes before COBS
constexpr size_t   MAX_WIRE = MAX_BODY + MAX_BODY / 254 + 2;  // COBS + delimiter, worst case
constexpr size_t   CAN_HEADER_LEN = 14;                       // id(4) flags(1) len(1) ts_us(8)
constexpr size_t   CAN_MAX_DATA = 64;
constexpr size_t   CAN_PAYLOAD_MAX = CAN_HEADER_LEN + CAN_MAX_DATA;
constexpr size_t   NAME_FIELD_LEN = 16;                       // node id in HELLO_ACK, zero padded
constexpr size_t   NAME_MAX_CHARS = 15;

// ---- message types ---------------------------------------------------------------------------
enum MsgType : uint8_t {
  // host -> node
  MSG_HELLO = 0x01,     // u8 proto_ver
  MSG_CONFIG = 0x02,    // CanConfig (12 B)
  MSG_START = 0x03,     // "connect": initialise the MCP2518FD and enter the requested mode
  MSG_STOP = 0x04,      // "disconnect": back to configuration mode
  MSG_SET_NAME = 0x05,  // ASCII node name, 1..15 chars, stored in NVS
  MSG_TX_FRAME = 0x10,  // CAN payload (ts ignored)
  MSG_TX_BATCH = 0x11,  // u8 n, n x (u8 len, CAN payload)
  MSG_PING = 0x20,      // u32 token
  // node -> host
  MSG_HELLO_ACK = 0x81, // proto, fw_major, fw_minor, caps, char[16] node_id
  MSG_ACK = 0x82,       // u8 acked_type
  MSG_NACK = 0x83,      // u8 acked_type, u8 error code
  MSG_RX_FRAME = 0x90,  // CAN payload with node timestamp
  MSG_TX_CONFIRM = 0x91,
  MSG_STATUS = 0xA0,    // NodeStatus (20 B), 1 Hz
  MSG_PONG = 0xA1,      // u32 token
  MSG_ERROR = 0xAF,     // u8 code + ascii text
};

enum ErrorCode : uint8_t {
  ERR_BAD_CRC = 1,
  ERR_BAD_LEN = 2,       // malformed frame / payload length
  ERR_UNSUPPORTED = 3,
  ERR_BAD_CONFIG = 4,
  ERR_BUSY = 5,          // TX queue full / reconfigure while running
  ERR_MCP_FAULT = 6,     // MCP2518FD did not start (SPI wiring, oscillator, bit timing)
  ERR_NOT_STARTED = 7,
  ERR_STORAGE = 8,       // could not store the node name in flash (NVS)
};

// CAN frame flags (byte 4 of the CAN payload)
enum FrameFlag : uint8_t { FLAG_EXT = 0x01, FLAG_FD = 0x02, FLAG_BRS = 0x04, FLAG_ESI = 0x08, FLAG_RTR = 0x10 };

// CONFIG flags
enum ConfigFlag : uint8_t {
  CFG_FD = 0x01, CFG_BRS = 0x02, CFG_NON_ISO = 0x04, CFG_LISTEN_ONLY = 0x08, CFG_LOOPBACK = 0x10,
  CFG_TX_CONFIRM = 0x20,
};

// HELLO_ACK capability bits
enum Caps : uint8_t { CAP_FD = 0x01, CAP_HW_TIMESTAMP = 0x02 };

// ---- primitives ----------------------------------------------------------------------------
uint16_t crc16(const uint8_t* data, size_t n, uint16_t init = 0xFFFF);
size_t cobsEncode(const uint8_t* in, size_t n, uint8_t* out);                      // out >= n + n/254 + 1
bool cobsDecode(const uint8_t* in, size_t n, uint8_t* out, size_t cap, size_t& outLen);

// ---- packets -------------------------------------------------------------------------------
struct Packet {
  uint8_t type = 0;
  uint8_t seq = 0;
  uint16_t len = 0;
  uint8_t payload[MAX_PAYLOAD];
};

// Serialise one packet including the trailing 0x00 delimiter. Returns the byte count, 0 if it
// does not fit (`cap` should be >= MAX_WIRE for payloads up to MAX_PAYLOAD).
size_t encodePacket(uint8_t type, uint8_t seq, const uint8_t* payload, size_t len, uint8_t* out, size_t cap);

enum class DecodeStatus : uint8_t { Ok, BadCobs, TooShort, BadCrc, BadLength };
// `raw` is one COBS block WITHOUT the 0x00 delimiter.
DecodeStatus decodePacket(const uint8_t* raw, size_t n, Packet& out);

struct IPacketHandler {
  virtual ~IPacketHandler() {}
  virtual void onPacket(const Packet& p) = 0;
  virtual void onBadPacket(DecodeStatus why) { (void)why; }
};

// Byte-stream parser: splits on 0x00, decodes, dispatches. Resynchronises after garbage / overflow.
class StreamParser {
 public:
  explicit StreamParser(IPacketHandler& h) : handler_(h) {}
  void feed(uint8_t b);
  void feed(const uint8_t* data, size_t n) { for (size_t i = 0; i < n; i++) feed(data[i]); }
  uint32_t badPackets() const { return bad_; }
 private:
  IPacketHandler& handler_;
  uint8_t buf_[MAX_WIRE];
  size_t n_ = 0;
  bool overflow_ = false;
  uint32_t bad_ = 0;
  Packet pkt_;
};

// ---- CAN payload (TX_FRAME / RX_FRAME) -------------------------------------------------------
//   byte 0..3  can_id (LE)      byte 4  flags        byte 5  data_len (BYTES, not DLC)
//   byte 6..13 ts_us (LE)       byte 14.. data[data_len]   (omitted for remote frames)
struct CanFrame {
  uint32_t id = 0;
  uint8_t flags = 0;
  uint8_t len = 0;
  uint64_t ts_us = 0;
  uint8_t data[CAN_MAX_DATA] = {0};
  bool isExt() const { return flags & FLAG_EXT; }
  bool isFd() const { return flags & FLAG_FD; }
  bool isRtr() const { return flags & FLAG_RTR; }
};

size_t encodeCanPayload(const CanFrame& f, uint8_t* out);                  // out >= CAN_PAYLOAD_MAX
bool decodeCanPayload(const uint8_t* p, size_t n, CanFrame& f);            // false: truncated / len > 64
uint8_t nextValidFdLength(uint8_t n);                                       // 0..8,12,16,20,24,32,48,64; else 0xFF

// ---- CONFIG / STATUS / HELLO_ACK -------------------------------------------------------------
struct CanConfig {
  uint32_t nominal = 500000;
  uint32_t data = 2000000;
  uint8_t flags = CFG_FD | CFG_BRS;
  uint8_t spNominal = 80;   // sample points are accepted but the MCP2518FD driver picks its own timing
  uint8_t spData = 75;
  bool fd() const { return flags & CFG_FD; }
  bool nonIso() const { return flags & CFG_NON_ISO; }
  bool listenOnly() const { return flags & CFG_LISTEN_ONLY; }
  bool loopback() const { return flags & CFG_LOOPBACK; }
};
constexpr size_t CONFIG_LEN = 12;
bool decodeConfig(const uint8_t* p, size_t n, CanConfig& c);
void encodeConfig(const CanConfig& c, uint8_t* out);                       // out >= CONFIG_LEN

// Returns nullptr if usable, else a short human readable reason. `dataFactor` receives the integer
// data/nominal ratio (1 for classic CAN) that the MCP2518FD bit-timing needs (1..10).
const char* validateConfig(const CanConfig& c, uint8_t& dataFactor);

struct NodeStatus {
  uint8_t state = 0;      // 0 idle, 1 running, 2 fault
  uint8_t tec = 0, rec = 0;
  uint8_t busFlags = 0;   // b0 error passive, b1 bus-off
  uint32_t rx = 0, tx = 0, err = 0, dropped = 0;
};
constexpr size_t STATUS_LEN = 20;
void encodeStatus(const NodeStatus& s, uint8_t* out);
constexpr size_t HELLO_ACK_LEN = 20;
void encodeHelloAck(uint8_t fwMajor, uint8_t fwMinor, uint8_t caps, const char* name, uint8_t* out);

bool validNodeName(const uint8_t* s, size_t n);                            // 1..15 of [A-Za-z0-9_.-]

// little endian helpers
inline void putLE16(uint8_t* p, uint16_t v) { p[0] = uint8_t(v); p[1] = uint8_t(v >> 8); }
inline void putLE32(uint8_t* p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = uint8_t(v >> (8 * i)); }
inline void putLE64(uint8_t* p, uint64_t v) { for (int i = 0; i < 8; i++) p[i] = uint8_t(v >> (8 * i)); }
inline uint16_t getLE16(const uint8_t* p) { return uint16_t(p[0] | (p[1] << 8)); }
inline uint32_t getLE32(const uint8_t* p) { return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24); }
inline uint64_t getLE64(const uint8_t* p) { uint64_t v = 0; for (int i = 7; i >= 0; i--) v = (v << 8) | p[i]; return v; }

}  // namespace autosim
