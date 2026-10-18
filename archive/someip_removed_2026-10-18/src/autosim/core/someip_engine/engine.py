"""SomeIpEngine: owns the asyncio loop thread, the SD agent and all providers/consumers."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from dataclasses import dataclass
from typing import Callable

from autosim.core.common import events
from autosim.core.common.events import EventBus
from autosim.core.someip_engine.client import ServiceConsumer
from autosim.core.someip_engine.config_loader import ServiceDef
from autosim.core.someip_engine.sd import SdAgent, UdpSdTransport
from autosim.core.someip_engine.server import ServiceProvider


@dataclass
class NetConfig:
    local_ip: str = "127.0.0.1"          # IP of the NIC (e.g. the RAD-MOON adapter) every socket binds to
    sd_multicast_ip: str = "224.0.0.1"   # AUTOSAR deployments commonly use 224.224.224.245
    sd_port: int = 30490


class PyThread:
    """Default loop runner: a plain daemon thread. The GUI injects a QThread-based one."""

    def __init__(self, target: Callable[[], None], name: str) -> None:
        self._t = threading.Thread(target=target, name=name, daemon=True)

    def start(self) -> None:
        self._t.start()

    def join(self, timeout: float) -> None:
        self._t.join(timeout)


ThreadFactory = Callable[[Callable[[], None], str], object]   # -> object with start() / join(timeout)


class SomeIpEngine:
    """All asyncio work happens on one background thread; the public API is thread-safe."""

    def __init__(self, eventbus: EventBus, thread_factory: ThreadFactory = PyThread) -> None:
        self.eventbus = eventbus
        self._thread_factory = thread_factory
        self.cfg = NetConfig()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.agent: SdAgent | None = None
        self.transport: UdpSdTransport | None = None
        self.providers: list[ServiceProvider] = []
        self.consumers: list[ServiceConsumer] = []
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.loop is not None

    @property
    def local_ip(self) -> str:
        return self.cfg.local_ip

    # ---- thread helpers -----------------------------------------------------
    def submit(self, coro) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def call(self, coro, timeout: float = 5.0):
        return self.submit(coro).result(timeout)

    def call_soon(self, fn, *args) -> None:
        self.loop.call_soon_threadsafe(fn, *args)

    # ---- lifecycle ----------------------------------------------------------
    def start(self, cfg: NetConfig) -> None:
        if self.running:
            raise RuntimeError("SOME/IP engine already running")
        self.cfg = cfg
        ready = threading.Event()
        loop = asyncio.new_event_loop()

        def run():
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()
            loop.close()

        self._thread = self._thread_factory(run, "someip-loop")
        self._thread.start()
        ready.wait()
        self.loop = loop
        try:
            self.call(self._setup())
        except Exception:
            self.stop()
            raise

    async def _setup(self) -> None:
        c = self.cfg
        self.agent = SdAgent(c.local_ip, c.sd_port, None, self.eventbus)
        self.transport = UdpSdTransport(c.local_ip, c.sd_port, c.sd_multicast_ip, self.agent.on_datagram)
        await self.transport.start(self.loop)
        self.agent.transport = self.transport
        if self.transport.multicast_ok:
            self.eventbus.publish(events.LOG, f"SOME/IP-SD on {c.local_ip}, group {c.sd_multicast_ip}:{c.sd_port}")
        else:
            self.eventbus.publish(events.LOG, "SOME/IP-SD multicast unavailable on "
                                  f"{c.local_ip} ({self.transport.multicast_error}); local/unicast mode only")

    async def _shutdown(self) -> None:
        for p in list(self.providers):
            await p._stop()
        for c in list(self.consumers):
            await c._stop()
        if self.transport:
            self.transport.close()

    def stop(self) -> None:
        if not self.loop:
            return
        try:
            self.call(self._shutdown(), 5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(3)
        self.loop = self.agent = self.transport = self._thread = None
        self.providers.clear()
        self.consumers.clear()

    # ---- services -----------------------------------------------------------
    def add_provider(self, svc: ServiceDef) -> ServiceProvider:
        p = ServiceProvider(self, svc)
        self.providers.append(p)
        return p

    def add_consumer(self, svc: ServiceDef) -> ServiceConsumer:
        c = ServiceConsumer(self, svc)
        self.consumers.append(c)
        return c

    def remove(self, obj) -> None:
        try:
            obj.stop()
        finally:
            for lst in (self.providers, self.consumers):
                if obj in lst:
                    lst.remove(obj)
