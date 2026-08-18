"""麻酔記録チャート用の共通設定."""

from __future__ import annotations

import matplotlib
import matplotlib.font_manager as fm

_JP_FONT_CANDIDATES = [
    # Windows
    "Yu Gothic",
    "Meiryo",
    "MS Gothic",
    "MS PGothic",
    # Linux
    "Noto Sans CJK JP",
    "IPAGothic",
    "IPAPGothic",
    "TakaoGothic",
    "VL Gothic",
    "WenQuanYi Zen Hei",
]


def _configure_japanese_font() -> None:
    available = {f.name for f in fm.fontManager.ttflist}
    for name in _JP_FONT_CANDIDATES:
        if name in available:
            matplotlib.rcParams["font.family"] = name
            break
    matplotlib.rcParams["axes.unicode_minus"] = False
