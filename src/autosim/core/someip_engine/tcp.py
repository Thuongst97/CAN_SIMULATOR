"""SOME/IP over TCP: message framing on a byte stream."""
from __future__ import annotations

import asyncio

from autosim.core.someip_engine.wire import WireError

MAX_MESSAGE = 4 * 1024 * 1024


async def read_message(reader: asyncio.StreamReader) -> bytes:
    """Read exactly one SOME/IP message (header + payload). Raises IncompleteReadError at EOF,
    WireError on an impossible length (the connection should then be closed)."""
    head = await reader.readexactly(8)
    length = int.from_bytes(head[4:8], "big")      # bytes following the length field
    if length < 8 or length > MAX_MESSAGE:
        raise WireError(f"bad SOME/IP length {length} on TCP stream")
    return head + await reader.readexactly(length)
