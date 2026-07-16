"""バイタルモニタリング: CSVポーリング / VitalRecorder TCP接続."""

from __future__ import annotations

import csv
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ..vitals import VitalsTable, VitalSeries, load_vitals


class VitalsMonitor:
    """バイタルデータのリアルタイム取得.

    mode:
        - "csv": CSVファイルを定期的に再読み込み（追記監視）
        - "tcp": VitalRecorderのTCPサーバに接続してストリーム受信
    """

    def __init__(
        self,
        mode: str = "csv",
        path: Optional[str] = None,
        host: str = "127.0.0.1",
        port: int = 8887,
        callback: Optional[Callable[[VitalsTable], None]] = None,
        interval_sec: float = 2.0,
    ) -> None:
        self.mode = mode
        self.path = path
        self.host = host
        self.port = port
        self.callback = callback
        self.interval_sec = interval_sec
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_line_count = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self.mode == "csv":
            self._thread = threading.Thread(target=self._csv_loop, daemon=True)
        elif self.mode == "tcp":
            self._thread = threading.Thread(target=self._tcp_loop, daemon=True)
        else:
            raise ValueError(f"Unknown mode: {self.mode}")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _csv_loop(self) -> None:
        """CSVファイルを定期ポーリングして変更を検出."""
        while self._running:
            try:
                if self.path and Path(self.path).exists():
                    vitals = load_vitals(self.path)
                    if self.callback:
                        self.callback(vitals)
            except Exception:
                pass  # ファイルロック等のエラーは無視して次回リトライ
            time.sleep(self.interval_sec)

    def _tcp_loop(self) -> None:
        """VitalRecorder TCP接続でリアルタイムデータ受信.

        VitalRecorderのTCPサーバはCSV形式で1行ずつデータを送信する。
        フォーマット: timestamp,HR,SpO2,SBP,DBP,EtCO2,BIS,Temp,...
        """
        buffer = ""
        series_data: dict[str, list[tuple[datetime, float]]] = {}

        while self._running:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((self.host, self.port))
                sock.settimeout(2.0)

                while self._running:
                    try:
                        data = sock.recv(4096).decode("utf-8", errors="replace")
                        if not data:
                            break
                        buffer += data
                        lines = buffer.split("\n")
                        buffer = lines[-1]  # 不完全な行は次回に

                        for line in lines[:-1]:
                            line = line.strip()
                            if not line:
                                continue
                            self._parse_tcp_line(line, series_data)

                        # コールバックでVitalsTable更新
                        if series_data and self.callback:
                            vitals = self._build_vitals_table(series_data)
                            self.callback(vitals)
                    except socket.timeout:
                        continue

            except (ConnectionRefusedError, OSError):
                pass
            finally:
                try:
                    sock.close()
                except Exception:
                    pass

            # 再接続待ち
            if self._running:
                time.sleep(self.interval_sec)

    def _parse_tcp_line(self, line: str, series_data: dict[str, list[tuple[datetime, float]]]) -> None:
        """VitalRecorder TCP行をパース.

        想定フォーマット(ヘッダ行がない場合):
            2024-01-01 10:00:00,72,98,120,70,35,60,36.5
        想定パラメータ順: HR,SpO2,SBP,DBP,EtCO2,BIS,Temp

        またはヘッダ行付きCSV。
        """
        parts = line.split(",")
        if len(parts) < 2:
            return

        # 最初の要素が時刻かどうか
        try:
            ts = self._parse_time(parts[0])
        except ValueError:
            # ヘッダ行 or パース不能
            return

        param_names = ["HR", "SpO2", "SBP", "DBP", "EtCO2", "BIS", "Temp"]
        for i, name in enumerate(param_names):
            idx = i + 1
            if idx >= len(parts):
                break
            try:
                val = float(parts[idx])
                if name not in series_data:
                    series_data[name] = []
                series_data[name].append((ts, val))
            except ValueError:
                continue

    def _parse_time(self, s: str) -> datetime:
        s = s.strip()
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S.%f", "%H:%M:%S"]:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise ValueError(f"Cannot parse time: {s}")

    def _build_vitals_table(self, series_data: dict[str, list[tuple[datetime, float]]]) -> VitalsTable:
        """蓄積データからVitalsTableを構築."""
        params: dict[str, VitalSeries] = {}
        for key, points in series_data.items():
            times = [p[0] for p in points]
            values = [p[1] for p in points]
            params[key] = VitalSeries(name=key, times=times, values=values)
        return VitalsTable(parameters=params, time_column="time")
