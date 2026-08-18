"""麻酔記録（補助記録）アプリ コアパッケージ.

GE CARESCAPE B650 等から取得したバイタルと、投薬イベント・コスト算定・
効果部位濃度(Ce)推定・局所麻酔薬の極量管理を扱う。

設計方針:
- 薬剤マスタはプログラム外の YAML（data/drug_master.yaml）として保持し、起動時に
読み込む。
- 帳票レイアウトは様式テンプレートとして差し替え可能（JSA様式 / 院内様式 等）。
"""

from .models import Patient, DrugMaster, MedEvent, Sex, Delivery, OutputEvent, OutputCategory
from .drug_master import DrugMasterFile, load_drug_master
from .events import (
    ClinicalEvent,
    DEFAULT_EVENT_ICONS,
    EventLog,
    STANDARD_EVENT_TYPES,
    load_event_log,
)
from .vitals import (
    VitalsConfig,
    VitalsTable,
    VitalSeries,
    Waveform,
    load_vitals,
    load_vitals_csv,
    load_vital_file,
)
from .chart_erga import DurationSpec
from .anesthesia_fee import (
    AnesthesiaEvent,
    AnesthesiaFeeConfig,
    AnesthesiaFeeResult,
    AnesthesiaMethod,
    FeeLineItem,
    PositionEvent,
    PositionSurcharge,
    SeveritySurcharge,
    compute_anesthesia_fee,
    load_anesthesia_fee,
)

__all__ = [
    "Patient",
    "DrugMaster",
    "MedEvent",
    "Sex",
    "Delivery",
    "OutputEvent",
    "OutputCategory",
    "DrugMasterFile",
    "load_drug_master",
    "ClinicalEvent",
    "DEFAULT_EVENT_ICONS",
    "EventLog",
    "load_event_log",
    "STANDARD_EVENT_TYPES",
    "VitalsConfig",
    "VitalsTable",
    "VitalSeries",
    "Waveform",
    "DurationSpec",
    "load_vitals",
    "load_vitals_csv",
    "load_vital_file",
    "AnesthesiaEvent",
    "AnesthesiaFeeConfig",
    "AnesthesiaFeeResult",
    "AnesthesiaMethod",
    "FeeLineItem",
    "PositionEvent",
    "PositionSurcharge",
    "SeveritySurcharge",
    "compute_anesthesia_fee",
    "load_anesthesia_fee",
]
