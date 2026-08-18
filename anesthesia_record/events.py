"""臨床イベント注記の入力・管理.

麻酔/手術の開始終了、挿管/抜管、切皮、体位変換などのタイムスタンプ付き注記。
投薬(MedEvent)とは別系統で、時間軸上の出来事を記録する。
外部YAML/JSONへの読み書きに対応（補助記録なので電子署名・確定操作は持たない）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import yaml

# よく使う標準イベント種別 -> 既定表示ラベル。
# プログラム外で自由ラベルも使えるよう、type は任意文字列を許容する。
STANDARD_EVENT_TYPES: dict[str, str] = {
    "anesthesia_start": "麻酔開始",
    "anesthesia_end": "麻酔終了",
    "surgery_start": "手術開始",
    "surgery_end": "手術終了",
    "induction": "導入",
    "intubation": "挿管",
    "extubation": "抜管",
    "incision": "執刀(切皮)",
    "closure": "閉創",
    "position_change": "体位変換",
    "position_supine": "仰臥位",
    "position_lateral": "側臥位",
    "position_prone": "腹臥位",
    "position_lithotomy": "砕石位",
    "position_sitting": "坐位",
    "tourniquet_on": "駆血開始",
    "tourniquet_off": "駆血解除",
    "block": "神経ブロック",
    "severity_start": "重症加算開始",
    "note": "メモ",
}

DEFAULT_EVENT_ICONS: dict[str, str] = {
    "anesthesia_start": "▲",
    "anesthesia_end": "▼",
    "surgery_start": "◆",
    "surgery_end": "◇",
    "induction": "●",
    "intubation": "▲",
    "extubation": "▼",
    "incision": "◆",
    "closure": "◇",
    "position_change": "↔",
    "position_supine": "○",
    "position_lateral": "▷",
    "position_prone": "▽",
    "position_lithotomy": "△",
    "position_sitting": "□",
    "tourniquet_on": "●",
    "tourniquet_off": "○",
    "block": "■",
    "severity_start": "★",
    "note": "•",
}

_TIME_FMT = "%Y-%m-%dT%H:%M:%S"


@dataclass
class ClinicalEvent:
    """臨床イベント1件."""

    time: datetime
    type: str
    label: Optional[str] = None
    note: Optional[str] = None
    icon: Optional[str] = None

    @property
    def display_label(self) -> str:
        if self.label:
            return self.label
        return STANDARD_EVENT_TYPES.get(self.type, self.type)

    @property
    def display_icon(self) -> str:
        if self.icon:
            return self.icon
        return DEFAULT_EVENT_ICONS.get(self.type, "#")


@dataclass
class EventLog:
    """臨床イベントの集合（時刻順に整列して返す）."""

    events: list[ClinicalEvent] = field(default_factory=list)

    def add(
        self,
        time: datetime,
        type: str,
        label: Optional[str] = None,
        note: Optional[str] = None,
        icon: Optional[str] = None,
    ) -> ClinicalEvent:
        ev = ClinicalEvent(time=time, type=type, icon=icon, label=label, note=note)
        self.events.append(ev)
        return ev

    def sorted(self) -> list[ClinicalEvent]:
        return sorted(self.events, key=lambda e: e.time)

    def between(self, start: datetime, end: datetime) -> list[ClinicalEvent]:
        return [e for e in self.sorted() if start <= e.time <= end]

    def to_dict(self) -> dict:
        return {
            "events": [
                {
                    "time": e.time.strftime(_TIME_FMT),
                    "type": e.type,
                    **({"icon": e.icon} if e.icon else {}),
                    **({"label": e.label} if e.label else {}),
                    **({"note": e.note} if e.note else {}),
                }
                for e in self.sorted()
            ]
        }

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(
                self.to_dict(), fh, allow_unicode=True, sort_keys=False
            )


def _parse_time(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.strptime(str(value).strip(), _TIME_FMT)


def load_event_log(path: str) -> EventLog:
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    log = EventLog()
    for raw in doc.get("events", []):
        if not isinstance(raw, dict) or "time" not in raw or "type" not in raw:
            raise ValueError(f"イベントに time/type が必要です: {raw!r}")
        log.add(
            time=_parse_time(raw["time"]),
            type=str(raw["type"]),
            icon=raw.get("icon"),
            label=raw.get("label"),
            note=raw.get("note"),
        )
    return log
