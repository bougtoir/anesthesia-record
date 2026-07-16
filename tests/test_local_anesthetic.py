import os
from datetime import datetime

import pytest

from anesthesia_record.drug_master import load_drug_master
from anesthesia_record.models import MedEvent, Patient, Sex, Delivery
from anesthesia_record.local_anesthetic import assess_local_anesthetics

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "drug_master.yaml")


@pytest.fixture
def master():
    return load_drug_master(DATA)


def test_lidocaine_within_limit():
    m = load_drug_master(DATA)
    p = Patient(age_years=50, sex=Sex.MALE, weight_kg=60, height_cm=170)
    # 60kg * 4.5 = 270mg 上限。100mg投与 -> ok
    t = datetime(2026, 6, 30, 9, 0, 0)
    ev = MedEvent("lidocaine_1pct", t, Delivery.BOLUS, dose=100, dose_unit="mg")
    st = assess_local_anesthetics([ev], m, p)[0]
    assert st.max_mg == pytest.approx(270.0)
    assert st.level == "ok"
    assert st.remaining_mg == pytest.approx(170.0)


def test_lidocaine_over_limit():
    m = load_drug_master(DATA)
    p = Patient(age_years=50, sex=Sex.MALE, weight_kg=60, height_cm=170)
    t = datetime(2026, 6, 30, 9, 0, 0)
    ev = MedEvent("lidocaine_1pct", t, Delivery.BOLUS, dose=300, dose_unit="mg")
    st = assess_local_anesthetics([ev], m, p)[0]
    assert st.level == "over"
    assert st.fraction > 1.0


def test_with_epi_raises_limit():
    m = load_drug_master(DATA)
    p = Patient(age_years=50, sex=Sex.MALE, weight_kg=60, height_cm=170)
    t = datetime(2026, 6, 30, 9, 0, 0)
    ev = MedEvent("lidocaine_1pct", t, Delivery.BOLUS, dose=300, dose_unit="mg")
    st = assess_local_anesthetics([ev], m, p, with_epi=True)[0]
    # 60kg * 7.0 = 420mg 上限 -> 300mg は ok
    assert st.max_mg == pytest.approx(420.0)
    assert st.level == "ok"


def test_caution_threshold_can_raise_ok_level():
    m = load_drug_master(DATA)
    p = Patient(age_years=50, sex=Sex.MALE, weight_kg=60, height_cm=170)
    t = datetime(2026, 6, 30, 9, 0, 0)
    ev = MedEvent("lidocaine_1pct", t, Delivery.BOLUS, dose=230, dose_unit="mg")
    st = assess_local_anesthetics([ev], m, p, caution_threshold=0.9)[0]
    assert st.fraction == pytest.approx(0.852, rel=1e-3)
    assert st.level == "ok"
