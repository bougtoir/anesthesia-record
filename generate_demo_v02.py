"""v0.2 デモチャート生成スクリプト.

GUIを使わずにchart_liveモジュールでリアルタイム描画のサンプル出力を生成する。
"""

from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure

from anesthesia_record.gui.session import AnesthesiaSession
from anesthesia_record.gui.chart_live import render_live_chart
from anesthesia_record.models import Delivery, MedEvent, OutputCategory, OutputEvent, Patient, Sex
from anesthesia_record.events import ClinicalEvent
from anesthesia_record.drug_master import load_drug_master
from anesthesia_record.anesthesia_fee import load_anesthesia_fee
from anesthesia_record.vitals import VitalsTable, VitalSeries

import numpy as np


def _generate_demo_vitals(t_start: datetime, duration_min: int = 120) -> VitalsTable:
    """デモ用のバイタルデータを生成."""
    params: dict[str, VitalSeries] = {}
    n_points = duration_min * 2  # 30秒間隔

    times = [t_start + timedelta(seconds=30 * i) for i in range(n_points)]

    # HR: 60-80
    hr_vals = 70 + 5 * np.sin(np.linspace(0, 4 * np.pi, n_points)) + np.random.normal(0, 2, n_points)
    params["HR"] = VitalSeries(name="HR", times=list(times), values=[float(v) for v in hr_vals])

    # SBP: 110-130
    sbp_times = [t_start + timedelta(minutes=5 * i) for i in range(duration_min // 5)]
    sbp_vals = 120 + 8 * np.sin(np.linspace(0, 2 * np.pi, len(sbp_times))) + np.random.normal(0, 3, len(sbp_times))
    params["SBP"] = VitalSeries(name="SBP", times=list(sbp_times), values=[float(v) for v in sbp_vals])

    # DBP: 60-80
    dbp_vals = 70 + 5 * np.sin(np.linspace(0, 2 * np.pi, len(sbp_times))) + np.random.normal(0, 2, len(sbp_times))
    params["DBP"] = VitalSeries(name="DBP", times=list(sbp_times), values=[float(v) for v in dbp_vals])

    # SpO2: 97-100
    spo2_vals = 98.5 + 0.8 * np.sin(np.linspace(0, 3 * np.pi, n_points)) + np.random.normal(0, 0.3, n_points)
    spo2_vals = np.clip(spo2_vals, 95, 100)
    params["SpO2"] = VitalSeries(name="SpO2", times=list(times), values=[float(v) for v in spo2_vals])

    return VitalsTable(parameters=params, time_column="time")


def main():
    base_dir = Path(__file__).parent
    drug_master = load_drug_master(str(base_dir / "data" / "drug_master.yaml"))
    fee_config = load_anesthesia_fee(str(base_dir / "data" / "anesthesia_fee.yaml"))

    # セッション構築
    session = AnesthesiaSession()
    t0 = datetime(2024, 6, 1, 9, 0, 0)

    session.patient = Patient(
        patient_id="DEMO-001",
        age_years=55,
        sex=Sex.MALE,
        weight_kg=70,
        height_cm=172,
        asa_ps=2,
    )
    session.anesthesia_start = t0
    session.anesthesia_end = t0 + timedelta(hours=2, minutes=15)

    # イベント
    session.events = [
        ClinicalEvent(time=t0, type="anesthesia_start", label="麻酔開始"),
        ClinicalEvent(time=t0 + timedelta(minutes=5), type="intubation", label="挿管"),
        ClinicalEvent(time=t0 + timedelta(minutes=15), type="surgery_start", label="執刀"),
        ClinicalEvent(time=t0 + timedelta(minutes=100), type="surgery_end", label="閉創"),
        ClinicalEvent(time=t0 + timedelta(minutes=125), type="extubation", label="抜管"),
        ClinicalEvent(time=t0 + timedelta(hours=2, minutes=15), type="anesthesia_end", label="麻酔終了"),
    ]

    # 薬剤投与
    session.med_events = [
        # プロポフォール導入
        MedEvent(drug_id="propofol_1pct", start_time=t0 + timedelta(minutes=2),
                 delivery=Delivery.BOLUS, dose=140.0, dose_unit="mg"),
        # レミフェンタニル持続
        MedEvent(drug_id="remifentanil_2mg", start_time=t0 + timedelta(minutes=1),
                 delivery=Delivery.INFUSION, rate=0.25, rate_unit="ug/kg/min",
                 end_time=t0 + timedelta(minutes=120)),
        # ロクロニウム
        MedEvent(drug_id="rocuronium_50mg", start_time=t0 + timedelta(minutes=4),
                 delivery=Delivery.BOLUS, dose=50.0, dose_unit="mg"),
        # プロポフォール追加
        MedEvent(drug_id="propofol_1pct", start_time=t0 + timedelta(minutes=35),
                 delivery=Delivery.BOLUS, dose=30.0, dose_unit="mg"),
        # 輸液
        MedEvent(drug_id="acetated_ringer_500", start_time=t0,
                 delivery=Delivery.INFUSION,
                 remaining_ml_start=500, remaining_ml_end=120,
                 end_time=t0 + timedelta(minutes=130)),
    ]

    # 出血・尿量
    session.output_events = [
        OutputEvent(category=OutputCategory.GAUZE, time=t0 + timedelta(minutes=30), amount=20.0),
        OutputEvent(category=OutputCategory.GAUZE, time=t0 + timedelta(minutes=60), amount=35.0),
        OutputEvent(category=OutputCategory.SUCTION, time=t0 + timedelta(minutes=50), amount=50.0),
        OutputEvent(category=OutputCategory.SUCTION, time=t0 + timedelta(minutes=80), amount=30.0),
        OutputEvent(category=OutputCategory.URINE, time=t0 + timedelta(minutes=60), amount=150.0),
        OutputEvent(category=OutputCategory.URINE, time=t0 + timedelta(minutes=120), amount=100.0),
    ]

    # バイタル
    session.vitals = _generate_demo_vitals(t0, duration_min=135)

    # 描画
    fig = Figure(figsize=(11.69, 8.27), dpi=150)
    render_live_chart(fig, session, drug_master, fee_config)

    out_path = base_dir / "demo_chart_v02.png"
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight", facecolor="white")
    print(f"デモチャート出力: {out_path}")


if __name__ == "__main__":
    main()
