"""麻酔記録チャート出力（様式テンプレート差し替え可能）.

内部データモデル（バイタル・投薬イベント・Ce）は様式非依存。
本モジュールは汎用トレンド + 投薬注記 + Ce のレイアウトを描画し、
PNG/PDF に出力する。JSA様式/院内様式はテンプレートとして拡張する。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from .chart_common import _configure_japanese_font


_configure_japanese_font()

from .drug_master import DrugMasterFile  # noqa: E402
from .events import ClinicalEvent  # noqa: E402
from .models import MedEvent, Delivery  # noqa: E402
from .pkpd import CeResult  # noqa: E402
from .vitals import VitalsTable  # noqa: E402


def render_chart(
    vitals: VitalsTable,
    events: Sequence[MedEvent],
    master: DrugMasterFile,
    out_path: str,
    vital_params: Optional[Sequence[str]] = None,
    ce_results: Optional[dict[str, CeResult]] = None,
    ce_t0: Optional[datetime] = None,
    clinical_events: Optional[Sequence[ClinicalEvent]] = None,
    title: str = "麻酔記録",
) -> str:
    """トレンド + 投薬/臨床イベント + Ce を1枚に描画し out_path に保存.

    バイタルは取得値をそのまま線で結ぶ（スムージング/補間は行わない）。
    """
    params = list(vital_params or vitals.parameter_names())
    has_ce = bool(ce_results)
    nrows = 2 if has_ce else 1
    fig, axes = plt.subplots(
        nrows, 1, figsize=(11.69, 8.27), sharex=True,
        gridspec_kw={"height_ratios": [3, 1] if has_ce else [1]},
    )
    ax_v = axes[0] if has_ce else axes

    for name in params:
        if name not in vitals.parameters:
            continue
        s = vitals.parameters[name]
        xs = [t for t, v in zip(s.times, s.values) if v is not None]
        ys = [v for v in s.values if v is not None]
        if xs:
            # 生値をそのまま描画（drawstyle 既定=直線結線、スムージングなし）
            ax_v.plot(xs, ys, marker=".", ms=3, lw=0.8, label=name)

    _annotate_events(ax_v, events, master)
    _annotate_clinical(ax_v, clinical_events or [])

    ax_v.set_ylabel("バイタル")
    ax_v.legend(loc="upper right", fontsize=8, ncol=2)
    ax_v.grid(True, ls=":", alpha=0.5)
    ax_v.set_title(title)

    if has_ce and ce_t0 is not None:
        ax_c = axes[1]
        for drug_id, res in ce_results.items():
            xs = [
                ce_t0.timestamp() + m * 60.0 for m in res.times_min
            ]
            xs_dt = [datetime.fromtimestamp(x) for x in xs]
            label = f"{master.get(drug_id).generic_name} Ce ({res.conc_unit})"
            ax_c.plot(xs_dt, res.ce, lw=1.0, label=label)
        ax_c.set_ylabel("効果部位濃度")
        ax_c.legend(loc="upper right", fontsize=8)
        ax_c.grid(True, ls=":", alpha=0.5)
        ax_c.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    else:
        ax_v.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def _annotate_events(ax, events: Sequence[MedEvent], master: DrugMasterFile) -> None:
    ymin, ymax = ax.get_ylim()
    for ev in events:
        drug = master.get(ev.drug_id)
        color = drug.color or "#555555"
        ax.axvline(ev.start_time, color=color, ls="--", lw=0.6, alpha=0.6)
        marker = "▼" if ev.delivery is Delivery.BOLUS else "▶"
        ax.annotate(
            f"{marker}{drug.generic_name}",
            xy=(ev.start_time, ymax),
            xytext=(0, -8),
            textcoords="offset points",
            rotation=90,
            fontsize=7,
            color=color,
            va="top",
            ha="center",
        )


def _annotate_clinical(ax, events: Sequence[ClinicalEvent]) -> None:
    ymin, ymax = ax.get_ylim()
    for ev in events:
        ax.axvline(ev.time, color="#222222", ls="-", lw=0.7, alpha=0.7)
        ax.annotate(
            ev.display_label,
            xy=(ev.time, ymin),
            xytext=(0, 4),
            textcoords="offset points",
            rotation=90,
            fontsize=7,
            color="#222222",
            va="bottom",
            ha="center",
        )
