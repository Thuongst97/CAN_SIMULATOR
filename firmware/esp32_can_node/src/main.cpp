// PlatformIO entry point: the same library the Arduino IDE uses (lib/AutoSimCanNode), configured from optional
// -D build flags in platformio.ini, e.g.  build_flags = -DCS_PIN=9 -DINT_PIN=14 -DOSC_FREQ=20 -DLED_PIN=38
// Without flags the chip's defaults apply (see AutoSimCanNode.h).
#include <AutoSimCanNode.h>

void setup() {
  autosim::BoardConfig cfg = autosim::defaultBoardConfig();
#ifdef SCK_PIN
  cfg.sck = SCK_PIN;
#endif
#ifdef MOSI_PIN
  cfg.mosi = MOSI_PIN;
#endif
#ifdef MISO_PIN
  cfg.miso = MISO_PIN;
#endif
#ifdef CS_PIN
  cfg.cs = CS_PIN;
#endif
#ifdef INT_PIN
  cfg.intPin = INT_PIN;
#endif
#ifdef OSC_FREQ
  cfg.oscMHz = OSC_FREQ;
#endif
#ifdef LED_PIN
  cfg.ledPin = LED_PIN;
#endif
#ifdef LED_RGB
  cfg.ledRgb = LED_RGB;
#endif
#ifdef LED_ACTIVE_LOW
  cfg.ledActiveLow = LED_ACTIVE_LOW;
#endif
#ifdef LED_RGB_LEVEL
  cfg.ledLevel = LED_RGB_LEVEL;
#endif
#ifdef AUTOSIM_UART_BAUD
  cfg.uartBaud = AUTOSIM_UART_BAUD;
#endif
#ifdef AUTOSIM_XSTBY
  cfg.xstby = AUTOSIM_XSTBY;
#endif
#ifdef AUTOSIM_NODE_NAME
  cfg.nodeName = AUTOSIM_NODE_NAME;
#endif
  autosim::startCanNode(cfg);
}

void loop() { vTaskDelay(portMAX_DELAY); }                 // everything runs in the node's tasks
