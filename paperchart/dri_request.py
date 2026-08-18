#!/usr/bin/env python3
"""GE CARESCAPE B650 の DRI シリアル出力を開始させて取得するツール.

DRI プロトコルでは、収集側ホストが**送信リクエスト**を送らないとモニタは
生理データ(phdb)をストリームしない。TeraTerm で受け身にポートを開くだけだと
何も来ない（全て 0x00 になる）ため、このスクリプトでリクエストを送ってから
受信・保存する。

依存: pyserial （`pip install pyserial`）

使い方 (Windows 例):
    python paperchart/dri_request.py --port COM5 --seconds 30 --out capture.bin

取得後の確認:
    python paperchart/dri_viewer.py capture.bin --text
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anesthesia_record.dri_decode import (  # noqa: E402
    DRI_PHDBCL_REQ_BASIC,
    build_phdb_request,
    decode_stream,
)
from anesthesia_record.paperchart_config import load_paperchart_config  # noqa: E402

DEFAULT_CONFIG = Path(__file__).resolve().parent / "b650.txt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="シリアルポート (例 COM5, /dev/ttyUSB0)")
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="ボーレート (既定 115200 = CARESCAPE Bx50 DRI v6。旧S/5は19200)",
    )
    parser.add_argument(
        "--parity",
        choices=["none", "even", "odd"],
        default="even",
        help="パリティ (既定 even = Datex S/5 DRI標準, 実機でも確認済み)",
    )
    parser.add_argument(
        "--rtscts",
        action="store_true",
        help="RTS/CTS ハードウェアフロー制御を使う (VSCaptureはRTSハンドシェイク)",
    )
    parser.add_argument("--seconds", type=float, default=30.0, help="受信時間 [秒]")
    parser.add_argument("--interval", type=int, default=5, help="phdb 送信間隔")
    parser.add_argument(
        "--class-bf",
        type=lambda s: int(s, 0),
        default=DRI_PHDBCL_REQ_BASIC,
        help="要求する physdb クラスのビット和 (既定 0x1=basic)",
    )
    parser.add_argument("--out", default="capture.bin", help="保存先バイナリ")
    parser.add_argument(
        "--resend",
        type=float,
        default=10.0,
        help="リクエスト再送間隔 [秒] (0 で単発)",
    )
    args = parser.parse_args(argv)

    try:
        import serial  # type: ignore
    except ImportError:
        parser.error("pyserial が必要です: pip install pyserial")

    request = build_phdb_request(tx_interval=args.interval, class_bf=args.class_bf)

    parity_map = {
        "none": serial.PARITY_NONE,
        "even": serial.PARITY_EVEN,
        "odd": serial.PARITY_ODD,
    }
    ser = serial.Serial(
        port=args.port,
        baudrate=args.baud,
        bytesize=serial.EIGHTBITS,
        parity=parity_map[args.parity],
        stopbits=serial.STOPBITS_ONE,
        rtscts=args.rtscts,
        timeout=0.2,
    )
    # 一部機器は DTR/RTS のアサートで送信可能になる
    ser.setDTR(True)
    ser.setRTS(True)

    print(f"request frame ({len(request)} bytes): {request.hex(' ')}", file=sys.stderr)

    collected = bytearray()
    start = time.monotonic()
    last_req = 0.0
    try:
        ser.write(request)
        ser.flush()
        while time.monotonic() - start < args.seconds:
            if args.resend and (time.monotonic() - last_req) >= args.resend:
                ser.write(request)
                ser.flush()
                last_req = time.monotonic()
            chunk = ser.read(4096)
            if chunk:
                collected += chunk
    finally:
        ser.close()

    Path(args.out).write_bytes(bytes(collected))
    print(f"received {len(collected)} bytes -> {args.out}", file=sys.stderr)

    if collected:
        config = load_paperchart_config(str(DEFAULT_CONFIG))
        frames = decode_stream(bytes(collected), config)
        ok = sum(1 for f in frames if f.checksum_ok)
        print(f"frames={len(frames)}  checksum OK={ok}", file=sys.stderr)
        if ok == 0:
            print(
                "警告: checksum OK が 0 です。ボーレート/パリティ/配線(ヌルモデム)を"
                "見直してください。",
                file=sys.stderr,
            )
    else:
        print(
            "警告: 1 バイトも受信できませんでした。配線(TX/RX/GND, ヌルモデム)と "
            "モニタ側のシリアル出力設定を確認してください。",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
