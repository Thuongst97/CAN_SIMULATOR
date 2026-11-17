// Fixed sizes and timings of the node (not user settings).
#pragma once

#define AUTOSIM_TX_QUEUE_LEN     128   // frames host -> CAN
#define AUTOSIM_RX_QUEUE_LEN     512   // frames CAN -> host (absorbs bursts while the serial link is busy)
#define AUTOSIM_CTL_QUEUE_LEN    8
#define AUTOSIM_OUT_QUEUE_LEN    32    // ACK / NACK / STATUS / ERROR / PONG ...
#define AUTOSIM_DRIVER_RX_FIFO   256   // ACAN2517FD software receive FIFO
#define AUTOSIM_DRIVER_TX_FIFO   64    // ACAN2517FD software transmit FIFO
#define AUTOSIM_LINK_STACK       6144
#define AUTOSIM_CAN_STACK        6144
#define AUTOSIM_LINK_PRIO        3
#define AUTOSIM_CAN_PRIO         5
#define AUTOSIM_WATCHDOG_MS      5000  // host silent this long while running -> CAN stopped
#define AUTOSIM_STATUS_PERIOD_MS 1000

// Host link: PlatformIO environments pass -DAUTOSIM_LINK_UART / -DAUTOSIM_LINK_USB_CDC. Otherwise (Arduino IDE)
// it follows the board's "USB CDC On Boot" menu: Enabled = native USB port, Disabled = USB-UART bridge.
#if !defined(AUTOSIM_LINK_UART) && !defined(AUTOSIM_LINK_USB_CDC)
  #if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
    #define AUTOSIM_LINK_USB_CDC 1
  #else
    #define AUTOSIM_LINK_UART 1
  #endif
#endif
