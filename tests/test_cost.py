import os
from datetime import datetime, timedelta

import pytest

from anesthesia_record.drug_master import load_drug_master
from anesthesia_record.models import MedEvent, Patient, Sex, Delivery
from anesthesia_record.cost import compute_cost

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "drug_master.yaml")


@pytest.fixture
def master():
    return load_drug_master(DATA)


@pytest.fixture
def patient():
    return Patient(age_years=50, sex=Sex.MALE, weight_kg=60, height_cm=170)


def test_round_up_vial_bolus(master, patient):
    # フェンタニル 0.1mg/2ml アンプル。150 ug 投与 -> 3ml -> 2アンプル(切上げ)
    t = datetime(2026, 6, 30, 9, 0, 0)
    ev = MedEvent("fentanyl_0_1mg", t, Delivery.BOLUS, dose=150, dose_unit="ug")
    rep = compute_cost([ev], master, patient)
    item = rep.items[0]
    assert item.total_mass_mg == pytest.approx(0.15)
    assert item.total_volume_ml == pytest.approx(3.0)
    assert item.containers_charged == 2  # ceil(3ml / 2ml)


def test_aggregate_across_events(master, patient):
    # 同一薬剤の複数回投与は合算してから容器数算定
    t = datetime(2026, 6, 30, 9, 0, 0)
    evs = [
        MedEvent("fentanyl_0_1mg", t, Delivery.BOLUS, dose=100, dose_unit="ug"),
        MedEvent("fentanyl_0_1mg", t, Delivery.BOLUS, dose=100, dose_unit="ug"),
    ]
    rep = compute_cost(evs, master, patient)
    item = rep.items[0]
    # 200ug -> 4ml -> 2アンプル
    assert item.total_volume_ml == pytest.approx(4.0)
    assert item.containers_charged == 2


def test_infusion_propofol_cost(master, patient):
    # プロポフォール 6 mg/kg/h を 60分 -> 360mg -> 36ml -> 2バイアル(20ml)
    t = datetime(2026, 6, 30, 9, 0, 0)
    ev = MedEvent(
        "propofol_1pct", t, Delivery.INFUSION,
        rate=6, rate_unit="mg/kg/h", end_time=t + timedelta(hours=1),
    )
    rep = compute_cost([ev], master, patient)
    item = rep.items[0]
    assert item.total_mass_mg == pytest.approx(360.0)
    assert item.total_volume_ml == pytest.approx(36.0)
    assert item.containers_charged == 2  # ceil(36/20)


def test_total_and_by_category(master, patient):
    t = datetime(2026, 6, 30, 9, 0, 0)
    evs = [
        MedEvent("fentanyl_0_1mg", t, Delivery.BOLUS, dose=100, dose_unit="ug"),
        MedEvent("rocuronium_50mg", t, Delivery.BOLUS, dose=50, dose_unit="mg"),
    ]
    rep = compute_cost(evs, master, patient)
    assert rep.total > 0
    cats = rep.by_category(master)
    assert "opioid" in cats and "muscle_relaxant" in cats
