"""局所麻酔薬の極量(最大投与量)管理.

体重あたりの最大投与量(mg/kg)に対する累積投与量を集計し、警告レベルを返す。
max_dose は施設基準・アドレナリン添加の有無で変わるため、薬剤マスタの
max_dose_mg_per_kg / max_dose_mg_per_kg_with_epi を参照する（要施設検証）。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from .drug_master import DrugMasterFile
from .models import MedEvent, Patient
from .units import consumed_amounts


@dataclass
class LocalAnestheticStatus:
    drug_id: str
    generic_name: str
    cumulative_mg: float
    max_mg: Optional[float]          # 体重×max_dose_mg_per_kg
    max_dose_mg_per_kg: Optional[float]
    with_epi: bool
    fraction: Optional[float]        # cumulative / max
    level: str                       # ok / caution / over / unknown

    @property
    def remaining_mg(self) -> Optional[float]:
        if self.max_mg is None:
            return None
        return self.max_mg - self.cumulative_mg


def _level(fraction: Optional[float]) -> str:
    if fraction is None:
        return "unknown"
    if fraction >= 1.0:
        return "over"
    if fraction >= 0.8:
        return "caution"
    return "ok"


def assess_local_anesthetics(
    events: list[MedEvent],
    master: DrugMasterFile,
    patient: Patient,
    with_epi: bool = False,
    caution_threshold: float = 0.8,
) -> list[LocalAnestheticStatus]:
    """局所麻酔薬カテゴリの薬剤について累積量と極量比を評価.

    with_epi=True かつ max_dose_mg_per_kg_with_epi が定義されていれば、
    アドレナリン添加時の極量を用いる。
    """
    cum: dict[str, float] = defaultdict(float)
    for ev in events:
        drug = master.get(ev.drug_id)
        if drug.category != "local_anesthetic":
            continue
        mass_mg, _ = consumed_amounts(drug, ev, patient)
        cum[ev.drug_id] += mass_mg

    out: list[LocalAnestheticStatus] = []
    for drug_id, total_mg in cum.items():
        drug = master.get(drug_id)
        per_kg = drug.max_dose_mg_per_kg
        if with_epi and drug.max_dose_mg_per_kg_with_epi is not None:
            per_kg = drug.max_dose_mg_per_kg_with_epi
        max_mg = None if per_kg is None else per_kg * patient.weight_kg
        fraction = None if max_mg in (None, 0) else total_mg / max_mg
        if fraction is None:
            level = "unknown"
        elif fraction >= 1.0:
            level = "over"
        elif fraction >= caution_threshold:
            level = "caution"
        else:
            level = "ok"
        out.append(
            LocalAnestheticStatus(
                drug_id=drug_id,
                generic_name=drug.generic_name,
                cumulative_mg=round(total_mg, 3),
                max_mg=None if max_mg is None else round(max_mg, 3),
                max_dose_mg_per_kg=per_kg,
                with_epi=with_epi,
                fraction=None if fraction is None else round(fraction, 3),
                level=level,
            )
        )
    out.sort(key=lambda s: s.drug_id)
    return out
