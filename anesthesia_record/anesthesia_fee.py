"""麻酔料算定モジュール.

外部YAMLファイルから麻酔方法・特殊体位・重症加算の設定を読み込み、
イベント情報から診療報酬点数を算定する。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

import yaml


# --- データモデル ---


@dataclass
class TimeFee:
    """時間区分別点数."""

    max_hours: Optional[float]  # None = 上限なし（以降全て）
    points_per_30min: int


@dataclass
class AnesthesiaMethod:
    """麻酔方法."""

    id: str
    name: str
    category: str
    icon_start: str
    icon_end: str
    time_fees: list[TimeFee]
    base_points: int = 0


@dataclass
class PositionSurcharge:
    """特殊体位加算."""

    id: str
    name: str
    icon: str
    points_per_case: int
    note: str = ""


@dataclass
class SeveritySurcharge:
    """重症加算."""

    id: str
    name: str
    multiplier: float
    conditions: list[str] = field(default_factory=list)
    additional_points: int = 0


@dataclass
class AnesthesiaFeeConfig:
    """麻酔料設定全体."""

    methods: list[AnesthesiaMethod]
    exclusive_categories: list[list[str]]
    positions: list[PositionSurcharge]
    supine_icon: str
    severity: list[SeveritySurcharge]

    def get_method(self, method_id: str) -> Optional[AnesthesiaMethod]:
        for m in self.methods:
            if m.id == method_id:
                return m
        return None

    def get_position(self, position_id: str) -> Optional[PositionSurcharge]:
        for p in self.positions:
            if p.id == position_id:
                return p
        return None

    def get_severity(self, severity_id: str) -> Optional[SeveritySurcharge]:
        for s in self.severity:
            if s.id == severity_id:
                return s
        return None


def load_anesthesia_fee(path: str | Path) -> AnesthesiaFeeConfig:
    """YAML設定ファイルを読み込む."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    methods = []
    for m in data.get("methods", []):
        time_fees = [TimeFee(tf["max_hours"], tf["points_per_30min"]) for tf in m.get("time_fees", [])]
        methods.append(AnesthesiaMethod(
            id=m["id"], name=m["name"], category=m["category"],
            icon_start=m["icon_start"], icon_end=m["icon_end"],
            time_fees=time_fees, base_points=m.get("base_points", 0),
        ))

    positions = []
    for p in data.get("positions", []):
        positions.append(PositionSurcharge(
            id=p["id"], name=p["name"], icon=p["icon"],
            points_per_case=p["points_per_case"], note=p.get("note", ""),
        ))

    severity = []
    for s in data.get("severity", []):
        severity.append(SeveritySurcharge(
            id=s["id"], name=s["name"], multiplier=s["multiplier"],
            conditions=s.get("conditions", []),
            additional_points=s.get("additional_points", 0),
        ))

    return AnesthesiaFeeConfig(
        methods=methods,
        exclusive_categories=data.get("exclusive_categories", []),
        positions=positions,
        supine_icon=data.get("supine_icon", "⊙"),
        severity=severity,
    )


# --- 算定イベント ---


@dataclass
class AnesthesiaEvent:
    """麻酔実施イベント（開始〜終了を1件とする）."""

    method_id: str
    start_time: datetime
    end_time: datetime


@dataclass
class PositionEvent:
    """特殊体位イベント（開始〜仰臥位復帰）."""

    position_id: str
    start_time: datetime
    end_time: datetime  # 仰臥位に戻った時刻


@dataclass
class FeeLineItem:
    """算定結果1行."""

    name: str
    points: int
    detail: str = ""


@dataclass
class AnesthesiaFeeResult:
    """算定結果全体."""

    items: list[FeeLineItem] = field(default_factory=list)

    @property
    def total_points(self) -> int:
        return sum(item.points for item in self.items)


# --- 算定ロジック ---


def _calc_time_points(method: AnesthesiaMethod, duration_min: float) -> int:
    """時間区分に基づく点数計算."""
    total_points = method.base_points
    remaining_min = duration_min
    prev_hours = 0.0

    for tf in method.time_fees:
        if tf.max_hours is not None:
            segment_hours = tf.max_hours - prev_hours
            segment_min = segment_hours * 60
        else:
            segment_min = remaining_min  # 残り全部

        applicable_min = min(remaining_min, segment_min)
        if applicable_min <= 0:
            break

        # 30分単位で切り上げ
        units = math.ceil(applicable_min / 30)
        total_points += units * tf.points_per_30min

        remaining_min -= applicable_min
        if tf.max_hours is not None:
            prev_hours = tf.max_hours

    return total_points


def _resolve_exclusive(
    events: Sequence[AnesthesiaEvent],
    config: AnesthesiaFeeConfig,
    duration_map: dict[str, float],
) -> list[AnesthesiaEvent]:
    """同時算定不可ルール適用: 同一排他カテゴリ内で最高点数のものだけ残す."""
    # カテゴリ → 排他グループ番号
    cat_to_group: dict[str, int] = {}
    for gidx, group in enumerate(config.exclusive_categories):
        for cat in group:
            cat_to_group[cat] = gidx

    # グループごとに最高点数のイベントを選択
    group_best: dict[int, tuple[int, AnesthesiaEvent]] = {}
    independent: list[AnesthesiaEvent] = []

    for ev in events:
        method = config.get_method(ev.method_id)
        if method is None:
            continue
        group_id = cat_to_group.get(method.category)
        if group_id is None:
            independent.append(ev)
            continue
        pts = _calc_time_points(method, duration_map[ev.method_id])
        if group_id not in group_best or pts > group_best[group_id][0]:
            group_best[group_id] = (pts, ev)

    result = list(independent)
    for _, ev in group_best.values():
        result.append(ev)
    return result


def compute_anesthesia_fee(
    anesthesia_events: Sequence[AnesthesiaEvent],
    position_events: Sequence[PositionEvent],
    severity_ids: Sequence[str],
    config: AnesthesiaFeeConfig,
) -> AnesthesiaFeeResult:
    """麻酔料を算定する.

    Args:
        anesthesia_events: 麻酔実施イベント（方法ごとの開始〜終了）
        position_events: 特殊体位イベント
        severity_ids: 適用する重症加算のID一覧
        config: 麻酔料設定

    Returns:
        算定結果（行項目リスト）
    """
    result = AnesthesiaFeeResult()

    # 1. 麻酔方法別の時間点数
    duration_map: dict[str, float] = {}
    for ev in anesthesia_events:
        dur = (ev.end_time - ev.start_time).total_seconds() / 60.0
        duration_map[ev.method_id] = dur

    # 排他ルール適用
    billable = _resolve_exclusive(anesthesia_events, config, duration_map)

    ga_points = 0  # 全身麻酔料（重症加算の基数）
    for ev in billable:
        method = config.get_method(ev.method_id)
        if method is None:
            continue
        dur = duration_map[ev.method_id]
        points = _calc_time_points(method, dur)
        hours = dur / 60
        result.items.append(FeeLineItem(
            name=method.name,
            points=points,
            detail=f"{hours:.1f}h ({dur:.0f}分)",
        ))
        if method.category == "general":
            ga_points += points

    # 2. 特殊体位加算
    for pev in position_events:
        pos = config.get_position(pev.position_id)
        if pos is None:
            continue
        dur_min = (pev.end_time - pev.start_time).total_seconds() / 60.0
        result.items.append(FeeLineItem(
            name=f"特殊体位({pos.name})",
            points=pos.points_per_case,
            detail=f"{dur_min:.0f}分",
        ))

    # 3. 重症加算
    for sev_id in severity_ids:
        sev = config.get_severity(sev_id)
        if sev is None:
            continue
        if sev.multiplier != 1.0 and ga_points > 0:
            # 全身麻酔料に係数を適用（差分を加算）
            surcharge = int(ga_points * (sev.multiplier - 1.0))
            result.items.append(FeeLineItem(
                name=sev.name,
                points=surcharge,
                detail=f"全身麻酔料{ga_points}点 × {sev.multiplier}倍 の増分",
            ))
        if sev.additional_points > 0:
            result.items.append(FeeLineItem(
                name=sev.name,
                points=sev.additional_points,
                detail="追加点数",
            ))

    return result
