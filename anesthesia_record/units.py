"""投与量の単位変換: イベント → 投与質量(mg) / 投与容量(ml).

薬剤マスタの concentration は strength_unit（例 mg/ml）当たりの値とみなす。
質量系単位は mg に正規化する。輸液(ml/ml)は容量がそのまま"質量"扱いとなる。
"""

from __future__ import annotations

from typing import Optional

from .models import DrugMaster, MedEvent, Patient, Delivery

# 質量単位 → mg 係数
_MASS_TO_MG = {
    "g": 1000.0,
    "mg": 1.0,
    "ug": 1e-3,
    "mcg": 1e-3,
    "ng": 1e-6,
}


def _mass_factor(unit: str) -> Optional[float]:
    return _MASS_TO_MG.get(unit)


def consumed_amounts(
    drug: DrugMaster, event: MedEvent, patient: Optional[Patient] = None
) -> tuple[float, float]:
    """イベントの投与質量(mg)と投与容量(ml)を返す.

    対応単位:
      bolus:    mg, ug, ng, g / mg/kg, ug/kg / ml
      infusion: ml/h / mg/kg/h / ug/kg/min / mg/h / ug/min / ml（総量直接）
    濃度から mass<->volume を相互換算する。
    """
    conc = drug.concentration  # 例 mg/ml
    if conc <= 0:
        raise ValueError(f"concentration が不正: {drug.id}")

    if event.delivery is Delivery.BOLUS:
        value = event.dose
        unit = event.dose_unit
        if value is None or unit is None:
            raise ValueError(f"bolus には dose/dose_unit が必要: {drug.id}")
        return _amount_from_value(drug, patient, value, unit, conc, minutes=None)

    # infusion
    # 輸液残量入力方式: remaining_ml_start/end から消費量を直接算出
    if event.remaining_ml_start is not None and event.remaining_ml_end is not None:
        consumed_ml = event.remaining_ml_start - event.remaining_ml_end
        mass_mg = consumed_ml * conc  # conc = mg/ml (輸液は1 ml/ml)
        return mass_mg, consumed_ml

    minutes = event.duration_min()
    if event.rate is None or event.rate_unit is None:
        raise ValueError(f"infusion には rate/rate_unit が必要: {drug.id}")
    if minutes is None:
        raise ValueError(f"infusion には end_time が必要(区間確定): {drug.id}")
    return _amount_from_value(
        drug, patient, event.rate, event.rate_unit, conc, minutes=minutes
    )


def rate_mg_per_min(
    drug: DrugMaster, value: float, unit: str, patient: Optional[Patient] = None
) -> float:
    """レート指定を mg/min に換算する.

    infusion のみを想定し、時間成分のない単位（mg/kg や ml）は不正とする。
    """
    conc = drug.concentration
    if conc <= 0:
        raise ValueError(f"concentration が不正: {drug.id}")
    if unit == "ml":
        raise ValueError("ml は infusion rate としては不正です")
    parts = unit.split("/")
    if len(parts) == 1 and unit not in {"mg/h", "ug/h", "ng/h", "mcg/h"}:
        raise ValueError(f"時間成分のない単位は infusion rate として不正です: {unit}")
    if "h" not in parts[1:] and "min" not in parts[1:]:
        raise ValueError(f"時間成分のない単位は infusion rate として不正です: {unit}")
    if unit == "ml/h":
        return value / 60.0 * conc
    return _amount_from_value(drug, patient, value, unit, conc, minutes=1.0)[0]


def _amount_from_value(
    drug: DrugMaster,
    patient: Optional[Patient],
    value: float,
    unit: str,
    conc: float,
    minutes: Optional[float],
) -> tuple[float, float]:
    # 容量直接（ml, ml/h）
    if unit == "ml":
        volume = value if minutes is None else value  # bolus: ml総量
        if minutes is not None:
            # rate_unit=ml の指定は不正（総量はbolus扱い）。ここには来ない想定。
            pass
        return volume * conc, volume
    if unit == "ml/h":
        if minutes is None:
            raise ValueError("ml/h は infusion 専用")
        volume = value * (minutes / 60.0)
        return volume * conc, volume

    # 質量系（/kg, /h, /min を分解）
    mass_mg = _rate_or_dose_to_mg(patient, value, unit, minutes)
    volume = mass_mg / conc
    return mass_mg, volume


def _rate_or_dose_to_mg(
    patient: Optional[Patient], value: float, unit: str, minutes: Optional[float]
) -> float:
    parts = unit.split("/")
    base = parts[0]
    factor = _mass_factor(base)
    if factor is None:
        raise ValueError(f"未対応の単位: {unit}")
    amount = value * factor  # mg 基準（まだ /kg /time 未適用）

    per_kg = "kg" in parts[1:]
    per_min = "min" in parts[1:]
    per_h = "h" in parts[1:]

    if per_kg:
        if patient is None:
            raise ValueError(f"体重ベース単位 {unit} には patient が必要")
        amount *= patient.weight_kg

    if per_min or per_h:
        if minutes is None:
            raise ValueError(f"時間ベース単位 {unit} は infusion 専用")
        if per_min:
            amount *= minutes
        else:  # per_h
            amount *= minutes / 60.0

    return amount
