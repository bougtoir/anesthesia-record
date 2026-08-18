import os

import pytest

from anesthesia_record.drug_master import load_drug_master
from anesthesia_record.models import Delivery

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "drug_master.yaml")


def test_load_master():
    m = load_drug_master(DATA)
    assert m.schema_version == 1
    prop = m.get("propofol_1pct")
    assert prop.generic_name == "プロポフォール"
    assert prop.supports(Delivery.INFUSION)
    assert prop.mass_per_container == pytest.approx(200.0)  # 10 mg/ml * 20 ml


def test_categories_and_la():
    m = load_drug_master(DATA)
    la = m.by_category("local_anesthetic")
    assert any(d.id == "lidocaine_1pct" for d in la)
    lido = m.get("lidocaine_1pct")
    assert lido.max_dose_mg_per_kg == 4.5
    assert lido.max_dose_mg_per_kg_with_epi == 7.0


def test_missing_drug_raises():
    m = load_drug_master(DATA)
    with pytest.raises(KeyError):
        m.get("does_not_exist")


def test_nhi_price_date_is_string_after_load(tmp_path):
    yaml_text = """\
schema_version: 1
drugs:
  - id: demo
    generic_name: Demo
    category: anesthetic
    container_volume_ml: 1
    concentration: 1
    strength_unit: mg/ml
    package_unit: vial
    delivery: [bolus]
    dose_units: [mg]
    nhi_price_date: 2024-04-01
"""
    path = tmp_path / "master.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    m = load_drug_master(path)
    assert m.get("demo").nhi_price_date == "2024-04-01"
