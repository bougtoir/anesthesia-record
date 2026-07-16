"""バイタル取り込み: VSCapture/VitalRecorder の CSV 読み込みと時刻整合.

- VSCapture / VitalRecorder のエクスポート CSV を想定（1列目が時刻、残りがパラメータ）。
- モニタ時計と PC 時計のズレを clock_offset_sec で補正できる。
- `.vital` ファイルは vitaldb パッケージがあれば読み込む（任意依存）。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_TIME_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%H:%M:%S",
]

_CANONICAL_NAMES = ("HR", "SBP", "DBP", "SpO2", "EtCO2", "BIS", "Temp")

_VSCAPTURE_ALIASES: dict[str, tuple[str, ...]] = {
    "HR": ("HR", "PULSE", "PULSE_RATE"),
    "SBP": ("NIBP_SYS", "NIBP_SBP", "SBP", "SYS"),
    "DBP": ("NIBP_DIA", "NIBP_DBP", "DBP", "DIA"),
    "SpO2": ("SPO2", "SpO2", "PLETH_SPO2", "SAT"),
    "EtCO2": ("ETCO2", "EtCO2", "etCO2", "CO2_ET"),
    "BIS": ("BIS",),
    "Temp": ("TEMP", "Temp", "TEMP1", "Temperature"),
}

_VITALRECORDER_ALIASES: dict[str, tuple[str, ...]] = {
    "HR": ("ECG_HR",),
    "SBP": ("NIBP_SBP", "NIBP_SYS", "ABP_SYS"),
    "DBP": ("NIBP_DBP", "NIBP_DIA", "ABP_DIA"),
    "SpO2": ("PLETH_SPO2",),
    "EtCO2": ("CO2_ET",),
    "BIS": ("BIS",),
    "Temp": ("TEMP1",),
}


def _parse_time(value: str) -> datetime:
    v = value.strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    raise ValueError(f"時刻列を解釈できません: {value!r}")


@dataclass
class VitalSeries:
    """単一パラメータの時系列."""

    name: str
    times: list[datetime] = field(default_factory=list)
    values: list[Optional[float]] = field(default_factory=list)


@dataclass
class Waveform:
    """連続波形."""

    name: str
    sample_rate_hz: float
    start_time: datetime
    values: list[Optional[float]] = field(default_factory=list)


@dataclass
class VitalsTable:
    parameters: dict[str, VitalSeries]
    time_column: str
    waveforms: dict[str, Waveform] = field(default_factory=dict)

    def series(self, name: str) -> VitalSeries:
        return self.parameters[name]

    def parameter_names(self) -> list[str]:
        return list(self.parameters)


@dataclass(frozen=True)
class VitalsConfig:
    source: str = "auto"
    time_column: Optional[str] = None
    column_map: Optional[dict[str, str]] = None
    clock_offset_sec: float = 0.0
    delimiter: str = ","
    interval_sec: float = 1.0


def _normalize(name: str) -> str:
    return "".join(ch for ch in name.strip().upper() if ch.isalnum())


def _build_alias_lookup(alias_map: dict[str, tuple[str, ...]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, aliases in alias_map.items():
        for alias in aliases:
            lookup[_normalize(alias)] = canonical
    return lookup


_VSCAPTURE_LOOKUP = _build_alias_lookup(_VSCAPTURE_ALIASES)
_VITALRECORDER_LOOKUP = _build_alias_lookup(_VITALRECORDER_ALIASES)


def _default_column_map(source: str) -> dict[str, str]:
    if source not in {"vscapture_csv", "vitalrecorder_csv"}:
        raise ValueError(f"不明な source です: {source!r}")
    out: dict[str, str] = {}
    for canonical, aliases in (
        _VSCAPTURE_ALIASES.items() if source == "vscapture_csv" else _VITALRECORDER_ALIASES.items()
    ):
        for alias in aliases:
            out[alias] = canonical
    return out


def _detect_csv_source(header: list[str]) -> str:
    counts = {"vscapture_csv": 0, "vitalrecorder_csv": 0}
    matched = {"vscapture_csv": set(), "vitalrecorder_csv": set()}
    for col in header:
        norm = _normalize(col)
        if norm in _VSCAPTURE_LOOKUP:
            counts["vscapture_csv"] += 1
            matched["vscapture_csv"].add(_VSCAPTURE_LOOKUP[norm])
        if norm in _VITALRECORDER_LOOKUP:
            counts["vitalrecorder_csv"] += 1
            matched["vitalrecorder_csv"].add(_VITALRECORDER_LOOKUP[norm])
    if counts["vscapture_csv"] == 0 and counts["vitalrecorder_csv"] == 0:
        raise ValueError(
            "CSV の列名から VSCapture/VitalRecorder を判定できません。"
            " source='vscapture_csv' または source='vitalrecorder_csv' を明示してください。"
        )
    if counts["vscapture_csv"] and counts["vitalrecorder_csv"]:
        if counts["vscapture_csv"] > counts["vitalrecorder_csv"] and matched["vscapture_csv"] - matched["vitalrecorder_csv"]:
            return "vscapture_csv"
        if counts["vitalrecorder_csv"] > counts["vscapture_csv"] and matched["vitalrecorder_csv"] - matched["vscapture_csv"]:
            return "vitalrecorder_csv"
        raise ValueError(
            "CSV の列名が VSCapture/VitalRecorder の両方にまたがっており曖昧です。"
            " source='vscapture_csv' か source='vitalrecorder_csv' を明示してください。"
        )
    return "vscapture_csv" if counts["vscapture_csv"] else "vitalrecorder_csv"


def _resolve_column_map(header: list[str], column_map: Optional[dict[str, str]], source: str) -> dict[str, str]:
    if column_map is not None:
        return dict(column_map)
    default_map = _default_column_map(source)
    resolved: dict[str, str] = {}
    for col in header:
        if col in default_map:
            resolved[col] = default_map[col]
            continue
        norm = _normalize(col)
        if source == "vscapture_csv" and norm in _VSCAPTURE_LOOKUP:
            resolved[col] = _VSCAPTURE_LOOKUP[norm]
        elif source == "vitalrecorder_csv" and norm in _VITALRECORDER_LOOKUP:
            resolved[col] = _VITALRECORDER_LOOKUP[norm]
        else:
            resolved[col] = col
    return resolved


def _load_csv_vitals(
    path: str,
    *,
    time_column: Optional[str],
    column_map: Optional[dict[str, str]],
    clock_offset_sec: float,
    delimiter: str,
    source: str,
) -> VitalsTable:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        header = next(reader, None)
        if not header:
            raise ValueError("CSV が空です")

        tcol = time_column or header[0]
        if tcol not in header:
            raise ValueError(f"時刻列 {tcol!r} が見つかりません: {header}")
        tidx = header.index(tcol)
        resolved = _resolve_column_map(header, column_map, source)
        series: dict[str, VitalSeries] = {}
        for i, h in enumerate(header):
            if i == tidx:
                continue
            name = resolved.get(h, h)
            if name not in series:
                series[name] = VitalSeries(name=name)

        offset = timedelta(seconds=clock_offset_sec)
        for row in reader:
            if not row or len(row) <= tidx:
                continue
            try:
                t = _parse_time(row[tidx]) + offset
            except ValueError:
                continue
            row_values: dict[str, Optional[float]] = {}
            for i, h in enumerate(header):
                if i == tidx:
                    continue
                raw = row[i].strip() if i < len(row) else ""
                try:
                    row_values[resolved.get(h, h)] = float(raw) if raw != "" else None
                except ValueError:
                    row_values[resolved.get(h, h)] = None
            for name, s in series.items():
                s.times.append(t)
                s.values.append(row_values.get(name))

    return VitalsTable(parameters=series, time_column=tcol)


def load_vitals_csv(
    path: str,
    time_column: Optional[str] = None,
    clock_offset_sec: float = 0.0,
    delimiter: str = ",",
) -> VitalsTable:
    """CSV を読み込み、各パラメータの時系列に変換する."""
    return _load_csv_vitals(
        path,
        time_column=time_column,
        column_map=None,
        clock_offset_sec=clock_offset_sec,
        delimiter=delimiter,
        source="vscapture_csv",
    )


def load_vital_file(path: str, interval_sec: float = 1.0) -> VitalsTable:
    """VitalRecorder の .vital を読み込む（vitaldb 任意依存）."""
    try:
        import vitaldb  # type: ignore
    except ImportError as exc:  # pragma: no cover - 任意依存
        raise ImportError(
            ".vital の読み込みには vitaldb パッケージが必要です "
            "(pip install vitaldb)"
        ) from exc

    vf = vitaldb.VitalFile(path)
    track_names = vf.get_track_names()
    df = vf.to_pandas(track_names, interval_sec)
    base = datetime.fromtimestamp(vf.dtstart) if getattr(vf, "dtstart", 0) else datetime(1970, 1, 1)

    params: dict[str, VitalSeries] = {}
    for name in track_names:
        if name not in df.columns:
            continue
        s = VitalSeries(name=name)
        for i, v in enumerate(df[name].tolist()):
            s.times.append(base + timedelta(seconds=i * interval_sec))
            s.values.append(None if v != v else float(v))  # NaN -> None
        params[name] = s
    return VitalsTable(parameters=params, time_column="time")


def load_vitals(path: str, config: VitalsConfig = VitalsConfig()) -> VitalsTable:
    """入力ソースを自動判定してバイタルを読み込む."""
    suffix = Path(path).suffix.lower()
    source = config.source
    if source == "auto":
        if suffix == ".vital":
            source = "vital"
        elif suffix in {".csv", ".txt"}:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                reader = csv.reader(fh, delimiter=config.delimiter)
                header = next(reader, None)
            if not header:
                raise ValueError("CSV が空です")
            source = _detect_csv_source(header)
        else:
            raise ValueError(
                f"入力形式を自動判定できません: {path!r}. source を明示してください。"
            )

    if source == "vital":
        return load_vital_file(path, interval_sec=config.interval_sec)
    if source not in {"vscapture_csv", "vitalrecorder_csv"}:
        raise ValueError(f"不明な source です: {source!r}")
    return _load_csv_vitals(
        path,
        time_column=config.time_column,
        column_map=config.column_map,
        clock_offset_sec=config.clock_offset_sec,
        delimiter=config.delimiter,
        source=source,
    )
