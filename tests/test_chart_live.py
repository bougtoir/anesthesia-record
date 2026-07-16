"""chart_live モジュールのテスト."""

from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
import pytest

from anesthesia_record.gui.chart_live import render_live_chart, _get_drug_rows
from anesthesia_record.gui.session import AnesthesiaSession
from anesthesia_record.models import Delivery, MedEvent, OutputCategory, OutputEvent, Patient, Sex
from anesthesia_record.events import ClinicalEvent
from anesthesia_record.drug_master import load_drug_master
from anesthesia_record.anesthesia_fee import load_anesthesia_fee


@pytest.fixture
def drug_master():
    return load_drug_master(str(Path(__file__).parent.parent / "data" / "drug_master.yaml"))


@pytest.fixture
def fee_config():
    return load_anesthesia_fee(str(Path(__file__).parent.parent / "data" / "anesthesia_fee.yaml"))


@pytest.fixture
def active_session():
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
    s.events.append(ClinicalEvent(time=now + timedelta(minutes=10), type="surgery_start", label="執刀"))
    s.anesthesia_end = now + timedelta(hours=2)

    # 薬剤イベント
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
        end_time=now + timedelta(minutes=90),
    ))

    # 出血・尿量
    s.output_events.append(OutputEvent(
        category=OutputCategory.URINE,
        time=now + timedelta(minutes=30),
        amount=100.0,
    ))
    s.output_events.append(OutputEvent(
        category=OutputCategory.GAUZE,
        time=now + timedelta(minutes=45),
        amount=30.0,
    ))
    return s


class TestRenderLiveChart:
    def test_empty_session_renders(self, drug_master, fee_config):
        fig = Figure(figsize=(12, 7))
        session = AnesthesiaSession()
        render_live_chart(fig, session, drug_master, fee_config)
        # 開始前メッセージが表示される
        axes = fig.get_axes()
        assert len(axes) == 1
        plt.close(fig)

    def test_active_session_renders(self, active_session, drug_master, fee_config):
        fig = Figure(figsize=(12, 7))
        render_live_chart(fig, active_session, drug_master, fee_config)
        axes = fig.get_axes()
        # ヘッダ + バイタル + 薬剤行(2) + 出血尿量 + 下部バー = 6以上
        assert len(axes) >= 5
        plt.close(fig)

    def test_chart_export_to_png(self, active_session, drug_master, fee_config, tmp_path):
        fig = Figure(figsize=(12, 7))
        render_live_chart(fig, active_session, drug_master, fee_config)
        out_path = tmp_path / "test_output.png"
        fig.savefig(str(out_path), dpi=100, bbox_inches="tight")
        assert out_path.exists()
        assert out_path.stat().st_size > 1000  # 非空のPNG
        plt.close(fig)


class TestGetDrugRows:
    def test_drug_ordering(self, drug_master):
        now = datetime(2024, 6, 1, 9, 0, 0)
        events = [
            MedEvent(drug_id="acetated_ringer_500", start_time=now, delivery=Delivery.INFUSION,
                     remaining_ml_start=500),
            MedEvent(drug_id="propofol_1pct", start_time=now, delivery=Delivery.BOLUS,
                     dose=120, dose_unit="mg"),
            MedEvent(drug_id="remifentanil_2mg", start_time=now, delivery=Delivery.INFUSION,
                     rate=0.3, rate_unit="ug/kg/min"),
        ]
        rows = _get_drug_rows(events, drug_master)
        keys = list(rows.keys())
        # iv_anesthetic (propofol) before opioid (remifentanil) before fluid
        assert keys.index("propofol_1pct") < keys.index("remifentanil_2mg")
        # fluid は最後
        assert keys.index("acetated_ringer_500") == len(keys) - 1
