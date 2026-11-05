"""Reference ``autosim-bridge`` in Python (same protocol as the C daemon in bridge/autosim-bridge).

Uses:
  * Linux PC with a vcan interface (no Android needed)::

        sudo ip link add dev vcan0 type vcan && sudo ip link set vcan0 mtu 72 up
        python -m autosim.hardware.bridge.sim_bridge -i vcan0
        # in AutoSim: port bridge://127.0.0.1:29536 ; candump / cansend vcan0 play "VHAL"

  * No CAN at all (Windows too): ``--demo`` replaces the interface with a fake VHAL that sends id 0x21 with
    data 10 then 20 every second and prints every frame AutoSim answers::

        python -m autosim.hardware.bridge.sim_bridge --demo

  * Tests: ``MemoryCanPort`` lets a test play the Android side (inject frames, see what AutoSim transmitted).
"""
from __future__ import annotations

import argparse
import select
import socket
import sys
import threading
import time
from collections.abc import Callable

from autosim.core.common.frame import CanFrame
from autosim.hardware.bridge import protocol as P

SOFTWARE = "autosim-bridge-py 1.0"
MAX_CLIENTS = 4


class CanPort:
    """The CAN side of the bridge."""
    name = "can"
    fd_capable = True

    def set_rx(self, cb: Callable[[CanFrame], None]) -> None:
        self._rx = cb

    def write(self, frame: CanFrame) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class MemoryCanPort(CanPort):
    """In-memory interface: `inject()` = a frame written by Android (VHAL); `written` / `on_write` = frames the
    bridge put on the interface (what Android would read)."""

    def __init__(self, name: str = "vcan0", fd_capable: bool = True) -> None:
        self.name, self.fd_capable = name, fd_capable
        self.written: list[CanFrame] = []
        self.on_write: Callable[[CanFrame], None] = lambda f: None
        self._rx: Callable[[CanFrame], None] = lambda f: None
        self._cv = threading.Condition()

    def inject(self, frame: CanFrame) -> None:
        self._rx(frame)

    def write(self, frame: CanFrame) -> None:
        if frame.is_fd and not self.fd_capable:
            raise OSError(22, "Invalid argument (CAN FD frame on a classic CAN interface)")
        with self._cv:
            self.written.append(frame)
            self._cv.notify_all()
        self.on_write(frame)

    def wait_written(self, n: int, timeout: float = 2.0) -> bool:
        end = time.monotonic() + timeout
        with self._cv:
            while len(self.written) < n:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._cv.wait(left)
        return True


class SocketCanPort(CanPort):
    """A real SocketCAN interface (Linux) through python-can."""

    def __init__(self, ifname: str) -> None:
        import can
        self.name = ifname
        self._bus = can.Bus(interface="socketcan", channel=ifname, fd=True)
        self._stop = threading.Event()
        self._rx: Callable[[CanFrame], None] = lambda f: None
        self._t = threading.Thread(target=self._loop, name="socketcan-rx", daemon=True)
        self._t.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            m = self._bus.recv(0.1)
            if m is None or m.is_error_frame:
                continue
            self._rx(CanFrame(m.arbitration_id, bytes(m.data), m.is_extended_id, bool(m.is_fd),
                              bool(m.bitrate_switch)))

    def write(self, frame: CanFrame) -> None:
        import can
        self._bus.send(can.Message(arbitration_id=frame.arbitration_id, data=frame.data,
                                   is_extended_id=frame.is_extended, is_fd=frame.is_fd,
                                   bitrate_switch=frame.bitrate_switch))

    def close(self) -> None:
        self._stop.set()
        self._t.join(1)
        self._bus.shutdown()


class _Client:
    def __init__(self, sock: socket.socket, addr) -> None:
        self.sock, self.addr = sock, addr
        self.parser = P.PacketParser()
        self.last_rx = time.monotonic()
        self.lock = threading.Lock()

    def send(self, data: bytes) -> None:
        with self.lock:
            self.sock.sendall(data)


class BridgeServer:
    """TCP server side of ABP. Several AutoSim clients may connect; every frame read from the interface goes to
    all of them, frames from any client are written to the interface."""

    def __init__(self, port: CanPort, host: str = "127.0.0.1", tcp_port: int = P.DEFAULT_PORT,
                 status_period_s: float = 1.0, idle_timeout_s: float = 10.0, log=print) -> None:
        self.can = port
        self.status = P.BridgeStatus(can_up=True)
        self.status_period_s, self.idle_timeout_s, self.log = status_period_s, idle_timeout_s, log
        self._srv = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, tcp_port))
        self._srv.listen(4)
        self.address = self._srv.getsockname()[:2]
        self._clients: list[_Client] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        port.set_rx(self._from_can)

    @property
    def clients(self) -> int:
        with self._lock:
            return len(self._clients)

    def start(self) -> BridgeServer:
        for fn, name in ((self._accept_loop, "abp-accept"), (self._status_loop, "abp-status")):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass
        with self._lock:
            clients, self._clients = self._clients, []
        for c in clients:
            self._close(c)
        for t in self._threads:
            t.join(2)
        self.can.close()

    def kick_all(self) -> None:
        """Drop every client (tests: simulate a bridge restart / lost link)."""
        with self._lock:
            clients, self._clients = self._clients, []
        for c in clients:
            self._close(c)

    # ---- CAN -> clients ----------------------------------------------------------------------------
    def _from_can(self, frame: CanFrame) -> None:
        self.status.rx += 1
        with self._lock:
            clients = list(self._clients)
        if not clients:
            self.status.dropped += 1
            return
        pkt = P.encode_frame(frame, int(time.time() * 1e6))
        for c in clients:
            try:
                c.send(pkt)
            except OSError:
                self._drop(c)

    # ---- clients -> CAN ----------------------------------------------------------------------------
    def _handle(self, c: _Client, ptype: int, payload: bytes) -> None:
        if ptype == P.T_FRAME:
            try:
                frame, _f, _ts = P.decode_frame(payload)
            except P.ProtocolError as e:
                c.send(P.encode_error(P.E_BAD_FRAME, str(e)))
                return
            if frame.is_fd and not self.can.fd_capable:
                self.status.tx_errors += 1
                c.send(P.encode_error(P.E_BAD_FRAME, f"0x{frame.arbitration_id:X}: CAN FD frame but "
                                                     f"{self.can.name} is a classic CAN interface"))
                return
            try:
                self.can.write(frame)
                self.status.tx += 1
            except OSError as e:
                self.status.tx_errors += 1
                c.send(P.encode_error(P.E_CAN_WRITE, f"write {self.can.name}: {e}"))
        elif ptype == P.T_PING:
            c.send(P.packet(P.T_PONG, payload))
        elif ptype == P.T_HELLO:
            info = P.decode_hello(payload)
            self.log(f"client {c.addr[0]}:{c.addr[1]}: {info['name']} ({info['software']})")

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                r, _, _ = select.select([self._srv], [], [], 0.2)
                if not r:
                    continue
                sock, addr = self._srv.accept()
            except OSError:
                return
            if self.clients >= MAX_CLIENTS:
                sock.close()
                continue
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            c = _Client(sock, addr)
            try:
                c.send(P.encode_hello(self.can.name, SOFTWARE, P.CAP_FD if self.can.fd_capable else 0))
            except OSError:
                sock.close()
                continue
            with self._lock:
                self._clients.append(c)
            self.log(f"client connected: {addr[0]}:{addr[1]}")
            t = threading.Thread(target=self._client_loop, args=(c,), name="abp-client", daemon=True)
            t.start()

    def _client_loop(self, c: _Client) -> None:
        while not self._stop.is_set():
            try:
                data = c.sock.recv(65536)
            except OSError:
                break
            if not data:
                break
            c.last_rx = time.monotonic()
            try:
                for ptype, payload in c.parser.feed(data):
                    self._handle(c, ptype, payload)
            except P.ProtocolError as e:
                try:
                    c.send(P.encode_error(P.E_PROTOCOL, str(e)))
                except OSError:
                    pass
                break
            except OSError:
                break
        self._drop(c)

    def _drop(self, c: _Client) -> None:
        with self._lock:
            if c not in self._clients:
                return
            self._clients.remove(c)
        self._close(c)
        self.log(f"client disconnected: {c.addr[0]}:{c.addr[1]}")

    @staticmethod
    def _close(c: _Client) -> None:
        try:
            c.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        c.sock.close()

    def _status_loop(self) -> None:
        while not self._stop.wait(self.status_period_s):
            with self._lock:
                clients = list(self._clients)
            pkt = P.encode_status(self.status)
            for c in clients:
                if time.monotonic() - c.last_rx > self.idle_timeout_s:
                    self._drop(c)                       # AutoSim pings every second: this one is gone
                    continue
                try:
                    c.send(pkt)
                except OSError:
                    self._drop(c)


# ---- command line ------------------------------------------------------------------------------------------
def _demo_vhal(port: MemoryCanPort, stop: threading.Event, period: float) -> None:
    values = (10, 20)
    i = 0
    while not stop.wait(period):
        v = values[i % len(values)]
        i += 1
        port.inject(CanFrame(0x21, bytes([v]) + bytes(7)))
        print(f"VHAL  -> 0x021  {v:3d}   [{bytes([v]).hex()} 00 00 00 00 00 00 00]")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AutoSim bridge (Python reference): SocketCAN <-> TCP")
    ap.add_argument("-i", "--interface", default="vcan0", help="SocketCAN interface (default vcan0)")
    ap.add_argument("-b", "--bind", default="127.0.0.1", help="address to listen on (default 127.0.0.1; "
                                                              "0.0.0.0 = every network interface)")
    ap.add_argument("-p", "--port", type=int, default=P.DEFAULT_PORT)
    ap.add_argument("--demo", action="store_true", help="no CAN interface: a fake VHAL sends 0x21 = 10 / 20")
    ap.add_argument("--period", type=float, default=1.0, help="demo: seconds between frames")
    a = ap.parse_args(argv)
    stop = threading.Event()
    if a.demo:
        port: CanPort = MemoryCanPort("demo-vhal")
        port.on_write = lambda f: print(f"VHAL  <- 0x{f.arbitration_id:03X}  "
                                        f"{'FD ' if f.is_fd else ''}[{f.data.hex(' ')}]")
        threading.Thread(target=_demo_vhal, args=(port, stop, a.period), daemon=True).start()
    else:
        port = SocketCanPort(a.interface)
    srv = BridgeServer(port, a.bind, a.port).start()
    print(f"autosim-bridge (python) listening on {srv.address[0]}:{srv.address[1]}, interface {port.name}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
