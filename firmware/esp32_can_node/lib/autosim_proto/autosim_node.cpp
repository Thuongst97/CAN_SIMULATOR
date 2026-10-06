#include "autosim_node.h"

#include <stdio.h>
#include <string.h>

namespace autosim {

Node::Node(const Env& env, const char* name, uint8_t fwMajor, uint8_t fwMinor)
    : env_(env), fwMajor_(fwMajor), fwMinor_(fwMinor) {
  memset(name_, 0, sizeof(name_));
  strncpy(name_, name ? name : "node", NAME_MAX_CHARS);
}

void Node::ack(const Packet& p) {
  const uint8_t b = p.type;
  env_.sink->send(MSG_ACK, p.seq, &b, 1);
}

void Node::nack(uint8_t type, uint8_t seq, uint8_t code) {
  const uint8_t b[2] = {type, code};
  env_.sink->send(MSG_NACK, seq, b, 2);
}

void Node::error(uint8_t code, const char* text) {
  uint8_t b[1 + 90];                              // fits an OutMsg (96 bytes)
  b[0] = code;
  size_t n = 0;
  while (text && text[n] && n < 90) { b[1 + n] = uint8_t(text[n]); n++; }
  env_.sink->send(MSG_ERROR, 0, b, 1 + n);
}

void Node::onBadPacket(DecodeStatus why) {
  err_++;
  // Tell the host once per bad packet; it cannot know the seq, so it is reported with seq 0.
  const uint8_t code = why == DecodeStatus::BadCrc ? ERR_BAD_CRC : ERR_BAD_LEN;
  error(code, why == DecodeStatus::BadCrc ? "crc" : "framing");
}

// ---------------------------------------------------------------------------------------------
// link task
// ---------------------------------------------------------------------------------------------
void Node::onPacket(const Packet& p) {
  switch (p.type) {
    case MSG_HELLO: {
      uint8_t out[HELLO_ACK_LEN];
      encodeHelloAck(fwMajor_, fwMinor_, CAP_FD | CAP_HW_TIMESTAMP, name_, out);
      env_.sink->send(MSG_HELLO_ACK, p.seq, out, sizeof(out));
      break;
    }
    case MSG_PING: {
      env_.sink->send(MSG_PONG, p.seq, p.payload, p.len < 4 ? p.len : 4);
      break;
    }
    case MSG_SET_NAME: {
      if (!validNodeName(p.payload, p.len)) { nack(p.type, p.seq, ERR_BAD_CONFIG); break; }
      char tmp[NAME_FIELD_LEN] = {0};
      memcpy(tmp, p.payload, p.len);
      if (env_.names && !env_.names->save(tmp)) { nack(p.type, p.seq, ERR_STORAGE); break; }
      memcpy(name_, tmp, sizeof(name_));
      ack(p);
      break;
    }
    case MSG_CONFIG:
    case MSG_START:
    case MSG_STOP:
      forwardControl(p);
      break;
    case MSG_TX_FRAME:
      handleTx(p);
      break;
    case MSG_TX_BATCH:
      handleTxBatch(p);
      break;
    default:
      nack(p.type, p.seq, ERR_UNSUPPORTED);
      break;
  }
}

void Node::forwardControl(const Packet& p) {
  ControlCmd c;
  if (p.len > sizeof(c.payload)) { nack(p.type, p.seq, ERR_BAD_LEN); return; }
  c.type = p.type;
  c.seq = p.seq;
  c.len = p.len;
  if (p.len) memcpy(c.payload, p.payload, p.len);
  if (!env_.ctl->submit(c)) nack(p.type, p.seq, ERR_BUSY);
}

// Validates a host frame and hands it to the CAN task. Returns 0 or an ErrorCode.
uint8_t Node::acceptFrame(CanFrame& f) {
  if (!running_.load()) return ERR_NOT_STARTED;
  const bool fd = f.isFd();
  const uint32_t maxId = f.isExt() ? 0x1FFFFFFFu : 0x7FFu;
  if (f.id > maxId) return ERR_BAD_LEN;
  if (fd && !fdEnabled_.load()) return ERR_UNSUPPORTED;      // controller runs in classic mode
  if (f.isRtr()) {
    if (fd || f.len > 8) return ERR_BAD_LEN;                 // remote frames are classic only
  } else if (!fd) {
    if (f.len > 8) return ERR_BAD_LEN;
  } else {
    const uint8_t n = nextValidFdLength(f.len);              // FD lengths: pad with zeros up to 12,16,20,24,32,48,64
    if (n == 0xFF) return ERR_BAD_LEN;
    for (uint8_t i = f.len; i < n; i++) f.data[i] = 0;
    f.len = n;
  }
  return env_.tx->queue(f) ? 0 : ERR_BUSY;
}

void Node::handleTx(const Packet& p) {
  CanFrame f;
  if (!decodeCanPayload(p.payload, p.len, f)) { nack(p.type, p.seq, ERR_BAD_LEN); return; }
  const uint8_t rc = acceptFrame(f);
  if (rc) {
    if (rc == ERR_BUSY) dropped_++;
    nack(p.type, p.seq, rc);
  }
}

// TX_BATCH: u8 n, then n x (u8 len, CAN payload). One NACK (first error) if anything was rejected.
void Node::handleTxBatch(const Packet& p) {
  if (p.len < 1) { nack(p.type, p.seq, ERR_BAD_LEN); return; }
  size_t pos = 1;
  uint8_t firstErr = 0;
  for (uint8_t i = 0; i < p.payload[0]; i++) {
    if (pos >= p.len) { firstErr = firstErr ? firstErr : uint8_t(ERR_BAD_LEN); break; }
    const size_t n = p.payload[pos++];
    CanFrame f;
    if (pos + n > p.len || !decodeCanPayload(p.payload + pos, n, f)) {
      firstErr = firstErr ? firstErr : uint8_t(ERR_BAD_LEN);
      break;
    }
    pos += n;
    const uint8_t rc = acceptFrame(f);
    if (rc == ERR_BUSY) dropped_++;
    if (rc && !firstErr) firstErr = rc;
  }
  if (firstErr) nack(p.type, p.seq, firstErr);
}

void Node::tickWatchdog(uint32_t nowMs, uint32_t timeoutMs) {
  if (!running_.load()) return;
  if (uint32_t(nowMs - lastHostMs_.load()) <= timeoutMs) return;
  ControlCmd stop;
  stop.type = MSG_STOP;
  stop.seq = 0;
  stop.internal = true;
  if (env_.ctl->submit(stop)) error(ERR_NOT_STARTED, "host silent: CAN stopped");
  lastHostMs_.store(nowMs);                         // do not retrigger every tick
}

// ---------------------------------------------------------------------------------------------
// CAN task
// ---------------------------------------------------------------------------------------------
void Node::executeControl(const ControlCmd& c) {
  Packet p;                                         // only type/seq are used by ack()/nack()
  p.type = c.type;
  p.seq = c.seq;
  switch (c.type) {
    case MSG_CONFIG: {
      if (running_.load()) { nack(c.type, c.seq, ERR_BUSY); return; }       // STOP first
      CanConfig cfg;
      uint8_t factor = 1;
      if (!decodeConfig(c.payload, c.len, cfg)) { nack(c.type, c.seq, ERR_BAD_LEN); return; }
      if (const char* why = validateConfig(cfg, factor)) {
        error(ERR_BAD_CONFIG, why);
        nack(c.type, c.seq, ERR_BAD_CONFIG);
        return;
      }
      cfg_ = cfg;
      configured_ = true;
      env_.can->configure(cfg, factor);
      ack(p);
      break;
    }
    case MSG_START: {
      if (running_.load()) { ack(p); return; }                              // idempotent
      if (!configured_) {                                                   // sensible defaults: 500k / 2M FD
        uint8_t factor = 1;
        validateConfig(cfg_, factor);
        env_.can->configure(cfg_, factor);
        configured_ = true;
      }
      char err[96] = {0};
      if (!env_.can->start(err, sizeof(err))) {
        error(ERR_MCP_FAULT, err[0] ? err : "MCP2518FD start failed");
        nack(c.type, c.seq, ERR_MCP_FAULT);
        return;
      }
      fdEnabled_.store(cfg_.fd());
      running_.store(true);
      ack(p);
      break;
    }
    case MSG_STOP: {
      env_.can->stop();
      running_.store(false);
      if (!c.internal) ack(p);
      break;
    }
    default:
      nack(c.type, c.seq, ERR_UNSUPPORTED);
  }
}

void Node::emitStatus(uint8_t tec, uint8_t rec, uint8_t busFlags) {
  NodeStatus s;
  s.state = running_.load() ? 1 : 0;
  if (busFlags & 0x02) s.state = 2;                                          // bus-off => fault
  s.tec = tec;
  s.rec = rec;
  s.busFlags = busFlags;
  s.rx = rx_.load();
  s.tx = tx_.load();
  s.err = err_.load();
  s.dropped = dropped_.load();
  uint8_t out[STATUS_LEN];
  encodeStatus(s, out);
  env_.sink->send(MSG_STATUS, 0, out, sizeof(out));
}

}  // namespace autosim
