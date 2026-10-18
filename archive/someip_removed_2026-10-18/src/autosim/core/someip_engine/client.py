"""Client role: find a service, subscribe eventgroups, call methods (UDP or TCP)."""
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


@dataclass
class MethodResult:
    ok: bool
    return_code: int = 0
    payload: bytes = b""
    latency_ms: float = 0.0
    error: str = ""


class ServiceConsumer:
    """Thread-safe facade. Callbacks (on_availability, on_event) run on the engine loop thread.

    Transport: if svc.reliable is set and the offer carries a TCP endpoint, a TCP connection to
    it is used for method calls and (via a TCP endpoint option in the subscription) for events.
    Otherwise everything uses UDP.
    """

    SUB_TTL = 5

    def __init__(self, engine, svc: ServiceDef, client_id: int = 0x0100) -> None:
        self.engine, self.svc, self.client_id = engine, svc, client_id
        self.available = False
        self.provider_ep: Addr | None = None     # UDP endpoint from the offer
        self.provider_tcp: Addr | None = None    # TCP endpoint from the offer
        self.provider_sd: Addr | None = None
        self.subscribed: set[int] = set()
        self.acked: set[int] = set()
        self.on_availability: Callable[[bool], None] = lambda up: None
        self.on_event: Callable[[int, bytes], None] = lambda event_id, payload: None
        self.running = False
        self._ep: UdpEndpoint | None = None
        self._tcp_w: asyncio.StreamWriter | None = None
        self._tcp_task: asyncio.Task | None = None
        self._task: asyncio.Task | None = None
        self._expiry = 0.0
        self._session = 0
        self._calls: dict[int, asyncio.Future] = {}

    @property
    def uses_tcp(self) -> bool:
        return bool(self.svc.reliable and self.provider_tcp)

    # ---- public (any thread) ------------------------------------------------
    def start(self) -> None:
        self.engine.call(self._start())

    def stop(self) -> None:
        self.engine.call(self._stop())

    def subscribe(self, eventgroup_id: int) -> None:
        self.engine.submit(self._subscribe(eventgroup_id))

    def unsubscribe(self, eventgroup_id: int) -> None:
        self.engine.call_soon(self._unsubscribe, eventgroup_id)

    def call_method(self, method_id: int, payload: bytes = b"", timeout: float = 2.0,
                    fire_and_forget: bool = False) -> MethodResult:
        """Blocking; do not call from the GUI thread (use call_method_async)."""
        return self.engine.call(self._call(method_id, payload, timeout, fire_and_forget), timeout + 1)

    def call_method_async(self, method_id: int, payload: bytes, timeout: float,
                          cb: Callable[[MethodResult], None], fire_and_forget: bool = False) -> None:
        fut = self.engine.submit(self._call(method_id, payload, timeout, fire_and_forget))
        fut.add_done_callback(lambda f: cb(f.result() if not f.exception()
                                          else MethodResult(False, error=repr(f.exception()))))

    # ---- loop thread --------------------------------------------------------
    async def _start(self) -> None:
        if self.running:
            return
        self._ep = UdpEndpoint(self._on_udp)
        await self._ep.open(asyncio.get_running_loop(), (self.engine.local_ip, 0))
        self.engine.agent.add_listener(self)
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def _stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self._task.cancel()
        if self.available and self.provider_sd:
            for eg in self.subscribed:
                await self._send_subscribe(eg, ttl=0)
        self.engine.agent.remove_listener(self)
        self._close_tcp()
        self._ep.close()
        self._set_available(False)

    def _matches(self, e: SdEntry) -> bool:
        s = self.svc
        return (e.service_id == s.service_id and s.instance_id in (e.instance_id, ANY_INSTANCE)
                and e.major in (s.major, 0xFF))

    def _set_available(self, up: bool) -> None:
        if up != self.available:
            self.available = up
            if not up:
                self.acked.clear()
                self._close_tcp()
            self.on_availability(up)

    async def _loop(self) -> None:
        tick = 0
        while True:
            if self.available and time.monotonic() > self._expiry:
                self._set_available(False)
            if not self.available:
                s = self.svc
                self.engine.agent.send([SdEntry(SdType.FIND, s.service_id, s.instance_id, s.major, 3, minor=s.minor)])
            elif tick % 2 == 0:
                for eg in list(self.subscribed):
                    await self._send_subscribe(eg)
            tick += 1
            await asyncio.sleep(1.0)

    def handle_sd(self, e: SdEntry, sender: Addr) -> None:
        if not self.running or not self._matches(e):
            return
        if e.type == SdType.OFFER:
            if e.ttl == 0:
                self._set_available(False)
                return
            udp = next((o for o in e.options if o.proto == UDP), None)
            tcp = next((o for o in e.options if o.proto == TCP), None)
            if udp is None and tcp is None:
                return
            self.provider_ep = (udp.ip, udp.port) if udp else None
            new_tcp = (tcp.ip, tcp.port) if tcp else None
            if new_tcp != self.provider_tcp:
                self._close_tcp()
            self.provider_tcp = new_tcp
            self.provider_sd = sender
            self._expiry = time.monotonic() + (1e9 if e.ttl == 0xFFFFFF else e.ttl)
            was = self.available
            self._set_available(True)
            if not was:
                for eg in list(self.subscribed):
                    asyncio.get_running_loop().create_task(self._send_subscribe(eg))
        elif e.type == SdType.SUBSCRIBE_ACK and e.eventgroup_id in self.subscribed:
            if e.ttl > 0:
                self.acked.add(e.eventgroup_id)
            else:
                self.acked.discard(e.eventgroup_id)
                self.engine.eventbus.publish(
                    events.LOG, f"Subscription to eventgroup 0x{e.eventgroup_id:04X} of {self.svc.name} rejected")

    # ---- TCP -------------------------------------------------------------------
    async def _ensure_tcp(self) -> bool:
        if self._tcp_w is not None and not self._tcp_w.is_closing():
            return True
        if not self.uses_tcp:
            return False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(*self.provider_tcp, local_addr=(self.engine.local_ip, 0)), 3.0)
        except (OSError, asyncio.TimeoutError) as e:
            self.engine.eventbus.publish(events.LOG, f"{self.svc.name}: TCP connect to {self.provider_tcp} failed: {e}")
            return False
        self._tcp_w = writer
        self._tcp_task = asyncio.create_task(self._tcp_reader(reader, writer))
        return True

    def _close_tcp(self) -> None:
        if self._tcp_task:
            self._tcp_task.cancel()
            self._tcp_task = None
        if self._tcp_w:
            self._tcp_w.close()
            self._tcp_w = None

    async def _tcp_reader(self, reader, writer) -> None:
        try:
            while True:
                self._on_message(SomeIpMessage.unpack(await read_message(reader)))
        except (asyncio.IncompleteReadError, ConnectionError, WireError, asyncio.CancelledError):
            pass
        finally:
            if self._tcp_w is writer:
                self._tcp_w = None
            writer.close()

    # ---- subscriptions --------------------------------------------------------
    async def _send_subscribe(self, eventgroup_id: int, ttl: int | None = None) -> None:
        s = self.svc
        if self.uses_tcp and await self._ensure_tcp():
            ip, port = self._tcp_w.get_extra_info("sockname")[:2]
            opt = IPv4Option(ip, port, TCP)
        else:
            opt = IPv4Option(self.engine.local_ip, self._ep.local_addr[1], UDP)
        entry = SdEntry(SdType.SUBSCRIBE, s.service_id, s.instance_id, s.major,
                        self.SUB_TTL if ttl is None else ttl, eventgroup_id=eventgroup_id, options=[opt])
        self.engine.agent.send([entry], self.provider_sd)

    async def _subscribe(self, eg: int) -> None:
        self.subscribed.add(eg)
        if self.available:
            await self._send_subscribe(eg)

    def _unsubscribe(self, eg: int) -> None:
        self.subscribed.discard(eg)
        self.acked.discard(eg)
        if self.available:
            asyncio.get_running_loop().create_task(self._send_subscribe(eg, ttl=0))

    # ---- incoming data -----------------------------------------------------------
    def _on_udp(self, data: bytes, addr: Addr) -> None:
        try:
            self._on_message(SomeIpMessage.unpack(data))
        except WireError:
            return

    def _on_message(self, m: SomeIpMessage) -> None:
        if m.service_id != self.svc.service_id:
            return
        if m.msg_type == MsgType.NOTIFICATION:
            self.engine.eventbus.publish(events.SOMEIP_EVENT, {
                "t": time.time(), "service": self.svc.name, "event_id": m.method_id, "payload": m.payload})
            self.on_event(m.method_id, m.payload)
        elif m.msg_type in (MsgType.RESPONSE, MsgType.ERROR) and m.client_id == self.client_id:
            fut = self._calls.get(m.session_id)
            if fut and not fut.done():
                fut.set_result(m)

    async def _call(self, method_id: int, payload: bytes, timeout: float, fire_and_forget: bool) -> MethodResult:
        if not (self.running and self.available and (self.provider_ep or self.provider_tcp)):
            return MethodResult(False, error="service not available")
        self._session = self._session % 0xFFFF + 1
        session = self._session
        msg = SomeIpMessage(self.svc.service_id, method_id, self.client_id, session,
                            MsgType.REQUEST_NO_RETURN if fire_and_forget else MsgType.REQUEST,
                            ReturnCode.OK, payload, self.svc.major).pack()
        t0 = time.perf_counter()
        use_tcp = await self._ensure_tcp() if self.uses_tcp else False
        if not use_tcp and self.provider_ep is None:
            return MethodResult(False, error="provider offers only TCP and the connection failed")

        def send() -> None:
            if use_tcp:
                self._tcp_w.write(msg)
            else:
                self._ep.sendto(msg, self.provider_ep)

        if fire_and_forget:
            send()
            return MethodResult(True)
        fut = asyncio.get_running_loop().create_future()
        self._calls[session] = fut
        try:
            send()
            resp: SomeIpMessage = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return MethodResult(False, error=f"timeout after {timeout:g}s")
        except ConnectionError as e:
            return MethodResult(False, error=f"connection error: {e}")
        finally:
            self._calls.pop(session, None)
        ms = (time.perf_counter() - t0) * 1000
        rc = ReturnCode(resp.return_code) if resp.return_code in iter(ReturnCode) else resp.return_code
        return MethodResult(resp.return_code == 0, int(resp.return_code), resp.payload, ms,
                            "" if resp.return_code == 0 else f"return code {getattr(rc, 'name', rc)}")
