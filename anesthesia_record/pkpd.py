"""効果部位濃度(Ce)推定エンジン.

3-コンパートメント + 効果部位(ke0)モデルで、bolus と infusion を時間積分し
血漿濃度(Cp)と効果部位濃度(Ce)を算出する。

対応モデル:
  propofol     : marsh, schnider
  remifentanil : minto
  fentanyl     : shafer  （文献値。臨床判断前に各施設で検証すること）

濃度単位の慣習:
  propofol     -> ug/ml  (質量 mg, 容積 L)
  opioid       -> ng/ml  (質量 ug, 容積 L)

注意: これらはあくまで母集団PKモデルによる推定であり、個体差・臨床状況を反映しない。
本アプリは補助記録用途であり、投与判断の根拠に用いてはならない。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import DrugMaster, MedEvent, Patient, Delivery


@dataclass
class PKParams:
    """微小速度定数(/min)・分布容積(L)・ke0(/min)・質量スケール・濃度単位."""

    v1: float
    k10: float
    k12: float
    k21: float
    k13: float
    k31: float
    ke0: float
    mass_scale_from_mg: float   # mg からモデル質量単位への係数 (propofol=1, opioid=1000)
    conc_unit: str
    model: str
    approximate: bool = False


def _lbm_james(p: Patient) -> float:
    return p.lbm_james


def marsh(p: Patient) -> PKParams:
    w = p.weight_kg
    v1 = 0.228 * w
    return PKParams(
        v1=v1, k10=0.119, k12=0.112, k21=0.055, k13=0.0419, k31=0.0033,
        ke0=0.26, mass_scale_from_mg=1.0, conc_unit="ug/ml", model="marsh",
    )


def schnider(p: Patient) -> PKParams:
    age, w, h = p.age_years, p.weight_kg, p.height_cm
    lbm = _lbm_james(p)
    v1 = 4.27
    v2 = 18.9 - 0.391 * (age - 53.0)
    v3 = 238.0
    cl1 = 1.89 + 0.0456 * (w - 77.0) - 0.0681 * (lbm - 59.0) + 0.0264 * (h - 177.0)
    cl2 = 1.29 - 0.024 * (age - 53.0)
    cl3 = 0.836
    return PKParams(
        v1=v1,
        k10=cl1 / v1,
        k12=cl2 / v1,
        k21=cl2 / v2,
        k13=cl3 / v1,
        k31=cl3 / v3,
        ke0=0.456,
        mass_scale_from_mg=1.0,
        conc_unit="ug/ml",
        model="schnider",
    )


def minto(p: Patient) -> PKParams:
    age = p.age_years
    lbm = _lbm_james(p)
    v1 = 5.1 - 0.0201 * (age - 40.0) + 0.072 * (lbm - 55.0)
    v2 = 9.82 - 0.0811 * (age - 40.0) + 0.108 * (lbm - 55.0)
    v3 = 5.42
    cl1 = 2.6 - 0.0162 * (age - 40.0) + 0.0191 * (lbm - 55.0)
    cl2 = 2.05 - 0.0301 * (age - 40.0)
    cl3 = 0.076 - 0.00113 * (age - 40.0)
    return PKParams(
        v1=v1,
        k10=cl1 / v1,
        k12=cl2 / v1,
        k21=cl2 / v2,
        k13=cl3 / v1,
        k31=cl3 / v3,
        ke0=0.595 - 0.007 * (age - 40.0),
        mass_scale_from_mg=1000.0,   # mg -> ug
        conc_unit="ng/ml",
        model="minto",
    )


def shafer_fentanyl(p: Patient) -> PKParams:
    # 文献値（Shafer）。原報は体重スケールしないため V1 は固定。要検証。
    v1 = 12.7
    return PKParams(
        v1=v1, k10=0.083, k12=0.471, k21=0.102, k13=0.225, k31=0.00565,
        ke0=0.146, mass_scale_from_mg=1000.0, conc_unit="ng/ml",
        model="shafer", approximate=True,
    )


_MODELS = {
    "marsh": marsh,
    "schnider": schnider,
    "minto": minto,
    "shafer": shafer_fentanyl,
}


def get_params(drug: DrugMaster, patient: Patient) -> PKParams:
    if not drug.pkpd_enabled or drug.pk_model is None:
        raise ValueError(f"{drug.id} は Ce 推定の対象外 (pkpd_enabled=False)")
    try:
        fn = _MODELS[drug.pk_model]
    except KeyError as exc:
        raise ValueError(f"未対応の pk_model: {drug.pk_model}") from exc
    return fn(patient)


@dataclass
class CeResult:
    times_min: list[float]      # t0 からの経過(分)
    cp: list[float]             # 血漿濃度
    ce: list[float]             # 効果部位濃度
    conc_unit: str
    model: str
    approximate: bool

    @property
    def ce_max(self) -> float:
        return max(self.ce) if self.ce else 0.0


def _mass_mg_bolus(drug: DrugMaster, ev: MedEvent, patient: Patient) -> float:
    from .units import consumed_amounts

    mass_mg, _ = consumed_amounts(drug, ev, patient)
    return mass_mg


def _rate_mg_per_min(drug: DrugMaster, ev: MedEvent, patient: Patient) -> float:
    from .units import rate_mg_per_min

    if ev.rate is None or ev.rate_unit is None:
        return 0.0
    return rate_mg_per_min(drug, ev.rate, ev.rate_unit, patient)


def simulate(
    drug: DrugMaster,
    patient: Patient,
    events: list[MedEvent],
    t0: datetime,
    duration_min: float,
    dt_s: float = 1.0,
) -> CeResult:
    """単一薬剤の Cp/Ce を t0 起点で duration_min 分シミュレートする.

    events は当該薬剤のイベントのみを渡す（呼び出し側で drug_id フィルタ）。
    bolus は瞬時投与、infusion は start_time..end_time の定速投与として扱う。
    """
    pk = get_params(drug, patient)
    scale = pk.mass_scale_from_mg

    boluses: list[tuple[float, float]] = []     # (min_from_t0, mass)
    infusions: list[tuple[float, float, float]] = []  # (start_min, end_min, rate_mass/min)
    for ev in events:
        if ev.drug_id != drug.id:
            continue
        start_min = (ev.start_time - t0).total_seconds() / 60.0
        if ev.delivery is Delivery.BOLUS:
            boluses.append((start_min, _mass_mg_bolus(drug, ev, patient) * scale))
        else:
            end_min = (
                (ev.end_time - t0).total_seconds() / 60.0
                if ev.end_time
                else duration_min
            )
            infusions.append(
                (start_min, end_min, _rate_mg_per_min(drug, ev, patient) * scale)
            )

    dt = dt_s / 60.0
    n = int(round(duration_min / dt)) + 1

    a1 = a2 = a3 = ce = 0.0
    bolus_idx = sorted(boluses)
    bi = 0

    times: list[float] = []
    cps: list[float] = []
    ces: list[float] = []

    for i in range(n):
        t = i * dt
        # この時刻までの bolus を投入
        while bi < len(bolus_idx) and bolus_idx[bi][0] <= t + 1e-9:
            a1 += bolus_idx[bi][1]
            bi += 1

        inrate = 0.0
        for s, e, r in infusions:
            if s <= t < e:
                inrate += r

        cp = a1 / pk.v1
        da1 = -(pk.k10 + pk.k12 + pk.k13) * a1 + pk.k21 * a2 + pk.k31 * a3 + inrate
        da2 = pk.k12 * a1 - pk.k21 * a2
        da3 = pk.k13 * a1 - pk.k31 * a3
        dce = pk.ke0 * (cp - ce)

        times.append(round(t, 4))
        cps.append(cp)
        ces.append(ce)

        a1 += da1 * dt
        a2 += da2 * dt
        a3 += da3 * dt
        ce += dce * dt

    return CeResult(
        times_min=times, cp=cps, ce=ces,
        conc_unit=pk.conc_unit, model=pk.model, approximate=pk.approximate,
    )
