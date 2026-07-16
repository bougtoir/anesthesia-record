"""薬剤マスタ（外部YAML）のローダ.

プログラム外の参照ファイルを読み込み、ホットリロードを可能にする。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

from .models import DrugMaster, Delivery

SUPPORTED_SCHEMA_VERSION = 1


def _to_delivery_list(values: list[str]) -> list[Delivery]:
    return [Delivery(v) for v in values]


def _parse_drug(raw: dict) -> DrugMaster:
    known = {
        "id",
        "generic_name",
        "brand_name",
        "category",
        "formulation",
        "container_volume_ml",
        "concentration",
        "strength_unit",
        "package_unit",
        "nhi_price",
        "nhi_price_date",
        "billing_rule",
        "routes",
        "delivery",
        "dose_units",
        "unit_bolus",
        "unit_rate",
        "pkpd_enabled",
        "pk_model",
        "color",
        "max_dose_mg_per_kg",
        "max_dose_mg_per_kg_with_epi",
        "fluid_type",
        "is_output",
        "display_order",
    }
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"未知のフィールド {sorted(unknown)} (drug id={raw.get('id')})")

    data = dict(raw)
    data["delivery"] = _to_delivery_list(data.get("delivery", []))
    data.setdefault("billing_rule", "round_up_vial")
    data.setdefault("routes", [])
    data.setdefault("pkpd_enabled", False)
    data.setdefault("is_output", False)
    if data.get("nhi_price_date") is not None:
        data["nhi_price_date"] = str(data["nhi_price_date"])
    return DrugMaster(**data)


@dataclass
class DrugMasterFile:
    """読み込んだ薬剤マスタ全体。id 引きを提供。"""

    path: str
    schema_version: int
    drugs: dict[str, DrugMaster]

    def get(self, drug_id: str) -> DrugMaster:
        try:
            return self.drugs[drug_id]
        except KeyError as exc:
            raise KeyError(f"薬剤 id '{drug_id}' がマスタに存在しません") from exc

    def by_category(self, category: str) -> list[DrugMaster]:
        return [d for d in self.drugs.values() if d.category == category]

    def reload(self) -> "DrugMasterFile":
        """ファイルを再読込した新しいインスタンスを返す（ホットリロード）."""
        return load_drug_master(self.path)


def load_drug_master(path: str) -> DrugMasterFile:
    if not os.path.exists(path):
        raise FileNotFoundError(f"薬剤マスタが見つかりません: {path}")
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)

    version = doc.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"未対応の schema_version={version} "
            f"(対応={SUPPORTED_SCHEMA_VERSION})"
        )

    drugs: dict[str, DrugMaster] = {}
    for raw in doc.get("drugs", []):
        drug = _parse_drug(raw)
        if drug.id in drugs:
            raise ValueError(f"薬剤 id の重複: {drug.id}")
        drugs[drug.id] = drug

    return DrugMasterFile(path=path, schema_version=version, drugs=drugs)
