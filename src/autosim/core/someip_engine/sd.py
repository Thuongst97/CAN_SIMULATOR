"""SOME/IP-SD agent (transport-agnostic) and the UDP endpoints it runs on."""
from __future__ import annotations

import asyncio
import socket
import struct
import time
from typing import Callable

from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.core.someip_engine.wire import (SdEntry, SomeIpMessage, WireError, is_sd, pack_sd, unpack_sd)

Addr = tuple[str, int]


class UdpEndpoint(asyncio.DatagramProtocol):
    """Plain UDP socket on the engine loop; `handler(data, addr)` gets each datagram."""

    def __init__(self, handler: Callable[[bytes, Addr], None]) -> None:
        self.handler = handler
        self.transport: asyncio.DatagramTransport | None = None

    async def open(self, loop: asyncio.AbstractEventLoop, local: Addr | None = None,
                   sock: socket.socket | None = None) -> None:
        if sock is not None:
            await loop.create_datagram_endpoint(lambda: self, sock=sock)
        else:
            await loop.create_datagram_endpoint(lambda: self, local_addr=local)

    @property
    def local_addr(self) -> Addr:
        return self.transport.get_extra_info("sockname")[:2]

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self.handler(data, (addr[0], addr[1]))

    def error_received(self, exc) -> None:   # e.g. WinError 10054 on UDP to a closed port
        pass

    def sendto(self, data: bytes, addr: Addr) -> None:
        if self.transport and not self.transport.is_closing():
            self.transport.sendto(data, addr)

    def close(self) -> None:
        if self.transport:
            self.transport.close()


class UdpSdTransport(UdpEndpoint):
    """SD socket: bound to the SD port on all addresses, joined to the multicast group on the
    selected NIC. If the multicast join fails (e.g. loopback adapter) it degrades to
    unicast-only and `multicast_ok` is False."""

    def __init__(self, local_ip: str, port: int, group: str,
                 handler: Callable[[bytes, Addr], None]) -> None:
        super().__init__(handler)
        self.local_ip, self.port, self.group = local_ip, port, group
        self.multicast_ok = False
        self.multicast_error = ""

    async def start(self, loop) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", self.port))
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self.local_ip))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                         struct.pack("4s4s", socket.inet_aton(self.group), socket.inet_aton(self.local_ip)))
            self.multicast_ok = True
        except OSError as e:
            self.multicast_error = str(e)
        s.setblocking(False)
        await self.open(loop, sock=s)

    def send_multicast(self, data: bytes) -> None:
        if self.multicast_ok:
            self.sendto(data, (self.group, self.port))


class SdAgent:
    """Builds/parses SD datagrams, logs every entry and fans received entries out to listeners
    (ServiceProvider / ServiceConsumer objects with `handle_sd(entry, sender)`).

    Entries addressed to ourselves (own multicast, or unicast to our own SD address) are
    dispatched in-process, so a provider and a consumer in the same engine see each other
    even without a network.
    """

    def __init__(self, local_ip: str, sd_port: int, transport: UdpSdTransport | None,
                 eventbus: EventBus | None = None) -> None:
        self.addr: Addr = (local_ip, sd_port)
        self.transport = transport
        self.eventbus = eventbus
        self._listeners: list = []
        self._session = 0
        self._reboot = True

    def add_listener(self, l) -> None:
        if l not in self._listeners:
            self._listeners.append(l)

    def remove_listener(self, l) -> None:
        if l in self._listeners:
            self._listeners.remove(l)

    def _log(self, direction: str, e: SdEntry, peer: Addr | None) -> None:
        if self.eventbus:
            self.eventbus.publish(events.SOMEIP_SD, {
                "t": time.time(), "dir": direction, "peer": f"{peer[0]}:{peer[1]}" if peer else "multicast",
                "entry": e.describe()})

    def send(self, entries: list[SdEntry], dest: Addr | None = None) -> None:
        self._session += 1
        if self._session > 0xFFFF:
            self._session, self._reboot = 1, False
        data = pack_sd(entries, self._session, (0x80 if self._reboot else 0) | 0x40)
        for e in entries:
            self._log("tx", e, dest)
        if dest is None or dest == self.addr:
            asyncio.get_running_loop().call_soon(self._dispatch, entries, self.addr)
        if self.transport is not None:
            if dest is None:
                self.transport.send_multicast(data)
            elif dest != self.addr:
                self.transport.sendto(data, dest)

    def on_datagram(self, data: bytes, addr: Addr) -> None:
        if addr == self.addr:
            return                                  # our own multicast echo; already dispatched locally
        try:
            msg = SomeIpMessage.unpack(data)
            if not is_sd(msg):
                return
            entries = unpack_sd(msg)
        except WireError as e:
            if self.eventbus:
                self.eventbus.publish(events.LOG, f"SD: dropped malformed datagram from {addr[0]}: {e}")
            return
        for e in entries:
            self._log("rx", e, addr)
        self._dispatch(entries, addr)

    def _dispatch(self, entries: list[SdEntry], sender: Addr) -> None:
        for e in entries:
            for l in list(self._listeners):
                try:
                    l.handle_sd(e, sender)
                except Exception as ex:
                    if self.eventbus:
                        self.eventbus.publish(events.LOG, f"SD listener error: {ex!r}")
