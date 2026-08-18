"""pcap/pcapng 解析ツールのテスト（合成キャプチャで検証）."""

from __future__ import annotations

import struct
from pathlib import Path

from anesthesia_record.dri_decode import build_displ_payload, stuff_frame
from paperchart.pcap_dri import (
    IPPROTO_UDP,
    build_flows,
    read_capture,
)


def _udp_ethernet_frame(
    src_ip: str, dst_ip: str, src_port: int, dst_port: int, payload: bytes
) -> bytes:
    """Ethernet/IPv4/UDP フレームを組み立てる（テスト用の最小実装）."""
    eth = b"\x11\x22\x33\x44\x55\x66" + b"\xaa\xbb\xcc\xdd\xee\xff" + b"\x08\x00"
    udp = struct.pack(">HHHH", src_port, dst_port, 8 + len(payload), 0) + payload

    def _ip_bytes(ip: str) -> bytes:
        return bytes(int(x) for x in ip.split("."))

    total = 20 + len(udp)
    ip = (
        bytes([0x45, 0x00])
        + struct.pack(">H", total)
        + b"\x00\x00\x00\x00\x40"
        + bytes([IPPROTO_UDP])
        + b"\x00\x00"
        + _ip_bytes(src_ip)
        + _ip_bytes(dst_ip)
    )
    return eth + ip + udp


def _write_classic_pcap(path: Path, frames: list[bytes]) -> None:
    with path.open("wb") as fh:
        fh.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for frame in frames:
            fh.write(struct.pack("<IIII", 0, 0, len(frame), len(frame)))
            fh.write(frame)


def _write_pcapng(path: Path, frames: list[bytes]) -> None:
    with path.open("wb") as fh:
        # Section Header Block
        shb_body = struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)
        shb_len = 12 + len(shb_body)
        fh.write(struct.pack("<II", 0x0A0D0D0A, shb_len) + shb_body
                 + struct.pack("<I", shb_len))
        # Interface Description Block (linktype=1 Ethernet)
        idb_body = struct.pack("<HHI", 1, 0, 65535)
        idb_len = 12 + len(idb_body)
        fh.write(struct.pack("<II", 0x00000001, idb_len) + idb_body
                 + struct.pack("<I", idb_len))
        # Enhanced Packet Blocks
        for frame in frames:
            pad = (-len(frame)) % 4
            data = frame + b"\x00" * pad
            epb_body = struct.pack("<IIIII", 0, 0, 0, len(frame), len(frame)) + data
            epb_len = 12 + len(epb_body)
            fh.write(struct.pack("<II", 0x00000006, epb_len) + epb_body
                     + struct.pack("<I", epb_len))


def _dri_udp_frame() -> bytes:
    payload = build_displ_payload(r_time=0x1234, basic_values={0: 98})
    stuffed = stuff_frame(payload)
    return _udp_ethernet_frame("172.16.12.143", "172.16.0.10", 7000, 7000, stuffed)


def test_read_classic_pcap(tmp_path: Path) -> None:
    frame = _dri_udp_frame()
    path = tmp_path / "cap.pcap"
    _write_classic_pcap(path, [frame, frame])
    packets = read_capture(path)
    assert len(packets) == 2
    assert packets[0].proto == IPPROTO_UDP
    assert packets[0].src_ip == "172.16.12.143"
    assert packets[0].dst_port == 7000


def test_read_pcapng(tmp_path: Path) -> None:
    frame = _dri_udp_frame()
    path = tmp_path / "cap.pcapng"
    _write_pcapng(path, [frame])
    packets = read_capture(path)
    assert len(packets) == 1
    assert packets[0].src_ip == "172.16.12.143"


def test_flow_grouping_and_dri_detection(tmp_path: Path) -> None:
    frame = _dri_udp_frame()
    path = tmp_path / "cap.pcapng"
    _write_pcapng(path, [frame, frame, frame])
    flows = build_flows(read_capture(path))
    assert len(flows) == 1
    flow = flows[0]
    assert flow.packets == 3
    # DRI フレームには 0x7E 区切りが含まれる
    assert bytes(flow.payload).count(0x7E) >= 2
