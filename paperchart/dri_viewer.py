#!/usr/bin/env python3
"""B650 DRI バイナリログの 2 ペイン・ビューア.

TeraTerm 等で保存した GE CARESCAPE B650 のシリアル(DRI)出力バイナリを読み、
- 左ペイン: オリジナルの生バイナリ（16進ダンプ）
- 右ペイン: 解析結果（フレーム/チェックサム/時刻/デコード数値）
を並べて表示する。

使い方:
    python paperchart/dri_viewer.py capture.bin            # GUI (tkinter)
    python paperchart/dri_viewer.py capture.bin --text     # 端末に2ペイン出力
    python paperchart/dri_viewer.py --demo                 # 合成データでデモ

GUI が使えない環境（DISPLAY 無し）では自動的に --text 相当にフォールバックする。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# パッケージ外(paperchart/ 直下)からの直接実行に対応
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anesthesia_record.dri_decode import (  # noqa: E402
    DriFrame,
    build_displ_payload,
    decode_stream,
    stuff_frame,
)
from anesthesia_record.paperchart_config import (  # noqa: E402
    PaperChartConfig,
    load_paperchart_config,
)

DEFAULT_CONFIG = Path(__file__).resolve().parent / "b650.txt"
HEX_BYTES_PER_LINE = 16


def hexdump(data: bytes, bytes_per_line: int = HEX_BYTES_PER_LINE) -> str:
    """オフセット | 16進 | ASCII 形式のダンプ文字列."""
    lines: list[str] = []
    for base in range(0, len(data), bytes_per_line):
        chunk = data[base : base + bytes_per_line]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        hex_part = f"{hex_part:<{bytes_per_line * 3 - 1}}"
        ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{base:08X}  {hex_part}  {ascii_part}")
    return "\n".join(lines)


def render_frame(index: int, frame: DriFrame) -> str:
    """1 フレームの解析結果を文字列化する."""
    lines: list[str] = []
    status = "OK" if frame.checksum_ok else "NG"
    lines.append(f"── frame #{index}  ({len(frame.raw)} bytes, checksum {status}) ──")

    if frame.header is not None:
        h = frame.header
        dt = h.datetime_utc
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "(no time)"
        lines.append(f"  time={dt_str}  maintype={h.r_maintype}  r_nbr={h.r_nbr}")
        types = [
            sr.sr_type for sr in h.subrecords if sr.sr_type != 0xFF
        ]
        lines.append(f"  subrecords(type)={types}")

    if frame.note:
        lines.append(f"  note: {frame.note}")

    shown = [v for v in frame.values if v.valid]
    if shown:
        for v in shown:
            unit = f" {v.unit}" if v.unit else ""
            lines.append(f"    {v.name:8} = {v.value:g}{unit}  (raw={v.raw})")
    elif frame.header is not None and not frame.note:
        lines.append("    (有効な数値なし)")

    return "\n".join(lines)


def render_analysis(frames: list[DriFrame]) -> str:
    ok = sum(1 for f in frames if f.checksum_ok)
    header = f"frames={len(frames)}  checksum OK={ok}  NG={len(frames) - ok}"
    body = "\n\n".join(render_frame(i, f) for i, f in enumerate(frames))
    return f"{header}\n\n{body}"


def _demo_bytes() -> bytes:
    """デモ用の合成 DRI ストリーム（HR/NIBP/SpO2/TEMP を含む 2 フレーム）."""
    f1 = stuff_frame(
        build_displ_payload(1_700_000_000, {6: 72, 78: 12000, 92: 3650, 124: 9800})
    )
    f2 = stuff_frame(
        build_displ_payload(1_700_000_002, {6: 75, 78: 11800, 92: 3660, 124: 9700})
    )
    return f1 + f2


def run_text(raw: bytes, config: PaperChartConfig, stream=sys.stdout) -> None:
    """端末向けに左(hex)/右(解析)を横並びで出力する."""
    frames = decode_stream(raw, config)
    left = hexdump(raw).splitlines()
    right = render_analysis(frames).splitlines()

    left_width = max((len(line) for line in left), default=0)
    rows = max(len(left), len(right))
    sep = " │ "
    print("ORIGINAL (hex)".ljust(left_width) + sep + "ANALYSIS", file=stream)
    print("-" * left_width + sep + "-" * 40, file=stream)
    for i in range(rows):
        lcell = left[i] if i < len(left) else ""
        rcell = right[i] if i < len(right) else ""
        print(f"{lcell:<{left_width}}{sep}{rcell}", file=stream)


def run_gui(raw: bytes, config: PaperChartConfig, title: str) -> bool:
    """tkinter で 2 ペイン表示する。GUI 不可なら False を返す."""
    try:
        import tkinter as tk
        from tkinter import font as tkfont
        from tkinter.scrolledtext import ScrolledText
    except Exception:
        return False

    try:
        root = tk.Tk()
    except Exception:
        return False

    root.title(title)
    root.geometry("1200x720")

    mono = tkfont.nametofont("TkFixedFont")

    paned = tk.PanedWindow(root, orient=tk.HORIZONTAL, sashwidth=6)
    paned.pack(fill=tk.BOTH, expand=True)

    left_frame = tk.Frame(paned)
    right_frame = tk.Frame(paned)
    paned.add(left_frame, stretch="always")
    paned.add(right_frame, stretch="always")

    tk.Label(left_frame, text="ORIGINAL (hex)", anchor="w").pack(fill=tk.X)
    left = ScrolledText(left_frame, wrap=tk.NONE, font=mono)
    left.pack(fill=tk.BOTH, expand=True)
    left.insert(tk.END, hexdump(raw))
    left.configure(state=tk.DISABLED)

    tk.Label(right_frame, text="ANALYSIS", anchor="w").pack(fill=tk.X)
    right = ScrolledText(right_frame, wrap=tk.NONE, font=mono)
    right.pack(fill=tk.BOTH, expand=True)
    right.insert(tk.END, render_analysis(decode_stream(raw, config)))
    right.configure(state=tk.DISABLED)

    root.mainloop()
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "capture", nargs="?", help="DRI バイナリログ（TeraTerm 等で保存）"
    )
    parser.add_argument(
        "--config", default=str(DEFAULT_CONFIG), help="paperChart 設定ファイル"
    )
    parser.add_argument("--text", action="store_true", help="GUI を使わず端末出力")
    parser.add_argument("--demo", action="store_true", help="合成データでデモ表示")
    args = parser.parse_args(argv)

    if args.demo:
        raw = _demo_bytes()
        title = "DRI viewer (demo)"
    elif args.capture:
        raw = Path(args.capture).read_bytes()
        title = f"DRI viewer - {Path(args.capture).name}"
    else:
        parser.error("capture ファイルか --demo を指定してください")

    config = load_paperchart_config(args.config)

    if args.text:
        run_text(raw, config)
        return 0

    if not run_gui(raw, config, title):
        run_text(raw, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
