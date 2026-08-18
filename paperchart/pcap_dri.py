"""ネットワークキャプチャ(pcap/pcapng)から S/5(DRI) 候補フローを抽出・解析する.

B650 は背面に RS-232 を持たず、DRI(S/5) データは MC ネットワーク上に流れる
(Configuration -> Network -> Select Network Type: S/5)。Wireshark 等で MC
ネットワークを受動キャプチャした .pcap / .pcapng を本ツールに渡すと、

* UDP/TCP フローごとにペイロードを結合し
* DRI のフレーム区切り 0x7E の有無・バイト統計を出し
* それらしいフローに対して既存の DRI デコーダを適用する

依存パッケージ不要（pcap/pcapng を素の Python で解析）。

使い方::

    python paperchart/pcap_dri.py capture.pcapng
    python paperchart/pcap_dri.py capture.pcapng --config paperchart/b650.txt
    python paperchart/pcap_dri.py capture.pcapng --ip 172.16.12.143 --decode
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anesthesia_record.dri_decode import (  # noqa: E402
    FRAMECHAR,
    decode_stream,
)
from anesthesia_record.paperchart_config import (  # noqa: E402
    PaperChartConfig,
    load_paperchart_config,
)

ETHERTYPE_IPV4 = 0x0800
ETHERTYPE_VLAN = 0x8100
IPPROTO_TCP = 6
IPPROTO_UDP = 17


@dataclass
class Packet:
    """1 パケット分の L3/L4 情報とペイロード."""

    proto: int
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    payload: bytes


@dataclass
class Flow:
    """同一 (proto, src, dst) の結合フロー."""

    proto: int
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    payload: bytearray = field(default_factory=bytearray)
    packets: int = 0

    @property
    def key(self) -> tuple[int, str, str, int, int]:
        return (self.proto, self.src_ip, self.dst_ip, self.src_port, self.dst_port)

    def label(self) -> str:
        name = {IPPROTO_UDP: "UDP", IPPROTO_TCP: "TCP"}.get(self.proto, str(self.proto))
        return f"{name} {self.src_ip}:{self.src_port} -> {self.dst_ip}:{self.dst_port}"


def _ipv4(raw: bytes) -> str:
    return ".".join(str(b) for b in raw)


def _parse_ethernet(data: bytes) -> Packet | None:
    """Ethernet フレームから IPv4/UDP・TCP ペイロードを取り出す."""
    if len(data) < 14:
        return None
    ethertype = struct.unpack_from(">H", data, 12)[0]
    offset = 14
    while ethertype == ETHERTYPE_VLAN:
        if len(data) < offset + 4:
            return None
        ethertype = struct.unpack_from(">H", data, offset + 2)[0]
        offset += 4
    if ethertype != ETHERTYPE_IPV4:
        return None
    if len(data) < offset + 20:
        return None
    ver_ihl = data[offset]
    if ver_ihl >> 4 != 4:
        return None
    ihl = (ver_ihl & 0x0F) * 4
    proto = data[offset + 9]
    src_ip = _ipv4(data[offset + 12 : offset + 16])
    dst_ip = _ipv4(data[offset + 16 : offset + 20])
    l4 = offset + ihl
    if proto == IPPROTO_UDP:
        if len(data) < l4 + 8:
            return None
        src_port, dst_port, length = struct.unpack_from(">HHH", data, l4)
        payload = data[l4 + 8 : l4 + max(length, 8)]
        return Packet(proto, src_ip, dst_ip, src_port, dst_port, payload)
    if proto == IPPROTO_TCP:
        if len(data) < l4 + 20:
            return None
        src_port, dst_port = struct.unpack_from(">HH", data, l4)
        data_offset = (data[l4 + 12] >> 4) * 4
        payload = data[l4 + data_offset :]
        return Packet(proto, src_ip, dst_ip, src_port, dst_port, payload)
    return None


def _iter_classic_pcap(blob: bytes) -> "list[bytes]":
    """classic pcap のリンク層フレームを返す (Ethernet 前提)."""
    magic = blob[:4]
    if magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        endian = ">"
    elif magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        endian = "<"
    else:
        raise ValueError("classic pcap のマジックが不正です")
    linktype = struct.unpack_from(endian + "I", blob, 20)[0]
    if linktype != 1:
        raise ValueError(f"未対応の linktype={linktype} (Ethernet=1 のみ対応)")
    frames: list[bytes] = []
    pos = 24
    n = len(blob)
    while pos + 16 <= n:
        _, _, incl_len, _ = struct.unpack_from(endian + "IIII", blob, pos)
        pos += 16
        if pos + incl_len > n:
            break
        frames.append(blob[pos : pos + incl_len])
        pos += incl_len
    return frames


def _iter_pcapng(blob: bytes) -> "list[bytes]":
    """pcapng の Enhanced/Simple Packet Block からフレームを返す."""
    frames: list[bytes] = []
    pos = 0
    endian = "<"
    n = len(blob)
    while pos + 12 <= n:
        block_type = struct.unpack_from(endian + "I", blob, pos)[0]
        if block_type == 0x0A0D0D0A:  # Section Header Block
            bom = struct.unpack_from("<I", blob, pos + 8)[0]
            endian = "<" if bom == 0x1A2B3C4D else ">"
            block_type = struct.unpack_from(endian + "I", blob, pos)[0]
        total_len = struct.unpack_from(endian + "I", blob, pos + 4)[0]
        if total_len < 12 or pos + total_len > n:
            break
        body = blob[pos + 8 : pos + total_len - 4]
        if block_type == 0x00000006:  # Enhanced Packet Block
            cap_len = struct.unpack_from(endian + "I", body, 12)[0]
            frames.append(body[20 : 20 + cap_len])
        elif block_type == 0x00000003:  # Simple Packet Block
            orig_len = struct.unpack_from(endian + "I", body, 0)[0]
            frames.append(body[4 : 4 + orig_len])
        pos += total_len
    return frames


def read_capture(path: str | Path) -> list[Packet]:
    """pcap / pcapng を読み IPv4 UDP/TCP パケット列に変換する."""
    blob = Path(path).read_bytes()
    if blob[:4] == b"\x0a\x0d\x0d\x0a":
        frames = _iter_pcapng(blob)
    else:
        frames = _iter_classic_pcap(blob)
    packets: list[Packet] = []
    for frame in frames:
        pkt = _parse_ethernet(frame)
        if pkt is not None and pkt.payload:
            packets.append(pkt)
    return packets


def build_flows(packets: list[Packet]) -> list[Flow]:
    """パケットを (proto, src, dst, ports) フローに結合する."""
    flows: dict[tuple[int, str, str, int, int], Flow] = {}
    for pkt in packets:
        key = (pkt.proto, pkt.src_ip, pkt.dst_ip, pkt.src_port, pkt.dst_port)
        flow = flows.get(key)
        if flow is None:
            flow = Flow(pkt.proto, pkt.src_ip, pkt.dst_ip, pkt.src_port, pkt.dst_port)
            flows[key] = flow
        flow.payload += pkt.payload
        flow.packets += 1
    return sorted(flows.values(), key=lambda f: len(f.payload), reverse=True)


def summarize(flow: Flow) -> str:
    """フローのバイト統計 + DRI らしさ (0x7E 数) を 1 行で返す."""
    data = bytes(flow.payload)
    n = len(data)
    framechars = data.count(FRAMECHAR)
    zeros = data.count(0)
    return (
        f"{flow.label()}  pkts={flow.packets} bytes={n} "
        f"0x7E={framechars} 0x00={zeros} "
        f"({'DRI候補' if framechars >= 2 else '非DRI?'})"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", help="pcap / pcapng ファイル")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parent / "b650.txt"),
        help="paperChart 設定ファイル (既定 paperchart/b650.txt)",
    )
    parser.add_argument("--ip", help="このIPが送信元/宛先のフローのみ対象")
    parser.add_argument(
        "--decode",
        action="store_true",
        help="DRI候補フローに DRI デコーダを適用し数値を表示",
    )
    args = parser.parse_args(argv)

    packets = read_capture(args.capture)
    if args.ip:
        packets = [p for p in packets if args.ip in (p.src_ip, p.dst_ip)]
    flows = build_flows(packets)

    if not flows:
        print("IPv4 UDP/TCP ペイロードが見つかりませんでした。")
        return 1

    print(f"# フロー一覧 ({len(flows)} 件, ペイロード降順)")
    for flow in flows:
        print("  " + summarize(flow))

    if not args.decode:
        print("\n--decode で DRI候補フローのデコードを試みます。")
        return 0

    config: PaperChartConfig = load_paperchart_config(args.config)
    candidates = [f for f in flows if bytes(f.payload).count(FRAMECHAR) >= 2]
    if not candidates:
        print("\nDRI候補 (0x7E>=2) のフローがありません。"
              " ネットワーク種別が S/5 か、キャプチャ位置を確認してください。")
        return 2

    for flow in candidates:
        frames = decode_stream(bytes(flow.payload), config)
        ok = sum(1 for fr in frames if fr.checksum_ok)
        print(f"\n## {flow.label()}: frames={len(frames)} checksum_ok={ok}")
        for i, frame in enumerate(frames[:5]):
            maintype = frame.header.r_maintype if frame.header else None
            print(f"  frame#{i} ok={frame.checksum_ok} maintype={maintype}")
            for value in frame.values[:10]:
                print(f"    {value.name} = {value.value} {value.unit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
