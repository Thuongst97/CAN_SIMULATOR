"""Software model of the ESP32 + MCP2518FD firmware (same wire protocol).

Used for hardware-free development, the "Simulated node" option in the UI and tests.
Behaviour: answers HELLO/CONFIG/START/STOP/PING, loops every TX_FRAME back as RX_FRAME
(optional) and sends STATUS once per second while running.
"""
from __future__ import annotations

import struct
import threading
import time

from autosim.hardware.esp32_gateway import protocol as P
from autosim.hardware.esp32_gateway.protocol import MsgType, Packet
from autosim.hardware.esp32_gateway.transport import PipeEnd, make_pipe


class SimNode:
    def __init__(self, transport: PipeEnd, loopback: bool = True, node_id: str = "SIM-NODE") -> None:
        self.t, self.loopback, self.node_id = transport, loopback, node_id
        self.config: dict | None = None
        self.running = False
        self.rx_count = self.tx_count = 0
        self.drop_next_acks = 0          # test hook: simulate lost ACKs
        self._stop = threading.Event()
        self._seq = 0
        self._t0 = time.monotonic()
        threading.Thread(target=self._run, name="sim-node", daemon=True).start()
        threading.Thread(target=self._status_loop, name="sim-node-status", daemon=True).start()

    @classmethod
    def create_pair(cls, **kw):
        """-> (host_transport, SimNode)"""
        host, node = make_pipe()
        return host, cls(node, **kw)

    def shutdown(self) -> None:
        self._stop.set()

    def _send(self, mtype: MsgType, seq: int, payload: bytes = b"") -> None:
        try:
            self.t.write(Packet(mtype, seq, payload).encode())
        except OSError:
            self._stop.set()

    def _ack(self, req: Packet) -> None:
        if self.drop_next_acks > 0:
            self.drop_next_acks -= 1
            return
        self._send(MsgType.ACK, req.seq, bytes([req.type]))

    def inject(self, frame) -> None:
        """Emit a frame as if it arrived from the bus."""
        self._send_rx(frame)

    def _send_rx(self, frame) -> None:
        self._seq = (self._seq + 1) & 0xFF
        payload = bytearray(P.encode_can_payload(frame))
        payload[6:14] = struct.pack("<Q", int((time.monotonic() - self._t0) * 1e6))
        self.rx_count += 1
        self._send(MsgType.RX_FRAME, self._seq, bytes(payload))

    def _run(self) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            chunk = self.t.read_some()
            if not chunk:
                continue
            buf += chunk
            while (i := buf.find(0)) >= 0:
                block, buf = bytes(buf[:i]), buf[i + 1:]
                if block:
                    try:
                        self._on_packet(Packet.decode(block))
                    except (P.ProtocolError, ValueError):
                        pass

    def _on_packet(self, p: Packet) -> None:
        if p.type == MsgType.HELLO:
            if self.drop_next_acks > 0:
                self.drop_next_acks -= 1
                return
            self._send(MsgType.HELLO_ACK, p.seq, P.encode_hello_ack(1, 0, 0b11, self.node_id))
        elif p.type == MsgType.CONFIG:
            try:
                self.config = P.decode_config(p.payload)
                from types import SimpleNamespace
                P.validate_config(SimpleNamespace(
                    nominal_bitrate=self.config["nominal"], data_bitrate=self.config["data"],
                    fd_enabled=bool(self.config["flags"] & P.CFG_FD)))
            except (struct.error, ValueError) as e:
                self.config = None
                self._send(MsgType.ERROR, 0, bytes([P.ERR_BAD_CONFIG]) + str(e).encode()[:48])
                self._send(MsgType.NACK, p.seq, bytes([p.type, P.ERR_BAD_CONFIG]))
                return
            self._ack(p)
        elif p.type == MsgType.SET_NAME:
            name = p.payload.decode("ascii", "replace")
            if not P.valid_node_name(name):
                self._send(MsgType.NACK, p.seq, bytes([p.type, P.ERR_BAD_CONFIG]))
                return
            self.node_id = name
            self._ack(p)
        elif p.type == MsgType.START:
            self.running = True
            self._ack(p)
        elif p.type == MsgType.STOP:
            self.running = False
            self._ack(p)
        elif p.type == MsgType.PING:
            self._send(MsgType.PONG, p.seq, p.payload[:4])
        elif p.type == MsgType.TX_FRAME:
            if not self.running:
                self._send(MsgType.NACK, p.seq, bytes([p.type, 7]))
                return
            self.tx_count += 1
            if self.loopback:
                self._send_rx(P.decode_can_payload(p.payload, ""))

    def _status_loop(self) -> None:
        while not self._stop.wait(1.0):
            if self.running:
                st = P.NodeStatus(state=1, rx_count=self.rx_count, tx_count=self.tx_count)
                self._send(MsgType.STATUS, 0, st.encode())
