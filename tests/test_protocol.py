from autosim.hardware.esp32_gateway import protocol as p
from autosim.hardware.esp32_gateway.protocol import Packet, MsgType, cobs_decode, cobs_encode
from autosim.core.common.frame import CanFrame


def test_cobs_roundtrip():
    for data in (b"", b"\x00", b"\x00\x00", b"abc", bytes(range(256)) * 2, b"\x01" * 300):
        assert cobs_decode(cobs_encode(data)) == data


def test_packet_roundtrip():
    pkt = Packet(MsgType.TX_FRAME, 7, b"\x00\x01\x02")
    raw = pkt.encode()
    assert raw.endswith(b"\x00") and b"\x00" not in raw[:-1]
    assert Packet.decode(raw[:-1]) == pkt


def test_can_payload_roundtrip():
    f = CanFrame(0x123, bytes(range(64)), is_fd=True, bitrate_switch=True)
    g = p.decode_can_payload(p.encode_can_payload(f), "ch")
    assert (g.arbitration_id, g.data, g.is_fd, g.bitrate_switch) == (0x123, f.data, True, True)
