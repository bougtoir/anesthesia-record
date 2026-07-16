"""リアルタイム用チャート描画: chart_erga と同一の見た目で Figure に描画.

chart_erga.render_chart_erga のレンダリング関数を直接再利用し、
Figure オブジェクトに描画してGUI上で表示する。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import matplotlib.dates as mdates
from matplotlib.figure import Figure

from ..chart_common import _configure_japanese_font
from ..chart_erga import (
    BandSpec,
    DEFAULT_AXIS_SPECS,
    DEFAULT_DISPLAY_INTERVALS,
    DEFAULT_DURATION_SPECS,
    DEFAULT_EVENT_ICONS,
    DEFAULT_VITAL_STYLES,
    _drug_rows,
    _render_drug_lane,
    _render_header,
    _render_info_4pane,
    _render_output_bowling,
    _render_vitals,
    _sorted_events,
)
from ..cost import CostReport, compute_cost
from ..drug_master import DrugMasterFile
from ..events import ClinicalEvent
from ..models import MedEvent, OutputEvent, Patient
from ..anesthesia_fee import AnesthesiaFeeConfig, AnesthesiaFeeResult, AnesthesiaEvent, PositionEvent, compute_anesthesia_fee
from .session import AnesthesiaSession
from ..vitals import VitalsTable


_configure_japanese_font()


def _get_drug_rows(med_events: list[MedEvent], master: DrugMasterFile) -> dict[str, list[MedEvent]]:
    """薬剤IDごとにイベントをグループ化し、表示順にソート (テスト互換ラッパー)."""
    lanes = _drug_rows(_sorted_events(med_events), master)
    return {lane.drug_id: lane.events for lane in lanes}


def render_live_chart(
    fig: Figure,
    session: AnesthesiaSession,
    drug_master: DrugMasterFile,
    fee_config: Optional[AnesthesiaFeeConfig] = None,
) -> None:
    """セッションデータからリアルタイムチャートを描画.

    chart_erga と完全に同一のレイアウト・スタイルで fig を描画する。
    GUIの定期更新から呼ばれる。
    """
    if session.anesthesia_start is None:
        ax = fig.add_subplot(111)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, "麻酔記録 v0.2\n\n患者情報を入力して「麻酔開始」を押してください",
                ha="center", va="center", fontsize=14, color="#666")
        ax.axis("off")
        return

    t_start = session.anesthesia_start
    t_end = session.anesthesia_end or datetime.now()
    t_end_display = t_end + timedelta(minutes=2)
    main_window = (t_start, t_end_display)

    ordered_events = _sorted_events(session.med_events)
    clinical_sorted = sorted(session.events, key=lambda e: e.time)
    try:
        drug_rows = _drug_rows(ordered_events, drug_master)
    except KeyError:
        # マスタ未登録の薬剤がある場合はスキップ
        drug_rows = []

    # コスト算定
    cost_report: Optional[CostReport] = None
    try:
        cost_report = compute_cost(session.med_events, drug_master, session.patient)
    except (ValueError, TypeError, KeyError):
        pass

    # 麻酔料算定
    anesthesia_fee_result: Optional[AnesthesiaFeeResult] = None
    if fee_config and session.anesthesia_start and session.anesthesia_end:
        try:
            fee_events, pos_events, sev_ids = _build_fee_events(session)
            anesthesia_fee_result = compute_anesthesia_fee(fee_events, pos_events, sev_ids, fee_config)
        except (ValueError, TypeError, KeyError):
            pass

    # --- バンド構成 (chart_erga と同一) ---
    bands: list[BandSpec] = [
        BandSpec(
            0.62,
            lambda ax: _render_header(ax, session.patient, None, "麻酔記録", main_window),
            sharex=False,
        ),
        BandSpec(
            4.9,
            lambda ax: _render_vitals(
                ax,
                session.vitals or VitalsTable(parameters={}, time_column="time"),
                dict(DEFAULT_VITAL_STYLES),
                list(DEFAULT_AXIS_SPECS),
                {spec.key: spec for spec in DEFAULT_AXIS_SPECS},
                dict(DEFAULT_EVENT_ICONS),
                clinical_sorted,
                False,  # show_floating_latest
                "upper right",
                5.0,
                "white",
                False,  # draggable_latest
                main_window,
                None,  # tick_interval_min
                dict(DEFAULT_DISPLAY_INTERVALS),
                {},  # display_modes
            ),
        ),
    ]
    for lane in drug_rows:
        bands.append(BandSpec(0.12, lambda ax, lane=lane: _render_drug_lane(ax, lane, drug_master, main_window, session.patient)))
    if session.output_events:
        bands.append(BandSpec(1.0, lambda ax: _render_output_bowling(ax, session.output_events, main_window)))
    # コスト・イベント・時間・術後指示 4ペイン
    _has_info = cost_report is not None or clinical_sorted or session.postop_notes or anesthesia_fee_result is not None
    if _has_info:
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
            lambda ax: _render_info_4pane(
                ax, cost_report, clinical_sorted,
                list(DEFAULT_DURATION_SPECS),
                session.postop_notes or None,
                anesthesia_fee_result,
            ),
            sharex=False,
        ))

    # --- Figure レイアウト ---
    right_margin = 0.94
    fig.subplots_adjust(left=0.05, right=right_margin, top=0.97, bottom=0.04)
    gs = fig.add_gridspec(len(bands), 1, height_ratios=[b.height for b in bands])

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

    fig.tight_layout(h_pad=0.0)


def _build_fee_events(session: AnesthesiaSession) -> tuple[list[AnesthesiaEvent], list[PositionEvent], list[str]]:
    """セッションの臨床イベントから麻酔料算定用イベントを構築.

    デフォルトで全身麻酔(GA)イベントを anesthesia_start〜end から生成する。
    """
    assert session.anesthesia_start is not None
    assert session.anesthesia_end is not None

    # 全身麻酔イベント (method_id: "general_anesthesia")
    fee_events = [
        AnesthesiaEvent(
            method_id="general_anesthesia",
            start_time=session.anesthesia_start,
            end_time=session.anesthesia_end,
        )
    ]

    # 特殊体位イベント (position_start/end から構築)
    pos_events: list[PositionEvent] = []
    pos_starts: dict[str, datetime] = {}
    for ev in sorted(session.events, key=lambda e: e.time):
        if ev.type == "position_start" or ev.type == "position_change":
            pos_id = ev.label or "unknown"
            pos_starts[pos_id] = ev.time
        elif ev.type == "position_end":
            pos_id = ev.label or "unknown"
            start = pos_starts.pop(pos_id, None)
            if start:
                pos_events.append(PositionEvent(
                    position_id=pos_id,
                    start_time=start,
                    end_time=ev.time,
                ))

    # 重症加算: セッションから取得 (現時点ではデフォルト空)
    sev_ids: list[str] = []

    return fee_events, pos_events, sev_ids
