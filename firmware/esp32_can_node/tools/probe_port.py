"""Find out what is listening on a serial port: is the AutoSim firmware there, and at which baud rate?

    uv run python firmware/esp32_can_node/tools/probe_port.py COM4

For every common baud rate it opens the port (this usually resets an ESP32 dev board), listens for
unsolicited output (ESP32 boot log / another firmware's logs at 115200), sends HELLO and checks for a valid
HELLO_ACK. Read-only apart from the HELLO packets; nothing is written to flash.
"""
import sys
import time
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from autosim.hardware.esp32_gateway import protocol as P  # noqa: E402
from autosim.hardware.esp32_gateway.protocol import MsgType, Packet  # noqa: E402

BAUDS = (115200, 230400, 460800, 921600, 1000000, 2000000)


def probe(port: str, baud: int) -> str:
    s = serial.Serial(port, baud, timeout=0.1)
    time.sleep(0.6)                                  # opening the port resets most boards: let it boot
    quiet = s.read(4096)
    s.reset_input_buffer()
    got = b""
    for i in range(3):
        s.write(Packet(MsgType.HELLO, 10 + i, bytes([1])).encode())
        time.sleep(0.25)
        got += s.read(4096)
    s.close()
    for chunk in got.split(b"\x00"):
        try:
            pkt = Packet.decode(chunk)
        except Exception:
            continue
        if pkt.type == MsgType.HELLO_ACK:
            return f"AutoSim firmware found: {P.decode_hello_ack(pkt.payload)}"
    text = quiet.decode("ascii", "ignore")
    if "ESP-ROM" in text or "cpu_start" in text:
        first = next((ln.strip() for ln in text.replace("\r", "").split("\n") if "ESP-ROM" in ln or "Project name" in ln), "")
        return f"ESP32 boot log / other firmware running ({first[:60]!r}) - not the AutoSim firmware"
    if len(set(got)) <= 3 and len(got) > 100:
        return "line noise (the board talks at a different baud rate)"
    return "no answer"


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for baud in BAUDS:
        try:
            print(f"{baud:>8}: {probe(sys.argv[1], baud)}")
        except serial.SerialException as e:
            print(f"{baud:>8}: cannot open {sys.argv[1]}: {e}")
            break


if __name__ == "__main__":
    main()
