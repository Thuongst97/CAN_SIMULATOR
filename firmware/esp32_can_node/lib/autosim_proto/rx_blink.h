// Activity blinker: turns "a CAN frame was received" events into visible LED blinks.
//
// Every pulse() lights the LED for `onMs`; after that it stays dark for at least `offMs` so that continuous
// traffic (e.g. a 10 ms cyclic message) shows as steady blinking instead of a LED that is simply on.
// Frames arriving while the LED is lit or in its dark gap are remembered and produce the next blink.
// Hardware independent (unit tested on the PC); the caller drives the pin from the returned state.
#pragma once
#include <stdint.h>

namespace autosim {

class RxBlink {
 public:
  explicit RxBlink(uint32_t onMs = 20, uint32_t offMs = 40) : onMs_(onMs), offMs_(offMs) {}

  // A frame was received at `now` (ms). Returns true when the LED must be switched on right now.
  bool pulse(uint32_t now) {
    if (state_ == State::Ready) {
      start(now);
      return true;
    }
    pending_ = true;
    return false;
  }

  // Call periodically. Returns +1 = switch the LED on, -1 = switch it off, 0 = no change.
  int tick(uint32_t now) {
    if (state_ == State::On && now - since_ >= onMs_) {
      state_ = State::Gap;
      since_ = now;
      return -1;
    }
    if (state_ == State::Gap && now - since_ >= offMs_) {
      state_ = State::Ready;
      if (pending_) {
        start(now);
        return +1;
      }
    }
    return 0;
  }

  bool lit() const { return state_ == State::On; }
  uint32_t blinks() const { return blinks_; }

 private:
  enum class State : uint8_t { Ready, On, Gap };
  void start(uint32_t now) {
    state_ = State::On;
    since_ = now;
    pending_ = false;
    blinks_++;
  }
  uint32_t onMs_, offMs_;
  State state_ = State::Ready;
  uint32_t since_ = 0;
  bool pending_ = false;
  uint32_t blinks_ = 0;
};

}  // namespace autosim
