/*
 * autosim-bridge - forwards CAN / CAN FD frames between a SocketCAN interface and AutoSim Studio over TCP.
 *
 * Runs inside the Android VM of the head unit. The VHAL keeps using its AF_CAN socket; the CAN interface it
 * talks to is a vcan (normally renamed so that the VHAL still finds "can0"), and this daemon is the other end:
 *
 *     VHAL --AF_CAN--> vcan0 <--AF_CAN--> autosim-bridge <--TCP (ABP)--> AutoSim Studio (PC)
 *
 * Every frame the VHAL writes is sent to every connected AutoSim client; every frame a client sends is written
 * to the interface, where the VHAL reads it. Single-threaded poll() loop, non-blocking sockets, per-client
 * output buffer (frames are dropped and counted, never blocking the CAN side, if a client cannot keep up).
 * The interface may appear / disappear at any time: the bridge retries every second and reports it.
 *
 * Build: Android.bp (vendor), build_ndk.sh (standalone NDK binary), Makefile (Linux host; `make fake` builds
 * a test variant whose "interface" is a unix SOCK_SEQPACKET socket, used by the AutoSim test-suite).
 *
 * Usage: autosim-bridge [-i can0] [-b 127.0.0.1] [-p 29536] [-v]
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <arpa/inet.h>
#include <net/if.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/un.h>

#include <linux/can.h>
#include <linux/can/raw.h>

#include "abp.h"

#ifdef ABRIDGE_ANDROID_LOG
#include <android/log.h>
#endif

#ifndef CANFD_FDF
#define CANFD_FDF 0x04
#endif

#define SOFTWARE "autosim-bridge 1.0"
#define MAX_CLIENTS 4
#define IN_CAP (4 * (ABP_HDR_SIZE + ABP_MAX_PAYLOAD))
#define OUT_CAP (512 * 1024)
#define IDLE_TIMEOUT_MS 10000          /* AutoSim pings every second */
#define TICK_MS 1000
#define CAN_BURST 256                  /* frames read per poll wake-up */
#define MAX_ERRORS_PER_S 20            /* ERROR packets per second (all clients) */
#define TXQ_LEN 4096                   /* frames waiting for the CAN socket (EAGAIN / ENOBUFS during bursts) */

struct client {
    int fd;
    char addr[64];
    uint8_t in[IN_CAP];
    size_t in_len;
    uint8_t *out;
    size_t out_len;
    uint64_t last_rx_ms;
    int dead;
};

static struct {
    const char *ifname;
    const char *bind_addr;
    int port;
    int verbose;
    int can_fd;                        /* -1: not open */
    int fd_capable;
    int can_up;
    uint32_t rx, tx, tx_err, dropped;
    uint64_t next_retry_ms;
    char can_problem[160];             /* last reported interface problem (reported once) */
    int errors_this_second;
} g = { "can0", "127.0.0.1", ABP_DEFAULT_PORT, 0, -1, 0, 0, 0, 0, 0, 0, 0, "", 0 };

static struct client *clients[MAX_CLIENTS];

/* frames the CAN socket could not take yet (full socket buffer / tx queue): sent in order as soon as possible */
static struct {
    struct canfd_frame f[TXQ_LEN];
    size_t mtu[TXQ_LEN];
    size_t head, len;
} txq;
static volatile sig_atomic_t g_stop;

#define PRINTF_LIKE(f, a) __attribute__((format(printf, f, a)))

/* ---- logging ---------------------------------------------------------------------------------------- */
static void logmsg(int err, const char *fmt, ...) PRINTF_LIKE(2, 3);
static void logmsg(int err, const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
#ifdef ABRIDGE_ANDROID_LOG
    __android_log_print(err ? ANDROID_LOG_ERROR : ANDROID_LOG_INFO, "autosim-bridge", "%s", buf);
#endif
    fprintf(err ? stderr : stdout, "autosim-bridge: %s\n", buf);
    fflush(err ? stderr : stdout);
}

static uint64_t now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

static uint64_t wall_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u;
}

/* ---- clients: buffered non-blocking output ---------------------------------------------------------- */
static void client_flush(struct client *c)
{
    while (c->out_len && !c->dead) {
        ssize_t n = send(c->fd, c->out, c->out_len, MSG_NOSIGNAL | MSG_DONTWAIT);
        if (n > 0) {
            memmove(c->out, c->out + n, c->out_len - (size_t)n);
            c->out_len -= (size_t)n;
        } else if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
            return;
        } else {
            c->dead = 1;
        }
    }
}

/* droppable: a CAN frame (counted in `dropped` when the client is too slow); control packets are never
 * dropped silently - a client whose buffer is full of unsent data that long is dead anyway. */
static void client_send(struct client *c, const uint8_t *data, size_t n, int droppable)
{
    if (c->dead)
        return;
    if (c->out_len + n > OUT_CAP) {
        if (droppable) {
            g.dropped++;
            return;
        }
        c->dead = 1;
        return;
    }
    memcpy(c->out + c->out_len, data, n);
    c->out_len += n;
    client_flush(c);
}

static void broadcast(const uint8_t *data, size_t n, int droppable)
{
    for (int i = 0; i < MAX_CLIENTS; i++)
        if (clients[i])
            client_send(clients[i], data, n, droppable);
}

static void send_error(struct client *c, uint16_t code, const char *fmt, ...) PRINTF_LIKE(3, 4);
static void send_error(struct client *c, uint16_t code, const char *fmt, ...)
{
    char text[400];
    uint8_t pkt[ABP_HDR_SIZE + ABP_MAX_PAYLOAD];
    va_list ap;
    if (g.errors_this_second >= MAX_ERRORS_PER_S)
        return;
    g.errors_this_second++;
    va_start(ap, fmt);
    vsnprintf(text, sizeof(text), fmt, ap);
    va_end(ap);
    size_t n = abp_encode_error(pkt, code, text);
    if (c)
        client_send(c, pkt, n, 0);
    else
        broadcast(pkt, n, 0);
    if (g.verbose)
        logmsg(1, "error %u: %s", code, text);
}

static int client_count(void)
{
    int n = 0;
    for (int i = 0; i < MAX_CLIENTS; i++)
        n += clients[i] != NULL;
    return n;
}

static void client_close(int i)
{
    struct client *c = clients[i];
    if (!c)
        return;
    logmsg(0, "client %s disconnected", c->addr);
    shutdown(c->fd, SHUT_RDWR);
    close(c->fd);
    free(c->out);
    free(c);
    clients[i] = NULL;
}

/* ---- CAN interface ---------------------------------------------------------------------------------- */
static void can_problem(const char *fmt, ...) PRINTF_LIKE(1, 2);
static void can_problem(const char *fmt, ...)
{
    char text[sizeof(g.can_problem)];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof(text), fmt, ap);
    va_end(ap);
    if (strcmp(text, g.can_problem) == 0)
        return;                                    /* already reported */
    snprintf(g.can_problem, sizeof(g.can_problem), "%s", text);
    logmsg(1, "%s", text);
    send_error(NULL, ABP_E_CAN_DOWN, "%s", text);
}

static void can_close(void)
{
    if (g.can_fd >= 0)
        close(g.can_fd);
    g.can_fd = -1;
    g.can_up = 0;
    g.tx_err += (uint32_t)txq.len;           /* queued frames are lost with the interface */
    txq.head = txq.len = 0;
}

static int would_block(int e)
{
    return e == EAGAIN || e == EWOULDBLOCK || e == ENOBUFS || e == EINTR;
}

/* write queued frames until the socket refuses one; returns 0, or -1 with errno on a real error */
static int can_flush_tx(void)
{
    while (txq.len && g.can_fd >= 0) {
        ssize_t w = write(g.can_fd, &txq.f[txq.head], txq.mtu[txq.head]);
        if (w < 0 && would_block(errno))
            return 0;
        if (w != (ssize_t)txq.mtu[txq.head]) {
            int e = w < 0 ? errno : EIO;
            g.tx_err++;
            txq.head = (txq.head + 1) % TXQ_LEN;
            txq.len--;
            errno = e;
            return -1;
        }
        g.tx++;
        txq.head = (txq.head + 1) % TXQ_LEN;
        txq.len--;
    }
    return 0;
}

#ifdef ABRIDGE_FAKE_CAN
/* test build: the "interface" is a unix SOCK_SEQPACKET socket at path `ifname`; each packet is a struct
 * can_frame (CAN_MTU bytes) or struct canfd_frame (CANFD_MTU bytes), exactly like a raw CAN socket. */
static int can_open(void)
{
    struct sockaddr_un sa;
    int s = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (s < 0)
        return -1;
    memset(&sa, 0, sizeof(sa));
    sa.sun_family = AF_UNIX;
    snprintf(sa.sun_path, sizeof(sa.sun_path), "%s", g.ifname);
    if (connect(s, (struct sockaddr *)&sa, sizeof(sa)) < 0) {
        can_problem("%s: %s (interface not available, retrying)", g.ifname, strerror(errno));
        close(s);
        return -1;
    }
    g.can_fd = s;
    g.fd_capable = getenv("ABRIDGE_FAKE_CLASSIC") == NULL;
    g.can_up = 1;
    return 0;
}

static void can_refresh_flags(void) {}
#else
static int can_ifreq(int s, unsigned long req, struct ifreq *ifr)
{
    memset(ifr, 0, sizeof(*ifr));
    snprintf(ifr->ifr_name, IFNAMSIZ, "%s", g.ifname);
    return ioctl(s, req, ifr);
}

static int can_open(void)
{
    struct ifreq ifr;
    struct sockaddr_can addr;
    int on = 1;
    int s = socket(PF_CAN, SOCK_RAW | SOCK_CLOEXEC | SOCK_NONBLOCK, CAN_RAW);
    if (s < 0) {
        can_problem("socket(PF_CAN): %s (kernel without CONFIG_CAN_RAW? SELinux?)", strerror(errno));
        return -1;
    }
    if (can_ifreq(s, SIOCGIFINDEX, &ifr) < 0) {
        can_problem("%s: %s (create it: ip link add dev %s type vcan; retrying)", g.ifname, strerror(errno),
                    g.ifname);
        close(s);
        return -1;
    }
    int ifindex = ifr.ifr_ifindex;
    g.fd_capable = 0;
    if (setsockopt(s, SOL_CAN_RAW, CAN_RAW_FD_FRAMES, &on, sizeof(on)) == 0 &&
        can_ifreq(s, SIOCGIFMTU, &ifr) == 0 && ifr.ifr_mtu == (int)CANFD_MTU)
        g.fd_capable = 1;
    memset(&addr, 0, sizeof(addr));
    addr.can_family = AF_CAN;
    addr.can_ifindex = ifindex;
    if (bind(s, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        can_problem("bind %s: %s", g.ifname, strerror(errno));
        close(s);
        return -1;
    }
    g.can_fd = s;
    g.can_up = can_ifreq(s, SIOCGIFFLAGS, &ifr) == 0 && (ifr.ifr_flags & IFF_UP);
    if (!g.can_up)
        can_problem("%s is down (ip link set %s up)", g.ifname, g.ifname);
    if (!g.fd_capable)
        logmsg(0, "%s is a classic CAN interface (MTU 16): CAN FD frames will be rejected "
                  "(ip link set %s mtu 72)", g.ifname, g.ifname);
    return 0;
}

static void can_refresh_flags(void)
{
    struct ifreq ifr;
    if (g.can_fd < 0)
        return;
    if (can_ifreq(g.can_fd, SIOCGIFINDEX, &ifr) < 0) {            /* interface deleted */
        can_close();
        can_problem("%s disappeared (retrying)", g.ifname);
        return;
    }
    int up = can_ifreq(g.can_fd, SIOCGIFFLAGS, &ifr) == 0 && (ifr.ifr_flags & IFF_UP);
    if (up && !g.can_up) {
        g.can_problem[0] = 0;
        logmsg(0, "%s is up", g.ifname);
    } else if (!up && g.can_up) {
        can_problem("%s is down (ip link set %s up)", g.ifname, g.ifname);
    }
    g.can_up = up;
}
#endif

static void can_try_open(void)
{
    if (g.can_fd >= 0 || now_ms() < g.next_retry_ms)
        return;
    g.next_retry_ms = now_ms() + 1000;
    if (can_open() == 0) {
        g.can_problem[0] = 0;
        logmsg(0, "interface %s open (%s)", g.ifname, g.fd_capable ? "CAN FD" : "classic CAN");
    }
}

static void can_read_all(void)
{
    struct canfd_frame f;
    uint8_t pkt[ABP_HDR_SIZE + ABP_FRAME_HDR + 64];
    for (int i = 0; i < CAN_BURST && g.can_fd >= 0; i++) {
        ssize_t n = read(g.can_fd, &f, sizeof(f));
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)
                return;
            if (errno == ENETDOWN) {                                  /* stays bound, works again when up */
                g.can_up = 0;
                can_problem("%s is down (ip link set %s up)", g.ifname, g.ifname);
                return;
            }
            int e = errno;
            can_close();
            can_problem("read %s: %s (reopening)", g.ifname, strerror(e));
            return;
        }
        if (n == 0) {                                                 /* fake interface closed */
            can_close();
            can_problem("%s closed (reopening)", g.ifname);
            return;
        }
        if (n != (ssize_t)CAN_MTU && n != (ssize_t)CANFD_MTU)
            continue;
        if (f.can_id & CAN_ERR_FLAG)
            continue;
        if (!g.can_up) {
            g.can_up = 1;
            g.can_problem[0] = 0;
        }
        uint8_t flags = 0;
        uint32_t id;
        if (f.can_id & CAN_EFF_FLAG) {
            flags |= ABP_F_EXT;
            id = f.can_id & CAN_EFF_MASK;
        } else {
            id = f.can_id & CAN_SFF_MASK;
        }
        if (f.can_id & CAN_RTR_FLAG)
            flags |= ABP_F_RTR;
        uint8_t len = f.len;
        if (n == (ssize_t)CANFD_MTU) {
            flags |= ABP_F_FD;
            if (f.flags & CANFD_BRS)
                flags |= ABP_F_BRS;
            if (f.flags & CANFD_ESI)
                flags |= ABP_F_ESI;
            if (len > CANFD_MAX_DLEN)
                len = CANFD_MAX_DLEN;
        } else if (len > CAN_MAX_DLEN) {
            len = CAN_MAX_DLEN;
        }
        g.rx++;
        if (g.verbose)
            logmsg(0, "can -> tcp  %s%03X [%u]%s", (flags & ABP_F_EXT) ? "x" : "", id, len,
                   (flags & ABP_F_FD) ? " FD" : "");
        if (!client_count()) {
            g.dropped++;
            continue;
        }
        size_t sz = abp_encode_frame(pkt, id, flags, f.data, len, wall_us());
        broadcast(pkt, sz, 1);
    }
}

/* ---- client packets --------------------------------------------------------------------------------- */
static void handle_frame(struct client *c, const uint8_t *p, size_t n)
{
    if (n < ABP_FRAME_HDR) {
        send_error(c, ABP_E_BAD_FRAME, "short FRAME packet");
        return;
    }
    uint32_t id = abp_get32(p);
    uint8_t flags = p[4], len = p[5];
    int fd = (flags & ABP_F_FD) != 0, ext = (flags & ABP_F_EXT) != 0;
    if (n != (size_t)ABP_FRAME_HDR + len || (fd ? !abp_fd_len_valid(len) : len > CAN_MAX_DLEN)) {
        send_error(c, ABP_E_BAD_FRAME, "0x%X: invalid %s data length %u", id, fd ? "FD" : "classic", len);
        return;
    }
    if (id > (ext ? CAN_EFF_MASK : CAN_SFF_MASK)) {
        send_error(c, ABP_E_BAD_FRAME, "id 0x%X does not fit %s bits", id, ext ? "29" : "11");
        return;
    }
    if (g.can_fd < 0) {
        g.tx_err++;
        send_error(c, ABP_E_CAN_DOWN, "0x%X not sent: %s is not available", id, g.ifname);
        return;
    }
    if (fd && !g.fd_capable) {
        g.tx_err++;
        send_error(c, ABP_E_BAD_FRAME, "0x%X: CAN FD frame but %s is a classic CAN interface (MTU 16)", id,
                   g.ifname);
        return;
    }
    struct canfd_frame f;
    memset(&f, 0, sizeof(f));
    f.can_id = id | (ext ? CAN_EFF_FLAG : 0) | ((flags & ABP_F_RTR) && !fd ? CAN_RTR_FLAG : 0);
    f.len = len;
    memcpy(f.data, p + ABP_FRAME_HDR, len);
    size_t mtu = CAN_MTU;
    if (fd) {
        f.flags = CANFD_FDF | ((flags & ABP_F_BRS) ? CANFD_BRS : 0) | ((flags & ABP_F_ESI) ? CANFD_ESI : 0);
        mtu = CANFD_MTU;
    }
    if (txq.len == TXQ_LEN) {
        g.tx_err++;
        send_error(c, ABP_E_CAN_WRITE, "0x%X not sent: %s TX queue full (%d frames waiting)", id, g.ifname,
                   TXQ_LEN);
        return;
    }
    size_t tail = (txq.head + txq.len) % TXQ_LEN;
    txq.f[tail] = f;
    txq.mtu[tail] = mtu;
    txq.len++;
    if (can_flush_tx() < 0)
        send_error(c, ABP_E_CAN_WRITE, "write %s: %s", g.ifname, strerror(errno));
    if (g.verbose)
        logmsg(0, "tcp -> can  %s%03X [%u]%s", ext ? "x" : "", id, len, fd ? " FD" : "");
}

static void handle_packet(struct client *c, uint8_t type, const uint8_t *p, size_t n)
{
    uint8_t out[ABP_HDR_SIZE + ABP_MAX_PAYLOAD];
    switch (type) {
    case ABP_T_FRAME:
        handle_frame(c, p, n);
        break;
    case ABP_T_PING:
        memcpy(out + ABP_HDR_SIZE, p, n);
        client_send(c, out, abp_header(out, ABP_T_PONG, n), 0);
        break;
    case ABP_T_HELLO:
        if (n >= 5) {
            unsigned len = p[4];
            if (5 + len <= n)
                logmsg(0, "client %s is %.*s (protocol v%u)", c->addr, (int)len, (const char *)p + 5,
                       abp_get16(p));
        }
        break;
    default:                                         /* PONG, unknown types: ignored (forward compatibility) */
        break;
    }
}

static void client_read(struct client *c)
{
    for (;;) {
        ssize_t n = recv(c->fd, c->in + c->in_len, IN_CAP - c->in_len, MSG_DONTWAIT);
        if (n == 0) {
            c->dead = 1;
            return;
        }
        if (n < 0) {
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
                c->dead = 1;
            return;
        }
        c->in_len += (size_t)n;
        c->last_rx_ms = now_ms();
        size_t off = 0;
        while (c->in_len - off >= ABP_HDR_SIZE) {
            const uint8_t *h = c->in + off;
            size_t len = abp_get16(h + 2);
            if (h[0] != ABP_MAGIC || len > ABP_MAX_PAYLOAD) {
                send_error(c, ABP_E_PROTOCOL, "bad packet header (magic 0x%02X, length %zu): closing", h[0], len);
                client_flush(c);
                c->dead = 1;
                return;
            }
            if (c->in_len - off < ABP_HDR_SIZE + len)
                break;
            handle_packet(c, h[1], h + ABP_HDR_SIZE, len);
            off += ABP_HDR_SIZE + len;
        }
        memmove(c->in, c->in + off, c->in_len - off);
        c->in_len -= off;
    }
}

static void accept_client(int lfd)
{
    struct sockaddr_in sa;
    socklen_t sl = sizeof(sa);
    int fd = accept4(lfd, (struct sockaddr *)&sa, &sl, SOCK_NONBLOCK | SOCK_CLOEXEC);
    if (fd < 0)
        return;
    int slot = -1;
    for (int i = 0; i < MAX_CLIENTS; i++)
        if (!clients[i]) {
            slot = i;
            break;
        }
    struct client *c = slot < 0 ? NULL : calloc(1, sizeof(*c));
    uint8_t *out = c ? malloc(OUT_CAP) : NULL;
    if (!c || !out) {
        logmsg(1, "client refused (%s)", slot < 0 ? "too many clients" : "out of memory");
        free(c);
        close(fd);
        return;
    }
    int on = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &on, sizeof(on));
    setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &on, sizeof(on));
    c->fd = fd;
    c->out = out;
    c->last_rx_ms = now_ms();
    snprintf(c->addr, sizeof(c->addr), "%s:%u", inet_ntoa(sa.sin_addr), ntohs(sa.sin_port));
    clients[slot] = c;
    logmsg(0, "client %s connected", c->addr);
    uint8_t pkt[ABP_HDR_SIZE + ABP_MAX_PAYLOAD];
    client_send(c, pkt, abp_encode_hello(pkt, g.fd_capable ? ABP_CAP_FD : 0, g.ifname, SOFTWARE), 0);
    if (g.can_problem[0]) {                          /* tell the newcomer what is wrong right away */
        g.errors_this_second = 0;
        send_error(c, ABP_E_CAN_DOWN, "%s", g.can_problem);
    }
}

static void tick(void)
{
    uint8_t pkt[ABP_HDR_SIZE + 32];
    uint64_t t = now_ms();
    g.errors_this_second = 0;
    can_try_open();
    can_refresh_flags();
    size_t n = abp_encode_status(pkt, g.rx, g.tx, g.tx_err, g.dropped, g.can_fd >= 0 && g.can_up);
    for (int i = 0; i < MAX_CLIENTS; i++) {
        struct client *c = clients[i];
        if (!c)
            continue;
        if (t - c->last_rx_ms > IDLE_TIMEOUT_MS) {
            logmsg(1, "client %s silent for %d s: dropped", c->addr, IDLE_TIMEOUT_MS / 1000);
            c->dead = 1;
        } else {
            client_send(c, pkt, n, 0);
        }
    }
}

static int listen_socket(void)
{
    struct sockaddr_in sa;
    int on = 1;
    int s = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (s < 0) {
        logmsg(1, "socket(AF_INET): %s", strerror(errno));
        return -1;
    }
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));
    memset(&sa, 0, sizeof(sa));
    sa.sin_family = AF_INET;
    sa.sin_port = htons((uint16_t)g.port);
    if (inet_pton(AF_INET, g.bind_addr, &sa.sin_addr) != 1) {
        logmsg(1, "invalid bind address %s", g.bind_addr);
        close(s);
        return -1;
    }
    if (bind(s, (struct sockaddr *)&sa, sizeof(sa)) < 0 || listen(s, 4) < 0) {
        logmsg(1, "listen %s:%d: %s", g.bind_addr, g.port, strerror(errno));
        close(s);
        return -1;
    }
    return s;
}

static void on_signal(int sig)
{
    (void)sig;
    g_stop = 1;
}

static void usage(const char *prog)
{
    printf("usage: %s [-i IFACE] [-b ADDR] [-p PORT] [-v]\n"
           "  -i IFACE  SocketCAN interface (default can0; normally a vcan the VHAL uses instead of the real one)\n"
           "  -b ADDR   IPv4 address to listen on (default 127.0.0.1 = through adb forward; 0.0.0.0 = Ethernet)\n"
           "  -p PORT   TCP port (default %d)\n"
           "  -v        log every frame\n"
           "  -V        print the version\n", prog, ABP_DEFAULT_PORT);
}

int main(int argc, char **argv)
{
    int opt;
    while ((opt = getopt(argc, argv, "i:b:p:vVh")) != -1) {
        switch (opt) {
        case 'i': g.ifname = optarg; break;
        case 'b': g.bind_addr = optarg; break;
        case 'p': g.port = atoi(optarg); break;
        case 'v': g.verbose = 1; break;
        case 'V': printf("%s (protocol v%d)\n", SOFTWARE, ABP_VERSION); return 0;
        case 'h': usage(argv[0]); return 0;
        default: usage(argv[0]); return 2;
        }
    }
    if (g.port <= 0 || g.port > 65535) {
        logmsg(1, "invalid port %d", g.port);
        return 2;
    }
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_signal;
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);

    int lfd = listen_socket();
    if (lfd < 0)
        return 1;
    logmsg(0, "%s listening on %s:%d, interface %s", SOFTWARE, g.bind_addr, g.port, g.ifname);
    can_try_open();
    uint64_t next_tick = now_ms() + TICK_MS;

    while (!g_stop) {
        struct pollfd pfd[2 + MAX_CLIENTS];
        int idx[MAX_CLIENTS];
        int n = 0;
        pfd[n].fd = lfd;
        pfd[n++].events = POLLIN;
        int can_slot = -1;
        if (g.can_fd >= 0) {
            can_slot = n;
            pfd[n].fd = g.can_fd;
            pfd[n++].events = POLLIN | (txq.len ? POLLOUT : 0);
        }
        int first_client = n;
        for (int i = 0; i < MAX_CLIENTS; i++) {
            if (!clients[i])
                continue;
            idx[n - first_client] = i;
            pfd[n].fd = clients[i]->fd;
            pfd[n++].events = POLLIN | (clients[i]->out_len ? POLLOUT : 0);
        }
        uint64_t t = now_ms();
        int timeout = next_tick > t ? (int)(next_tick - t) : 0;
        if (txq.len && timeout > 1)
            timeout = 1;                     /* ENOBUFS does not always wake POLLOUT: retry every ms */
        int r = poll(pfd, (nfds_t)n, timeout);
        if (r < 0 && errno != EINTR) {
            logmsg(1, "poll: %s", strerror(errno));
            break;
        }
        if (r > 0) {
            if (can_slot >= 0 && (pfd[can_slot].revents & (POLLIN | POLLERR | POLLHUP)))
                can_read_all();
            for (int k = first_client; k < n; k++) {
                struct client *c = clients[idx[k - first_client]];
                if (!c)
                    continue;
                if (pfd[k].revents & (POLLIN | POLLHUP | POLLERR))
                    client_read(c);
                if (pfd[k].revents & POLLOUT)
                    client_flush(c);
            }
            if (pfd[0].revents & POLLIN)
                accept_client(lfd);
        }
        if (txq.len && can_flush_tx() < 0)
            send_error(NULL, ABP_E_CAN_WRITE, "write %s: %s", g.ifname, strerror(errno));
        if (now_ms() >= next_tick) {
            next_tick += TICK_MS;
            if (next_tick < now_ms())
                next_tick = now_ms() + TICK_MS;
            tick();
        }
        for (int i = 0; i < MAX_CLIENTS; i++)
            if (clients[i] && clients[i]->dead)
                client_close(i);
    }
    logmsg(0, "stopping");
    for (int i = 0; i < MAX_CLIENTS; i++)
        client_close(i);
    close(lfd);
    can_close();
    return 0;
}
