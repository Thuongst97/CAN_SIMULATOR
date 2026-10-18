"""Show every connected CANable (slcan) adapter: COM port, USB serial number, firmware version and the latched
error register. Close AutoSim Studio first (it keeps the COM ports open).

    uv run python tools/canable_info.py

Use it before and after a firmware update (docs/CANABLE_FIRMWARE.md).
"""
from __future__ import annotations

import sys

from autosim.hardware.esp32_gateway.manager import NodeManager
from autosim.hardware.slcan import protocol as S
from autosim.hardware.slcan.node import NodeError, SlcanNode


def main() -> int:
    ports = [p for p in NodeManager().scan() if p["kind"] == "slcan"]
    if not ports:
        print("No CANable (USB 16D0:117E, slcan firmware) found. In DFU mode the board shows up as "
              "'STM32 BOOTLOADER' (USB 0483:DF11) instead - see docs/CANABLE_FIRMWARE.md.")
        return 1
    for p in ports:
        node = SlcanNode(p["port"], "info", serial_number=p["serial"])
        try:
            node.connect()
            reg = node.read_error_register()
            flags = [name for bit, name in S.ERROR_BITS.items() if reg and reg & (1 << bit)] if reg is not None else []
            print(f"{p['port']}: serial {p['serial'] or '?'}")
            print(f"    firmware : {node.info.get('fw') or '?'}")
            print(f"    error register: {'0x%02X' % reg if reg is not None else '?'}"
                  + (f" ({', '.join(flags)}; latched until the adapter is re-plugged)" if flags else ""))
        except NodeError as e:
            hint = " - close AutoSim Studio (or any terminal using the port) and retry" if "denied" in str(e) else ""
            print(f"{p['port']}: {e}{hint}")
        finally:
            node.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
