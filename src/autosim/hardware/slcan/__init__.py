"""USB-CAN FD adapters speaking slcan (CANable 2.0 firmware)."""
from __future__ import annotations

# USB VID:PID pairs of adapters that speak slcan over USB-CDC
SLCAN_USB_IDS = {
    (0x16D0, 0x117E): "CANable 2.0 (slcan)",
}


def device_kind(vid: int | None, pid: int | None) -> str:
    """'slcan' for known slcan adapters, else 'autosim' (ESP32 + MCP2518FD running the AutoSim firmware)."""
    return "slcan" if (vid, pid) in SLCAN_USB_IDS else "autosim"
