"""麻酔セッション: リアルタイムのデータコンテナ."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from ..models import Delivery, MedEvent, OutputCategory, OutputEvent, Patient, Sex
from ..drug_master import DrugMasterFile
from ..events import ClinicalEvent
from ..vitals import VitalsTable, VitalSeries


@dataclass
class AnesthesiaSession:
    """1症例分のリアルタイムセッションデータ."""

    patient: Optional[Patient] = None
    anesthesia_start: Optional[datetime] = None
    anesthesia_end: Optional[datetime] = None
    med_events: list[MedEvent] = field(default_factory=list)
    events: list[ClinicalEvent] = field(default_factory=list)
    output_events: list[OutputEvent] = field(default_factory=list)
    vitals: Optional[VitalsTable] = None
    postop_notes: list[str] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.anesthesia_start is not None and self.anesthesia_end is None

    @property
    def time_range(self) -> tuple[Optional[datetime], Optional[datetime]]:
        """チャート描画用の時間範囲."""
        start = self.anesthesia_start
        end = self.anesthesia_end or datetime.now()
        return start, end

    def elapsed_minutes(self) -> Optional[float]:
        if self.anesthesia_start is None:
            return None
        end = self.anesthesia_end or datetime.now()
        return (end - self.anesthesia_start).total_seconds() / 60.0

    def save_to_yaml(self, path: str) -> None:
        """セッションデータをYAMLに保存."""
        data: dict = {}
        if self.patient:
            data["patient"] = {
                "patient_id": self.patient.patient_id,
                "age_years": self.patient.age_years,
                "sex": self.patient.sex.value,
                "weight_kg": self.patient.weight_kg,
                "height_cm": self.patient.height_cm,
                "asa_ps": self.patient.asa_ps,
            }
        if self.anesthesia_start:
            data["anesthesia_start"] = self.anesthesia_start.isoformat()
        if self.anesthesia_end:
            data["anesthesia_end"] = self.anesthesia_end.isoformat()

        data["med_events"] = []
        for m in self.med_events:
            entry: dict = {
                "drug_id": m.drug_id,
                "start_time": m.start_time.isoformat(),
                "delivery": m.delivery.value,
            }
            if m.dose is not None:
                entry["dose"] = m.dose
                entry["dose_unit"] = m.dose_unit
            if m.rate is not None:
                entry["rate"] = m.rate
                entry["rate_unit"] = m.rate_unit
            if m.end_time:
                entry["end_time"] = m.end_time.isoformat()
            if m.remaining_ml_start is not None:
                entry["remaining_ml_start"] = m.remaining_ml_start
            if m.remaining_ml_end is not None:
                entry["remaining_ml_end"] = m.remaining_ml_end
            data["med_events"].append(entry)

        data["events"] = []
        for ev in self.events:
            entry = {
                "time": ev.time.isoformat(),
                "type": ev.type,
                "label": ev.label,
            }
            if ev.note:
                entry["note"] = ev.note
            data["events"].append(entry)

        data["output_events"] = []
        for oe in self.output_events:
            data["output_events"].append({
                "category": oe.category.value,
                "time": oe.time.isoformat(),
                "amount": oe.amount,
            })

        if self.postop_notes:
            data["postop_notes"] = self.postop_notes

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def load_from_yaml(self, path: str, drug_master: DrugMasterFile) -> None:
        """YAMLからセッションデータを復元."""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if "patient" in data:
            p = data["patient"]
            self.patient = Patient(
                patient_id=p.get("patient_id"),
                age_years=float(p["age_years"]),
                sex=Sex(p["sex"]),
                weight_kg=float(p["weight_kg"]),
                height_cm=float(p["height_cm"]),
                asa_ps=p.get("asa_ps"),
            )

        if "anesthesia_start" in data:
            self.anesthesia_start = datetime.fromisoformat(data["anesthesia_start"])
        if "anesthesia_end" in data:
            self.anesthesia_end = datetime.fromisoformat(data["anesthesia_end"])

        self.med_events = []
        for m in data.get("med_events", []):
            med = MedEvent(
                drug_id=m["drug_id"],
                start_time=datetime.fromisoformat(m["start_time"]),
                delivery=Delivery(m["delivery"]),
                dose=m.get("dose"),
                dose_unit=m.get("dose_unit"),
                rate=m.get("rate"),
                rate_unit=m.get("rate_unit"),
                end_time=datetime.fromisoformat(m["end_time"]) if m.get("end_time") else None,
                remaining_ml_start=m.get("remaining_ml_start"),
                remaining_ml_end=m.get("remaining_ml_end"),
            )
            self.med_events.append(med)

        self.events = []
        for ev in data.get("events", []):
            self.events.append(ClinicalEvent(
                time=datetime.fromisoformat(ev["time"]),
                type=ev["type"],
                label=ev.get("label", ev["type"]),
                note=ev.get("note"),
            ))

        self.output_events = []
        for oe in data.get("output_events", []):
            self.output_events.append(OutputEvent(
                category=OutputCategory(oe["category"]),
                time=datetime.fromisoformat(oe["time"]),
                amount=float(oe["amount"]),
            ))

        self.postop_notes = data.get("postop_notes", [])
