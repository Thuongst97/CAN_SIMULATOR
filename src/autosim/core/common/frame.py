"""Hardware-neutral CAN frame and bus configuration types."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CanFrame:
    arbitration_id: int
    data: bytes
    is_extended: bool = False
    is_fd: bool = False
    bitrate_switch: bool = False
    timestamp: float = 0.0        # seconds, host monotonic-aligned
    channel: str = ""             # logical channel name, e.g. "ESP32-1"
    is_rx: bool = True


@dataclass(slots=True)
class CanChannelConfig:
    """Per-node CAN FD configuration (maps to CONFIG packet)."""
    nominal_bitrate: int = 500_000
    data_bitrate: int = 2_000_000
    fd_enabled: bool = True
    brs: bool = True
    iso_mode: bool = True         # ISO 11898-1:2015 vs non-ISO (Bosch) CRC
    listen_only: bool = False
    sample_point_nominal: float = 0.8
    sample_point_data: float = 0.75
    extras: dict = field(default_factory=dict)
