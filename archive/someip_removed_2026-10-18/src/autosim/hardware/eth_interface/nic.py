"""NIC enumeration for the automotive Ethernet media converter (RAD-MOON)."""
from __future__ import annotations

import socket
import struct
from dataclasses import dataclass, field

import psutil

INTREPID_OUI = ("00:FC:70",)   # Intrepid Control Systems


@dataclass
class NicInfo:
    name: str
    ipv4: list[str] = field(default_factory=list)
    mac: str = ""
    is_up: bool = False
    speed_mbps: int = 0

    @property
    def likely_intrepid(self) -> bool:
        return self.mac.upper().replace("-", ":").startswith(INTREPID_OUI) or "intrepid" in self.name.lower() \
            or "rad-moon" in self.name.lower()

    @property
    def label(self) -> str:
        ips = ", ".join(self.ipv4) or "no IPv4"
        return f"{self.name} ({ips}){'  [Intrepid]' if self.likely_intrepid else ''}"


def list_nics() -> list[NicInfo]:
    stats = psutil.net_if_stats()
    out = []
    for name, addrs in psutil.net_if_addrs().items():
        nic = NicInfo(name)
        for a in addrs:
            if a.family == socket.AF_INET:
                nic.ipv4.append(a.address)
            elif a.family == psutil.AF_LINK:
                nic.mac = a.address
        st = stats.get(name)
        if st:
            nic.is_up, nic.speed_mbps = st.isup, st.speed
        out.append(nic)
    return sorted(out, key=lambda n: (not n.likely_intrepid, not n.is_up, n.name))


def validate_multicast(local_ip: str, group: str) -> tuple[bool, str]:
    """Try to join `group` on the interface owning `local_ip`. -> (ok, message)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                     struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton(local_ip)))
        return True, "multicast join OK"
    except OSError as e:
        return False, f"multicast join failed: {e}"
    finally:
        s.close()
