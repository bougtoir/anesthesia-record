from pathlib import Path

import pytest

from anesthesia_record.paperchart_config import (
    load_paperchart_config,
    parse_paperchart_config,
)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "paperchart" / "b650.txt"


@pytest.fixture(scope="module")
def cfg():
    return load_paperchart_config(str(CONFIG_PATH))


def test_b650_config_file_exists():
    assert CONFIG_PATH.is_file(), f"設定ファイルが見つかりません: {CONFIG_PATH}"


def test_b650_config_is_shift_jis():
    raw = CONFIG_PATH.read_bytes()
    # cp932 でデコードでき、UTF-8 では壊れる（= Shift-JIS で保存されている）
    raw.decode("cp932")
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")


def test_b650_config_uses_crlf():
    raw = CONFIG_PATH.read_bytes()
    assert b"\r\n" in raw
    # 素の LF（CRLF でない改行）が無いこと
    assert raw.replace(b"\r\n", b"").find(b"\n") == -1


def test_port_and_validation(cfg):
    assert cfg.rs232c_port == "com1:"
    assert cfg.validate() == []


def test_core_pdm_numerics_present(cfg):
    # B650+PDM 単体で取得できる循環/SpO2/体温系
    for name in ("HR", "BP1", "NIBP", "SpO2", "TEMP1"):
        assert cfg.numeric(name) is not None, f"{name} が numerics に無い"


def test_numeric_offsets_match_dri_layout(cfg):
    # DRI physdata 上のオフセット/スケールが S/5(=Bx50) 共通値であること
    hr = cfg.numeric("HR")
    assert (hr.subrecord_class, hr.group_offset, hr.scale) == (0, 0, 1.0)
    assert hr.unit == "/min"

    bp1 = cfg.numeric("BP1")
    assert (bp1.subrecord_class, bp1.group_offset) == (0, 16)
    assert bp1.labelset == "lblP"
    assert bp1.scale == pytest.approx(0.01)

    nibp = cfg.numeric("NIBP")
    assert nibp.group_offset == 72
    assert nibp.unit == "mmHg"


def test_labelsets_defined_and_referenced(cfg):
    assert set(cfg.labels) >= {"lblP", "lblT", "lblEA", "lblIA"}
    # BP 系ラベルに ART/CVP 等が含まれる
    assert "ART" in cfg.labels["lblP"]
    assert "Tesop" in cfg.labels["lblT"]


def test_waves_and_initial_waves_resolve(cfg):
    ecg1 = cfg.wave("ecg1")
    assert ecg1 is not None
    assert ecg1.subrecord_type == 1
    assert ecg1.samples_per_sec == 300
    # initial_waves の各 subrecord type が waves に存在する
    types = {w.subrecord_type for w in cfg.waves}
    for t in cfg.initial_waves:
        assert t in types


def test_error_messages_present(cfg):
    assert {"err_success", "err_silent", "err_comm", "err_first"} <= set(
        cfg.messages
    )
    # B650 向けに書き換えたメッセージであること
    assert any("B650" in line for line in cfg.messages["err_first"])


def test_comment_stripping_preserves_units_with_slash():
    text = 'numerics {\n MVex = 0 178 "" 20 - n 0.01 L/min;\n}\n'
    cfg = parse_paperchart_config(text)
    mvex = cfg.numeric("MVex")
    assert mvex is not None
    assert mvex.unit == "L/min"
    assert mvex.scale == pytest.approx(0.01)


def test_validate_detects_unknown_labelset():
    text = 'numerics {\n BPX = 0 16 lblMissing 6 - y 0.01 mmHg;\n}\n'
    cfg = parse_paperchart_config(text)
    problems = cfg.validate()
    assert any("lblMissing" in p for p in problems)
