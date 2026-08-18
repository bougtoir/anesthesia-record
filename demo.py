"""デモ: 薬剤マスタ読込→コスト算定→局所麻酔極量→Ce推定→チャート出力.

実行:
    python demo.py
出力:
    demo_chart.png （バイタル + 投薬注記 + Ce）
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta

from anesthesia_record.drug_master import load_drug_master
from anesthesia_record.models import MedEvent, Patient, Sex, Delivery, OutputEvent, OutputCategory
from anesthesia_record.cost import compute_cost
from anesthesia_record.local_anesthetic import assess_local_anesthetics
from anesthesia_record.events import EventLog
from anesthesia_record import pkpd
from anesthesia_record.vitals import VitalsTable, VitalSeries, Waveform
from anesthesia_record.chart import render_chart
from anesthesia_record.chart_erga import render_chart_erga
from anesthesia_record.anesthesia_fee import (
    AnesthesiaEvent,
    PositionEvent,
    compute_anesthesia_fee,
    load_anesthesia_fee,
)

DATA = os.path.join(os.path.dirname(__file__), "data", "drug_master.yaml")
FEE_DATA = os.path.join(os.path.dirname(__file__), "data", "anesthesia_fee.yaml")


def _synthetic_vitals(t0: datetime, minutes: int) -> VitalsTable:
    hr = VitalSeries("HR")
    sbp = VitalSeries("SBP")
    dbp = VitalSeries("DBP")
    spo2 = VitalSeries("SpO2")
    temp = VitalSeries("Temp")
    for i in range(minutes * 6):  # 10秒間隔
        t = t0 + timedelta(seconds=10 * i)
        m = i / 6.0
        hr.times.append(t)
        hr.values.append(72 + 8 * math.sin(m / 5.0))
        sbp.times.append(t)
        sbp.values.append(120 + 15 * math.sin(m / 8.0 + 1))
        dbp.times.append(t)
        dbp.values.append(70 + 8 * math.sin(m / 7.0 + 0.4))
        if i < minutes * 5:
            spo2.times.append(t)
            spo2.values.append(99 - 0.2 * math.sin(m / 5.0))
        else:
            spo2.times.append(t)
            spo2.values.append(None)
        temp.times.append(t)
        temp.values.append(36.5 + 0.1 * math.sin(m / 8.0))
    return VitalsTable(
        parameters={"HR": hr, "SBP": sbp, "DBP": dbp, "SpO2": spo2, "Temp": temp},
        time_column="Time",
    )


def _synthetic_ecg_waveform(t0: datetime, minutes: int) -> Waveform:
    sample_rate_hz = 300.0
    duration_sec = minutes * 60
    samples = int(duration_sec * sample_rate_hz)
    values: list[float] = []
    beat_period = 60.0 / 72.0
    for i in range(samples):
        sec = i / sample_rate_hz
        phase = sec % beat_period
        x = phase / beat_period
        value = 0.0
        value += 0.08 * math.exp(-((x - 0.16) / 0.03) ** 2)
        value += -0.12 * math.exp(-((x - 0.32) / 0.012) ** 2)
        value += 1.1 * math.exp(-((x - 0.35) / 0.01) ** 2)
        value += -0.18 * math.exp(-((x - 0.38) / 0.015) ** 2)
        value += 0.22 * math.exp(-((x - 0.58) / 0.06) ** 2)
        value += 0.02 * math.sin(sec * 2.0 * math.pi / 8.0)
        values.append(value)
    return Waveform(name="ECG", sample_rate_hz=sample_rate_hz, start_time=t0, values=values)


def main() -> None:
    master = load_drug_master(DATA)
    patient = Patient(age_years=45, sex=Sex.MALE, weight_kg=65, height_cm=172, asa_ps=2)
    t0 = datetime(2026, 6, 30, 9, 0, 0)
    clinical_log = EventLog()
    clinical_log.add(t0, "anesthesia_start", icon="▲")
    clinical_log.add(t0 + timedelta(minutes=3), "intubation")
    clinical_log.add(t0 + timedelta(minutes=12), "incision")
    clinical_log.add(t0 + timedelta(minutes=28), "surgery_end")
    clinical_log.add(t0 + timedelta(minutes=32), "anesthesia_end", icon="▼")

    events = [
        MedEvent("fentanyl_0_1mg", t0, Delivery.BOLUS, dose=100, dose_unit="ug", note="導入"),
        MedEvent("propofol_1pct", t0 + timedelta(seconds=30), Delivery.BOLUS,
                 dose=130, dose_unit="mg", note="導入"),
        MedEvent("rocuronium_50mg", t0 + timedelta(minutes=1), Delivery.BOLUS,
                 dose=50, dose_unit="mg"),
        MedEvent("remifentanil_2mg", t0 + timedelta(minutes=2), Delivery.INFUSION,
                 rate=0.2, rate_unit="ug/kg/min", end_time=t0 + timedelta(minutes=30)),
        MedEvent("propofol_1pct", t0 + timedelta(minutes=2), Delivery.INFUSION,
                 rate=6, rate_unit="mg/kg/h", end_time=t0 + timedelta(minutes=30)),
        MedEvent("ropivacaine_0_75pct", t0 + timedelta(minutes=5), Delivery.BOLUS,
                 dose=15, dose_unit="ml", note="末梢神経ブロック"),
        # 輸液（残量入力方式）
        MedEvent("acetated_ringer_500", t0, Delivery.INFUSION,
                 end_time=t0 + timedelta(minutes=32),
                 remaining_ml_start=500, remaining_ml_end=393),
    ]

    print("=== コスト算定 ===")
    rep = compute_cost(events, master, patient)
    for it in rep.items:
        price = "薬価未設定" if it.cost is None else f"{it.cost:.0f}円"
        print(f"  {it.generic_name:12s} {it.total_mass_mg:8.2f}mg "
              f"-> {it.containers_charged}容器 {price} ({it.billing_rule})")
    print(f"  合計: {rep.total:.0f}円 (薬剤/輸液費のみ・サンプル薬価)")

    print("\n=== 局所麻酔薬 極量 ===")
    for st in assess_local_anesthetics(events, master, patient):
        print(f"  {st.generic_name}: {st.cumulative_mg}mg / 上限{st.max_mg}mg "
              f"({st.fraction}) -> {st.level}")

    print("\n=== 効果部位濃度(Ce) ===")
    ce_results = {}
    for drug_id in ("propofol_1pct", "remifentanil_2mg", "fentanyl_0_1mg"):
        drug = master.get(drug_id)
        res = pkpd.simulate(drug, patient, events, t0, duration_min=90, dt_s=1.0)
        ce_results[drug_id] = res
        approx = " (近似/要検証)" if res.approximate else ""
        print(f"  {drug.generic_name}[{res.model}]: Ce_max={res.ce_max:.2f} "
              f"{res.conc_unit}{approx}")

    vitals = _synthetic_vitals(t0, 30)
    ecg_waveform = _synthetic_ecg_waveform(t0, 35)
    out = render_chart(
        vitals, events, master, "demo_chart.png",
        ce_results=ce_results, ce_t0=t0, title="麻酔記録(デモ)",
        clinical_events=clinical_log.sorted(),
    )
    print(f"\nチャート出力: {out}")

    # 出血・尿量デモデータ
    output_evts = [
        OutputEvent(OutputCategory.GAUZE, t0 + timedelta(minutes=14), 30),
        OutputEvent(OutputCategory.GAUZE, t0 + timedelta(minutes=22), 50),
        OutputEvent(OutputCategory.SUCTION, t0 + timedelta(minutes=16), 20),
        OutputEvent(OutputCategory.SUCTION, t0 + timedelta(minutes=26), 35),
        OutputEvent(OutputCategory.URINE, t0 + timedelta(minutes=20), 50),
        OutputEvent(OutputCategory.URINE, t0 + timedelta(minutes=30), 80),
    ]

    # --- 麻酔料算定 ---
    fee_config = load_anesthesia_fee(FEE_DATA)
    anes_events = [
        AnesthesiaEvent("general_anesthesia", t0, t0 + timedelta(minutes=32)),
    ]
    pos_events = [
        PositionEvent("lateral", t0 + timedelta(minutes=5), t0 + timedelta(minutes=25)),
    ]
    fee_result = compute_anesthesia_fee(anes_events, pos_events, ["critical"], fee_config)
    print("\n=== 麻酔料算定 ===")
    for item in fee_result.items:
        print(f"  {item.name}: {item.points}点 ({item.detail})")
    print(f"  合計: {fee_result.total_points}点")

    # 臨床イベントに特殊体位を追加
    clinical_log.add(t0 + timedelta(minutes=5), "position_lateral")
    clinical_log.add(t0 + timedelta(minutes=25), "position_supine")

    postop = [
        "アセトアミノフェン 1000mg IV 6h毎",
        "フルルビプロフェン 50mg IV 疼痛時",
        "メトクロプラミド 10mg IV 嘔気時",
        "飲水: 覚醒2h後から可",
    ]

    out_erga = render_chart_erga(
        vitals, events, master, "demo_chart_erga.png",
        patient=patient, clinical_events=clinical_log.sorted(),
        cost_report=rep, ce_results=ce_results, ce_t0=t0,
        show_floating_latest=True, latest_panel_loc="upper right",
        ce_horizon_min=60, ecg_waveform=ecg_waveform,
        ecg_snapshot_times=[t0 + timedelta(minutes=10), t0 + timedelta(minutes=20), t0 + timedelta(minutes=25)],
        output_events=output_evts,
        postop_orders=postop,
        anesthesia_fee_result=fee_result,
        title="麻酔記録(院内様式)",
    )
    print(f"院内様式チャート出力: {out_erga}")


if __name__ == "__main__":
    main()
