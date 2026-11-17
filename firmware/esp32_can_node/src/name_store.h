// Persistent node name (NVS flash). Each ESP32 gets a unique default from its MAC address so several
// nodes can be told apart in the app; the app can rename a node with SET_NAME.
#pragma once
#include <Arduino.h>
#include <Preferences.h>

#include "autosim_node.h"
#include "board_config.h"

class NvsNames : public autosim::INameStore {
 public:
  // Fills `out` (>= 16 bytes) with the stored name, else the compile-time / MAC based default.
  static void load(char* out) {
    Preferences p;
    String name;
    if (p.begin("autosim", true)) {
      name = p.getString("name", "");
      p.end();
    }
    if (name.length() == 0) {
#ifdef AUTOSIM_NODE_NAME
      name = AUTOSIM_NODE_NAME;
#else
      char buf[16];
      const uint64_t mac = ESP.getEfuseMac();                      // 6 byte factory MAC, unique per chip
      snprintf(buf, sizeof(buf), "ESP32-%02X%02X%02X", uint8_t(mac >> 24), uint8_t(mac >> 32), uint8_t(mac >> 40));
      name = buf;
#endif
    }
    strncpy(out, name.c_str(), 15);
    out[15] = 0;
  }

  bool save(const char* name) override {
    Preferences p;
    if (!p.begin("autosim", false)) return false;
    const bool ok = p.putString("name", name) > 0;
    p.end();
    return ok;
  }
};
