"""anesthesia-record: 研究用麻酔記録チャート生成ツール.

使い方:
    python main.py case.yaml                  # 症例ファイルからチャート生成
    python main.py case.yaml -o output.png    # 出力先を指定
    python main.py --demo                     # デモデータでチャート生成

症例ファイル (YAML) の書式は docs/manual.md を参照してください。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from anesthesia_record.drug_master import load_drug_master
from anesthesia_record.models import (
    Delivery,
    MedEvent,
    OutputCategory,
    OutputEvent,
    Patient,
    Sex,
)
from anesthesia_record.cost import compute_cost
from anesthesia_record.events import EventLog
from anesthesia_record import pkpd
from anesthesia_record.vitals import VitalsConfig, VitalsTable, VitalSeries, load_vitals
from anesthesia_record.chart_erga import render_chart_erga
from anesthesia_record.anesthesia_fee import (
    AnesthesiaEvent,
    PositionEvent,
    compute_anesthesia_fee,
    load_anesthesia_fee,
)


def _base_dir() -> Path:
    """PyInstaller exe or script directory."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).parent


def _resolve_data(rel: str) -> str:
    return str(_base_dir() / rel)


def _parse_datetime(s: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"日時を解釈できません: {s!r}")


def _load_case(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_patient(data: dict) -> Patient:
    sex = Sex.MALE if data.get("sex", "male").lower() in ("male", "m", "男") else Sex.FEMALE
    return Patient(
        age_years=data["age"],
        sex=sex,
        weight_kg=data["weight_kg"],
        height_cm=data["height_cm"],
        patient_id=data.get("id"),
        asa_ps=data.get("asa_ps"),
    )


def _build_events(data: list[dict], t0: datetime) -> list[MedEvent]:
    events: list[MedEvent] = []
    for d in data:
        t = _parse_datetime(d["time"]) if "time" in d else t0 + timedelta(minutes=d.get("min", 0))
        delivery = Delivery.INFUSION if d.get("delivery", "bolus") == "infusion" else Delivery.BOLUS
        kwargs: dict[str, Any] = {
            "drug_id": d["drug_id"],
            "start_time": t,
            "delivery": delivery,
        }
        if "dose" in d:
            kwargs["dose"] = d["dose"]
            kwargs["dose_unit"] = d.get("dose_unit", "mg")
        if "rate" in d:
            kwargs["rate"] = d["rate"]
            kwargs["rate_unit"] = d.get("rate_unit", "mg/kg/h")
        if "end_time" in d:
            kwargs["end_time"] = _parse_datetime(d["end_time"])
        elif "end_min" in d:
            kwargs["end_time"] = t0 + timedelta(minutes=d["end_min"])
        if "remaining_ml_start" in d:
            kwargs["remaining_ml_start"] = d["remaining_ml_start"]
            kwargs["remaining_ml_end"] = d.get("remaining_ml_end", 0)
        if "note" in d:
            kwargs["note"] = d["note"]
        events.append(MedEvent(**kwargs))
    return events


def _build_clinical(data: list[dict], t0: datetime) -> EventLog:
    log = EventLog()
    for d in data:
        t = _parse_datetime(d["time"]) if "time" in d else t0 + timedelta(minutes=d.get("min", 0))
        log.add(t, d["name"], icon=d.get("icon"))
    return log


def _build_outputs(data: list[dict], t0: datetime) -> list[OutputEvent]:
    cat_map = {
        "gauze": OutputCategory.GAUZE,
        "suction": OutputCategory.SUCTION,
        "urine": OutputCategory.URINE,
    }
    events: list[OutputEvent] = []
    for d in data:
        t = _parse_datetime(d["time"]) if "time" in d else t0 + timedelta(minutes=d.get("min", 0))
        cat = cat_map.get(d["category"].lower(), OutputCategory.GAUZE)
        events.append(OutputEvent(cat, t, d["amount"]))
    return events


def run_case(case_path: str, output_path: str | None = None) -> str:
    """症例ファイルからチャートを生成する."""
    case = _load_case(case_path)
    case_dir = Path(case_path).parent

    # 薬剤マスタ
    master_path = case.get("drug_master", _resolve_data("data/drug_master.yaml"))
    if not Path(master_path).is_absolute():
        master_path = str(case_dir / master_path)
    master = load_drug_master(master_path)

    # 麻酔料設定
    fee_path = case.get("anesthesia_fee", _resolve_data("data/anesthesia_fee.yaml"))
    if not Path(fee_path).is_absolute():
        fee_path = str(case_dir / fee_path)

    # 患者
    patient = _build_patient(case["patient"])

    # 手術開始時刻
    t0 = _parse_datetime(case.get("start_time", case["patient"].get("start_time", "2026-01-01 09:00")))

    # 投薬イベント
    events = _build_events(case.get("medications", []), t0)

    # 臨床イベント
    clinical_log = _build_clinical(case.get("clinical_events", []), t0)

    # 出血・尿量
    output_evts = _build_outputs(case.get("outputs", []), t0)

    # バイタル
    vitals: VitalsTable | None = None
    vitals_path = case.get("vitals_file")
    if vitals_path:
        if not Path(vitals_path).is_absolute():
            vitals_path = str(case_dir / vitals_path)
        cfg = VitalsConfig(
            source=case.get("vitals_source", "auto"),
            clock_offset_sec=case.get("clock_offset_sec", 0.0),
        )
        vitals = load_vitals(vitals_path, cfg)

    if vitals is None:
        # 最小限のバイタル(時間軸のみ)
        duration = case.get("duration_min", 60)
        hr = VitalSeries("HR")
        for i in range(duration * 6):
            hr.times.append(t0 + timedelta(seconds=10 * i))
            hr.values.append(None)
        vitals = VitalsTable(parameters={"HR": hr}, time_column="Time")

    # コスト算定
    cost_report = compute_cost(events, master, patient)

    # Ce推定
    ce_results = {}
    ce_drugs = case.get("ce_drugs", [])
    for drug_id in ce_drugs:
        drug = master.get(drug_id)
        if drug is None:
            continue
        duration_min = case.get("ce_horizon_min", 90)
        res = pkpd.simulate(drug, patient, events, t0, duration_min=duration_min, dt_s=1.0)
        ce_results[drug_id] = res

    # 麻酔料
    fee_result = None
    if "anesthesia_events" in case:
        fee_config = load_anesthesia_fee(fee_path)
        anes_events = [
            AnesthesiaEvent(d["type"], _parse_datetime(d["start"]), _parse_datetime(d["end"]))
            for d in case["anesthesia_events"]
        ]
        pos_events = [
            PositionEvent(d["position"], _parse_datetime(d["start"]), _parse_datetime(d["end"]))
            for d in case.get("position_events", [])
        ]
        severity = case.get("severity_flags", [])
        fee_result = compute_anesthesia_fee(anes_events, pos_events, severity, fee_config)

    # 術後指示
    postop = case.get("postop_orders", [])

    # 出力
    if output_path is None:
        output_path = str(case_dir / "anesthesia_record.png")

    out = render_chart_erga(
        vitals, events, master, output_path,
        patient=patient,
        clinical_events=clinical_log.sorted(),
        cost_report=cost_report,
        ce_results=ce_results,
        ce_t0=t0,
        show_floating_latest=case.get("show_floating_latest", True),
        latest_panel_loc=case.get("latest_panel_loc", "upper right"),
        ce_horizon_min=case.get("ce_horizon_min", 90),
        output_events=output_evts,
        postop_orders=postop,
        anesthesia_fee_result=fee_result,
        title=case.get("title", "麻酔記録"),
    )
    return out


def run_demo(output_path: str | None = None) -> str:
    """デモデータでチャート出力."""
    from demo import main as demo_main
    demo_main()
    return "demo_chart_erga.png"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="anesthesia-record: 研究用麻酔記録チャート生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="詳細は docs/manual.md を参照してください。",
    )
    parser.add_argument("case_file", nargs="?", help="症例定義ファイル (YAML)")
    parser.add_argument("-o", "--output", help="出力ファイルパス (デフォルト: anesthesia_record.png)")
    parser.add_argument("--demo", action="store_true", help="デモデータでチャートを生成")
    parser.add_argument("--version", action="version", version="anesthesia-record v0.1")

    args = parser.parse_args()

    if args.demo:
        out = run_demo(args.output)
        print(f"デモチャート出力: {out}")
        return

    if not args.case_file:
        parser.print_help()
        print("\n" + "=" * 50)
        print("使用例:")
        print("  anesthesia_record case.yaml")
        print("  anesthesia_record case.yaml -o output.png")
        print("  anesthesia_record --demo")
        print("=" * 50)
        input("\nEnter で終了...")
        return

    if not Path(args.case_file).exists():
        print(f"[エラー] ファイルが見つかりません: {args.case_file}")
        sys.exit(1)

    out = run_case(args.case_file, args.output)
    print(f"チャート出力: {out}")


if __name__ == "__main__":
    main()
