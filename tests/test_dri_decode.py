from pathlib import Path

import pytest

from anesthesia_record.dri_decode import (
    DRI_MT_PHDB,
    DRI_PH_XMIT_REQ,
    DRI_EOL_SUBR_LIST,
    FRAMECHAR,
    build_displ_payload,
    build_phdb_request,
    decode_stream,
    parse_header,
    stuff_frame,
    unstuff_frames,
    verify_checksum,
)
from anesthesia_record.paperchart_config import load_paperchart_config

CONFIG_PATH = Path(__file__).resolve().parents[1] / "paperchart" / "b650.txt"


@pytest.fixture(scope="module")
def cfg():
    return load_paperchart_config(str(CONFIG_PATH))


def test_checksum_roundtrip():
    payload = bytes([0x01, 0x02, 0x03, 0xFE])
    frame = stuff_frame(payload)
    # 0x7E で囲まれている
    assert frame[0] == FRAMECHAR and frame[-1] == FRAMECHAR
    bodies = unstuff_frames(frame)
    assert len(bodies) == 1
    assert verify_checksum(bodies[0])
    assert bodies[0][:-1] == payload


def test_byte_stuffing_roundtrip():
    # 0x7E / 0x7D をデータに含めても unstuff で元に戻る
    payload = bytes([FRAMECHAR, 0x7D, 0x10, FRAMECHAR])
    frame = stuff_frame(payload)
    # フレーム内部には裸の 0x7E が末尾以外に無い
    assert FRAMECHAR not in frame[1:-1]
    bodies = unstuff_frames(frame)
    assert bodies[0][:-1] == payload


def test_bad_checksum_detected(cfg):
    payload = build_displ_payload(1_700_000_000, {6: 72})
    frame = bytearray(stuff_frame(payload))
    # チェックサム直前のバイトを壊す
    frame[-2] ^= 0xFF
    frames = decode_stream(bytes(frame), cfg)
    assert frames and frames[0].checksum_ok is False


def test_parse_header_fields():
    payload = build_displ_payload(1_700_000_000, {})
    header = parse_header(payload)
    assert header.r_maintype == DRI_MT_PHDB
    assert header.r_time == 1_700_000_000
    assert header.subrecords[0].sr_type == 1  # DISPL
    assert header.datetime_utc is not None


def test_decode_basic_numerics(cfg):
    # basic physdata: HR@6, NIBP-sys@78, TEMP1@92, SpO2@124
    payload = build_displ_payload(
        1_700_000_000, {6: 72, 78: 12000, 92: 3650, 124: 9800}
    )
    frames = decode_stream(stuff_frame(payload), cfg)
    assert len(frames) == 1
    vals = {v.name: v for v in frames[0].values}
    assert vals["HR"].value == pytest.approx(72)
    assert vals["HR"].unit == "/min"
    assert vals["NIBP"].value == pytest.approx(120.0)
    assert vals["TEMP1"].value == pytest.approx(36.5)
    assert vals["SpO2"].value == pytest.approx(98.0)


def test_invalid_value_marked(cfg):
    # DATA_INVALID (-32767) は valid=False
    payload = build_displ_payload(1_700_000_000, {6: -32767})
    frames = decode_stream(stuff_frame(payload), cfg)
    hr = next(v for v in frames[0].values if v.name == "HR")
    assert hr.valid is False
    assert hr.value is None


def test_stuff_frame_uses_standard_escapes():
    # 0x7E と 0x7D を含む本体 -> 標準 DRI/HDLC の 0x5E/0x5D でエスケープされる
    stuffed = stuff_frame(bytes([FRAMECHAR, 0x7D]))
    assert bytes([0x7D, 0x5E]) in stuffed  # 0x7E -> 7D 5E
    assert bytes([0x7D, 0x5D]) in stuffed  # 0x7D -> 7D 5D
    # ラウンドトリップも維持
    body = unstuff_frames(stuffed)[0]
    assert verify_checksum(body)
    assert body[:-1] == bytes([FRAMECHAR, 0x7D])


def test_phdb_request_frame():
    req = build_phdb_request(tx_interval=5)
    assert req[0] == FRAMECHAR and req[-1] == FRAMECHAR
    body = unstuff_frames(req)[0]
    assert verify_checksum(body)
    header = parse_header(body[:-1])
    assert header.r_len == 49  # 40 header + 9 phdb_request
    assert header.r_maintype == DRI_MT_PHDB
    assert header.subrecords[0].sr_type == DRI_PH_XMIT_REQ
    assert header.subrecords[1].sr_type == DRI_EOL_SUBR_LIST


def test_multiple_frames(cfg):
    p1 = stuff_frame(build_displ_payload(1_700_000_000, {6: 60}))
    p2 = stuff_frame(build_displ_payload(1_700_000_002, {6: 80}))
    frames = decode_stream(p1 + p2, cfg)
    assert len(frames) == 2
    hrs = [next(v.value for v in f.values if v.name == "HR") for f in frames]
    assert hrs == [60, 80]
