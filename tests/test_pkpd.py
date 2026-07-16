import os
from datetime import datetime, timedelta

import pytest

from anesthesia_record.drug_master import load_drug_master
from anesthesia_record.models import MedEvent, Patient, Sex, Delivery
from anesthesia_record import pkpd

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "drug_master.yaml")


@pytest.fixture
def master():
    return load_drug_master(DATA)


@pytest.fixture
def patient():
    return Patient(age_years=40, sex=Sex.MALE, weight_kg=70, height_cm=170)


def test_propofol_bolus_ce_rises_and_decays(master, patient):
    drug = master.get("propofol_1pct")
    t0 = datetime(2026, 6, 30, 9, 0, 0)
    ev = MedEvent("propofol_1pct", t0, Delivery.BOLUS, dose=140, dose_unit="mg")
    res = pkpd.simulate(drug, patient, [ev], t0, duration_min=15, dt_s=1.0)
    assert res.conc_unit == "ug/ml"
    # Cp は初期に最大、Ce は遅れてピーク
    assert res.cp[1] > 0
    ce_peak_idx = max(range(len(res.ce)), key=lambda i: res.ce[i])
    cp_peak_idx = max(range(len(res.cp)), key=lambda i: res.cp[i])
    assert ce_peak_idx > cp_peak_idx           # 効果部位はヒステリシスで遅れる
    assert res.ce[-1] < res.ce[ce_peak_idx]    # その後は減衰


def test_remifentanil_infusion_reaches_steady(master, patient):
    drug = master.get("remifentanil_2mg")
    t0 = datetime(2026, 6, 30, 9, 0, 0)
    ev = MedEvent(
        "remifentanil_2mg", t0, Delivery.INFUSION,
        rate=0.2, rate_unit="ug/kg/min", end_time=t0 + timedelta(minutes=20),
    )
    res = pkpd.simulate(drug, patient, [ev], t0, duration_min=20, dt_s=1.0)
    assert res.conc_unit == "ng/ml"
    assert res.ce_max > 0
    # 持続投与中は単調増加に近い（後半が前半より高い）
    assert res.ce[-1] > res.ce[len(res.ce) // 4]


def test_non_pkpd_drug_raises(master, patient):
    drug = master.get("rocuronium_50mg")
    t0 = datetime(2026, 6, 30, 9, 0, 0)
    with pytest.raises(ValueError):
        pkpd.simulate(drug, patient, [], t0, duration_min=5)


def test_open_ended_remifentanil_infusion_contributes_ce(master, patient):
    drug = master.get("remifentanil_2mg")
    t0 = datetime(2026, 6, 30, 9, 0, 0)
    ev = MedEvent(
        "remifentanil_2mg", t0, Delivery.INFUSION,
        rate=0.2, rate_unit="ug/kg/min", end_time=None,
    )
    res = pkpd.simulate(drug, patient, [ev], t0, duration_min=20, dt_s=1.0)
    assert res.ce_max > 0
    assert res.ce[-1] > res.ce[len(res.ce) // 4]
