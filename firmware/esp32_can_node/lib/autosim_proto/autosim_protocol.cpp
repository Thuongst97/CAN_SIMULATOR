#include "autosim_protocol.h"

#include <string.h>

namespace autosim {

// ---------------------------------------------------------------------------------------------
// CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no final xor ("123456789" -> 0x29B1)
// ---------------------------------------------------------------------------------------------
uint16_t crc16(const uint8_t* data, size_t n, uint16_t init) {
  uint16_t crc = init;
  for (size_t i = 0; i < n; i++) {
    crc ^= uint16_t(data[i]) << 8;
    for (int b = 0; b < 8; b++) crc = (crc & 0x8000) ? uint16_t((crc << 1) ^ 0x1021) : uint16_t(crc << 1);
  }
  return crc;
}

// ---------------------------------------------------------------------------------------------
// COBS: removes every 0x00 from the data so that 0x00 can delimit packets on the wire.
// Each block starts with a code byte = (distance to the next zero) + 1; a code of 0xFF means
// "254 data bytes follow and no zero is implied".
// ---------------------------------------------------------------------------------------------
size_t cobsEncode(const uint8_t* in, size_t n, uint8_t* out) {
  size_t read = 0, write = 1, codeIdx = 0;
  uint8_t code = 1;
  while (read < n) {
    if (in[read] == 0) {
      out[codeIdx] = code;
      code = 1;
      codeIdx = write++;
      read++;
    } else {
      out[write++] = in[read++];
      code++;
      if (code == 0xFF) {
        out[codeIdx] = code;
        code = 1;
        codeIdx = write++;
      }
    }
  }
  out[codeIdx] = code;
  return write;
}

bool cobsDecode(const uint8_t* in, size_t n, uint8_t* out, size_t cap, size_t& outLen) {
  size_t i = 0, w = 0;
  while (i < n) {
    const uint8_t code = in[i];
    if (code == 0 || i + code > n) return false;
    if (w + (code - 1) > cap) return false;
    for (uint8_t j = 1; j < code; j++) out[w++] = in[i + j];
    i += code;
    if (code < 0xFF && i < n) {
      if (w >= cap) return false;
      out[w++] = 0;
    }
  }
  outLen = w;
  return true;
}

// ---------------------------------------------------------------------------------------------
// Packets
// ---------------------------------------------------------------------------------------------
size_t encodePacket(uint8_t type, uint8_t seq, const uint8_t* payload, size_t len, uint8_t* out, size_t cap) {
  if (len > MAX_PAYLOAD) return 0;
  uint8_t body[MAX_BODY];
  body[0] = type;
  body[1] = seq;
  putLE16(body + 2, uint16_t(len));
  if (len) memcpy(body + 4, payload, len);
  putLE16(body + 4 + len, crc16(body, 4 + len));
  const size_t bodyLen = 6 + len;
  if (cap < bodyLen + bodyLen / 254 + 2) return 0;
  const size_t n = cobsEncode(body, bodyLen, out);
  out[n] = 0x00;
  return n + 1;
}

DecodeStatus decodePacket(const uint8_t* raw, size_t n, Packet& out) {
  uint8_t body[MAX_BODY];
  size_t bl = 0;
  if (!cobsDecode(raw, n, body, sizeof(body), bl)) return DecodeStatus::BadCobs;
  if (bl < 6) return DecodeStatus::TooShort;
  if (crc16(body, bl - 2) != getLE16(body + bl - 2)) return DecodeStatus::BadCrc;
  const uint16_t len = getLE16(body + 2);
  if (len != bl - 6 || len > MAX_PAYLOAD) return DecodeStatus::BadLength;
  out.type = body[0];
  out.seq = body[1];
  out.len = len;
  if (len) memcpy(out.payload, body + 4, len);
  return DecodeStatus::Ok;
}

void StreamParser::feed(uint8_t b) {
  if (b != 0x00) {
    if (n_ < sizeof(buf_)) buf_[n_++] = b;
    else overflow_ = true;                      // garbage / oversized: drop until the next delimiter
    return;
  }
  const bool had = n_ > 0 || overflow_;
  const size_t n = n_;
  const bool overflow = overflow_;
  n_ = 0;
  overflow_ = false;
  if (!had) return;                             // empty block (back-to-back delimiters)
  if (overflow) { bad_++; handler_.onBadPacket(DecodeStatus::BadLength); return; }
  const DecodeStatus st = decodePacket(buf_, n, pkt_);
  if (st == DecodeStatus::Ok) handler_.onPacket(pkt_);
  else { bad_++; handler_.onBadPacket(st); }
}

// ---------------------------------------------------------------------------------------------
// CAN payload
// ---------------------------------------------------------------------------------------------
size_t encodeCanPayload(const CanFrame& f, uint8_t* out) {
  putLE32(out, f.id);
  out[4] = f.flags;
  out[5] = f.len;
  putLE64(out + 6, f.ts_us);
  size_t n = CAN_HEADER_LEN;
  if (!f.isRtr() && f.len) {
    memcpy(out + n, f.data, f.len);
    n += f.len;
  }
  return n;
}

bool decodeCanPayload(const uint8_t* p, size_t n, CanFrame& f) {
  if (n < CAN_HEADER_LEN) return false;
  f.id = getLE32(p);
  f.flags = p[4];
  f.len = p[5];
  f.ts_us = getLE64(p + 6);
  if (f.len > CAN_MAX_DATA) return false;
  memset(f.data, 0, sizeof(f.data));
  if (f.isRtr()) return true;                   // remote frame: len is the requested DLC, no data bytes
  if (n < CAN_HEADER_LEN + f.len) return false;
  if (f.len) memcpy(f.data, p + CAN_HEADER_LEN, f.len);
  return true;
}

uint8_t nextValidFdLength(uint8_t n) {
  if (n <= 8) return n;
  if (n <= 12) return 12;
  if (n <= 16) return 16;
  if (n <= 20) return 20;
  if (n <= 24) return 24;
  if (n <= 32) return 32;
  if (n <= 48) return 48;
  if (n <= 64) return 64;
  return 0xFF;
}

// ---------------------------------------------------------------------------------------------
// CONFIG / STATUS / HELLO_ACK
// ---------------------------------------------------------------------------------------------
bool decodeConfig(const uint8_t* p, size_t n, CanConfig& c) {
  if (n < CONFIG_LEN) return false;
  c.nominal = getLE32(p);
  c.data = getLE32(p + 4);
  c.flags = p[8];
  c.spNominal = p[9];
  c.spData = p[10];
  return true;
}

void encodeConfig(const CanConfig& c, uint8_t* out) {
  putLE32(out, c.nominal);
  putLE32(out + 4, c.data);
  out[8] = c.flags;
  out[9] = c.spNominal;
  out[10] = c.spData;
  out[11] = 0;
}

const char* validateConfig(const CanConfig& c, uint8_t& dataFactor) {
  dataFactor = 1;
  if (c.nominal < 10000 || c.nominal > 1000000) return "nominal bit rate must be 10 kbit/s .. 1 Mbit/s";
  if (!c.fd()) return nullptr;                  // classic CAN: no data phase
  if (c.data < c.nominal) return "data bit rate must be >= nominal bit rate";
  if (c.data % c.nominal != 0) return "data rate must be an integer multiple of the nominal rate";
  const uint32_t factor = c.data / c.nominal;
  if (factor > 10) return "data/nominal ratio must be an integer from 1 to 10 (e.g. 500k/5M, 1M/8M)";
  dataFactor = uint8_t(factor);
  return nullptr;
}

void encodeStatus(const NodeStatus& s, uint8_t* out) {
  out[0] = s.state;
  out[1] = s.tec;
  out[2] = s.rec;
  out[3] = s.busFlags;
  putLE32(out + 4, s.rx);
  putLE32(out + 8, s.tx);
  putLE32(out + 12, s.err);
  putLE32(out + 16, s.dropped);
}

void encodeHelloAck(uint8_t fwMajor, uint8_t fwMinor, uint8_t caps, const char* name, uint8_t* out) {
  out[0] = PROTO_VERSION;
  out[1] = fwMajor;
  out[2] = fwMinor;
  out[3] = caps;
  memset(out + 4, 0, NAME_FIELD_LEN);
  size_t n = 0;
  while (name && name[n] && n < NAME_MAX_CHARS) { out[4 + n] = uint8_t(name[n]); n++; }
}

bool validNodeName(const uint8_t* s, size_t n) {
  if (n < 1 || n > NAME_MAX_CHARS) return false;
  for (size_t i = 0; i < n; i++) {
    const uint8_t c = s[i];
    const bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.';
    if (!ok) return false;
  }
  return true;
}

}  // namespace autosim
