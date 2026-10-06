// Node command logic, independent of FreeRTOS / the MCP2518FD so it can be tested on the PC.
//
// Two entry points mirror the two firmware tasks:
//   * link task  (core 0):  Node::onPacket()        parses host packets, answers HELLO/PING/SET_NAME,
//                           queues TX frames, forwards CONFIG/START/STOP to the CAN task
//   * CAN task   (core 1):  Node::executeControl()  runs CONFIG/START/STOP against the controller
//                           Node::emitStatus()      1 Hz STATUS
// All hardware access goes through the small interfaces below.
#pragma once
#include <atomic>

#include "autosim_protocol.h"

namespace autosim {

// Everything the node sends to the host goes through this (a FreeRTOS queue on the target).
struct ISink {
  virtual ~ISink() {}
  virtual bool send(uint8_t type, uint8_t seq, const uint8_t* payload, size_t len) = 0;
};

// Frames for the CAN bus (txQueue on the target). Returns false when full.
struct ITxPort {
  virtual ~ITxPort() {}
  virtual bool queue(const CanFrame& f) = 0;
};

// A command for the CAN task.
struct ControlCmd {
  uint8_t type = 0;
  uint8_t seq = 0;
  uint16_t len = 0;
  bool internal = false;          // issued by the firmware itself (watchdog): no ACK to the host
  uint8_t payload[16] = {0};
};
struct IControlPort {
  virtual ~IControlPort() {}
  virtual bool submit(const ControlCmd& c) = 0;   // false when the CAN task cannot take it
};

struct ICanController {
  virtual ~ICanController() {}
  // Remember the settings (validated by the caller). Nothing touches the bus yet.
  virtual void configure(const CanConfig& cfg, uint8_t dataFactor) = 0;
  // Initialise the MCP2518FD and enter the requested mode. On failure fill `err` and return false.
  virtual bool start(char* err, size_t cap) = 0;
  virtual void stop() = 0;
};

struct INameStore {
  virtual ~INameStore() {}
  virtual bool save(const char* name) = 0;
};

class Node {
 public:
  struct Env {
    ISink* sink = nullptr;
    ITxPort* tx = nullptr;
    IControlPort* ctl = nullptr;
    ICanController* can = nullptr;
    INameStore* names = nullptr;
  };

  Node(const Env& env, const char* name, uint8_t fwMajor, uint8_t fwMinor);

  // ---- link task ----
  void onPacket(const Packet& p);
  void onBadPacket(DecodeStatus why);
  // Host silent for too long while running -> stop the controller (watchdog). Call periodically.
  void tickWatchdog(uint32_t nowMs, uint32_t timeoutMs = 5000);
  const char* name() const { return name_; }
  bool running() const { return running_.load(); }

  // ---- CAN task ----
  void executeControl(const ControlCmd& c);
  void emitStatus(uint8_t tec, uint8_t rec, uint8_t busFlags);

  // counters, updated by the tasks
  void countRx() { rx_++; }
  void countTx() { tx_++; }
  void countError() { err_++; }
  void countDropped() { dropped_++; }
  uint32_t rxCount() const { return rx_.load(); }
  uint32_t txCount() const { return tx_.load(); }
  uint32_t droppedCount() const { return dropped_.load(); }
  uint32_t errorCount() const { return err_.load(); }

  void notifyHostActivity(uint32_t nowMs) { lastHostMs_.store(nowMs); }

 private:
  void ack(const Packet& p);
  void nack(uint8_t type, uint8_t seq, uint8_t code);
  void error(uint8_t code, const char* text);
  uint8_t acceptFrame(CanFrame& f);              // 0 = queued, else ErrorCode
  void handleTx(const Packet& p);
  void handleTxBatch(const Packet& p);
  void forwardControl(const Packet& p);

  Env env_;
  char name_[NAME_FIELD_LEN];
  uint8_t fwMajor_, fwMinor_;
  std::atomic<bool> running_{false};
  std::atomic<bool> fdEnabled_{true};
  std::atomic<uint32_t> lastHostMs_{0};
  std::atomic<uint32_t> rx_{0}, tx_{0}, err_{0}, dropped_{0};
  CanConfig cfg_;                                  // CAN task only
  bool configured_ = false;                        // CAN task only
};

}  // namespace autosim
