"""コスト算定: 剤型(容器)を考慮した薬剤・輸液費の集計.

billing_rule:
  round_up_vial : 容器単位に切上げ（残液破棄も課金）。既定。
  per_ml / exact: 使用量按分（残液は課金しない）。

注意: nhi_price は施設で要検証のサンプル値。手技料・管理料は含めない（薬剤/輸液費のみ）。
複数バイアルにまたがる薬剤は、同一薬剤の総使用量を合算してから容器数を算定する。
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from .drug_master import DrugMasterFile
from .models import MedEvent, Patient
from .units import consumed_amounts


@dataclass
class DrugCost:
    drug_id: str
    generic_name: str
    total_volume_ml: float
    total_mass_mg: float
    containers_charged: float
    unit_price: Optional[float]
    cost: Optional[float]
    billing_rule: str
    price_unverified: bool = True


@dataclass
class CostReport:
    items: list[DrugCost]

    @property
    def total(self) -> float:
        return sum(i.cost for i in self.items if i.cost is not None)

    def by_category(self, master: DrugMasterFile) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for i in self.items:
            cat = master.get(i.drug_id).category
            if i.cost is not None:
                out[cat] += i.cost
        return dict(out)


def _containers(total_volume_ml: float, container_ml: float, rule: str) -> float:
    if container_ml <= 0:
        raise ValueError("container_volume_ml が不正")
    frac = total_volume_ml / container_ml
    if rule == "round_up_vial":
        return float(math.ceil(frac - 1e-9)) if total_volume_ml > 0 else 0.0
    if rule in ("per_ml", "exact"):
        return frac
    raise ValueError(f"未対応の billing_rule: {rule}")


def compute_cost(
    events: list[MedEvent],
    master: DrugMasterFile,
    patient: Optional[Patient] = None,
) -> CostReport:
    """同一薬剤の使用量を合算し、剤型(容器)単位でコストを算定."""
    vol: dict[str, float] = defaultdict(float)
    mass: dict[str, float] = defaultdict(float)

    for ev in events:
        drug = master.get(ev.drug_id)
        m_mg, v_ml = consumed_amounts(drug, ev, patient)
        vol[ev.drug_id] += v_ml
        mass[ev.drug_id] += m_mg

    items: list[DrugCost] = []
    for drug_id, total_v in vol.items():
        drug = master.get(drug_id)
        containers = _containers(total_v, drug.container_volume_ml, drug.billing_rule)
        unit_price = drug.nhi_price
        cost = None if unit_price is None else containers * unit_price
        items.append(
            DrugCost(
                drug_id=drug_id,
                generic_name=drug.generic_name,
                total_volume_ml=round(total_v, 4),
                total_mass_mg=round(mass[drug_id], 4),
                containers_charged=containers,
                unit_price=unit_price,
                cost=None if cost is None else round(cost, 2),
                billing_rule=drug.billing_rule,
            )
        )
    items.sort(key=lambda i: i.drug_id)
    return CostReport(items=items)
