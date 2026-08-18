"""anesthesia-record v0.2: リアルタイム麻酔記録GUIの起動スクリプト.

使い方:
    python run_gui.py              # GUIアプリを起動
    python run_gui.py --headless   # ヘッドレスモード（テスト用）
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="麻酔記録 v0.2 リアルタイムGUI")
    parser.add_argument("--headless", action="store_true", help="ヘッドレスモード（テスト用）")
    args = parser.parse_args()

    if args.headless:
        # テスト用: GUIを表示せずにコアモジュールのインポート検証
        from anesthesia_record.gui.session import AnesthesiaSession
        from anesthesia_record.gui.vitals_monitor import VitalsMonitor
        from anesthesia_record.gui.chart_live import render_live_chart
        print("GUI core modules imported successfully (headless mode)")
        print(f"  AnesthesiaSession: {AnesthesiaSession}")
        print(f"  VitalsMonitor: {VitalsMonitor}")
        print(f"  render_live_chart: {render_live_chart}")
        sys.exit(0)

    from anesthesia_record.gui.app import AnesthesiaApp
    app = AnesthesiaApp()
    app.run()


if __name__ == "__main__":
    main()
