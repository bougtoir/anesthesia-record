"""院内様式の麻酔記録チャート出力."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Mapping, Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.gridspec import GridSpecFromSubplotSpec  # noqa: E402
from matplotlib.offsetbox import AnnotationBbox, TextArea, VPacker  # noqa: E402

from .chart_common import _configure_japanese_font  # noqa: E402
from .cost import CostReport  # noqa: E402
from .drug_master import DrugMasterFile  # noqa: E402
from .events import ClinicalEvent, DEFAULT_EVENT_ICONS  # noqa: E402
from .models import Delivery, MedEvent, OutputCategory, OutputEvent, Patient  # noqa: E402
from .pkpd import CeResult  # noqa: E402
from .units import consumed_amounts  # noqa: E402
from .vitals import VitalsTable, Waveform  # noqa: E402
from .anesthesia_fee import AnesthesiaFeeResult  # noqa: E402


_configure_japanese_font()


@dataclass(frozen=True)
class VitalPlotStyle:
    """バイタル描画の指定."""

    kind: str = "line"
    color: str = "#2f2f2f"
    marker: Optional[str] = None
    linestyle: str = "-"
    axis: str = "left"


@dataclass(frozen=True)
class VitalAxisSpec:
    """軸スケール・表示位置の指定."""

    key: str
    label: str
    color: str
    ymin: Optional[float] = None
    ymax: Optional[float] = None
    ticks: Optional[Sequence[float]] = None
    side: str = "left"


@dataclass(frozen=True)
class DurationSpec:
    """臨床イベントから算出する継続時間の定義."""

    label: str
    start_types: tuple[str, ...]
    end_types: tuple[str, ...]


DEFAULT_DURATION_SPECS: list[DurationSpec] = [
    DurationSpec(
        label="麻酔時間",
        start_types=("anesthesia_start",),
        end_types=("anesthesia_end",),
    ),
    DurationSpec(
        label="手術時間",
        start_types=("surgery_start", "incision"),
        end_types=("surgery_end", "closure"),
    ),
    DurationSpec(
        label="特殊体位時間",
        start_types=("position_start", "position_change"),
        end_types=("position_end",),
    ),
]

@dataclass
class FluidBalanceItem:
    """IN/OUT の1項目."""

    label: str
    volume_ml: float


@dataclass
class FluidBalanceSummary:
    """流量収支の事前集計."""

    in_items: list[FluidBalanceItem] = field(default_factory=list)
    out_items: list[FluidBalanceItem] = field(default_factory=list)
    balance_ml: Optional[float] = None


@dataclass(frozen=True)
class BandSpec:
    height: float
    render: Callable[[object], None]
    sharex: bool = True


DEFAULT_DISPLAY_INTERVALS: dict[str, float] = {
    "SBP": 300.0,
    "NIBP_SYS": 300.0,
    "NIBP_SBP": 300.0,
    "ABP_SYS": 300.0,
    "DBP": 300.0,
    "NIBP_DIA": 300.0,
    "NIBP_DBP": 300.0,
    "ABP_DIA": 300.0,
}


DEFAULT_VITAL_STYLES: dict[str, VitalPlotStyle] = {
    "HR": VitalPlotStyle(kind="line", color="#a31621", marker="o"),
    "PULSE": VitalPlotStyle(kind="line", color="#a31621", marker="o"),
    "SBP": VitalPlotStyle(kind="symbol", color="#c1121f", marker="v"),
    "NIBP_SYS": VitalPlotStyle(kind="symbol", color="#c1121f", marker="v"),
    "NIBP_SBP": VitalPlotStyle(kind="symbol", color="#c1121f", marker="v"),
    "ABP_SYS": VitalPlotStyle(kind="symbol", color="#c1121f", marker="v"),
    "SYS": VitalPlotStyle(kind="symbol", color="#c1121f", marker="v"),
    "DBP": VitalPlotStyle(kind="symbol", color="#1d4ed8", marker="^"),
    "NIBP_DIA": VitalPlotStyle(kind="symbol", color="#1d4ed8", marker="^"),
    "NIBP_DBP": VitalPlotStyle(kind="symbol", color="#1d4ed8", marker="^"),
    "ABP_DIA": VitalPlotStyle(kind="symbol", color="#1d4ed8", marker="^"),
    "DIA": VitalPlotStyle(kind="symbol", color="#1d4ed8", marker="^"),
    "SPO2": VitalPlotStyle(kind="line", color="#0f766e", marker="o", axis="right"),
    "SpO2": VitalPlotStyle(kind="line", color="#0f766e", marker="o", axis="right"),
    "ETCO2": VitalPlotStyle(kind="line", color="#2563eb", marker="o", axis="right"),
    "EtCO2": VitalPlotStyle(kind="line", color="#2563eb", marker="o", axis="right"),
    "BIS": VitalPlotStyle(kind="line", color="#7c3aed", marker="o", axis="right"),
    "TEMP": VitalPlotStyle(kind="line", color="#d97706", marker="o", axis="right"),
    "Temp": VitalPlotStyle(kind="line", color="#d97706", marker="o", axis="right"),
    "TEMP1": VitalPlotStyle(kind="line", color="#d97706", marker="o", axis="right"),
}

DEFAULT_AXIS_SPECS: list[VitalAxisSpec] = [
    VitalAxisSpec(
        key="BP_HR",
        label="HR / BP",
        color="#4b5563",
        ymin=0,
        ymax=200,
        ticks=[0, 50, 100, 150, 200],
        side="left",
    ),
    VitalAxisSpec(
        key="SPO2",
        label="SpO2",
        color="#0f766e",
        ymin=90,
        ymax=100,
        ticks=[90, 92, 94, 96, 98, 100],
        side="right",
    ),
    VitalAxisSpec(
        key="ETCO2",
        label="EtCO2",
        color="#2563eb",
        ymin=0,
        ymax=60,
        ticks=[0, 10, 20, 30, 40, 50, 60],
        side="right",
    ),
    VitalAxisSpec(
        key="BIS",
        label="BIS",
        color="#7c3aed",
        ymin=0,
        ymax=100,
        ticks=[0, 20, 40, 60, 80, 100],
        side="right",
    ),
    VitalAxisSpec(
        key="TEMP",
        label="Temp",
        color="#d97706",
        ymin=34,
        ymax=40,
        ticks=[34, 35, 36, 37, 38, 39, 40],
        side="right",
    ),
]

_AXIS_GROUP_ALIASES: dict[str, tuple[str, ...]] = {
    "BP_HR": (
        "HR",
        "PULSE",
        "SBP",
        "NIBP_SYS",
        "NIBP_SBP",
        "ABP_SYS",
        "SYS",
        "DBP",
        "NIBP_DIA",
        "NIBP_DBP",
        "ABP_DIA",
        "DIA",
    ),
    "SPO2": ("SPO2", "SpO2", "PLETH_SPO2"),
    "ETCO2": ("ETCO2", "EtCO2", "etCO2", "CO2_ET"),
    "BIS": ("BIS",),
    "TEMP": ("TEMP", "Temp", "TEMP1", "Temperature"),
}

_LATEST_PANEL_LOCATIONS = {
    "upper right": (0.99, 0.98, "right", "top"),
    "upper left": (0.01, 0.98, "left", "top"),
    "lower right": (0.99, 0.02, "right", "bottom"),
    "lower left": (0.01, 0.02, "left", "bottom"),
}


def render_chart_erga(
    vitals: VitalsTable,
    events: Sequence[MedEvent],
    master: DrugMasterFile,
    out_path: str,
    patient: Optional[Patient] = None,
    header_meta: Optional[Mapping[str, str]] = None,
    clinical_events: Optional[Sequence[ClinicalEvent]] = None,
    fluids: Optional[FluidBalanceSummary] = None,
    cost_report: Optional[CostReport] = None,
    ce_results: Optional[dict[str, CeResult]] = None,
    ce_t0: Optional[datetime] = None,
    show_floating_latest: bool = False,
    latest_panel_loc: str = "upper right",
    latest_stale_threshold_min: float = 5.0,
    latest_panel_facecolor: str = "white",
    draggable_latest: bool = False,
    axis_specs: Optional[Sequence[VitalAxisSpec]] = None,
    window: Optional[tuple[datetime, datetime]] = None,
    window_minutes: Optional[float] = None,
    tick_interval_min: Optional[float] = None,
    ce_window: Optional[tuple[datetime, datetime]] = None,
    ce_horizon_min: float = 60.0,
    duration_specs: Optional[Sequence[DurationSpec]] = None,
    display_intervals: Optional[Mapping[str, float]] = None,
    display_modes: Optional[Mapping[str, str]] = None,
    event_icon_map: Optional[Mapping[str, str]] = None,
    vital_style_map: Optional[Mapping[str, VitalPlotStyle]] = None,
    ecg_waveform: Optional[Waveform] = None,
    ecg_snapshot_times: Optional[Sequence[datetime]] = None,
    ecg_snapshot_window_sec: float = 10.0,
    output_events: Optional[Sequence["OutputEvent"]] = None,
    postop_orders: Optional[Sequence[str]] = None,
    anesthesia_fee_result: Optional["AnesthesiaFeeResult"] = None,
    title: str = "麻酔記録",
) -> str:
    """院内様式の印刷チャートを描画して保存する."""

    style_map = dict(DEFAULT_VITAL_STYLES)
    if vital_style_map:
        style_map.update(vital_style_map)
    icon_map = dict(DEFAULT_EVENT_ICONS)
    if event_icon_map:
        icon_map.update(event_icon_map)

    axis_spec_list = list(axis_specs or DEFAULT_AXIS_SPECS)
    axis_spec_map = {spec.key: spec for spec in axis_spec_list}
    main_window = _resolve_main_window(vitals, events, clinical_events, window, window_minutes)
    ce_window_resolved = _resolve_ce_window(main_window, ce_window, ce_horizon_min)
    duration_spec_list = list(duration_specs or DEFAULT_DURATION_SPECS)
    ordered_events = _sorted_events(events)
    clinical_sorted = sorted(list(clinical_events or []), key=lambda e: e.time)
    drug_rows = _drug_rows(ordered_events, master)
    display_interval_map = dict(DEFAULT_DISPLAY_INTERVALS)
    if display_intervals:
        display_interval_map.update(display_intervals)

    bands: list[BandSpec] = [
        BandSpec(
            0.62,
            lambda ax: _render_header(ax, patient, header_meta, title, main_window),
            sharex=False,
        ),
        BandSpec(
            4.9,
            lambda ax: _render_vitals(
                ax,
                vitals,
                style_map,
                axis_spec_list,
                axis_spec_map,
                icon_map,
                clinical_sorted,
                show_floating_latest,
                latest_panel_loc,
                latest_stale_threshold_min,
                latest_panel_facecolor,
                draggable_latest,
                main_window,
                tick_interval_min,
                display_interval_map,
                display_modes or {},
            ),
        ),
    ]
    for lane in drug_rows:
        bands.append(BandSpec(0.12, lambda ax, lane=lane: _render_drug_lane(ax, lane, master, main_window, patient)))
    if output_events:
        bands.append(BandSpec(1.0, lambda ax: _render_output_bowling(ax, output_events, main_window)))
    if fluids is not None:
        bands.append(BandSpec(0.7, lambda ax: _render_fluids(ax, fluids), sharex=False))
    if ce_results:
        bands.append(
            BandSpec(
                1.15,
                lambda ax: _render_ce(ax, ce_results, master, ce_t0, main_window, ce_window_resolved, tick_interval_min),
                sharex=False,
            )
        )
    # コスト・イベント・時間・術後指示を4ペイン構成で1バンドに
    _has_info_pane = cost_report is not None or clinical_sorted or postop_orders or anesthesia_fee_result is not None
    if _has_info_pane:
        cost_lines = (len(cost_report.items) + 2) if cost_report else 0
        if anesthesia_fee_result is not None:
            cost_lines += len(anesthesia_fee_result.items) + 2
        info_height = max(1.2, 0.22 * max(
            cost_lines,
            (len(clinical_sorted) + 1) if clinical_sorted else 0,
            4,
        ))
        bands.append(BandSpec(
            info_height,
            lambda ax: _render_info_4pane(ax, cost_report, clinical_sorted, duration_spec_list, postop_orders, anesthesia_fee_result),
            sharex=False,
        ))
    if ecg_waveform is not None:
        snapshot_times = _resolve_ecg_snapshot_times(clinical_sorted, ecg_snapshot_times)
        if snapshot_times:
            bands.append(
                BandSpec(
                    1.1,
                    lambda ax: _render_ecg_strips(
                        ax,
                        ecg_waveform,
                        snapshot_times,
                        clinical_sorted,
                        ecg_snapshot_window_sec,
                    ),
                    sharex=False,
                )
            )

    fig = plt.figure(figsize=(11.69, 8.27))
    right_margin = 0.94
    fig.subplots_adjust(left=0.05, right=right_margin, top=0.97, bottom=0.04)
    gs = fig.add_gridspec(len(bands), 1, height_ratios=[b.height for b in bands])

    axes: list[object] = []
    ax_main = None
    last_shared_index = max((i for i, b in enumerate(bands) if b.sharex), default=0)
    for idx, band in enumerate(bands):
        if idx == 0:
            ax = fig.add_subplot(gs[idx, 0])
        elif band.sharex and ax_main is not None:
            ax = fig.add_subplot(gs[idx, 0], sharex=ax_main)
        else:
            ax = fig.add_subplot(gs[idx, 0])
        if idx == 1:
            ax_main = ax
        band.render(ax)
        if band.sharex and idx < last_shared_index:
            ax.tick_params(labelbottom=False)
        axes.append(ax)

    if ax_main is not None:
        _apply_tick_interval(ax_main, tick_interval_min)
    if ce_results:
        ce_ax = axes[-1]
        _apply_tick_interval(ce_ax, tick_interval_min)

    fig.tight_layout(h_pad=0.0)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def _sorted_events(events: Sequence[MedEvent]) -> list[MedEvent]:
    out = list(events)
    out.sort(key=lambda e: (e.start_time, e.drug_id, e.delivery.value))
    return out


@dataclass
class DrugLane:
    drug_id: str
    label: str
    events: list[MedEvent]
    has_infusion: bool
    first_time: datetime


_CATEGORY_ORDER: dict[str, int] = {
    "iv_anesthetic": 0,
    "opioid": 1,
    "muscle_relaxant": 2,
    "antagonist": 3,
    "vasopressor": 4,
    "local_anesthetic": 5,
    "fluid": 90,
}


def _drug_rows(events: Sequence[MedEvent], master: DrugMasterFile) -> list[DrugLane]:
    grouped: dict[str, list[MedEvent]] = {}
    for ev in events:
        grouped.setdefault(ev.drug_id, []).append(ev)
    rows: list[DrugLane] = []
    for drug_id, evs in grouped.items():
        drug = master.get(drug_id)
        units = _lane_units(drug, evs)
        label = drug.generic_name
        if units:
            label = f"{label} ({'・'.join(units)})"
        rows.append(
            DrugLane(
                drug_id=drug_id,
                label=label,
                events=sorted(evs, key=lambda e: (e.start_time, e.delivery.value)),
                has_infusion=any(ev.delivery is Delivery.INFUSION for ev in evs),
                first_time=min(ev.start_time for ev in evs),
            )
        )
    rows.sort(key=lambda lane: _lane_sort_key(lane, master))
    return rows


def _lane_sort_key(lane: DrugLane, master: DrugMasterFile) -> tuple:
    """カテゴリ優先度 → display_order → 名前順."""
    drug = master.get(lane.drug_id)
    cat_order = _CATEGORY_ORDER.get(drug.category, 50)
    disp_order = drug.display_order if drug.display_order is not None else 999
    return (cat_order, disp_order, lane.label)


def _lane_units(drug, events: Sequence[MedEvent]) -> list[str]:
    units: list[str] = []
    has_infusion = any(ev.delivery is Delivery.INFUSION for ev in events)
    has_bolus = any(ev.delivery is Delivery.BOLUS for ev in events)
    if has_bolus:
        if drug.unit_bolus:
            units.append(drug.unit_bolus)
        else:
            for ev in events:
                if ev.delivery is Delivery.BOLUS and ev.dose_unit:
                    units.append(ev.dose_unit)
                    break
    if has_infusion:
        if drug.unit_rate:
            units.append(drug.unit_rate)
        else:
            for ev in events:
                if ev.delivery is Delivery.INFUSION and ev.rate_unit:
                    units.append(ev.rate_unit)
                    break
    deduped: list[str] = []
    for unit in units:
        if unit and unit not in deduped:
            deduped.append(unit)
    return deduped


def _resolve_main_window(
    vitals: VitalsTable,
    events: Sequence[MedEvent],
    clinical_events: Optional[Sequence[ClinicalEvent]],
    window: Optional[tuple[datetime, datetime]],
    window_minutes: Optional[float],
) -> tuple[datetime, datetime]:
    base_start, base_end = _infer_bounds(vitals, events, clinical_events or [])
    if window is not None:
        return window
    if window_minutes is not None:
        end = base_end
        start = end - timedelta(minutes=window_minutes)
        if start >= end:
            start = end - timedelta(minutes=1)
        return start, end
    return base_start, base_end


def _resolve_ce_window(
    main_window: tuple[datetime, datetime],
    ce_window: Optional[tuple[datetime, datetime]],
    ce_horizon_min: float,
) -> tuple[datetime, datetime]:
    if ce_window is not None:
        return ce_window
    start, end = main_window
    return start, end + timedelta(minutes=ce_horizon_min)


def _infer_bounds(
    vitals: VitalsTable,
    events: Sequence[MedEvent],
    clinical_events: Sequence[ClinicalEvent],
) -> tuple[datetime, datetime]:
    times: list[datetime] = []
    for series in vitals.parameters.values():
        times.extend(series.times)
    for ev in events:
        times.append(ev.start_time)
        if ev.end_time is not None:
            times.append(ev.end_time)
    for ev in clinical_events:
        times.append(ev.time)
    if not times:
        now = datetime.now()
        return now, now + timedelta(minutes=1)
    start = min(times)
    end = max(times)
    if end <= start:
        end = start + timedelta(minutes=1)
    return start, end


def _render_header(
    ax,
    patient: Optional[Patient],
    header_meta: Optional[Mapping[str, str]],
    title: str,
    bounds: tuple[datetime, datetime],
) -> None:
    ax.axis("off")
    start, _ = bounds
    values: list[str] = [title, start.strftime("%Y-%m-%d %H:%M")]
    if patient is not None:
        if patient.patient_id:
            values.append(str(patient.patient_id))
        if patient.age_years is not None:
            values.append(f"{patient.age_years:g}歳")
        if patient.sex is not None:
            values.append("男" if patient.sex.value == "male" else "女")
        if patient.weight_kg is not None:
            values.append(f"{patient.weight_kg:g} kg")
        if patient.height_cm is not None:
            values.append(f"{patient.height_cm:g} cm")
        if patient.asa_ps is not None:
            values.append(f"ASA {patient.asa_ps}")
    if header_meta:
        for key in ("name", "dept", "diagnosis", "procedure", "blood_type", "position", "anesthesia_method"):
            value = header_meta.get(key)
            if value:
                values.append(value)
    lines = _wrap_values(values, max_items_per_line=7)
    ax.text(0.01, 0.92, "\n".join(lines), ha="left", va="top", fontsize=9, transform=ax.transAxes)


def _wrap_values(values: Sequence[str], max_items_per_line: int) -> list[str]:
    if not values:
        return [""]
    lines: list[str] = []
    for i in range(0, len(values), max_items_per_line):
        lines.append("  ".join(values[i : i + max_items_per_line]))
    return lines


def _render_vitals(
    ax,
    vitals: VitalsTable,
    style_map: Mapping[str, VitalPlotStyle],
    axis_specs: Sequence[VitalAxisSpec],
    axis_spec_map: Mapping[str, VitalAxisSpec],
    icon_map: Mapping[str, str],
    clinical_events: Sequence[ClinicalEvent],
    show_floating_latest: bool,
    latest_panel_loc: str,
    latest_stale_threshold_min: float,
    latest_panel_facecolor: str,
    draggable_latest: bool,
    bounds: tuple[datetime, datetime],
    tick_interval_min: Optional[float],
    display_intervals: Mapping[str, float],
    display_modes: Mapping[str, str],
) -> None:
    ax.set_xlim(bounds[0], bounds[1])
    ax.grid(True, axis="y", ls=":", alpha=0.45)
    _render_time_gridlines(ax, bounds)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(True)
    ax.set_ylabel(axis_spec_map.get("BP_HR", DEFAULT_AXIS_SPECS[0]).label)
    _apply_axis_style(ax, axis_spec_map.get("BP_HR", DEFAULT_AXIS_SPECS[0]), main=True)

    series_groups = _group_series_by_axis(vitals, axis_specs)
    right_keys = [spec.key for spec in axis_specs if spec.side == "right" and spec.key in series_groups]
    twins: list[object] = []
    for offset, key in enumerate(right_keys, start=1):
        spec = axis_spec_map[key]
        twin = ax.twinx()
        twins.append(twin)
        twin.spines["right"].set_position(("axes", 1.0))
        twin.spines["right"].set_visible(False)
        twin.tick_params(axis="y", direction="in", pad=-24 - 20 * (offset - 1), labelsize=7)
        _apply_axis_style(twin, spec, main=False)
        _plot_axis_group(twin, key, series_groups[key], style_map, display_intervals, display_modes)

    if "BP_HR" in series_groups:
        _plot_axis_group(ax, "BP_HR", series_groups["BP_HR"], style_map, display_intervals, display_modes)
    for key, items in series_groups.items():
        if key in {"BP_HR", *right_keys}:
            continue
        _plot_axis_group(ax, key, items, style_map, display_intervals, display_modes)

    _render_event_icons(ax, clinical_events, icon_map)
    if show_floating_latest:
        # main axes を twin より上に描画し、フローティングパネルを最前面にする
        for twin in twins:
            twin.set_zorder(ax.get_zorder() - 1)
        ax.patch.set_visible(False)
        _render_latest_panel(
            ax,
            vitals,
            style_map,
            latest_panel_loc,
            latest_stale_threshold_min,
            latest_panel_facecolor,
            draggable_latest,
        )
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    if tick_interval_min is not None:
        _apply_tick_interval(ax, tick_interval_min)


def _group_series_by_axis(
    vitals: VitalsTable,
    axis_specs: Sequence[VitalAxisSpec],
) -> dict[str, list[tuple[str, object]]]:
    groups: dict[str, list[tuple[str, object]]] = {}
    for name, series in vitals.parameters.items():
        key = _axis_key_for_series(name, axis_specs)
        if key is None:
            key = "__main__"
        groups.setdefault(key, []).append((name, series))
    return groups


def _axis_key_for_series(name: str, axis_specs: Sequence[VitalAxisSpec]) -> Optional[str]:
    norm = _normalize(name)
    for spec in axis_specs:
        if _normalize(spec.key) == norm or _normalize(spec.label) == norm:
            return spec.key
    for key, aliases in _AXIS_GROUP_ALIASES.items():
        for alias in aliases:
            if _normalize(alias) == norm:
                return key
    return None


def _apply_axis_style(ax, spec: VitalAxisSpec, main: bool) -> None:
    color = spec.color
    side = spec.side
    if main:
        spine = ax.spines["left"]
    else:
        spine = ax.spines["right"]
    spine.set_color(color)
    ax.yaxis.label.set_color(color)
    ax.tick_params(axis="y", colors=color, labelsize=8)
    if spec.ymin is not None or spec.ymax is not None:
        ax.set_ylim(spec.ymin, spec.ymax)
    if spec.ticks is not None:
        ax.set_yticks(list(spec.ticks))
    if not main:
        ax.spines["left"].set_visible(False)
    ax.set_ylabel(spec.label)
    if side == "left":
        ax.yaxis.tick_left()
        ax.yaxis.set_label_position("left")
    else:
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")


def _plot_axis_group(
    ax,
    axis_key: str,
    items: Sequence[tuple[str, object]],
    style_map: Mapping[str, VitalPlotStyle],
    display_intervals: Mapping[str, float],
    display_modes: Mapping[str, str],
) -> None:
    all_values: list[float] = []
    for name, series in items:
        style = _pick_vital_style(name, style_map)
        interval = _display_interval_for_series(name, axis_key, display_intervals)
        mode = _display_mode_for_series(name, axis_key, display_modes, style.kind)
        xs, ys = _series_points_for_display(series, interval)
        all_values.extend(ys)
        if not xs:
            continue
        if mode == "scatter":
            ax.scatter(xs, ys, s=18, marker=style.marker or "o", color=style.color, label=name)
        elif mode == "line":
            ax.plot(xs, ys, linestyle=style.linestyle, marker=style.marker, ms=3, lw=0.9, color=style.color, label=name)
        elif style.kind == "symbol":
            ax.scatter(xs, ys, s=18, marker=style.marker or "o", color=style.color, label=name)
        else:
            ax.plot(xs, ys, linestyle=style.linestyle, marker=style.marker, ms=3, lw=0.9, color=style.color, label=name)
    if axis_key == "__main__" and all_values:
        ymin, ymax = min(all_values), max(all_values)
        if ymin == ymax:
            ymin -= 1
            ymax += 1
        pad = max((ymax - ymin) * 0.1, 1.0)
        ax.set_ylim(ymin - pad, ymax + pad)


def _series_points_for_display(series, interval_sec: Optional[float]) -> tuple[list[datetime], list[float]]:
    if interval_sec is None or interval_sec <= 0:
        return _decimate(series.times, series.values, None)
    return _decimate(series.times, series.values, interval_sec)


def _decimate(
    times: Sequence[datetime],
    values: Sequence[Optional[float]],
    interval_sec: Optional[float],
) -> tuple[list[datetime], list[float]]:
    xs: list[datetime] = []
    ys: list[float] = []
    if interval_sec is None or interval_sec <= 0:
        for t, v in zip(times, values):
            if v is None:
                continue
            xs.append(t)
            ys.append(float(v))
        return xs, ys
    if not times:
        return xs, ys
    base = times[0]
    seen: set[int] = set()
    for t, v in zip(times, values):
        if v is None:
            continue
        bucket = int((t - base).total_seconds() // interval_sec)
        if bucket in seen:
            continue
        seen.add(bucket)
        xs.append(t)
        ys.append(float(v))
    return xs, ys


def _render_time_gridlines(ax, bounds: tuple[datetime, datetime]) -> None:
    """15分おき細線 + 毎正時(1時間おき)太線の縦グリッドを描画."""
    start, end = bounds
    # 最初の15分刻みの時刻を算出
    base = start.replace(second=0, microsecond=0)
    if base.minute % 15 != 0:
        base = base.replace(minute=(base.minute // 15) * 15) + timedelta(minutes=15)
    t = base
    while t <= end:
        if t.minute == 0:
            ax.axvline(t, color="#555555", lw=0.9, alpha=0.7, zorder=0)
        else:
            ax.axvline(t, color="#aaaaaa", lw=0.4, alpha=0.5, zorder=0)
        t += timedelta(minutes=15)


def _render_event_icons(
    ax,
    events: Sequence[ClinicalEvent],
    icon_map: Mapping[str, str],
) -> None:
    for ev in events:
        icon = ev.icon or icon_map.get(ev.type) or DEFAULT_EVENT_ICONS.get(ev.type, "#")
        ax.text(
            ev.time,
            0.01,
            icon,
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="bottom",
            fontsize=8,
            color="#222222",
            clip_on=True,
        )


def _latest_non_missing_point(series) -> tuple[Optional[float], Optional[datetime]]:
    for time, value in reversed(list(zip(series.times, series.values))):
        if value is not None:
            return float(value), time
    return None, None


def _latest_reference_time(vitals: VitalsTable) -> Optional[datetime]:
    latest: Optional[datetime] = None
    for series in vitals.parameters.values():
        if series.times:
            series_latest = max(series.times)
            if latest is None or series_latest > latest:
                latest = series_latest
    return latest


def _render_latest_panel(
    ax,
    vitals: VitalsTable,
    style_map: Mapping[str, VitalPlotStyle],
    latest_panel_loc: str,
    latest_stale_threshold_min: float,
    latest_panel_facecolor: str,
    draggable_latest: bool,
) -> None:
    x, y, ha, va = _LATEST_PANEL_LOCATIONS.get(latest_panel_loc, _LATEST_PANEL_LOCATIONS["upper right"])
    ref_time = _latest_reference_time(vitals)
    threshold = timedelta(minutes=latest_stale_threshold_min)
    entries: list[tuple[str, str, str]] = []

    bp_sys = _latest_point_for(vitals, ("SBP", "NIBP_SYS", "NIBP_SBP", "ABP_SYS", "SYS"))
    bp_dia = _latest_point_for(vitals, ("DBP", "NIBP_DIA", "NIBP_DBP", "ABP_DIA", "DIA"))
    if bp_sys[0] is not None and bp_dia[0] is not None:
        bp_time = max(t for t in (bp_sys[1], bp_dia[1]) if t is not None)
        color = _series_color_for_group(vitals, style_map, ("SBP", "NIBP_SYS", "NIBP_SBP", "ABP_SYS", "SYS"))
        if _is_stale(bp_time, ref_time, threshold):
            color = _dim_color(color)
        entries.append(("BP", f"{int(round(bp_sys[0]))}/{int(round(bp_dia[0]))}", color))

    for key_group in (("HR", "PULSE"), ("SPO2", "SpO2"), ("ETCO2", "EtCO2"), ("BIS",), ("TEMP", "TEMP1")):
        latest, latest_time = _latest_point_for(vitals, key_group)
        if latest is None or latest_time is None:
            continue
        color = _series_color_for_group(vitals, style_map, key_group)
        if _is_stale(latest_time, ref_time, threshold):
            color = _dim_color(color)
        label = _latest_panel_label(key_group)
        entries.append((label, _format_latest_panel_value(label, latest), color))

    if not entries:
        return

    text_boxes = [
        TextArea(
            value,
            textprops={"color": color, "fontsize": 12},
        )
        for _, value, color in entries
    ]
    stack = VPacker(children=text_boxes, align="left", pad=0, sep=2)
    if ha == "right" and va == "top":
        align = (1, 1)
    elif ha == "left" and va == "top":
        align = (0, 1)
    elif ha == "right" and va == "bottom":
        align = (1, 0)
    else:
        align = (0, 0)
    panel = AnnotationBbox(
        stack,
        (x, y),
        xycoords=ax.transAxes,
        boxcoords=ax.transAxes,
        box_alignment=align,
        frameon=True,
        pad=0.35,
        annotation_clip=False,
        bboxprops={
            "facecolor": latest_panel_facecolor,
            "edgecolor": "#999999",
            "linewidth": 1.0,
            "alpha": 1.0,
        },
    )
    panel.set_zorder(999)
    panel.patch.set_zorder(999)
    ax.add_artist(panel)
    if draggable_latest:
        drag = panel.draggable(use_blit=False)
        stored = getattr(ax.figure, "_erga_drag_objects", [])
        stored.append(drag)
        ax.figure._erga_drag_objects = stored


def _latest_point_for(
    vitals: VitalsTable,
    key_group: tuple[str, ...],
) -> tuple[Optional[float], Optional[datetime]]:
    for key in key_group:
        series = _get_series_case_insensitive(vitals, key)
        if series is None:
            continue
        value, time = _latest_non_missing_point(series)
        if value is not None and time is not None:
            return value, time
    return None, None


def _series_color_for_group(
    vitals: VitalsTable,
    style_map: Mapping[str, VitalPlotStyle],
    key_group: tuple[str, ...],
) -> str:
    for key in key_group:
        series = _get_series_case_insensitive(vitals, key)
        if series is not None:
            style = _pick_vital_style(key, style_map)
            return style.color
    return "#2f2f2f"


def _latest_panel_label(key_group: tuple[str, ...]) -> str:
    normalized = tuple(_normalize(key) for key in key_group)
    return {
        ("HR", "PULSE"): "HR",
        ("SPO2", "SPO2"): "SpO2",
        ("ETCO2", "ETCO2"): "EtCO2",
        ("BIS",): "BIS",
        ("TEMP", "TEMP1"): "Temp",
    }[normalized]


def _format_latest_panel_value(label: str, value: float) -> str:
    if label == "Temp":
        return f"{value:.1f}"
    return f"{int(round(value))}"


def _get_series_case_insensitive(vitals: VitalsTable, key: str):
    target = _normalize(key)
    for name, series in vitals.parameters.items():
        if _normalize(name) == target:
            return series
    return None


def _enable_draggable_latest_panel(fig, inset) -> None:
    state = {"dragging": False, "offset": (0.0, 0.0)}

    def _inset_contains(event) -> bool:
        if event.inaxes is None:
            return False
        return event.inaxes == inset

    def on_press(event) -> None:
        if event.button != 1 or not _inset_contains(event):
            return
        bbox = inset.get_position()
        x = event.x / fig.bbox.width
        y = event.y / fig.bbox.height
        state["dragging"] = True
        state["offset"] = (x - bbox.x0, y - bbox.y0)

    def on_motion(event) -> None:
        if not state["dragging"] or event.x is None or event.y is None:
            return
        bbox = inset.get_position()
        width = bbox.width
        height = bbox.height
        x = event.x / fig.bbox.width
        y = event.y / fig.bbox.height
        left = x - state["offset"][0]
        bottom = y - state["offset"][1]
        left = min(max(left, 0.0), 1.0 - width)
        bottom = min(max(bottom, 0.0), 1.0 - height)
        inset.set_position([left, bottom, width, height])
        fig.canvas.draw_idle()

    def on_release(event) -> None:
        state["dragging"] = False

    cids = [
        fig.canvas.mpl_connect("button_press_event", on_press),
        fig.canvas.mpl_connect("motion_notify_event", on_motion),
        fig.canvas.mpl_connect("button_release_event", on_release),
    ]
    stored = getattr(fig, "_erga_drag_connections", [])
    stored.extend(cids)
    fig._erga_drag_connections = stored


def _is_stale(value_time: Optional[datetime], reference_time: Optional[datetime], threshold: timedelta) -> bool:
    if value_time is None or reference_time is None:
        return False
    return reference_time - value_time > threshold


def _dim_color(color: str) -> str:
    r, g, b = mcolors.to_rgb(color)
    blended = (1.0 - (1.0 - r) * 0.45, 1.0 - (1.0 - g) * 0.45, 1.0 - (1.0 - b) * 0.45)
    return mcolors.to_hex(blended)


def _pick_vital_style(name: str, style_map: Mapping[str, VitalPlotStyle]) -> VitalPlotStyle:
    key = _normalize(name)
    if name in style_map:
        return style_map[name]
    for candidate, style in style_map.items():
        if _normalize(candidate) == key:
            return style
    return VitalPlotStyle()


def _normalize(name: str) -> str:
    return "".join(ch for ch in name.upper() if ch.isalnum())


def _display_interval_for_series(name: str, axis_key: str, display_intervals: Mapping[str, float]) -> Optional[float]:
    for key in (name, axis_key):
        for candidate, interval in display_intervals.items():
            if _normalize(candidate) == _normalize(key):
                return interval
    return None


def _display_mode_for_series(name: str, axis_key: str, display_modes: Mapping[str, str], default_mode: str) -> str:
    for key in (name, axis_key):
        for candidate, mode in display_modes.items():
            if _normalize(candidate) == _normalize(key):
                return mode.lower()
    return default_mode.lower()


def _apply_tick_interval(ax, tick_interval_min: Optional[float]) -> None:
    if tick_interval_min is None:
        return
    interval = max(1, int(round(tick_interval_min)))
    locator = mdates.MinuteLocator(interval=interval)
    ax.xaxis.set_major_locator(locator)


def _render_drug_lane(
    ax,
    lane: DrugLane,
    master: DrugMasterFile,
    bounds: tuple[datetime, datetime],
    patient: Optional[Patient] = None,
) -> None:
    ax.set_xlim(bounds[0], bounds[1])
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.grid(True, axis="x", ls=":", alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.text(0.0, 1.7, lane.label, ha="left", va="bottom", fontsize=6.5, transform=ax.transAxes, clip_on=False)
    ax.axhline(0.0, color="#cccccc", lw=0.4, zorder=0)

    drug = master.get(lane.drug_id)
    is_fluid = drug.category == "fluid"

    infusion_events = [ev for ev in lane.events if ev.delivery is Delivery.INFUSION]
    infusion_starts = [ev.start_time for ev in infusion_events]
    line_color = drug.color or "#222222"

    # 右端に積算量(繰上げ単位量)を表示
    if not is_fluid:
        _render_lane_cumulative(ax, lane, drug, patient)

    if is_fluid:
        _render_fluid_lane_events(ax, infusion_events, drug, bounds, line_color)
    else:
        for ev in lane.events:
            if ev.delivery is Delivery.BOLUS:
                label = _format_dose_event(ev)
                if not label:
                    continue
                ax.annotate(
                    label,
                    xy=(ev.start_time, 0.08),
                    xycoords=("data", "axes fraction"),
                    ha="center",
                    va="bottom",
                    fontsize=6.5,
                    color="#222222",
                    bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.9, "pad": 0},
                    zorder=3,
                )
                ax.axvline(ev.start_time, ymin=0.15, ymax=0.75, color=line_color, lw=0.5, alpha=0.45, zorder=1)
            else:
                next_change = next((t for t in infusion_starts if t > ev.start_time), ev.end_time or bounds[1])
                ax.hlines(0.5, ev.start_time, next_change, color=line_color, lw=1.2, alpha=0.75, zorder=2)
                label = _format_rate_event(ev, master)
                if label:
                    ax.text(
                        ev.start_time,
                        0.08,
                        label,
                        ha="left",
                        va="bottom",
                        fontsize=6.5,
                        color="#222222",
                        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.9, "pad": 0},
                        zorder=3,
                    )
        # 最終持続イベントの終了地点に // マーカーを描画
        if infusion_events:
            last_inf = infusion_events[-1]
            end_t = last_inf.end_time
            if end_t is not None and end_t <= bounds[1]:
                ax.text(
                    end_t, 0.5, "//",
                    ha="center", va="center", fontsize=9, fontweight="bold",
                    color="#222222", zorder=4,
                )
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))


def _render_lane_cumulative(ax, lane: DrugLane, drug, patient: Optional[Patient] = None) -> None:
    """薬剤行の右端に「積算量(繰上げ単位量)」を表示."""
    import math as _math
    total_mg = 0.0
    for ev in lane.events:
        try:
            mass_mg, _ = consumed_amounts(drug, ev, patient)
            total_mg += mass_mg
        except (ValueError, TypeError):
            continue
    if total_mg <= 0:
        return
    # 繰上げ容器量 = ceil(total / mass_per_container) * mass_per_container
    mass_per = drug.mass_per_container
    if mass_per > 0:
        containers = _math.ceil(total_mg / mass_per)
        rounded = containers * mass_per
        label = f"{total_mg:.4g}({rounded:.4g})"
    else:
        label = f"{total_mg:.4g}"
    ax.text(
        0.99, 1.7, label,
        ha="right", va="bottom", fontsize=6, color="#555555",
        transform=ax.transAxes, clip_on=False,
    )


def _render_fluid_lane_events(ax, infusion_events: list, drug, bounds: tuple[datetime, datetime], line_color: str = "#222222") -> None:
    """輸液レーン: 開始時残量と終了時残量を表示."""
    for ev in infusion_events:
        end_t = ev.end_time or bounds[1]
        ax.hlines(0.5, ev.start_time, end_t, color=line_color, lw=1.2, alpha=0.75, zorder=2)
        # 開始時残量
        start_ml = ev.remaining_ml_start
        if start_ml is not None:
            ax.text(
                ev.start_time, 0.08, f"{start_ml:g}",
                ha="left", va="bottom", fontsize=6.5, color="#222222",
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.9, "pad": 0}, zorder=3,
            )
        # 終了時残量
        end_ml = ev.remaining_ml_end
        if end_ml is not None:
            ax.text(
                end_t, 0.08, f"{end_ml:g}",
                ha="right", va="bottom", fontsize=6.5, color="#222222",
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.9, "pad": 0}, zorder=3,
            )
    # 終了マーカー
    if infusion_events:
        last_inf = infusion_events[-1]
        end_t = last_inf.end_time
        if end_t is not None and end_t <= bounds[1]:
            ax.text(
                end_t, 0.5, "//",
                ha="center", va="center", fontsize=9, fontweight="bold",
                color="#222222", zorder=4,
            )


def _render_output_bowling(
    ax,
    output_events: Sequence[OutputEvent],
    bounds: tuple[datetime, datetime],
) -> None:
    """出血(ガーゼg/吸引cc)・尿量をボーリングスコア形式で描画.

    各カテゴリを行に分け、差分(上段)と積算(下段)を時系列で表示する。
    """
    ax.set_xlim(bounds[0], bounds[1])
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    categories = [
        (OutputCategory.GAUZE, "出血(ガーゼ g)", "#D32F2F"),
        (OutputCategory.SUCTION, "出血(吸引 cc)", "#C62828"),
        (OutputCategory.URINE, "尿量 (cc)", "#F9A825"),
    ]
    # 実際に存在するカテゴリのみ描画
    present = {ev.category for ev in output_events}
    rows = [(cat, lbl, clr) for cat, lbl, clr in categories if cat in present]
    if not rows:
        ax.axis("off")
        return
    row_height = 1.0 / len(rows)

    for row_idx, (cat, label, color) in enumerate(rows):
        y_top = 1.0 - row_idx * row_height
        y_mid = y_top - row_height * 0.5
        y_bot = y_top - row_height
        # 行区切り線
        ax.axhline(y_bot, color="#cccccc", lw=0.4, zorder=0)
        # ラベル
        ax.text(0.0, y_top - 0.02, label, ha="left", va="top", fontsize=6,
                transform=ax.transAxes, color=color)
        # カテゴリのイベントを時刻順に並べて差分＋積算を描画
        cat_events = sorted([ev for ev in output_events if ev.category == cat], key=lambda e: e.time)
        cumulative = 0.0
        for ev in cat_events:
            cumulative += ev.amount
            # 差分（上）
            ax.text(
                ev.time, y_mid + row_height * 0.15, f"+{ev.amount:g}",
                ha="center", va="bottom", fontsize=6, color="#222222", zorder=3,
            )
            # 積算（下）
            ax.text(
                ev.time, y_mid - row_height * 0.15, f"{cumulative:g}",
                ha="center", va="top", fontsize=6.5, fontweight="bold", color=color, zorder=3,
            )
            # 小さい縦線マーカー
            ax.axvline(ev.time, ymin=y_bot, ymax=y_top - row_height * 0.3,
                       color=color, lw=0.4, alpha=0.4, zorder=1)
        # 右端に総量表示
        if cumulative > 0:
            ax.text(0.99, y_mid, f"{cumulative:g}",
                    ha="right", va="center", fontsize=6.5, fontweight="bold",
                    color=color, transform=ax.transAxes, clip_on=False)


def _format_dose_event(ev: MedEvent) -> str:
    return f"{ev.dose:g}" if ev.dose is not None else ""


def _format_rate_event(ev: MedEvent, master: DrugMasterFile) -> str:
    return f"{ev.rate:g}" if ev.rate is not None else ""


def _render_fluids(ax, fluids: FluidBalanceSummary) -> None:
    ax.axis("off")
    lines = ["IN/OUT"]
    if fluids.in_items:
        lines.append("IN")
        for item in fluids.in_items:
            lines.append(f"  {item.label}: {item.volume_ml:g} mL")
    if fluids.out_items:
        lines.append("OUT")
        for item in fluids.out_items:
            lines.append(f"  {item.label}: {item.volume_ml:g} mL")
    if fluids.balance_ml is not None:
        lines.append(f"Balance: {fluids.balance_ml:g} mL")
    ax.text(0.01, 0.9, "\n".join(lines), ha="left", va="top", fontsize=8, transform=ax.transAxes)


def _render_info_4pane(
    ax,
    cost_report: Optional[CostReport],
    clinical_events: Sequence[ClinicalEvent],
    duration_specs: Sequence[DurationSpec],
    postop_orders: Optional[Sequence[str]],
    anesthesia_fee_result: Optional["AnesthesiaFeeResult"] = None,
) -> None:
    """コスト・イベント・時間・術後指示を横4ペイン構成で描画."""
    ax.axis("off")
    y_top = 0.95

    # ペイン1: コスト (x=0.0〜0.25)
    lines: list[str] = []
    if cost_report is not None:
        lines.append("コスト(薬剤)")
        for item in cost_report.items:
            price = "N/A" if item.cost is None else f"{item.cost:.0f} 円"
            lines.append(f"  {item.generic_name}: {price}")
        lines.append(f"  小計: {cost_report.total:.0f} 円")
    if anesthesia_fee_result is not None:
        if lines:
            lines.append("")
        lines.append("コスト(麻酔料)")
        for item in anesthesia_fee_result.items:
            lines.append(f"  {item.name}: {item.points}点")
        lines.append(f"  小計: {anesthesia_fee_result.total_points}点")
    if lines:
        ax.text(0.0, y_top, "\n".join(lines), ha="left", va="top", fontsize=7, transform=ax.transAxes)

    # ペイン2: イベント (x=0.26〜0.50)
    if clinical_events:
        event_lines = ["イベント"]
        for ev in clinical_events:
            event_lines.append(f"  {ev.time.strftime('%H:%M')} {ev.display_label}")
        ax.text(0.26, y_top, "\n".join(event_lines), ha="left", va="top", fontsize=7, transform=ax.transAxes)

    # ペイン3: 時間 (x=0.52〜0.74)
    if clinical_events:
        duration_lines = ["時間"]
        for label, minutes in _compute_durations(clinical_events, duration_specs):
            duration_lines.append(f"  {label}: {minutes}分" if minutes is not None else f"  {label}: —")
        ax.text(0.52, y_top, "\n".join(duration_lines), ha="left", va="top", fontsize=7, transform=ax.transAxes)

    # ペイン4: 術後指示 (x=0.76〜1.0)
    orders = postop_orders or []
    order_lines = ["術後指示"]
    for o in orders:
        order_lines.append(f"  {o}")
    if not orders:
        order_lines.append("  (記載なし)")
    ax.text(0.76, y_top, "\n".join(order_lines), ha="left", va="top", fontsize=7, transform=ax.transAxes)

    # ペイン区切り線
    for x in [0.25, 0.51, 0.75]:
        ax.plot([x, x], [0, 1], color="#cccccc", lw=0.5, transform=ax.transAxes, clip_on=False)


def _compute_durations(
    clinical_events: Sequence[ClinicalEvent],
    specs: Sequence[DurationSpec],
) -> list[tuple[str, Optional[int]]]:
    out: list[tuple[str, Optional[int]]] = []
    for spec in specs:
        start_times = [ev.time for ev in clinical_events if ev.type in spec.start_types]
        end_times = [ev.time for ev in clinical_events if ev.type in spec.end_types]
        if not start_times or not end_times:
            out.append((spec.label, None))
            continue
        start = min(start_times)
        end = max(end_times)
        if end < start:
            out.append((spec.label, None))
            continue
        out.append((spec.label, int(round((end - start).total_seconds() / 60.0))))
    return out


def _resolve_ecg_snapshot_times(
    clinical_events: Sequence[ClinicalEvent],
    extra_times: Optional[Sequence[datetime]],
) -> list[datetime]:
    times = {time for time in (extra_times or [])}
    for ev in clinical_events:
        if ev.type in {"anesthesia_start", "anesthesia_end"}:
            times.add(ev.time)
    return sorted(times)


def _render_ecg_strips(
    ax,
    waveform: Waveform,
    snapshot_times: Sequence[datetime],
    clinical_events: Sequence[ClinicalEvent],
    window_sec: float,
) -> None:
    snapshots: list[tuple[datetime, str, list[datetime], list[float]]] = []
    label_map: dict[datetime, str] = {}
    for ev in clinical_events:
        label_map.setdefault(ev.time, ev.display_label)
    half = timedelta(seconds=window_sec / 2.0)
    for center in snapshot_times:
        xs, ys = _slice_waveform(waveform, center - half, center + half)
        if not xs:
            continue
        snapshots.append((center, label_map.get(center, "任意"), xs, ys))
    ax.axis("off")
    if not snapshots:
        return
    sub = GridSpecFromSubplotSpec(1, len(snapshots), subplot_spec=ax.get_subplotspec(), wspace=0.15)
    for idx, (center, label, xs, ys) in enumerate(snapshots):
        strip = ax.figure.add_subplot(sub[0, idx])
        strip.plot(xs, ys, color="#222222", lw=0.7)
        strip.set_xlim(xs[0], xs[-1])
        strip.set_yticks([])
        strip.set_xticks([])
        strip.spines["top"].set_visible(False)
        strip.spines["right"].set_visible(False)
        strip.spines["left"].set_visible(False)
        strip.spines["bottom"].set_linewidth(0.3)
        strip.tick_params(axis="y", left=False, labelleft=False)
        strip.text(0.5, -0.12, center.strftime("%H:%M"), transform=strip.transAxes, ha="center", va="top", fontsize=7)


def _slice_waveform(
    waveform: Waveform,
    start_time: datetime,
    end_time: datetime,
) -> tuple[list[datetime], list[float]]:
    if waveform.sample_rate_hz <= 0:
        return [], []
    start_index = max(0, int((start_time - waveform.start_time).total_seconds() * waveform.sample_rate_hz))
    end_index = min(len(waveform.values), int((end_time - waveform.start_time).total_seconds() * waveform.sample_rate_hz) + 1)
    if end_index <= start_index:
        return [], []
    xs: list[datetime] = []
    ys: list[float] = []
    for idx in range(start_index, end_index):
        value = waveform.values[idx]
        if value is None:
            continue
        xs.append(waveform.start_time + timedelta(seconds=idx / waveform.sample_rate_hz))
        ys.append(float(value))
    return xs, ys


def _render_ce(
    ax,
    ce_results: dict[str, CeResult],
    master: DrugMasterFile,
    ce_t0: Optional[datetime],
    main_window: tuple[datetime, datetime],
    ce_window: tuple[datetime, datetime],
    tick_interval_min: Optional[float],
) -> None:
    ax.grid(True, ls=":", alpha=0.45)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if ce_t0 is None:
        ax.axis("off")
        return
    ax.set_xlim(ce_window)
    if tick_interval_min is not None:
        _apply_tick_interval(ax, tick_interval_min)
    end_main = main_window[1]
    ax.axvline(end_main, color="#666666", ls="--", lw=0.8)
    ax.text(end_main, 0.97, "予測", transform=ax.get_xaxis_transform(), ha="center", va="top", fontsize=7, color="#444444")
    for drug_id, res in ce_results.items():
        xs = [ce_t0 + timedelta(minutes=m) for m in res.times_min]
        label = f"{master.get(drug_id).generic_name} Ce ({res.conc_unit})"
        ax.plot(xs, res.ce, lw=0.9, label=label)
    ax.set_ylabel("Ce")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.legend(loc="upper right", fontsize=8)
