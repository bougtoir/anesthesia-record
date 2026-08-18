#!/usr/bin/env python3
"""UVC HDMIキャプチャ + OCR でB650等の生体情報モニタ数値を読み取る."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from collections import defaultdict
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Any, Callable, Optional

# MSMF バックエンドの長時間初期化を抑制
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import cv2
import numpy as np
import yaml
from rapidocr_onnxruntime import RapidOCR

from .vitals import VitalSeries, VitalsTable

DEFAULT_CONFIG = (
    Path(__file__).resolve().parent.parent / "paperchart" / "b650_video.yaml"
)

# ROI ごとの最終 OCR/解析実行時刻（秒）
_LAST_RUN: dict[str, float] = {}


def _log(msg: str) -> None:
    """`B650Video.log` 等へ進捗を出力（stderr 経由）."""
    print(f"[monitor_video] {datetime.now().isoformat(timespec='seconds')} {msg}", file=sys.stderr, flush=True)


def load_config(path: Path | str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _try_capture(
    idx: int,
    backend: int,
    width: int | None = None,
    height: int | None = None,
    timeout: float = 4.0,
) -> cv2.VideoCapture | None:
    """タイムアウト付きで VideoCapture をオープンし、フレームが取れれば返す.

    タイムアウト時やフレーム取得失敗時は、別スレッドで開いた cap も確実に
    release してカメラハンドルを解放する.
    """
    container: dict[str, Any] = {"cap": None, "success": False}

    def _open() -> None:
        cap: cv2.VideoCapture | None = None
        try:
            cap = cv2.VideoCapture(idx, backend)
            container["cap"] = cap
            if not cap.isOpened():
                return
            if width and height:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            ret = False
            frame: np.ndarray | None = None
            for _ in range(10):
                if not cap.isOpened():
                    return
                ret, frame = cap.read()
                if ret and frame is not None and frame.size > 0:
                    container["success"] = True
                    return
                time.sleep(0.05)
        except Exception:
            pass
        finally:
            # 成功していない場合はここで解放する目印を残す
            if not container.get("success") and cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    t = threading.Thread(target=_open, daemon=True)
    t.start()
    t.join(timeout=timeout)

    cap = container.get("cap")
    if container.get("success") and cap is not None and cap.isOpened():
        return cap

    # タイムアウトまたは失敗: cap がまだ開いていれば確実に解放
    if cap is not None:
        try:
            cap.release()
        except Exception:
            pass
    # ワーカースレッドが解放処理中の場合があるので短く待つ
    t.join(timeout=1.0)
    return None


# バックエンドごとのオープンタイムアウト（秒）
_BACKEND_TIMEOUTS: dict[int, float] = {
    cv2.CAP_DSHOW: 4.0,
    cv2.CAP_MSMF: 8.0,
    cv2.CAP_ANY: 10.0,
}


def find_camera_index() -> int | None:
    """0 から順に試し, フレームが取れる最初のデバイスを返す."""
    backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    for i in range(10):
        for backend in backends:
            timeout = _BACKEND_TIMEOUTS.get(backend, 4.0)
            for w, h in [(640, 480), (None, None)]:
                cap = _try_capture(i, backend, w, h, timeout=timeout)
                if cap is not None:
                    cap.release()
                    return i
    return None


def open_capture(config: dict[str, Any], verbose: bool = True) -> cv2.VideoCapture:
    src = config.get("source", {})
    idx = src.get("device_index")
    if idx is None:
        idx = find_camera_index()
        if idx is None:
            raise RuntimeError("UVC キャプチャデバイスが見つかりません")
    width = int(src.get("width", 640))
    height = int(src.get("height", 480))
    # 明示的な backend 指定があれば優先
    preferred: list[int] = []
    backend_name = str(src.get("backend", "")).lower()
    if backend_name == "dshow":
        preferred = [cv2.CAP_DSHOW]
    elif backend_name == "msmf":
        preferred = [cv2.CAP_MSMF]
    elif backend_name == "any":
        preferred = [cv2.CAP_ANY]
    backends = preferred + [b for b in [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY] if b not in preferred]
    last_err = None
    for backend in backends:
        timeout = _BACKEND_TIMEOUTS.get(backend, 4.0)
        for w, h in [(width, height), (None, None)]:
            cap = _try_capture(int(idx), backend, w, h, timeout=timeout)
            if cap is not None:
                ret, frame = cap.read()
                if ret and frame is not None:
                    actual_h, actual_w = frame.shape[:2]
                    if verbose:
                        print(f"Opened camera {idx} (backend={backend}): {actual_w}x{actual_h}", file=sys.stderr)
                    return cap
                cap.release()
        last_err = f"backend {backend} failed"
    raise RuntimeError(f"カメラ {idx} からフレームを取得できません: {last_err}")


def crop_roi(frame: np.ndarray, roi: dict[str, float]) -> np.ndarray:
    h, w = frame.shape[:2]
    x1 = int(roi["x"] * w)
    y1 = int(roi["y"] * h)
    x2 = int((roi["x"] + roi["w"]) * w)
    y2 = int((roi["y"] + roi["h"]) * h)
    return frame[y1:y2, x1:x2]


def run_ocr(engine: RapidOCR, image: np.ndarray) -> list[dict[str, Any]]:
    result, _ = engine(image)
    if not result:
        return []
    out: list[dict[str, Any]] = []
    for box, text, conf in result:
        out.append({"text": str(text), "conf": float(conf), "box": box})
    return out


TIME_RE = re.compile(r"(\d{1,2})\s*[:：]?\s*(\d{2})")
INT_RE = re.compile(r"\b(\d{2,3})\b")
FLOAT_RE = re.compile(r"(-?\d+\.\d+)")
SPACED_FLOAT_RE = re.compile(r"(-?\d)\s*[.．,]\s*(\d+)")
NIBP_RE = re.compile(r"(\d{2,3})/(\d{2,3})(?:.*?\((\d{2,3})\))?")
CUFF_RE = re.compile(r"(?:カフ|力[7フ]?)[:：\s-]*(\d{2,3})", re.UNICODE)
INTERVAL_RE = re.compile(r"(\d+)\s*min")


def _box_area(box: list[list[float]]) -> float:
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return float((max(xs) - min(xs)) * (max(ys) - min(ys)))


def _box_center(box: list[list[float]]) -> tuple[float, float]:
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def _is_numeric_token(text: str) -> bool:
    txt = text.strip()
    return bool(
        re.fullmatch(r"\d+", txt)
        or re.fullmatch(r"[.．,]\d+", txt)
        or re.fullmatch(r"\d+[.．,]", txt)
        or txt in (".", "．", ",")
    )


def _merge_digit_tokens(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OCR が 1 文字ずつ認識した数字・小数点を、近傍で結合する."""
    if not raw:
        return []
    numeric_items: list[dict[str, Any]] = []
    non_numeric_items: list[dict[str, Any]] = []
    for it in raw:
        if _is_numeric_token(it["text"]):
            numeric_items.append(dict(it))
        else:
            non_numeric_items.append(it)
    if len(numeric_items) < 2:
        return raw
    # 左から右へソート
    numeric_items.sort(key=lambda it: _box_center(it["box"])[0])

    def _can_merge(cur_text: str, nxt_text: str) -> bool:
        c = cur_text.strip()
        n = nxt_text.strip()
        # 一桁 + 一桁（例: 9 + 9 = 99）
        if re.fullmatch(r"\d", c) and re.fullmatch(r"\d", n):
            return True
        # 整数 + 小数部分（例: 0 + .1）
        if re.fullmatch(r"\d+", c) and re.fullmatch(r"[.．,]\d+", n):
            return True
        # 小数部分の継続（例: 0. + 1）
        if re.fullmatch(r"\d+[.．,]", c) and re.fullmatch(r"\d+", n):
            return True
        # 小数点のみのトークンを挟む（例: 0 + . + 1）
        if re.fullmatch(r"\d+", c) and n in (".", "．", ","):
            return True
        if c in (".", "．", ",") and re.fullmatch(r"\d+", n):
            return True
        return False

    merged: list[dict[str, Any]] = []
    i = 0
    while i < len(numeric_items):
        cur = numeric_items[i]
        cx, cy = _box_center(cur["box"])
        cw = max([p[0] for p in cur["box"]]) - min([p[0] for p in cur["box"]])
        ch = max([p[1] for p in cur["box"]]) - min([p[1] for p in cur["box"]])
        j = i + 1
        while j < len(numeric_items):
            ox, oy = _box_center(numeric_items[j]["box"])
            ow = max([p[0] for p in numeric_items[j]["box"]]) - min(
                [p[0] for p in numeric_items[j]["box"]]
            )
            # 右隣で、y 方向に近く、距離が数字幅の 2.5 倍以内なら結合
            if not (cx < ox and abs(oy - cy) <= ch and ox - cx <= max(cw, ow) * 2.5):
                break
            nxt = numeric_items[j]
            if not _can_merge(cur["text"], nxt["text"]):
                break
            # 小数点は正規化して結合
            ct = cur["text"].strip()
            nt = nxt["text"].strip()
            if re.fullmatch(r"\d+", ct) and re.fullmatch(r"[.．,]\d+", nt):
                cur_text = f"{ct}.{nt[1:]}"
            elif re.fullmatch(r"\d+[.．,]", ct) and re.fullmatch(r"\d+", nt):
                cur_text = f"{ct[:-1]}.{nt}"
            elif ct in (".", "．", ","):
                # 前後のトークンが整数なので "X.Y" 形を組み立て
                if merged and re.fullmatch(r"\d+", merged[-1]["text"].strip()):
                    prev = merged.pop()
                    cur_text = f"{prev['text'].strip()}.{nt}"
                    # box を統合
                    nxt["box"] = prev["box"]
                else:
                    cur_text = f"0.{nt}"
            elif nt in (".", "．", ","):
                cur_text = f"{ct}."
            else:
                cur_text = ct + nt
            all_x = [p[0] for p in cur["box"]] + [p[0] for p in nxt["box"]]
            all_y = [p[1] for p in cur["box"]] + [p[1] for p in nxt["box"]]
            new_box = [
                [min(all_x), min(all_y)],
                [max(all_x), min(all_y)],
                [max(all_x), max(all_y)],
                [min(all_x), max(all_y)],
            ]
            cur = {
                "text": cur_text,
                "conf": min(cur["conf"], nxt["conf"]),
                "box": new_box,
            }
            cx, cy = _box_center(cur["box"])
            cw = max([p[0] for p in cur["box"]]) - min([p[0] for p in cur["box"]])
            ch = max([p[1] for p in cur["box"]]) - min([p[1] for p in cur["box"]])
            j += 1
        merged.append(cur)
        i = j
    return non_numeric_items + merged


def parse_time(text: str) -> str | None:
    m = TIME_RE.search(text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def _best_nibp_assignment(nums: list[int]) -> dict[str, Any]:
    """1〜3 個の NIBP 数値から SBP>=MAP>=DBP となるようロールを割り当てる."""
    nums = sorted(nums, reverse=True)
    if len(nums) >= 3:
        return {"sys": nums[0], "map": nums[1], "dia": nums[2]}
    if len(nums) == 2:
        s, d = nums[0], nums[1]
        return {"sys": s, "dia": d, "map": round((s + 2 * d) / 3.0, 1)}
    if len(nums) == 1:
        return {"sys": nums[0], "dia": None, "map": None}
    return {"sys": None, "dia": None, "map": None}


def parse_nibp(text: str) -> dict[str, Any] | None:
    """NIBP 値をパース。必ず SBP>=MAP>=DBP を満たすよう再配置する."""
    nums: list[int] = []
    # スラッシュ・カッコの前後に空白があっても許容
    m = re.search(
        r"(\d{2,3})\s*/\s*(\d{2,3})(?:.*?\(\s*(\d{2,3})\s*\))?",
        text,
    )
    if m:
        nums = [int(m.group(1)), int(m.group(2))]
        if m.group(3):
            nums.append(int(m.group(3)))
    else:
        nums = [int(x) for x in re.findall(r"\b\d{2,3}\b", text)]

    out: dict[str, Any] = _best_nibp_assignment(nums)

    cm = CUFF_RE.search(text)
    if cm:
        out["measuring"] = True
        out["cuff_pressure"] = int(cm.group(1))
    else:
        out["measuring"] = False
        out["cuff_pressure"] = None
    return out if (out.get("sys") is not None or out.get("measuring")) else None


def _value_from_raw(raw: list[dict[str, Any]], name: str) -> Any:
    """OCR raw boxes から最も大きな領域を占める数値を選択する."""
    merged = _merge_digit_tokens(raw)
    best: tuple[float, Any] | None = None
    for it in merged:
        text = it["text"]
        area = _box_area(it["box"])
        if name == "st":
            m = SPACED_FLOAT_RE.search(text) or FLOAT_RE.search(text)
            if m:
                val = float(f"{m.group(1)}.{m.group(2)}")
                if best is None or area > best[0]:
                    best = (area, val)
        elif name in ("hr", "spo2", "bis"):
            # SpO2 は小さな '90OFF' 等の偽検出を避けるため 2 桁以上を優先
            m = INT_RE.search(text)
            if m:
                val = int(m.group(1))
                if best is None or area > best[0]:
                    best = (area, val)
    return best[1] if best else None


def parse_value(name: str, text: str, raw: list[dict[str, Any]] | None = None) -> Any:
    if not text:
        return None
    if name == "time":
        return parse_time(text)
    if name == "nibp":
        return parse_nibp(text)
    if raw:
        return _value_from_raw(raw, name)
    # raw がなければテキストのみでフォールバック
    if name == "st":
        m = SPACED_FLOAT_RE.search(text) or FLOAT_RE.search(text)
        if m:
            return float(f"{m.group(1)}.{m.group(2)}")
    if name in ("hr", "spo2", "bis"):
        ms = INT_RE.findall(text)
        if ms:
            return int(ms[0])
    return None


def _box_color(hsv: np.ndarray, box: list[list[float]]) -> str | None:
    """OCR box の dominant color を返す (white/red/yellow/green/cyan/blue)."""
    pts = np.array(box, dtype=np.int32)
    x, y, wb, hb = cv2.boundingRect(pts)
    h_h, h_w = hsv.shape[:2]
    x = max(0, min(x, h_w - 1))
    y = max(0, min(y, h_h - 1))
    wb = max(0, min(wb, h_w - x))
    hb = max(0, min(hb, h_h - y))
    if wb <= 0 or hb <= 0:
        return None
    crop = hsv[y : y + hb, x : x + wb]
    val = crop[..., 2]
    sat = crop[..., 1]
    lit = val > 80
    if not lit.any():
        return None
    s = sat[lit]
    v = val[lit]
    med_s = float(np.median(s))
    med_v = float(np.median(v))
    if med_s < 50 and med_v > 160:
        return "white"
    colored = s >= 40
    if not colored.any():
        return None
    h = crop[..., 0][lit]
    med_h = float(np.median(h[colored]))
    if (med_h <= 12 or med_h >= 168) and med_s > 50:
        return "red"
    if 18 <= med_h <= 42 and med_s > 50:
        return "yellow"
    if 42 <= med_h <= 78 and med_s > 50:
        return "green"
    if 78 <= med_h <= 105 and med_s > 50:
        return "cyan"
    if 105 <= med_h <= 135 and med_s > 50:
        return "blue"
    return None


_PURE_INT_RE = re.compile(r"^\d+$")
_PURE_FLOAT_RE = re.compile(r"^-?\d+[.．,]\d+$")


def _try_int(text: str) -> int | None:
    t = text.strip().replace(" ", "")
    if _PURE_INT_RE.match(t):
        try:
            return int(t)
        except ValueError:
            return None
    return None


def _try_float(text: str) -> float | None:
    t = text.strip().replace(" ", "").replace("．", ".").replace(",", ".")
    if _PURE_FLOAT_RE.match(t):
        try:
            return float(t)
        except ValueError:
            return None
    return None


def parse_numeric_panel(
    frame: np.ndarray, engine: RapidOCR | None = None
) -> dict[str, Any]:
    """右側数値パネルを色で読み取り、HR/ST/SpO2/BIS/time を返す."""
    _log("parse_numeric_panel start")
    if engine is None:
        engine = RapidOCR()
    h, w = frame.shape[:2]
    x1 = int(w * 0.62)
    y1 = 0
    x2 = w
    y2 = int(h * 0.74)
    panel = frame[y1:y2, x1:x2]
    if panel.size == 0:
        return {}
    raw = run_ocr(engine, panel)
    merged = _merge_digit_tokens(raw)
    hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
    candidates: dict[str, list[tuple[float, Any, str]]] = defaultdict(list)
    for it in merged:
        color = _box_color(hsv, it["box"])
        if color is None:
            continue
        text = it["text"].strip().replace(" ", "")
        area = _box_area(it["box"])
        if color == "white" and parse_time(text):
            candidates["time"].append((area, parse_time(text), text))
        elif color == "green":
            ival = _try_int(text)
            if ival is not None and 20 <= ival <= 250:
                candidates["hr"].append((area, ival, text))
            fval = _try_float(text)
            if fval is not None and abs(fval) < 10:
                candidates["st"].append((area, fval, text))
        elif color == "yellow":
            ival = _try_int(text)
            if ival is not None and 0 <= ival <= 100:
                candidates["spo2"].append((area, ival, text))
        elif color in ("blue", "cyan"):
            ival = _try_int(text)
            if ival is not None and 0 <= ival <= 100:
                candidates["bis"].append((area, ival, text))
    out: dict[str, Any] = {}
    for key in ("time", "hr", "st", "spo2", "bis"):
        if candidates.get(key):
            candidates[key].sort(key=lambda t: t[0], reverse=True)
            _, value, text = candidates[key][0]
            out[key] = {"value": value, "text": text, "raw": []}
    _log(f"parse_numeric_panel end: {list(out.keys())}")
    return out


def nibp_progress(crop: np.ndarray) -> dict[str, Any]:
    """NIBP ROI 下部の緑プログレスバー塗り率を返す（0.0〜1.0）."""
    h, w = crop.shape[:2]
    if h < 20 or w < 50:
        return {"bar_present": False, "progress": 0.0}
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (35, 80, 80), (85, 255, 255))
    bottom = green[int(h * 0.7) :, :]
    proj = np.sum(bottom > 0, axis=0).astype(np.float32)
    if proj.max() == 0:
        return {"bar_present": False, "progress": 0.0}
    # 軽く平滑化
    if len(proj) >= 5:
        proj = np.convolve(proj, np.ones(3) / 3, mode="same")
    # 緑の水平セグメントを抽出し最長を選択
    segments: list[tuple[int, int]] = []
    in_seg = False
    start = 0
    for x, p in enumerate(proj):
        if p > 0 and not in_seg:
            start = x
            in_seg = True
        if p == 0 and in_seg:
            segments.append((start, x - 1))
            in_seg = False
    if in_seg:
        segments.append((start, len(proj) - 1))
    if not segments:
        return {"bar_present": False, "progress": 0.0}
    start, end = max(segments, key=lambda s: s[1] - s[0])
    region = bottom[:, start : end + 1]
    ys, _ = np.where(region > 0)
    if len(ys) == 0:
        return {"bar_present": False, "progress": 0.0}
    bar_h = int(ys.max() - ys.min() + 1)
    bar_w = end - start + 1
    green_area = int(len(ys))
    bar_area = bar_w * bar_h
    progress = green_area / bar_area if bar_area > 0 else 0.0
    return {
        "bar_present": True,
        "progress": round(progress, 3),
        "bar_width": bar_w,
        "bar_height": bar_h,
    }


def extract_pleth_signal(crop: np.ndarray) -> np.ndarray | None:
    """SpO2/Pleth 波形を ROI から 1D 信号として抽出する.

    B650 の Pleth 波形は黄色（hue 10〜45）で、ECG 緑波形やトレンド表の
    白文字と分離できるため、まず黄色マスクを試す. Pleth が見つからなければ
    トレンド表示等で隠れているとみなし None を返す.
    """
    h, w = crop.shape[:2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # Pleth: yellow hue with decent saturation and value
    mask = cv2.inRange(hsv, (10, 40, 100), (45, 255, 255))
    if cv2.countNonZero(mask) < 100:
        return None
    # ECG の残りや上部ラベルを避けるため、下半分〜 70% を対象にする
    y_offset = int(h * 0.30)
    roi = mask[y_offset:, :]
    row_counts = np.count_nonzero(roi, axis=1)
    if len(row_counts) >= 5:
        smoothed = np.convolve(row_counts, np.ones(5) / 5, mode="same")
    else:
        smoothed = row_counts
    y_center = int(np.argmax(smoothed))
    max_count = smoothed[y_center]
    if max_count < 10:
        return None
    # 波形帯を切り出し
    band_h = max(20, min(40, h // 3))
    y0 = max(0, y_center - band_h // 2)
    y1 = min(roi.shape[0], y_center + band_h // 2)
    band = roi[y0:y1, :]
    signal: list[float] = []
    for x in range(band.shape[1]):
        ys = np.where(band[:, x] > 0)[0]
        if len(ys):
            signal.append(float(np.median(ys)) + y_offset + y0)
        else:
            signal.append(float("nan"))
    arr = np.array(signal, dtype=float)
    valid = np.isfinite(arr)
    if int(valid.sum()) < 20:
        return None
    # 画像座標は下に向かって大きいので上下反転して振幅を正にする
    arr = (h - 1) - arr
    x_all = np.arange(len(arr))
    arr = np.interp(x_all, x_all[valid], arr[valid])
    return arr


def compute_pi(signal: np.ndarray, crop_height: int | None = None) -> dict[str, Any] | None:
    """Pleth 信号から Perfusion Index のプロキシを計算.

    モニタ表示上の y 座標を PPG 振幅の代理として扱うため、絶対値は
    キャリブレーションが必要。トレンド比較・相対的な灌流評価に用いる.
    """
    if signal is None or len(signal) < 20:
        return None
    window = max(5, int(len(signal) * 0.05))
    if window % 2 == 0:
        window += 1
    half = window // 2
    padded = np.pad(signal, (half, half), mode="edge")
    baseline = np.convolve(padded, np.ones(window) / window, mode="valid")
    baseline = baseline[: len(signal)]
    if len(baseline) != len(signal):
        baseline = np.convolve(signal, np.ones(window) / window, mode="same")
    ac = signal - baseline
    ac_pp = float(np.max(ac) - np.min(ac))
    ac_rms = float(np.sqrt(np.mean(ac ** 2)))
    dc = float(np.mean(baseline))
    if dc <= 0:
        return None
    # トレンド画面等で Pleth 波形が隠れ、テキスト領域が検出された場合を棄却
    if crop_height:
        dc_rel = dc / crop_height
        if dc_rel < 0.15 or dc_rel > 0.85:
            return None
    # Perfusion Index プロキシ: RMS 振幅 / DC * 100
    pi = (ac_rms / dc) * 100.0
    return {
        "pi": round(pi, 2),
        "ac": round(ac_pp, 2),
        "ac_rms": round(ac_rms, 2),
        "dc": round(dc, 2),
        "samples": len(signal),
    }


class PlethBuffer:
    """連続フレームの Pleth AC 振幅を保持し PVI（Pleth Variability Index）を計算."""

    def __init__(self, window_sec: float = 30.0):
        self.window_sec = window_sec
        self.samples: list[tuple[float, float]] = []

    def update(self, timestamp: float, ac_pp: float):
        self.samples.append((timestamp, ac_pp))
        cutoff = timestamp - self.window_sec
        self.samples = [(t, v) for t, v in self.samples if t >= cutoff]

    def pvi(self) -> float | None:
        if len(self.samples) < 3:
            return None
        values = [v for _, v in self.samples]
        vmax = max(values)
        if vmax <= 0:
            return None
        vmin = min(values)
        return round((vmax - vmin) / vmax * 100, 2)

    def latest(self) -> tuple[float, float] | None:
        return self.samples[-1] if self.samples else None


def read_vitals(
    frame: np.ndarray,
    config: dict[str, Any],
    engine: RapidOCR | None = None,
    pleth_buffer: PlethBuffer | None = None,
    timestamp: float | None = None,
) -> dict[str, Any]:
    _log("read_vitals start")
    if engine is None:
        engine = RapidOCR()
    out: dict[str, Any] = {}
    h, w = frame.shape[:2]
    frame_hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # 右側パネルを色で読み取る（場所ではなく色で HR/ST/SpO2/BIS/time を判別）
    panel_values = parse_numeric_panel(frame, engine=engine)
    out.update(panel_values)

    for roi in config["rois"]:
        name = roi["name"]
        parameter = roi.get("parameter", name)

        # 色パネルで取得成功した数値項目は個別 ROI 処理をスキップ
        if name in panel_values and panel_values[name].get("value") is not None:
            continue

        crop = crop_roi(frame, roi)
        if crop.size == 0 or crop.max() < 30:
            out[name] = {"value": None, "text": "", "raw": []}
            continue

        # イベントROI（波形保存等）は通常の数値OCRをスキップ
        frequency = roi.get("frequency", "continuous")
        if frequency == "event" and parameter not in ("pleth",):
            out[name] = {"value": None, "text": "", "raw": []}
            continue

        now = timestamp if timestamp is not None else time.time()
        interval = roi.get("interval_sec", 30.0 if name == "nibp" else 0.0)
        should_run = (
            timestamp is None
            or frequency == "continuous"
            or interval <= 0.0
            or (now - _LAST_RUN.get(name, 0.0)) >= interval
        )

        if parameter == "pleth":
            _log(f"extracting pleth {name}")
            signal = extract_pleth_signal(crop)
            value = compute_pi(signal, crop_height=crop.shape[0])
            if value and pleth_buffer is not None and timestamp is not None:
                pleth_buffer.update(timestamp, value["ac"])
                value["pvi"] = pleth_buffer.pvi()
                value["buffer_age"] = (
                    timestamp - pleth_buffer.samples[0][0] if pleth_buffer.samples else 0.0
                )
            _LAST_RUN[name] = now
            out[name] = {"value": value, "text": "", "raw": []}
            continue

        # NIBP は一定間隔で OCR し、それ以外はカフプログレスだけ高速判定
        if name == "nibp" or parameter == "nibp":
            progress = nibp_progress(crop)
            if should_run:
                _log("running NIBP OCR")
                x1 = int(roi["x"] * w)
                y1 = int(roi["y"] * h)
                raw = run_ocr(engine, crop)
                full_text = " ".join(r["text"] for r in raw)
                red_raw = []
                for r in raw:
                    box = [[p[0] + x1, p[1] + y1] for p in r["box"]]
                    if _box_color(frame_hsv, box) == "red":
                        red_raw.append(r)
                if len(red_raw) < 2:
                    red_raw = raw
                red_text = " ".join(r["text"] for r in red_raw)
                value = parse_nibp(red_text)
                if value is None:
                    value = {}
                if not value.get("measuring"):
                    cm = CUFF_RE.search(full_text)
                    if cm:
                        value["measuring"] = True
                        value["cuff_pressure"] = int(cm.group(1))
                _LAST_RUN[name] = now
            else:
                value = {"text": ""}
                red_text = ""
                red_raw = []
            value.update(progress)
            if not value:
                value = None
            out[name] = {"value": value, "text": red_text, "raw": red_raw}
            continue

        # その他のROIは毎フレームOCR
        raw = run_ocr(engine, crop)
        text = " ".join(r["text"] for r in raw)
        value = parse_value(name, text, raw=raw)
        _LAST_RUN[name] = now
        out[name] = {"value": value, "text": text, "raw": raw}
    _log("read_vitals end")
    return out


def draw_rois(frame: np.ndarray, config: dict[str, Any]) -> np.ndarray:
    h, w = frame.shape[:2]
    out = frame.copy()
    for roi in config.get("rois", []):
        x1 = int(roi["x"] * w)
        y1 = int(roi["y"] * h)
        x2 = int((roi["x"] + roi["w"]) * w)
        y2 = int((roi["y"] + roi["h"]) * h)
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 255), 2)
        cv2.putText(
            out,
            roi["name"],
            (x1 + 2, y1 + 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2,
        )
    return out


def _monitor_time_to_datetime(value: Any) -> datetime:
    """OCR で読んだ 'HH:MM' 形式のモニタ時刻を本日の datetime に変換."""
    if isinstance(value, str):
        m = re.match(r"(\d{1,2}):(\d{2})", value)
        if m:
            return datetime.combine(
                date.today(), dt_time(int(m.group(1)), int(m.group(2)))
            )
    return datetime.now()


def _to_vitals_table(
    snapshot: dict[str, Any], now: datetime, max_points: int | None = None
) -> VitalsTable:
    """read_vitals() の出力を paperChart 用 VitalsTable に変換."""
    parameters: dict[str, VitalSeries] = {}
    waveforms: dict[str, Any] = {}

    def _append(name: str, val: Any):
        if name not in parameters:
            parameters[name] = VitalSeries(name=name)
        series = parameters[name]
        series.times.append(now)
        series.values.append(val)
        if max_points and len(series.times) > max_points:
            series.times = series.times[-max_points:]
            series.values = series.values[-max_points:]

    for name, info in snapshot.items():
        value = info.get("value")
        if name == "hr" and value is not None:
            _append("HR", float(value))
        elif name == "spo2" and value is not None:
            _append("SpO2", float(value))
        elif name == "st" and value is not None:
            _append("ST", float(value))
        elif name == "bis":
            _append("BIS", float(value) if value is not None else None)
        elif name in ("nibp",) and isinstance(value, dict):
            if value.get("measuring"):
                # カフ充填中は古い値なので記録しない
                _append("SBP", None)
                _append("DBP", None)
                _append("MAP", None)
            else:
                _append("SBP", float(v) if (v := value.get("sys")) is not None else None)
                _append("DBP", float(v) if (v := value.get("dia")) is not None else None)
                map_val = value.get("map")
                if map_val is None and value.get("sys") is not None and value.get("dia") is not None:
                    map_val = (value["sys"] + 2 * value["dia"]) / 3.0
                _append("MAP", float(map_val) if map_val is not None else None)
        elif name in ("spo2_waveform", "pleth") and isinstance(value, dict):
            _append("PI", float(v) if (v := value.get("pi")) is not None else None)
            _append("PVI", float(v) if (v := value.get("pvi")) is not None else None)

    return VitalsTable(parameters=parameters, time_column="time", waveforms=waveforms)


class MonitorVideo:
    """paperChart から利用できる UVC ビデオキャプチャ + OCR モジュール.

    使い方:
        from anesthesia_record.monitor_video import MonitorVideo

        def on_vitals(table: VitalsTable) -> None:
            print(table.parameters["HR"].values[-1])

        monitor = MonitorVideo(
            config_path="paperchart/b650_video.yaml",
            callback=on_vitals,
            interval_sec=1.0,
        )
        monitor.start()
        ...
        monitor.stop()
    """

    def __init__(
        self,
        config_path: Path | str = DEFAULT_CONFIG,
        callback: Optional[Callable[[VitalsTable], None]] = None,
        interval_sec: float = 1.0,
        device_index: Optional[int] = None,
        pvi_window_sec: float = 30.0,
        max_points: int = 10000,
        verbose: bool = False,
    ) -> None:
        self.config = load_config(config_path)
        if device_index is not None:
            self.config["source"]["device_index"] = device_index
        self.callback = callback
        self.interval_sec = interval_sec
        self.max_points = max_points
        self.verbose = verbose
        self.engine = RapidOCR()
        self.pleth_buffer = PlethBuffer(window_sec=pvi_window_sec)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._cap: Optional[cv2.VideoCapture] = None

    def start(self) -> None:
        if self._running:
            return
        self._cap = open_capture(self.config, verbose=self.verbose)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._cap:
            self._cap.release()
            self._cap = None

    def _loop(self) -> None:
        while self._running:
            if self._cap is None:
                break
            ret, frame = self._cap.read()
            if not ret:
                continue
            now = time.time()
            snapshot = read_vitals(
                frame,
                self.config,
                self.engine,
                pleth_buffer=self.pleth_buffer,
                timestamp=now,
            )
            monitor_time = snapshot.get("time", {}).get("value")
            dt = _monitor_time_to_datetime(monitor_time)
            table = _to_vitals_table(snapshot, dt, max_points=self.max_points)
            if self.callback:
                self.callback(table)
            time.sleep(self.interval_sec)


def _serialize_values(vitals: dict[str, Any]) -> dict[str, Any]:
    """JSON 出力用にシンプルな値だけを抽出."""
    return {name: info["value"] for name, info in vitals.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--image", type=Path, help="静的画像でテスト")
    parser.add_argument("--device", type=int, help="UVC デバイス番号")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--overlay", type=Path, help="ROI オーバーレイ画像を保存")
    parser.add_argument(
        "--pvi-window", type=float, default=30.0, help="PVI 計算窓（秒）"
    )
    parser.add_argument(
        "--json", action="store_true", help="JSON 行で標準出力 (paperChart 等との連携用)"
    )
    parser.add_argument(
        "--once", action="store_true", help="1 フレームだけ読み取って終了"
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.device is not None:
        config["source"]["device_index"] = args.device

    engine = RapidOCR()
    pleth_buffer = PlethBuffer(window_sec=args.pvi_window)

    if args.image:
        frame = cv2.imread(str(args.image))
        if frame is None:
            print(f"画像が読めません: {args.image}", file=sys.stderr)
            return 1
        h, w = frame.shape[:2]
        print(f"画像サイズ: {w}x{h}", file=sys.stderr)
        vitals = read_vitals(frame, config, engine, pleth_buffer=pleth_buffer)
        if args.json:
            print(json.dumps(_serialize_values(vitals), ensure_ascii=False))
        else:
            for name, info in vitals.items():
                print(f"{name}: {info['value']!r}  (raw: {info['text']})")
        if args.overlay:
            overlay = draw_rois(frame, config)
            cv2.imwrite(str(args.overlay), overlay)
            print(f"overlay saved: {args.overlay}")
        return 0

    _log("warming up OCR engine before capture loop")
    try:
        # 初回モデル読み込みをループ外で済ませ、以降のフレーム処理を速くする
        engine(np.zeros((100, 100, 3), dtype=np.uint8))
    except Exception as e:
        _log(f"OCR warm-up skipped: {e}")
    _log("OCR warm-up done")

    cap: cv2.VideoCapture | None = None
    while cap is None:
        try:
            cap = open_capture(config)
        except Exception as e:
            _log(f"open_capture failed: {e}; retry in 2s")
            time.sleep(2.0)

    fail_count = 0
    try:
        while True:
            _log("reading frame")
            ret, frame = cap.read()
            if not ret or frame is None:
                fail_count += 1
                _log(f"Frame read failed ({fail_count}), retrying...")
                time.sleep(0.5)
                if fail_count >= 30:
                    # 長時間フレームが来ない場合のみ再接続
                    _log("reconnecting camera")
                    cap.release()
                    time.sleep(2.0)
                    try:
                        cap = open_capture(config, verbose=False)
                    except Exception as e:
                        _log(f"reconnect failed: {e}; keep retrying")
                    fail_count = 0
                continue
            fail_count = 0
            _log("frame read ok")
            try:
                vitals = read_vitals(
                    frame, config, engine, pleth_buffer=pleth_buffer, timestamp=time.time()
                )
            except Exception as e:
                _log(f"read_vitals error: {e}")
                vitals = {}
            _log("read_vitals done")
            if args.json:
                json_text = json.dumps(_serialize_values(vitals), ensure_ascii=False)
                print(json_text, flush=True)
                _log(f"json sent: {json_text[:120]}")
            else:
                print({k: v["value"] for k, v in vitals.items()})
            if args.once:
                break
            time.sleep(args.interval)
    finally:
        if cap is not None:
            cap.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
