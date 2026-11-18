"""PEAK-System PCAN adapters (PCAN-USB, PCAN-USB FD, PCAN-USB Pro FD, …) through the PCAN-Basic API.

The PCAN-Basic library ships with PEAK's device driver (Windows: PCANBasic.dll; Linux: libpcanbasic.so with
PEAK's pcan driver). AutoSim does not bundle it: when it is missing the app says so and links to PEAK's download
page. Everything here goes through python-can's `pcan` interface, looked up at call time
(`can.interfaces.pcan.pcan.PCANBasic`) so tests can replace the library with a model.
"""
from __future__ import annotations

import os
import sys

PEAK_USB_VID = 0x0C72
DRIVER_URL = "https://www.peak-system.com/support/downloads/drivers/driver-packages/"
PORT_PREFIX = "PCAN_"


def is_pcan_port(port: str) -> bool:
    return port.upper().startswith(PORT_PREFIX)


def _api():
    """python-can's pcan module (PcanBus, PCANBasic): looked up at call time so tests can replace the library."""
    from can.interfaces.pcan import pcan as _pc
    return _pc


def _const():
    """PCAN-Basic constants (PCAN_ERROR_*, PCAN_CHANNEL_*, parameters)."""
    from can.interfaces.pcan import basic
    return basic


def driver_status() -> tuple[bool, str]:
    """(installed, detail). detail is the PCAN-Basic version when installed, else why it could not be loaded."""
    try:
        lib = _api().PCANBasic()
    except Exception as e:
        return False, str(e) or type(e).__name__
    try:
        k = _const()
        err, value = lib.GetValue(k.PCAN_NONEBUS, k.PCAN_API_VERSION)
        if err == k.PCAN_ERROR_OK:
            return True, value.decode("ascii", "replace") if isinstance(value, bytes) else str(value)
    except Exception:
        pass
    return True, ""


def driver_missing_text(detail: str = "") -> str:
    return ("PEAK driver (PCAN-Basic) not installed - PCAN adapters need it. Download: " + DRIVER_URL
            + (f"  ({detail})" if detail else ""))


def scan_channels() -> list[dict]:
    """PCAN channels with an adapter plugged in: [{port, label, supports_fd, device_id, condition, hwid}].
    Empty when the driver is missing."""
    ok, _ = driver_status()
    if not ok:
        return []
    k = _const()
    try:
        configs = _api().PcanBus._detect_available_configs()
    except Exception:
        return []
    out = []
    for c in configs:
        cond = c.get("channel_condition", k.PCAN_CHANNEL_AVAILABLE)
        if not cond:                                          # PCAN_CHANNEL_UNAVAILABLE
            continue
        name = c.get("device_name") or ("PCAN-USB FD" if c.get("supports_fd") else "PCAN")
        dev_id = c.get("device_id")
        busy = cond & k.PCAN_CHANNEL_OCCUPIED and not cond & k.PCAN_CHANNEL_AVAILABLE
        out.append({"port": c["channel"], "label": name, "supports_fd": bool(c.get("supports_fd")),
                    "device_id": dev_id, "occupied": bool(busy),
                    "hwid": f"PEAK {name}" + (f", device id {dev_id:#x}" if dev_id is not None else "")
                            + (", in use by another application" if busy else "")})
    return sorted(out, key=lambda d: (d["port"].rstrip("0123456789"), int("0" + d["port"][len(d["port"].rstrip("0123456789")):])))


def peak_usb_present() -> bool:
    """Best effort: has a PEAK USB device been plugged into this PC, whether or not its driver is installed?
    Linux: plugged in now (sysfs). Windows: known to Windows (the USB enumeration registry keeps devices seen
    before)."""
    try:
        if sys.platform.startswith("linux"):
            base = "/sys/bus/usb/devices"
            for d in os.listdir(base):
                try:
                    with open(os.path.join(base, d, "idVendor")) as f:
                        if int(f.read().strip(), 16) == PEAK_USB_VID:
                            return True
                except OSError:
                    continue
        elif sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB") as k:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    if name.upper().startswith(f"VID_{PEAK_USB_VID:04X}"):
                        return True
                    i += 1
    except Exception:
        pass
    return False
