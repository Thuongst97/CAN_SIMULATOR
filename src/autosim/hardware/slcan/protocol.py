"""slcan (Lawicel ASCII) codec with the CAN FD extensions of the CANable 2.0 firmware.

Reference: github.com/normaldotcom/canable2-fw (Src/slcan.c, Inc/can.h). Every command and frame is an ASCII
line terminated by '\\r'. The firmware does NOT acknowledge commands.

  O / C            open / close the channel
  S<n>             nominal rate: 0=10k 1=20k 2=50k 3=100k 4=125k 5=250k 6=500k 7=750k 8=1M 9=83.3k
  Y<n>             FD data rate: 2 = 2 Mbit/s, 5 = 5 Mbit/s (only these two)
  M<n>             0 = normal, 1 = silent;   A<n>  0/1 = automatic retransmission off/on
  V                firmware version line;    E    error register ("CANable Error Register: %X", no '\\r'!)
  t/T  iii[iiiii] L dd..   classic data frame, 11 / 29 bit id, L = DLC 0-8
  r/R  iii[iiiii] L        classic remote frame
  d/D  iii[iiiii] L dd..   CAN FD frame without bit-rate switch, L = DLC code 0-F
  b/B  iii[iiiii] L dd..   CAN FD frame with bit-rate switch
Received frames come back in exactly the same format (no timestamps).
"""
from __future__ import annotations

from autosim.core.common.frame import CanChannelConfig, CanFrame

NOMINAL_CODES = {10_000: 0, 20_000: 1, 50_000: 2, 100_000: 3, 125_000: 4, 250_000: 5, 500_000: 6,
                 750_000: 7, 1_000_000: 8, 83_333: 9}
DATA_CODES = {2_000_000: 2, 5_000_000: 5}
FD_LENGTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)        # index = DLC code
_FRAME_CHARS = set("tTrRdDbB")
# error register bits (Inc/error.h)
ERROR_BITS = {0: "peripheral init failed", 1: "USB TX busy", 2: "CAN TX failed",
              3: "CAN RX FIFO overflow", 4: "CAN TX buffer full", 5: "USB RX buffer full",
              6: "USB TX buffer full"}
ERR_CAN_TXFAIL, ERR_RX_OVERFLOW, ERR_FULLBUF_CANTX = 2, 3, 4


class SlcanError(ValueError):
    pass


def fd_length(n: int) -> int:
    """Smallest valid CAN FD payload length >= n."""
    for length in FD_LENGTHS:
        if length >= n:
            return length
    raise SlcanError(f"CAN FD frames carry at most 64 bytes (got {n})")


def config_commands(cfg: CanChannelConfig) -> list[str]:
    """Commands (without '\\r') that set up the channel before 'O'. Raises SlcanError if the CANable cannot
    do what is asked."""
    if cfg.nominal_bitrate not in NOMINAL_CODES:
        rates = ", ".join(f"{r // 1000}k" for r in sorted(NOMINAL_CODES))
        raise SlcanError(f"nominal bit rate {cfg.nominal_bitrate} not supported by the CANable (use {rates})")
    cmds = [f"S{NOMINAL_CODES[cfg.nominal_bitrate]}"]
    if cfg.fd_enabled:
        if cfg.data_bitrate not in DATA_CODES:
            raise SlcanError("the CANable 2.0 firmware supports CAN FD data rates of 2 Mbit/s and 5 Mbit/s only")
        cmds.append(f"Y{DATA_CODES[cfg.data_bitrate]}")
    if not cfg.iso_mode:
        raise SlcanError("the CANable 2.0 firmware only supports ISO CAN FD (non-ISO CRC is not available)")
    cmds.append("M1" if cfg.listen_only else "M0")
    cmds.append("A1")                                     # automatic retransmission (firmware default)
    return cmds


def encode_frame(f: CanFrame) -> bytes:
    """CanFrame -> slcan line (with '\\r'). FD payloads are zero-padded to the next valid length."""
    data = bytes(f.data)
    if f.is_fd:
        cmd = "b" if f.bitrate_switch else "d"
        n = fd_length(len(data))
        data = data + bytes(n - len(data))
        dlc = FD_LENGTHS.index(n)
    else:
        if len(data) > 8:
            raise SlcanError(f"classic CAN frames carry at most 8 bytes (got {len(data)})")
        cmd, dlc = "t", len(data)
    max_id = 0x1FFFFFFF if f.is_extended else 0x7FF
    if not 0 <= f.arbitration_id <= max_id:
        raise SlcanError(f"identifier 0x{f.arbitration_id:X} does not fit {'29' if f.is_extended else '11'} bits")
    ident = f"{f.arbitration_id:08X}" if f.is_extended else f"{f.arbitration_id:03X}"
    if f.is_extended:
        cmd = cmd.upper()
    return f"{cmd}{ident}{dlc:X}{data.hex().upper()}\r".encode("ascii")


def decode_frame(line: str) -> CanFrame | None:
    """slcan frame line (without '\\r') -> CanFrame; None if it is not a well-formed frame. The length must
    match the DLC exactly, which also keeps the error-register parser below unambiguous."""
    if not line or line[0] not in _FRAME_CHARS:
        return None
    c = line[0]
    ext = c.isupper()
    id_len = 8 if ext else 3
    try:
        ident = int(line[1:1 + id_len], 16)
        dlc = int(line[1 + id_len], 16)
    except (ValueError, IndexError):
        return None
    if len(line[1:1 + id_len]) != id_len:
        return None
    fd = c in "dDbB"
    rtr = c in "rR"
    if fd:
        nbytes = FD_LENGTHS[dlc]
    elif dlc > 8:
        return None
    else:
        nbytes = 0 if rtr else dlc
    hexdata = line[2 + id_len:]
    if len(hexdata) != 2 * nbytes:
        return None
    try:
        data = bytes.fromhex(hexdata)
    except ValueError:
        return None
    if ident > (0x1FFFFFFF if ext else 0x7FF):
        return None
    return CanFrame(ident, data, is_extended=ext, is_fd=fd, bitrate_switch=c in "bB", is_rx=True)


ERROR_PREFIX = "CANable Error Register: "


class LineSplitter:
    """Splits the device byte stream into events: ('frame', CanFrame), ('error_reg', int), ('text', str).

    The error-register reply has no terminating '\\r', so text after it may be the next frame on the same
    'line' (and frames may start with b/d/B/D, which are also hex digits). The register is at most 0x7F, so
    a 1- or 2-digit value is tried and the remainder must be empty or a complete, length-valid frame.
    """

    def __init__(self) -> None:
        self.buf = ""

    def feed(self, data: bytes, idle: bool = False) -> list[tuple[str, object]]:
        self.buf += data.decode("ascii", "replace")
        out: list[tuple[str, object]] = []
        while "\r" in self.buf:
            line, self.buf = self.buf.split("\r", 1)
            out.extend(self._line(line))
        # an error-register reply with nothing after it: complete once the stream went quiet
        if idle and self.buf.startswith(ERROR_PREFIX):
            out.extend(self._line(self.buf))
            self.buf = ""
        if len(self.buf) > 4096:                          # garbage without terminators
            self.buf = ""
        return out

    def _line(self, line: str) -> list[tuple[str, object]]:
        line = line.strip("\x07\n")
        if not line:
            return []
        if line.startswith(ERROR_PREFIX):
            tail = line[len(ERROR_PREFIX):]
            for k in (2, 1):
                head, rest = tail[:k], tail[k:]
                try:
                    reg = int(head, 16)
                except ValueError:
                    continue
                if rest == "":
                    return [("error_reg", reg)]
                frame = decode_frame(rest)
                if frame is not None:
                    return [("error_reg", reg), ("frame", frame)]
            return [("text", line)]
        frame = decode_frame(line)
        return [("frame", frame)] if frame is not None else [("text", line)]
