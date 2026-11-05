/*
 * AutoSim Bridge Protocol (ABP v1) - CAN / CAN FD frames over TCP.
 * Must stay byte-compatible with src/autosim/hardware/bridge/protocol.py.
 *
 * packet  : u8 magic 0xAB | u8 type | u16le payload length (<= ABP_MAX_PAYLOAD) | payload
 * FRAME   : u32le can id | u8 flags | u8 data length | u16 reserved | u64le timestamp us | data
 * HELLO   : u16le version | u16le caps | u8 n + name (interface) | u8 m + software version
 * STATUS  : u32le rx | u32le tx | u32le tx_errors | u32le dropped | u8 can_up
 * ERROR   : u16le code | utf-8 text
 * PING    : any payload, answered by a PONG carrying the same bytes
 */
#ifndef AUTOSIM_ABP_H
#define AUTOSIM_ABP_H

#include <stdint.h>
#include <string.h>

#define ABP_MAGIC        0xAB
#define ABP_VERSION      1
#define ABP_DEFAULT_PORT 29536
#define ABP_MAX_PAYLOAD  1024
#define ABP_HDR_SIZE     4
#define ABP_FRAME_HDR    16

enum abp_type {
    ABP_T_HELLO = 0x01,
    ABP_T_FRAME = 0x02,
    ABP_T_PING = 0x03,
    ABP_T_PONG = 0x04,
    ABP_T_STATUS = 0x05,
    ABP_T_ERROR = 0x06,
};

enum abp_flags {
    ABP_F_EXT = 0x01,
    ABP_F_FD = 0x02,
    ABP_F_BRS = 0x04,
    ABP_F_ESI = 0x08,
    ABP_F_RTR = 0x10,
};

#define ABP_CAP_FD 0x01

enum abp_error {
    ABP_E_CAN_WRITE = 1,
    ABP_E_CAN_DOWN = 2,
    ABP_E_BAD_FRAME = 3,
    ABP_E_PROTOCOL = 4,
};

static inline void abp_put16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static inline void abp_put32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i)); }
static inline void abp_put64(uint8_t *p, uint64_t v) { for (int i = 0; i < 8; i++) p[i] = (uint8_t)(v >> (8 * i)); }
static inline uint16_t abp_get16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static inline uint32_t abp_get32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* valid CAN FD payload lengths */
static inline int abp_fd_len_valid(unsigned n)
{
    return n <= 8 || n == 12 || n == 16 || n == 20 || n == 24 || n == 32 || n == 48 || n == 64;
}

/* writes header; returns total packet size */
static inline size_t abp_header(uint8_t *out, uint8_t type, size_t payload_len)
{
    out[0] = ABP_MAGIC;
    out[1] = type;
    abp_put16(out + 2, (uint16_t)payload_len);
    return ABP_HDR_SIZE + payload_len;
}

static inline size_t abp_encode_frame(uint8_t *out, uint32_t id, uint8_t flags, const uint8_t *data,
                                      uint8_t len, uint64_t ts_us)
{
    uint8_t *p = out + ABP_HDR_SIZE;
    abp_put32(p, id);
    p[4] = flags;
    p[5] = len;
    p[6] = p[7] = 0;
    abp_put64(p + 8, ts_us);
    memcpy(p + ABP_FRAME_HDR, data, len);
    return abp_header(out, ABP_T_FRAME, ABP_FRAME_HDR + len);
}

static inline size_t abp_encode_hello(uint8_t *out, uint16_t caps, const char *name, const char *software)
{
    uint8_t *p = out + ABP_HDR_SIZE;
    size_t n = strlen(name), m = strlen(software);
    if (n > 255) n = 255;
    if (m > 255) m = 255;
    abp_put16(p, ABP_VERSION);
    abp_put16(p + 2, caps);
    p[4] = (uint8_t)n;
    memcpy(p + 5, name, n);
    p[5 + n] = (uint8_t)m;
    memcpy(p + 6 + n, software, m);
    return abp_header(out, ABP_T_HELLO, 6 + n + m);
}

static inline size_t abp_encode_status(uint8_t *out, uint32_t rx, uint32_t tx, uint32_t tx_err, uint32_t dropped,
                                       int can_up)
{
    uint8_t *p = out + ABP_HDR_SIZE;
    abp_put32(p, rx);
    abp_put32(p + 4, tx);
    abp_put32(p + 8, tx_err);
    abp_put32(p + 12, dropped);
    p[16] = can_up ? 1 : 0;
    return abp_header(out, ABP_T_STATUS, 17);
}

static inline size_t abp_encode_error(uint8_t *out, uint16_t code, const char *text)
{
    uint8_t *p = out + ABP_HDR_SIZE;
    size_t n = strlen(text);
    if (n > ABP_MAX_PAYLOAD - 2) n = ABP_MAX_PAYLOAD - 2;
    abp_put16(p, code);
    memcpy(p + 2, text, n);
    return abp_header(out, ABP_T_ERROR, 2 + n);
}

#endif /* AUTOSIM_ABP_H */
