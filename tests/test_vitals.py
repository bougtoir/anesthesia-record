from datetime import datetime
import sys
import types

import pytest

from anesthesia_record.vitals import VitalsConfig, load_vitals, load_vitals_csv


def _write(tmp_path, text):
    p = tmp_path / "vitals.csv"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_csv_basic(tmp_path):
    p = _write(
        tmp_path,
        "Time,HR,SpO2\n"
        "2026-06-30T09:00:00,72,99\n"
        "2026-06-30T09:00:05,74,98\n"
        "2026-06-30T09:00:10,,97\n",
    )
    tbl = load_vitals_csv(str(p))
    assert set(tbl.parameter_names()) == {"HR", "SpO2"}
    hr = tbl.series("HR")
    assert hr.values == [72.0, 74.0, None]
    assert hr.times[0] == datetime(2026, 6, 30, 9, 0, 0)


def test_clock_offset_applied(tmp_path):
    p = _write(tmp_path, "Time,HR\n2026-06-30T09:00:00,72\n")
    tbl = load_vitals_csv(str(p), clock_offset_sec=10)
    assert tbl.series("HR").times[0] == datetime(2026, 6, 30, 9, 0, 10)


def test_load_vitals_auto_detects_vscapture_csv(tmp_path):
    p = _write(
        tmp_path,
        "Time,HR,SBP,DBP,SpO2,Temp\n"
        "2026-06-30T09:00:00,72,120,70,99,36.5\n",
    )
    tbl = load_vitals(str(p))
    assert set(tbl.parameter_names()) == {"HR", "SBP", "DBP", "SpO2", "Temp"}
    assert tbl.series("SBP").values == [120.0]


def test_load_vitals_explicit_vitalrecorder_csv(tmp_path):
    p = _write(
        tmp_path,
        "Time,ECG_HR,NIBP_SBP,NIBP_DBP,PLETH_SPO2\n"
        "2026-06-30T09:00:00,75,118,72,98\n",
    )
    tbl = load_vitals(
        str(p),
        VitalsConfig(source="vitalrecorder_csv", time_column="Time"),
    )
    assert set(tbl.parameter_names()) == {"HR", "SBP", "DBP", "SpO2"}
    assert tbl.series("HR").values == [75.0]


def test_load_vitals_ambiguous_csv_raises(tmp_path):
    p = _write(
        tmp_path,
        "Time,NIBP_SYS,NIBP_DBP,CO2_ET\n"
        "2026-06-30T09:00:00,120,70,35\n",
    )
    with pytest.raises(ValueError, match="曖昧"):
        load_vitals(str(p))


def test_load_vitals_auto_dispatches_vital(monkeypatch, tmp_path):
    fake_mod = types.SimpleNamespace()

    class FakeSeries:
        def __init__(self, values):
            self._values = values

        def tolist(self):
            return self._values

    class FakeDF:
        columns = ["ECG_HR"]

        def __getitem__(self, item):
            return FakeSeries([72.0, 73.0])

    class FakeVitalFile:
        def __init__(self, path):
            self.path = path
            self.dtstart = 0

        def get_track_names(self):
            return ["ECG_HR"]

        def to_pandas(self, track_names, interval_sec):
            return FakeDF()

    fake_mod.VitalFile = FakeVitalFile
    monkeypatch.setitem(sys.modules, "vitaldb", fake_mod)
    p = tmp_path / "sample.vital"
    p.write_text("dummy", encoding="utf-8")
    tbl = load_vitals(str(p))
    assert tbl.series("ECG_HR").values == [72.0, 73.0]
    assert tbl.series("ECG_HR").times[1] == datetime(1970, 1, 1, 0, 0, 1)
