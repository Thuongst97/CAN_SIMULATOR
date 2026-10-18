"""Server role: offer a service (UDP and optionally TCP), accept subscriptions, answer
methods, publish events."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable

from autosim.core.common import events
from autosim.core.someip_engine.config_loader import ServiceDef
from autosim.core.someip_engine.sd import Addr, UdpEndpoint
from autosim.core.someip_engine.tcp import read_message
from autosim.core.someip_engine.wire import (ANY_INSTANCE, TCP, UDP, IPv4Option, MsgType, ReturnCode, SdEntry,
                                              SdType, SomeIpMessage, WireError)

INFINITE_TTL = 0xFFFFFF


@dataclass
class Subscriber:
    endpoint: Addr
    eventgroup_id: int
    expires: float
    proto: int = UDP


class ServiceProvider:
    """Thread-safe facade: public methods may be called from any thread.

    The UDP endpoint is always opened on svc.port. If svc.reliable is set, a TCP server is
    opened on the same port number and advertised in the offer as well; requests are accepted
    on either, and events go to each subscriber over the transport it subscribed with.
    """

    def __init__(self, engine, svc: ServiceDef, offer_ttl: int = 5, offer_period_s: float = 1.0) -> None:
        self.engine, self.svc = engine, svc
        self.offer_ttl, self.offer_period_s = offer_ttl, offer_period_s
        self.offering = False
        self.handlers: dict[int, Callable[[bytes], bytes | None]] = {}
        self._responses: dict[int, bytes] = {}
        self._subs: list[Subscriber] = []
        self._ep: UdpEndpoint | None = None
        self._tcp_server: asyncio.AbstractServer | None = None
        self._tcp_conns: dict[Addr, asyncio.StreamWriter] = {}
        self._task: asyncio.Task | None = None
        self._cyclic: dict[int, asyncio.Task] = {}
        self._session = 0

    # ---- public (any thread) ------------------------------------------------
    def start(self) -> None:
        self.engine.call(self._start())

    def stop(self) -> None:
        self.engine.call(self._stop())

    @property
    def subscribers(self) -> list[Subscriber]:
        return list(self._subs)

    def set_response(self, method_id: int, payload: bytes | None) -> None:
        """Fixed response payload for a method (None => echo the request)."""
        if payload is None:
            self._responses.pop(method_id, None)
        else:
            self._responses[method_id] = bytes(payload)

    def publish_event(self, event_id: int, payload: bytes) -> None:
        self.engine.call_soon(self._publish, event_id, bytes(payload))

    def start_cyclic(self, event_id: int, period_ms: int, supplier: Callable[[], bytes]) -> None:
        self.engine.call_soon(self._start_cyclic, event_id, period_ms, supplier)

    def stop_cyclic(self, event_id: int) -> None:
        self.engine.call_soon(self._stop_cyclic, event_id)

    # ---- loop thread --------------------------------------------------------
    def _offer_entry(self, ttl: int) -> SdEntry:
        s, ip = self.svc, self.engine.local_ip
        opts = [IPv4Option(ip, self._ep.local_addr[1], UDP)]
        if self._tcp_server is not None:
            opts.append(IPv4Option(ip, s.port, TCP))
        return SdEntry(SdType.OFFER, s.service_id, s.instance_id, s.major, ttl, minor=s.minor, options=opts)

    async def _start(self) -> None:
        if self.offering:
            return
        loop = asyncio.get_running_loop()
        self._ep = UdpEndpoint(self._on_udp)
        await self._ep.open(loop, (self.engine.local_ip, self.svc.port))
        if self.svc.reliable:
            try:
                self._tcp_server = await asyncio.start_server(self._tcp_client, self.engine.local_ip, self.svc.port)
            except OSError:
                self._ep.close()
                raise
        self.engine.agent.add_listener(self)
        self.offering = True
        self._task = asyncio.create_task(self._offer_loop())

    async def _stop(self) -> None:
        if not self.offering:
            return
        self.offering = False
        self._task.cancel()
        for ev in list(self._cyclic):
            self._stop_cyclic(ev)
        self.engine.agent.send([self._offer_entry(0)])
        self.engine.agent.remove_listener(self)
        self._ep.close()
        if self._tcp_server is not None:
            self._tcp_server.close()
            for w in list(self._tcp_conns.values()):
                w.close()
            self._tcp_conns.clear()
            self._tcp_server = None
        self._subs.clear()

    async def _offer_loop(self) -> None:
        while True:
            now = time.monotonic()
            self._subs = [s for s in self._subs if s.expires > now]
            self.engine.agent.send([self._offer_entry(self.offer_ttl)])
            await asyncio.sleep(self.offer_period_s)

    def _matches(self, e: SdEntry) -> bool:
        s = self.svc
        return (e.service_id == s.service_id and e.instance_id in (s.instance_id, ANY_INSTANCE)
                and e.major in (s.major, 0xFF))

    def handle_sd(self, e: SdEntry, sender: Addr) -> None:
        if not self.offering or not self._matches(e):
            return
        if e.type == SdType.FIND:
            self.engine.agent.send([self._offer_entry(self.offer_ttl)], sender)
        elif e.type == SdType.SUBSCRIBE:
            known = {ev.eventgroup_id for ev in self.svc.events}
            ep = next((o for o in e.options if o.proto == TCP), None) if self._tcp_server is not None else None
            ep = ep or next((o for o in e.options if o.proto == UDP), None)
            ok = ep is not None and (not known or e.eventgroup_id in known)
            self._subs = [s for s in self._subs
                          if not (ep and s.endpoint == (ep.ip, ep.port) and s.eventgroup_id == e.eventgroup_id)]
            if ok and e.ttl > 0:
                ttl = 3600 if e.ttl == INFINITE_TTL else e.ttl
                self._subs.append(Subscriber((ep.ip, ep.port), e.eventgroup_id, time.monotonic() + ttl, ep.proto))
            ack = SdEntry(SdType.SUBSCRIBE_ACK, e.service_id, e.instance_id, e.major,
                          e.ttl if ok else 0, eventgroup_id=e.eventgroup_id, counter=e.counter)
            self.engine.agent.send([ack], sender)

    # ---- requests (UDP and TCP share one handler) ----------------------------
    def _on_udp(self, data: bytes, addr: Addr) -> None:
        try:
            m = SomeIpMessage.unpack(data)
        except WireError:
            return
        self._handle_request(m, addr, lambda out: self._ep.sendto(out, addr))

    async def _tcp_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = tuple(writer.get_extra_info("peername")[:2])
        self._tcp_conns[peer] = writer
        try:
            while True:
                try:
                    m = SomeIpMessage.unpack(await read_message(reader))
                except WireError:
                    break
                self._handle_request(m, peer, writer.write)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._tcp_conns.pop(peer, None)
            self._subs = [s for s in self._subs if not (s.proto == TCP and s.endpoint == peer)]
            writer.close()

    def _handle_request(self, m: SomeIpMessage, addr: Addr, send: Callable[[bytes], None]) -> None:
        if m.service_id != self.svc.service_id or m.msg_type not in (MsgType.REQUEST, MsgType.REQUEST_NO_RETURN):
            return
        known = {md.method_id for md in self.svc.methods}
        rc, out = ReturnCode.OK, m.payload
        if m.method_id in self.handlers:
            try:
                out = self.handlers[m.method_id](m.payload) or b""
            except Exception:
                rc, out = ReturnCode.NOT_OK, b""
        elif m.method_id in self._responses:
            out = self._responses[m.method_id]
        elif known and m.method_id not in known:
            rc, out = ReturnCode.UNKNOWN_METHOD, b""
        self.engine.eventbus.publish(events.SOMEIP_METHOD, {
            "t": time.time(), "service": self.svc.name, "method_id": m.method_id,
            "from": f"{addr[0]}:{addr[1]}", "request": m.payload, "response": out, "rc": int(rc)})
        if m.msg_type == MsgType.REQUEST:
            resp = SomeIpMessage(m.service_id, m.method_id, m.client_id, m.session_id,
                                 MsgType.RESPONSE if rc == ReturnCode.OK else MsgType.ERROR,
                                 rc, out, self.svc.major)
            send(resp.pack())

    # ---- events ------------------------------------------------------------------
    def _publish(self, event_id: int, payload: bytes) -> int:
        if not self.offering:
            return 0
        groups = {ev.eventgroup_id for ev in self.svc.events if ev.event_id == event_id}
        now = time.monotonic()
        targets = {(s.endpoint, s.proto) for s in self._subs
                   if s.expires > now and (not groups or s.eventgroup_id in groups)}
        self._session = self._session % 0xFFFF + 1
        msg = SomeIpMessage(self.svc.service_id, event_id, 0, self._session,
                            MsgType.NOTIFICATION, ReturnCode.OK, payload, self.svc.major).pack()
        sent = 0
        for ep, proto in targets:
            if proto == TCP:
                w = self._tcp_conns.get(ep)
                if w is not None and not w.is_closing():
                    w.write(msg)
                    sent += 1
            else:
                self._ep.sendto(msg, ep)
                sent += 1
        return sent

    def _start_cyclic(self, event_id: int, period_ms: int, supplier) -> None:
        self._stop_cyclic(event_id)

        async def run():
            while True:
                try:
                    self._publish(event_id, supplier())
                except Exception as ex:
                    self.engine.eventbus.publish(events.LOG, f"cyclic event 0x{event_id:04X}: {ex}")
                await asyncio.sleep(period_ms / 1000)
        self._cyclic[event_id] = asyncio.get_running_loop().create_task(run())

    def _stop_cyclic(self, event_id: int) -> None:
        t = self._cyclic.pop(event_id, None)
        if t:
            t.cancel()
