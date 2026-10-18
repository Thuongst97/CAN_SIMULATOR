> **Note (2026-10-18):** the ESP32 + MCP2518FD firmware was removed from the project (kept in
> git history, commit `fc1f393`). This protocol is still what the app's built-in simulated
> nodes (SIM1, SIM2) speak; real hardware is the CANable 2.0 (slcan) - see CANABLE_FIRMWARE.md.

# ESP32 + MCP2518FD Serial Protocol (v1)

Reference implementation: `src/autosim/hardware/esp32_gateway/protocol.py` (tested in `tests/test_protocol.py`).

## Transport
- UART at **921 600 baud** 8N1 by default (`AUTOSIM_UART_BAUD` in the firmware, `AUTOSIM_BAUD` env var in the app) or native USB-CDC (baud ignored). One node = one COM port = one logical CAN channel.
- Framing: **COBS** encoded packet followed by a `0x00` delimiter. Receiver resyncs on any `0x00`.
- ESP32 SPI to MCP2518FD at 20 MHz (SPI mode 0), INT pin driven; RX/TX FIFOs in the MCP2518FD, ring buffers in ESP32 RAM.

## Packet (before COBS)
| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 1 | type | `MsgType` |
| 1 | 1 | seq | sender counter, wraps; ACK/NACK echo it |
| 2 | 2 | len | payload length, LE |
| 4 | len | payload | |
| 4+len | 2 | crc16 | CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over bytes 0..3+len, LE |

Max payload 512 B.

## Message types
| Dir | Type | Code | Payload |
|---|---|---|---|
| PC→N | HELLO | 0x01 | `u8 proto_ver` |
| PC→N | CONFIG | 0x02 | see below |
| PC→N | START / STOP | 0x03 / 0x04 | empty (START = "connect": initialise the MCP2518FD; STOP = "disconnect") |
| PC→N | SET_NAME | 0x05 | ASCII node name, 1-15 chars of `A-Z a-z 0-9 _ . -`; stored in the node's flash; answers ACK / NACK |
| PC→N | TX_FRAME | 0x10 | CAN payload (ts = 0) |
| PC→N | TX_BATCH | 0x11 | `u8 n` + n × (`u8 len` + CAN payload) |
| PC→N | PING | 0x20 | `u32 token` |
| N→PC | HELLO_ACK | 0x81 | `u8 proto_ver, u8 fw_major, u8 fw_minor, u8 caps, char[16] node_id` (caps: b0 FD, b1 hw timestamp) |
| N→PC | ACK / NACK | 0x82 / 0x83 | `u8 acked_type`; NACK adds `u8 err_code` |
| N→PC | RX_FRAME | 0x90 | CAN payload with hw timestamp |
| N→PC | TX_CONFIRM | 0x91 | `u32 can_id, u64 ts_us` (optional echo, enabled by CONFIG flag) |
| N→PC | STATUS | 0xA0 | `u8 state, u8 tec, u8 rec, u8 bus_flags(b0 passive,b1 busoff), u32 rx_cnt, u32 tx_cnt, u32 err_cnt, u32 dropped` @ 1 Hz |
| N→PC | PONG | 0xA1 | `u32 token` |
| N→PC | ERROR | 0xAF | `u8 code` + ascii |

## CAN payload (TX_FRAME / RX_FRAME)
`u32 can_id | u8 flags | u8 data_len | u64 ts_us | data[data_len]` (little endian).
Flags: `0x01` extended, `0x02` FD, `0x04` BRS, `0x08` ESI, `0x10` RTR. `data_len` is bytes (0–64), not DLC code; firmware maps to DLC. FD frames whose length is not a valid CAN FD length are zero-padded by the node (9 -> 12, 13 -> 16, ... 49 -> 64); classic frames longer than 8 are rejected (`BAD_LEN`). Remote frames (classic only) send `data_len` = requested DLC and **no data bytes**.
`ts_us` = node microsecond counter. v1 host stamps frames with its own receive time (USB latency is ~1 ms); `ts_us` is reserved for future clock alignment.

## CONFIG payload
`u32 nominal_bitrate | u32 data_bitrate | u8 flags | u8 sp_nominal_pct | u8 sp_data_pct | u8 reserved`
Flags: b0 FD enable, b1 BRS default, b2 **non-ISO CRC** (clear = ISO 11898-1:2015), b3 listen-only, b4 loopback, b5 TX_CONFIRM enable (not implemented).

**Rate rule (MCP2518FD):** with FD enabled the data rate must be an integer multiple of the nominal rate with ratio 1..10 (500k/5M, 1M/8M, 250k/2M ok; 500k/8M rejected). Classic CAN ignores the data rate. The sample-point bytes are accepted but the driver picks the bit timing.

## Session
1. Host opens port, sends HELLO; node replies HELLO_ACK (retry 3× @ 200 ms, else "not an AutoSim node").
2. CONFIG → ACK. START → ACK; node enters RUNNING and streams RX_FRAME/STATUS.
3. PING every 1 s; 3 missed PONGs → host marks ERROR and reconnects. Node stops CAN after 5 s without host traffic (watchdog).
4. Control packets use request/ACK with 100 ms timeout and 3 retries; TX_FRAME is fire-and-forget (backpressure: node sends NACK `BUSY` when its TX ring is full).

## Error codes
`1 BAD_CRC, 2 BAD_LEN (malformed frame), 3 UNSUPPORTED, 4 BAD_CONFIG, 5 BUSY, 6 MCP_FAULT, 7 NOT_STARTED, 8 STORAGE (name not saved)`.

Before a NACK the node sends an `ERROR` packet (`u8 code` + ASCII reason, e.g. `data/nominal ratio must be an integer from 1 to 10`) and the host shows it. A bad packet on the wire (CRC / framing) is reported with `ERROR` and seq 0.

## Firmware
Implemented by `firmware/esp32_can_node` (see its README); `lib/autosim_proto` is the C++ twin of `protocol.py` and is cross-tested against it.

## Possible later extension
SLCAN-FD text mode (`Y`/`D` commands) could be added as a second backend so stock SLCAN firmware works; not planned for v1.
