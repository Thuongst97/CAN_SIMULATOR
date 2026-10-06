#include "can_mcp2518fd.h"

#include <ACAN2517FD.h>
#include <SPI.h>
#include <stdio.h>

#include "board_config.h"

using namespace autosim;

static ACAN2517FD gCan(CS_PIN, SPI, INT_PIN);

// Interrupt service routine for the INT pin: only gives a semaphore to the library's handler task.
static void IRAM_ATTR canIsr() { gCan.isr(); }

void McpCan::configure(const CanConfig& cfg, uint8_t dataFactor) {
  cfg_ = cfg;
  factor_ = dataFactor;
}

// ACAN2517FD::begin() returns a bit mask of problems; turn it into something a user can act on.
static void describe(uint32_t code, char* err, size_t cap) {
  const char* msg = "MCP2518FD start failed";
  if (code & ACAN2517FD::kRequestedConfigurationModeTimeOut) msg = "no answer from MCP2518FD (check SPI wiring, CS, 3V3, crystal)";
  else if (code & ACAN2517FD::kReadBackErrorWith1MHzSPIClock) msg = "SPI read-back error (check MOSI/MISO/SCK wiring)";
  else if (code & ACAN2517FD::kTooFarFromDesiredBitRate) msg = "bit rate not reachable with this oscillator";
  else if (code & ACAN2517FD::kInconsistentBitRateSettings) msg = "inconsistent bit timing";
  else if (code & ACAN2517FD::kINTPinIsNotAnInterrupt) msg = "INT pin is not interrupt capable";
  else if (code & ACAN2517FD::kX10PLLNotReadyWithin1MS) msg = "10x PLL not ready (check 4 MHz oscillator setting)";
  else if (code & ACAN2517FD::kRequestedModeTimeOut) msg = "mode switch timed out (bus / transceiver powered?)";
  snprintf(err, cap, "%s [0x%lX]", msg, (unsigned long)code);
}

bool McpCan::start(char* err, size_t cap) {
  if (begun_) stop();
  SPI.begin(SCK_PIN, MISO_PIN, MOSI_PIN);

  const ACAN2517FDSettings::Oscillator osc = (OSC_FREQ == 20) ? ACAN2517FDSettings::OSC_20MHz : ACAN2517FDSettings::OSC_40MHz;
  // data bit rate = nominal * factor (1 for classic CAN); the factor was validated by the protocol layer
  ACAN2517FDSettings settings(osc, cfg_.nominal, DataBitRateFactor(factor_));
  if (cfg_.loopback()) settings.mRequestedMode = ACAN2517FDSettings::InternalLoopBack;   // bench test without a bus
  else if (cfg_.listenOnly()) settings.mRequestedMode = ACAN2517FDSettings::ListenOnly;
  else settings.mRequestedMode = cfg_.fd() ? ACAN2517FDSettings::NormalFD : ACAN2517FDSettings::Normal20B;
  settings.mISOCRCEnabled = !cfg_.nonIso();                      // ISO 11898-1:2015 vs Bosch non-ISO CRC
  settings.mDriverReceiveFIFOSize = AUTOSIM_DRIVER_RX_FIFO;
  settings.mDriverTransmitFIFOSize = AUTOSIM_DRIVER_TX_FIFO;
  // Accept-all: begin() without a filter list lets every frame into the receive FIFO.

  if (!settings.mArbitrationBitRateClosedToDesiredRate) {
    snprintf(err, cap, "bit rate not reachable with the %d MHz oscillator", OSC_FREQ);
    return false;
  }
  const uint32_t code = gCan.begin(settings, canIsr);
  if (code != 0) {
    describe(code, err, cap);
    return false;
  }
  begun_ = true;
  return true;
}

void McpCan::stop() {
  if (begun_) {
    gCan.end();
    begun_ = false;
  }
}

bool McpCan::send(const CanFrame& f) {
  CANFDMessage m;
  m.id = f.id;
  m.ext = f.isExt();
  m.len = f.len;
  if (f.isRtr()) m.type = CANFDMessage::CAN_REMOTE;
  else if (!f.isFd()) m.type = CANFDMessage::CAN_DATA;
  else m.type = (f.flags & FLAG_BRS) ? CANFDMessage::CANFD_WITH_BIT_RATE_SWITCH : CANFDMessage::CANFD_NO_BIT_RATE_SWITCH;
  memcpy(m.data, f.data, f.len);
  return gCan.tryToSend(m);
}

bool McpCan::receive(CanFrame& out) {
  CANFDMessage m;
  if (!gCan.receive(m)) return false;
  out.id = m.id;
  out.len = m.len;
  out.flags = m.ext ? FLAG_EXT : 0;
  switch (m.type) {
    case CANFDMessage::CAN_REMOTE: out.flags |= FLAG_RTR; break;
    case CANFDMessage::CAN_DATA: break;
    case CANFDMessage::CANFD_NO_BIT_RATE_SWITCH: out.flags |= FLAG_FD; break;
    case CANFDMessage::CANFD_WITH_BIT_RATE_SWITCH: out.flags |= FLAG_FD | FLAG_BRS; break;
  }
  memset(out.data, 0, sizeof(out.data));
  if (!out.isRtr() && m.len) memcpy(out.data, m.data, m.len);
  return true;
}

// TREC register: bits 7:0 REC, 15:8 TEC, 19 RXBP, 20 TXBP (error passive), 21 TXBO (bus-off)
void McpCan::readStatus(uint8_t& tec, uint8_t& rec, uint8_t& busFlags) {
  const uint32_t trec = begun_ ? gCan.errorCounters() : 0;
  rec = uint8_t(trec & 0xFF);
  tec = uint8_t((trec >> 8) & 0xFF);
  busFlags = 0;
  if (trec & ((1UL << 19) | (1UL << 20))) busFlags |= 0x01;
  if (trec & (1UL << 21)) busFlags |= 0x02;
}

uint32_t McpCan::rxOverflows() const { return begun_ ? gCan.hardwareReceiveBufferOverflowCount() : 0; }
