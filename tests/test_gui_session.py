"""GUIセッションモジュールのテスト."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anesthesia_record.gui.session import AnesthesiaSession
from anesthesia_record.models import Delivery, MedEvent, OutputCategory, OutputEvent, Patient, Sex
from anesthesia_record.events import ClinicalEvent
from anesthesia_record.drug_master import load_drug_master


@pytest.fixture
def drug_master():
    return load_drug_master(str(Path(__file__).parent.parent / "data" / "drug_master.yaml"))


@pytest.fixture
def session_with_data():
    s = AnesthesiaSession()
    s.patient = Patient(
        patient_id="TEST001",
        age_years=50,
        sex=Sex.MALE,
        weight_kg=70,
        height_cm=170,
        asa_ps=2,
    )
    now = datetime(2024, 6, 1, 9, 0, 0)
    s.anesthesia_start = now
    s.events.append(ClinicalEvent(time=now, type="anesthesia_start", label="麻酔開始"))
    s.med_events.append(MedEvent(
        drug_id="propofol_1pct",
        start_time=now + timedelta(minutes=5),
        delivery=Delivery.BOLUS,
        dose=120.0,
        dose_unit="mg",
    ))
    s.med_events.append(MedEvent(
        drug_id="remifentanil_2mg",
        start_time=now + timedelta(minutes=3),
        delivery=Delivery.INFUSION,
        rate=0.3,
        rate_unit="ug/kg/min",
        end_time=now + timedelta(minutes=60),
    ))
    s.output_events.append(OutputEvent(
        category=OutputCategory.URINE,
        time=now + timedelta(minutes=30),
        amount=100.0,
    ))
    return s


class TestAnesthesiaSession:
    def test_initial_state(self):
        s = AnesthesiaSession()
        assert s.patient is None
        assert s.anesthesia_start is None
        assert not s.is_active
        assert s.elapsed_minutes() is None

    def test_is_active(self, session_with_data):
        assert session_with_data.is_active
        session_with_data.anesthesia_end = datetime(2024, 6, 1, 11, 0, 0)
        assert not session_with_data.is_active

    def test_elapsed_minutes(self, session_with_data):
        session_with_data.anesthesia_end = datetime(2024, 6, 1, 10, 30, 0)
        assert session_with_data.elapsed_minutes() == pytest.approx(90.0)

    def test_time_range(self, session_with_data):
        start, end = session_with_data.time_range
        assert start == datetime(2024, 6, 1, 9, 0, 0)
        # end は now() になるので anesthesia_start 以降であること
        assert end >= start

    def test_save_and_load_yaml(self, session_with_data, drug_master, tmp_path):
        path = str(tmp_path / "test_session.yaml")
        session_with_data.save_to_yaml(path)

        # 読み込み
        loaded = AnesthesiaSession()
        loaded.load_from_yaml(path, drug_master)

        assert loaded.patient is not None
        assert loaded.patient.patient_id == "TEST001"
        assert loaded.patient.weight_kg == 70
        assert loaded.anesthesia_start == datetime(2024, 6, 1, 9, 0, 0)
        assert len(loaded.med_events) == 2
        assert loaded.med_events[0].drug_id == "propofol_1pct"
        assert loaded.med_events[0].dose == 120.0
        assert loaded.med_events[1].drug_id == "remifentanil_2mg"
        assert loaded.med_events[1].rate == 0.3
        assert len(loaded.events) == 1
        assert loaded.events[0].type == "anesthesia_start"
        assert len(loaded.output_events) == 1
        assert loaded.output_events[0].category == OutputCategory.URINE
        assert loaded.output_events[0].amount == 100.0


class TestVitalsMonitor:
    def test_csv_monitor_creation(self):
        from anesthesia_record.gui.vitals_monitor import VitalsMonitor
        vm = VitalsMonitor(mode="csv", path="/tmp/test.csv", interval_sec=1.0)
        assert vm.mode == "csv"
        assert not vm._running

    def test_tcp_monitor_creation(self):
        from anesthesia_record.gui.vitals_monitor import VitalsMonitor
        vm = VitalsMonitor(mode="tcp", host="localhost", port=8887)
        assert vm.mode == "tcp"
        assert vm.host == "localhost"
        assert vm.port == 8887

    def test_parse_tcp_line(self):
        from anesthesia_record.gui.vitals_monitor import VitalsMonitor
        vm = VitalsMonitor(mode="tcp")
        series_data: dict = {}
        vm._parse_tcp_line("2024-06-01 09:00:00,72,98,120,70,35,60,36.5", series_data)
        assert "HR" in series_data
        assert series_data["HR"][0][1] == 72.0
        assert "SpO2" in series_data
        assert series_data["SpO2"][0][1] == 98.0

    def test_build_vitals_table(self):
        from anesthesia_record.gui.vitals_monitor import VitalsMonitor
        vm = VitalsMonitor(mode="tcp")
        series_data = {
            "HR": [(datetime(2024, 1, 1, 9, 0), 72.0), (datetime(2024, 1, 1, 9, 1), 75.0)],
            "SpO2": [(datetime(2024, 1, 1, 9, 0), 98.0)],
        }
        vt = vm._build_vitals_table(series_data)
        assert "HR" in vt.parameters
        assert len(vt.parameters["HR"].values) == 2
        assert vt.parameters["SpO2"].values[0] == 98.0
