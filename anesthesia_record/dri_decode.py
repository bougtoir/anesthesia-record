"""GE CARESCAPE B650 の DRI(Datex Record Interface) シリアル出力デコーダ.

TeraTerm 等で B650 のシリアル(DRI)出力を**バイナリ**保存したログを読み、
フレーム分解・チェックサム検証・ヘッダ解析・生理データ(physdb DISPL)の
数値デコードを行う純粋関数群を提供する。

フレーミング仕様（Datex-Ohmeda S/5 Computer Interface, M1017617-01。
UN-GCPDS/python-pycollect の実装で確認）:
- フレーム区切り文字 ``0x7E`` (FRAMECHAR)。
- エスケープ文字 ``0x7D`` (CTRLCHAR)。直後のバイトを ``| 0x7C`` して復元
  (0x5E→0x7E, 0x5D→0x7D)。
- フレーム末尾 1 バイトはチェックサム = それ以前の全バイト総和の下位 8bit。
- ヘッダ 40 バイト (datex_hdr)、続いてデータ領域。
- 生理データ表示 (DRI_PH_DISPL) のデータ領域は先頭 4 バイトが time、以降が
  physdata。physdata 上のバイトオフセット/スケールは paperChart の
  s5.txt / b650.txt と共通。

数値は 16bit 符号付きリトルエンディアン。``-32767`` はデータ無し、
``-32001`` 以下は特殊無効値として扱う。

本モジュールは公開仕様からの実装であり、実機 B650 の DRI 出力での検証は
未実施（要実機確認）。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .paperchart_config import NumericEntry, PaperChartConfig

FRAMECHAR = 0x7E
CTRLCHAR = 0x7D
BIT5 = 0x7C

DRI_MT_PHDB = 0
DRI_MT_WAVE = 1
DRI_PH_DISPL = 1
DRI_PH_XMIT_REQ = 0
DRI_EOL_SUBR_LIST = 0xFF

# phdb transmission request の physdb クラスビット
DRI_PHDBCL_REQ_BASIC = 0x0001
DRI_PHDBCL_REQ_EXT1 = 0x0002
DRI_PHDBCL_REQ_EXT2 = 0x0004
DRI_PHDBCL_REQ_EXT3 = 0x0008

HEADER_LEN = 40
PHDB_TIME_LEN = 4
PHDB_CLASS_LEN = 270

DATA_INVALID = -32767
DATA_INVALID_LIMIT = -32001

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class SubrecordDesc:
    offset: int
    sr_type: int


@dataclass(frozen=True)
class DriHeader:
    r_len: int
    r_nbr: int
    dri_level: int
    plug_id: int
    r_time: int
    r_maintype: int
    subrecords: tuple[SubrecordDesc, ...]

    @property
    def datetime_utc(self) -> datetime | None:
        if self.r_time <= 0:
            return None
        return EPOCH + timedelta(seconds=self.r_time)


@dataclass(frozen=True)
class DecodedValue:
    name: str
    value: float | None
    unit: str
    raw: int
    valid: bool


@dataclass
class DriFrame:
    """1 フレーム分の分解結果."""

    raw: bytes  # チェックサムを含むフレーム本体（0x7E は含まない、unstuff 済み）
    checksum_ok: bool
    header: DriHeader | None = None
    values: list[DecodedValue] = field(default_factory=list)
    note: str = ""


def unstuff_frames(raw: bytes) -> list[bytes]:
    """0x7E 区切りのバイト列を unstuff してフレーム本体（末尾=チェックサム）に分解する.

    先頭のゴミや不完全フレームは無視する。各要素はチェックサムを含む。
    """
    frames: list[bytes] = []
    current: bytearray | None = None
    escape = False

    for byte in raw:
        if byte == FRAMECHAR:
            if current is not None and len(current) > 0:
                frames.append(bytes(current))
            current = bytearray()
            escape = False
            continue

        if current is None:
            # まだ最初の 0x7E を見ていない
            continue

        if byte == CTRLCHAR:
            escape = True
            continue

        if escape:
            current.append(byte | BIT5)
            escape = False
        else:
            current.append(byte)

    return frames


def verify_checksum(frame: bytes) -> bool:
    """フレーム末尾 1 バイトがそれ以前の総和の下位 8bit と一致するか."""
    if len(frame) < 2:
        return False
    return (sum(frame[:-1]) & 0xFF) == frame[-1]


def parse_header(payload: bytes) -> DriHeader:
    """データ本体（チェックサムを除いた 40 バイト以上）からヘッダを解析する."""
    if len(payload) < HEADER_LEN:
        raise ValueError(f"ヘッダに満たない長さ: {len(payload)}")

    r_len, r_nbr, dri_level, plug_id, r_time = struct.unpack_from("<HBBHI", payload, 0)
    (r_maintype,) = struct.unpack_from("<H", payload, 14)

    subrecords: list[SubrecordDesc] = []
    pos = 16
    for _ in range(8):
        (offset,) = struct.unpack_from("<H", payload, pos)
        sr_type = payload[pos + 2]
        subrecords.append(SubrecordDesc(offset=offset, sr_type=sr_type))
        pos += 3

    return DriHeader(
        r_len=r_len,
        r_nbr=r_nbr,
        dri_level=dri_level,
        plug_id=plug_id,
        r_time=r_time,
        r_maintype=r_maintype,
        subrecords=tuple(subrecords),
    )


def _read_int16(buf: bytes, index: int) -> int | None:
    if index < 0 or index + 2 > len(buf):
        return None
    (val,) = struct.unpack_from("<h", buf, index)
    return val


def _value_offset(entry: NumericEntry) -> int | None:
    """numerics 行の group_offset + データフィールドまでの距離.

    raw_tokens[3] がデータフィールドまでの距離。数値でない ('-') 場合は
    group_offset 直上の値ではないため解決できない。
    """
    if len(entry.raw_tokens) < 4:
        return None
    dist_tok = entry.raw_tokens[3]
    try:
        dist = int(dist_tok)
    except ValueError:
        return None
    return entry.group_offset + dist


def decode_phdb_values(
    payload: bytes, header: DriHeader, config: PaperChartConfig
) -> list[DecodedValue]:
    """DISPL サブレコードから b650.txt の numerics を数値デコードする."""
    data = payload[HEADER_LEN:]

    # DISPL サブレコードを出現順に収集（class 0..3 = 出現順）。
    displ_offsets: list[int] = [
        sr.offset for sr in header.subrecords if sr.sr_type == DRI_PH_DISPL
    ]
    if not displ_offsets:
        return []

    results: list[DecodedValue] = []
    for entry in config.numerics:
        cls = entry.subrecord_class
        if cls >= len(displ_offsets):
            continue
        rel = _value_offset(entry)
        if rel is None:
            continue

        class_base = PHDB_TIME_LEN + displ_offsets[cls]
        raw = _read_int16(data, class_base + rel)
        if raw is None:
            continue

        valid = raw > DATA_INVALID_LIMIT
        value = raw * entry.scale if valid else None
        results.append(
            DecodedValue(
                name=entry.name,
                value=value,
                unit=entry.unit,
                raw=raw,
                valid=valid,
            )
        )
    return results


def decode_stream(raw: bytes, config: PaperChartConfig) -> list[DriFrame]:
    """バイナリログ全体を分解・検証・デコードする."""
    frames: list[DriFrame] = []
    for body in unstuff_frames(raw):
        ok = verify_checksum(body)
        payload = body[:-1] if ok else body
        frame = DriFrame(raw=body, checksum_ok=ok)

        if not ok:
            frame.note = "checksum NG"
            frames.append(frame)
            continue

        try:
            header = parse_header(payload)
        except ValueError as exc:
            frame.note = f"header parse error: {exc}"
            frames.append(frame)
            continue

        frame.header = header
        if header.r_maintype == DRI_MT_PHDB:
            frame.values = decode_phdb_values(payload, header, config)
        elif header.r_maintype == DRI_MT_WAVE:
            frame.note = "waveform record (数値デコード対象外)"
        else:
            frame.note = f"maintype={header.r_maintype}"
        frames.append(frame)
    return frames


# ── 合成フレーム生成（テスト・デモ用） ──────────────────────────────


def stuff_frame(payload: bytes) -> bytes:
    """データ本体（チェックサム前）から 0x7E 区切りの送出フレームを作る."""
    checksum = sum(payload) & 0xFF
    body = bytes(payload) + bytes([checksum])
    out = bytearray([FRAMECHAR])
    for byte in body:
        if byte == FRAMECHAR:
            out += bytes([CTRLCHAR, FRAMECHAR ^ 0x20])
        elif byte == CTRLCHAR:
            out += bytes([CTRLCHAR, CTRLCHAR ^ 0x20])
        else:
            out.append(byte)
    out.append(FRAMECHAR)
    return bytes(out)


def build_displ_payload(
    r_time: int, basic_values: dict[int, int]
) -> bytes:
    """basic クラスの physdata に指定オフセットの int16 を詰めた DISPL 本体を作る.

    basic_values: {physdata 内バイトオフセット: int16 値}
    """
    header = bytearray(HEADER_LEN)
    struct.pack_into("<H", header, 0, HEADER_LEN)  # r_len(概算)
    struct.pack_into("<I", header, 6, r_time)  # r_time
    struct.pack_into("<H", header, 14, DRI_MT_PHDB)  # r_maintype
    # sr_desc[0]: offset=0, type=DISPL
    struct.pack_into("<H", header, 16, 0)
    header[18] = DRI_PH_DISPL
    # sr_desc[1]: EOL
    struct.pack_into("<H", header, 19, 0)
    header[21] = DRI_EOL_SUBR_LIST

    data = bytearray(PHDB_TIME_LEN + PHDB_CLASS_LEN)
    struct.pack_into("<I", data, 0, r_time)
    for off, val in basic_values.items():
        struct.pack_into("<h", data, PHDB_TIME_LEN + off, val)

    return bytes(header) + bytes(data)


# ── 送出リクエスト生成（モニタに定期送信を開始させる） ──────────────


def build_phdb_request(
    tx_interval: int = 5,
    class_bf: int = DRI_PHDBCL_REQ_BASIC,
) -> bytes:
    """生理データ表示(DISPL)の送信リクエストフレームを作る（0x7E 区切り済み）.

    DRI では収集側ホストがこのリクエストを送らないとモニタは phdb を
    ストリームしない（TeraTerm で受け身に開くだけだと何も来ない）。
    ヘッダ 40 + phdb_request 9 = 49 バイト (r_len=0x0031)。

    tx_interval: 送信間隔。0 で単発(one shot)、>0 で周期送信(auto mode)。
    class_bf: 要求する physdb クラスのビット和（基本は BASIC）。
    """
    header = bytearray(HEADER_LEN)
    struct.pack_into("<H", header, 0, HEADER_LEN + 9)  # r_len = 49
    struct.pack_into("<H", header, 14, DRI_MT_PHDB)  # r_maintype
    # sr_desc[0]: offset=0, type=XMIT_REQ
    struct.pack_into("<H", header, 16, 0)
    header[18] = DRI_PH_XMIT_REQ
    # sr_desc[1]: EOL
    header[21] = DRI_EOL_SUBR_LIST

    req = bytearray(9)
    req[0] = DRI_PH_DISPL  # phdb_rcrd_type
    struct.pack_into("<H", req, 1, tx_interval)  # tx_interval
    struct.pack_into("<I", req, 3, class_bf)  # phdb_class_bf
    # req[7:9] reserved = 0

    return stuff_frame(bytes(header) + bytes(req))
