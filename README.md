# anesthesia-record

麻酔記録（**補助記録**）のためのコア・ライブラリ。GE CARESCAPE B650 等から取得した
バイタル（VitalRecorder / VSCapture の CSV・`.vital`）と、投薬イベント・コスト算定・
効果部位濃度(Ce)推定・局所麻酔薬の極量管理を扱う。

> ⚠️ 本ツールは**補助記録・研究用途**であり、正式な診療録や投与判断の根拠に用いてはならない。
> 薬価・極量・PKモデル等は**各施設で検証**すること（同梱マスタの薬価はサンプル値）。

## v0.2: リアルタイムGUI

**v0.2** ではリアルタイムGUIアプリを追加。術中にバイタルを自動取得しながら、
薬剤投与・臨床イベント・出血/尿量をその場で入力し、チャートがライブ更新される。

### 起動方法
```bash
python run_gui.py              # リアルタイムGUIを起動
python run_gui.py --headless   # ヘッドレスモード（インポートテスト用）
```

### v0.2 の機能
- **リアルタイムチャート表示**: tkinter + matplotlib embed、5秒ごと自動更新
- **バイタル自動取得**: CSVファイル監視 or VitalRecorder TCP接続
- **薬剤投与入力**: ボーラス/持続、輸液残量記録、持続終了
- **臨床イベント記録**: 挿管/抜管、体位変換、A-line、神経ブロック等
- **出血・尿量入力**: ガーゼ(g)/吸引(cc)/尿量(cc)、ボーリングスコア形式表示
- **症例YAML保存/読込**: セッションデータの永続化
- **チャート画像出力**: PNG/PDF/SVG形式でエクスポート
- **Windows exe化対応**: `setup.bat` でPyInstallerビルド

## 特徴 (v0.1 バッチモード)
- **薬剤・輸液マスタは外部参照ファイル**（`data/drug_master.yaml`、起動時ホットリロード）。
- **投薬イベント**: 時刻指定、単回(bolus)／持続(infusion)、体重ベース投与の自動換算。
- **コスト算定**: 剤型(容器)考慮。`billing_rule`（バイアル単位切上げ＝残液破棄課金／按分）切替。
- **効果部位濃度(Ce)推定**: 3-コンパートメント+ke0。propofol(Marsh/Schnider)、
  remifentanil(Minto)、fentanyl(Shafer・近似)。
- **局所麻酔薬の極量管理**: mg/kg 累積に対する警告（アドレナリン添加で上限切替）。
- **チャート出力**: バイタル + 投薬注記 + Ce を1枚に描画（様式テンプレート差し替え可能）。

## セットアップ
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
Windows: `setup.bat` をダブルクリック。

## デモ
```bash
python demo.py                 # v0.1 バッチデモ
python generate_demo_v02.py    # v0.2 チャートデモ出力
```

## テスト
```bash
pytest -q
```

## 構成
```
data/drug_master.yaml          薬剤・輸液マスタ（外部参照ファイル）
data/anesthesia_fee.yaml       麻酔料設定（診療報酬）
anesthesia_record/
  models.py                    Patient / DrugMaster / MedEvent
  drug_master.py               マスタYAMLローダ（ホットリロード）
  units.py                     投与量の単位変換（→ mg / ml）
  cost.py                      コスト算定（剤型考慮）
  pkpd.py                      効果部位濃度(Ce)推定エンジン
  local_anesthetic.py          局所麻酔薬 極量管理
  vitals.py                    バイタル CSV / .vital 取り込み・時刻整合
  events.py                    臨床イベント管理
  anesthesia_fee.py            麻酔料算定
  chart.py                     チャート描画（様式テンプレート）
  chart_erga.py                院内様式チャート(v0.1)
  gui/                         v0.2 リアルタイムGUI
    app.py                     メインGUIアプリ (tkinter)
    session.py                 セッションデータ管理
    vitals_monitor.py          リアルタイムバイタル取得
    chart_live.py              ライブチャート描画
run_gui.py                     GUI起動エントリポイント
tests/                         pytest (42テスト)
demo.py                        v0.1 デモ
generate_demo_v02.py           v0.2 チャートデモ
```

## 薬剤マスタの編集
`data/drug_master.yaml` をプログラム外で編集する。主なフィールドは README とファイル冒頭の
コメントを参照。`pkpd_enabled: true` の薬剤のみ Ce 推定の対象。`max_dose_mg_per_kg` を持つ
局所麻酔薬は極量管理の対象。
