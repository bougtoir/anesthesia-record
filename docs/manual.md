# anesthesia-record v0.1 操作マニュアル

## 1. 概要

院内様式の麻酔記録チャートを生成する Python ライブラリです。  
GE CARESCAPE B650 等のバイタル CSV と投薬イベントを入力し、印刷用の麻酔記録(PNG)を出力します。

> **注意**: 本ツールは**補助記録・研究用途**です。正式な診療録や投与判断の根拠に用いないでください。  
> 薬価・極量・PKモデル等は各施設で検証してください。

---

## 2. 環境構築

### 必要環境

- Python 3.10 以上
- OS: Windows 11 / macOS / Linux

### インストール

```bash
cd anesthesia-record
pip install -r requirements.txt
```

主な依存ライブラリ: `matplotlib`, `numpy`, `pyyaml`, `scipy`

### 動作確認

```bash
python demo.py
```

成功すると `demo_chart.png` と `demo_chart_erga.png` が生成されます。

---

## 3. 設定ファイル

### 3.1 薬剤マスタ (`data/drug_master.yaml`)

薬剤・輸液の定義ファイルです。プログラムを変更せずに薬剤の追加・編集が可能です。

```yaml
drugs:
  - id: propofol_1pct              # 一意のID（コード内で参照）
    generic_name: プロポフォール     # 表示名
    brand_name: ディプリバン         # 商品名（任意）
    category: iv_anesthetic         # カテゴリ（表示順に影響）
    container_volume_ml: 20         # 1容器の容量(ml)
    concentration: 10               # 濃度 (mg/ml)
    strength_unit: mg/ml
    package_unit: バイアル
    nhi_price: 594                  # 薬価(円/容器) ※要施設検証
    nhi_price_date: 2024-04-01
    billing_rule: round_up_vial     # 課金方式（容器単位切上げ）
    delivery: [bolus, infusion]     # 投与方式
    dose_units: [mg, mg/kg, mg/kg/h, ug/kg/min, ml/h]
    unit_bolus: mg                  # 単回投与の既定単位
    unit_rate: mg/kg/h              # 持続投与の既定単位
    color: "#F4C20D"                # チャート上の線色
    display_order: 10               # 表示順（小→上、未設定時はカテゴリ順）
    pkpd_enabled: true              # Ce推定対象
    pk_model: marsh                 # PKモデル (marsh/schnider/minto/shafer)
```

#### カテゴリ一覧と表示順

| カテゴリ | 説明 | 既定表示位置 |
|---------|------|------------|
| `iv_anesthetic` | 静脈麻酔薬 | 上 |
| `opioid` | オピオイド | 上 |
| `muscle_relaxant` | 筋弛緩薬 | 中 |
| `local_anesthetic` | 局所麻酔薬 | 中 |
| `fluid` | 輸液 | 下（最下部） |

`display_order` を設定すると、カテゴリ順より優先されます。

#### 薬剤の色指定

`color` フィールドに HTML カラーコード（例: `"#F4C20D"`）を指定します。  
未設定の場合は `#222222`（濃灰色）が使用されます。

### 3.2 麻酔料設定 (`data/anesthesia_fee.yaml`)

診療報酬の点数設定ファイルです（2026年6月改訂準拠）。

```yaml
methods:
  - id: general_anesthesia
    name: 全身麻酔
    category: general
    icon_start: "GA▶"
    icon_end: "GA■"
    time_fees:
      - { max_hours: 2, points_per_30min: 900 }    # 最初の2時間
      - { max_hours: null, points_per_30min: 600 }  # 2時間超
    base_points: 0

positions:
  - id: lateral
    name: 側臥位
    icon: "⊏"
    points_per_case: 100

severity:
  - id: critical
    name: 重症加算(特定集中治療室管理)
    multiplier: 1.5    # 全身麻酔料 × 1.5
```

#### 同時算定不可ルール

```yaml
exclusive_categories:
  - [general, mac]     # 全身麻酔とMACは同時不可
  - [neuraxial]        # 脊麻と硬膜外は同時不可
```

同じ排他カテゴリ内で複数の麻酔方法が実施された場合、**点数が最も高いもののみ**算定されます。  
異なるカテゴリ間（例: 全身麻酔 + 神経ブロック）は併算定可能です。

---

## 4. データ入力

### 4.1 患者情報

```python
from anesthesia_record import Patient, Sex

patient = Patient(
    age_years=45,
    sex=Sex.MALE,
    weight_kg=65,
    height_cm=172,
    asa_ps=2,           # ASA-PS (任意)
    patient_id="001",   # 患者ID (任意)
)
```

### 4.2 投薬イベント (MedEvent)

#### 単回投与 (bolus)

```python
from anesthesia_record import MedEvent, Delivery
from datetime import datetime, timedelta

t0 = datetime(2026, 6, 30, 9, 0, 0)

ev = MedEvent(
    drug_id="propofol_1pct",      # drug_master.yaml の id
    start_time=t0,
    delivery=Delivery.BOLUS,
    dose=130,                      # 投与量
    dose_unit="mg",
    note="導入",                   # 備考（任意）
)
```

#### 持続投与 (infusion)

```python
ev = MedEvent(
    drug_id="remifentanil_2mg",
    start_time=t0 + timedelta(minutes=2),
    delivery=Delivery.INFUSION,
    rate=0.2,                                    # 投与速度
    rate_unit="ug/kg/min",
    end_time=t0 + timedelta(minutes=30),         # 終了時刻（必須）
)
```

チャート上では、持続投与の終了地点に `//` マーカーが表示されます。

#### 輸液（残量入力方式）

```python
ev = MedEvent(
    drug_id="acetated_ringer_500",
    start_time=t0,
    delivery=Delivery.INFUSION,
    end_time=t0 + timedelta(minutes=32),
    remaining_ml_start=500,   # 開始時残量 (ml)
    remaining_ml_end=393,     # 終了時残量 (ml)
)
# 消費量 = 500 - 393 = 107 ml（自動計算）
```

> **注意**: 輸液は `rate` ではなく `remaining_ml_start` / `remaining_ml_end` で入力します。

### 4.3 臨床イベント (EventLog)

```python
from anesthesia_record import EventLog

clinical_log = EventLog()
clinical_log.add(t0, "anesthesia_start")                         # 麻酔開始 ▲
clinical_log.add(t0 + timedelta(minutes=3), "intubation")        # 挿管 ▲
clinical_log.add(t0 + timedelta(minutes=5), "position_lateral")  # 側臥位 ▷
clinical_log.add(t0 + timedelta(minutes=12), "incision")         # 執刀 ◆
clinical_log.add(t0 + timedelta(minutes=25), "position_supine")  # 仰臥位 ○
clinical_log.add(t0 + timedelta(minutes=28), "surgery_end")      # 手術終了 ◇
clinical_log.add(t0 + timedelta(minutes=32), "anesthesia_end")   # 麻酔終了 ▼
```

#### 標準イベント種別

| type | 表示 | アイコン |
|------|------|---------|
| `anesthesia_start` | 麻酔開始 | ▲ |
| `anesthesia_end` | 麻酔終了 | ▼ |
| `intubation` | 挿管 | ▲ |
| `extubation` | 抜管 | ▼ |
| `incision` | 執刀(切皮) | ◆ |
| `surgery_end` | 手術終了 | ◇ |
| `position_supine` | 仰臥位 | ○ |
| `position_lateral` | 側臥位 | ▷ |
| `position_prone` | 腹臥位 | ▽ |
| `position_lithotomy` | 砕石位 | △ |
| `position_sitting` | 坐位 | □ |
| `severity_start` | 重症加算開始 | ★ |

アイコンは `event_icon_map` パラメータで上書き可能です。

#### YAML からの読み込み

```python
from anesthesia_record import load_event_log

log = load_event_log("events.yaml")
```

```yaml
# events.yaml
events:
  - time: "2026-06-30T09:00:00"
    type: anesthesia_start
  - time: "2026-06-30T09:03:00"
    type: intubation
```

### 4.4 出血・尿量 (OutputEvent)

差分値を入力し、チャート上でボーリングスコア形式（差分 + 積算）で表示されます。

```python
from anesthesia_record import OutputEvent, OutputCategory

output_evts = [
    OutputEvent(OutputCategory.GAUZE, t0 + timedelta(minutes=14), 30),    # ガーゼ 30g
    OutputEvent(OutputCategory.SUCTION, t0 + timedelta(minutes=16), 20),  # 吸引 20cc
    OutputEvent(OutputCategory.URINE, t0 + timedelta(minutes=20), 50),    # 尿量 50cc
]
```

| カテゴリ | 単位 | 表示 |
|---------|------|------|
| `GAUZE` | g | 出血(ガーゼ g) |
| `SUCTION` | cc | 出血(吸引 cc) |
| `URINE` | cc | 尿量(cc) |

各カテゴリの総量がチャート右端に表示されます。

### 4.5 バイタルサイン (VitalsTable)

```python
from anesthesia_record import VitalsTable, VitalSeries, load_vitals_csv

# CSV から読み込み
vitals = load_vitals_csv("vitals.csv")

# または手動作成
hr = VitalSeries("HR")
hr.times.append(t0)
hr.values.append(72)

vitals = VitalsTable(
    parameters={"HR": hr, "SBP": sbp, "DBP": dbp, "SpO2": spo2, "Temp": temp},
    time_column="Time",
)
```

### 4.6 ECG 波形 (Waveform)

```python
from anesthesia_record import Waveform

ecg = Waveform(
    name="ECG",
    sample_rate_hz=300.0,
    start_time=t0,
    values=[0.1, 0.2, ...],  # サンプル値のリスト
)
```

---

## 5. チャート出力

### 5.1 院内様式チャート (render_chart_erga)

```python
from anesthesia_record.chart_erga import render_chart_erga

out_path = render_chart_erga(
    vitals,                          # バイタルサイン (必須)
    events,                          # 投薬イベント (必須)
    master,                          # 薬剤マスタ (必須)
    "output.png",                    # 出力パス (必須)
    patient=patient,                 # 患者情報
    clinical_events=log.sorted(),    # 臨床イベント
    cost_report=cost_report,         # コスト算定結果
    ce_results=ce_results,           # Ce推定結果
    ce_t0=t0,                        # Ce推定基準時刻
    show_floating_latest=True,       # 最新バイタルパネル表示
    latest_panel_loc="upper right",  # パネル位置
    ecg_waveform=ecg,                # ECG波形
    ecg_snapshot_times=[...],        # ECGスナップショット時刻
    output_events=output_evts,       # 出血・尿量
    postop_orders=postop,            # 術後指示
    anesthesia_fee_result=fee_result,# 麻酔料算定結果
    title="麻酔記録(院内様式)",
)
```

#### チャート構成

| セクション | 内容 |
|-----------|------|
| ヘッダー | 患者情報（年齢、性別、体重、身長、ASA-PS）、日時 |
| バイタルサイン | HR/BP(左軸)、SpO2/Temp(右軸)、15分細線+毎正時太線グリッド |
| 臨床イベント | 体位変換等のアイコン表示（バイタル下部） |
| 薬剤レーン | 薬品名(上)、投与量(下)、持続線(色付き)、右端に積算量(繰上げ単位量) |
| 輸液レーン | 開始残量→終了残量、終了に // マーカー |
| 出血・尿量 | ボーリングスコア形式（差分+積算）、右端に総量 |
| Ce推定曲線 | プロポフォール/レミフェンタニル/フェンタニルの効果部位濃度 |
| 4ペイン情報 | コスト(薬剤+麻酔料) &#124; イベント &#124; 時間 &#124; 術後指示 |
| ECG短冊 | 指定時刻のECG波形スナップショット（HH:MM ラベル） |
| フローティングパネル | 最新バイタル値（ドラッグ可能） |

---

## 6. コスト算定

### 6.1 薬剤コスト

```python
from anesthesia_record.cost import compute_cost

report = compute_cost(events, master, patient)
for item in report.items:
    print(f"{item.generic_name}: {item.cost:.0f}円")
print(f"合計: {report.total:.0f}円")
```

課金方式:
- `round_up_vial`: 容器単位切上げ（残液破棄分も課金）
- `per_ml`: ml 単位
- `exact`: 正確な使用量

### 6.2 麻酔料算定

```python
from anesthesia_record.anesthesia_fee import (
    AnesthesiaEvent, PositionEvent, compute_anesthesia_fee, load_anesthesia_fee,
)

config = load_anesthesia_fee("data/anesthesia_fee.yaml")

anes_events = [
    AnesthesiaEvent("general_anesthesia", t0, t0 + timedelta(minutes=120)),
]
pos_events = [
    PositionEvent("lateral", t0 + timedelta(minutes=10), t0 + timedelta(minutes=50)),
]
severity_ids = ["critical"]  # 重症加算の ID リスト

result = compute_anesthesia_fee(anes_events, pos_events, severity_ids, config)
for item in result.items:
    print(f"{item.name}: {item.points}点 ({item.detail})")
print(f"合計: {result.total_points}点")
```

#### 算定ロジック

1. **時間点数**: 麻酔時間を30分単位で切上げ、時間区分に応じた点数を加算
2. **同時算定不可**: 同一排他カテゴリ内では最高点数の方法のみ算定
3. **特殊体位加算**: 体位変換〜仰臥位復帰の時間に基づき加算
4. **重症加算**: 全身麻酔料に係数を乗算（例: 1.5倍の差分を加算）

### 6.3 局所麻酔薬 極量チェック

```python
from anesthesia_record.local_anesthetic import assess_local_anesthetics

for st in assess_local_anesthetics(events, master, patient):
    print(f"{st.generic_name}: {st.cumulative_mg}mg / 上限{st.max_mg}mg ({st.level})")
```

---

## 7. Ce (効果部位濃度) 推定

```python
from anesthesia_record import pkpd

result = pkpd.simulate(drug, patient, events, t0, duration_min=90, dt_s=1.0)
print(f"Ce_max={result.ce_max:.2f} {result.conc_unit}")
```

対応モデル:

| 薬剤 | モデル | 濃度単位 |
|------|--------|---------|
| プロポフォール | marsh, schnider | ug/ml |
| レミフェンタニル | minto | ng/ml |
| フェンタニル | shafer | ng/ml |

---

## 8. Windows 11 での実行ファイル作成

### 手順

1. Python 3.10+ を [python.org](https://www.python.org/downloads/) からインストール
2. コマンドプロンプトを開き、anesthesia-record フォルダに移動
3. 以下を実行:

```bat
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --onefile --add-data "data;data" --name anesthesia_demo demo.py
```

4. `dist\anesthesia_demo.exe` が生成されます
5. `dist` フォルダに移動して実行:

```bat
cd dist
anesthesia_demo.exe
```

`demo_chart.png` と `demo_chart_erga.png` が生成されます。

> **注意**: Windows では `--add-data` のセパレータが `;`（セミコロン）です。

### ビルドスクリプト (build_win.bat)

リポジトリに含まれる `build_win.bat` を実行するとワンクリックでビルドできます。

---

## 9. ファイル構成

```
anesthesia-record/
├── anesthesia_record/          # コアライブラリ
│   ├── __init__.py
│   ├── anesthesia_fee.py       # 麻酔料算定
│   ├── chart.py                # 基本チャート
│   ├── chart_erga.py           # 院内様式チャート
│   ├── cost.py                 # 薬剤コスト算定
│   ├── drug_master.py          # 薬剤マスタローダ
│   ├── events.py               # 臨床イベント管理
│   ├── local_anesthetic.py     # 局所麻酔薬極量
│   ├── models.py               # データモデル
│   ├── pkpd.py                 # Ce推定エンジン
│   ├── units.py                # 単位換算
│   └── vitals.py               # バイタルデータ
├── data/
│   ├── drug_master.yaml        # 薬剤マスタ（編集可）
│   └── anesthesia_fee.yaml     # 麻酔料設定（編集可）
├── tests/                      # テストスイート
├── demo.py                     # デモスクリプト
├── build_win.bat               # Windows用ビルドスクリプト
└── requirements.txt
```

---

## 10. よくある質問

**Q: 薬剤を追加するには？**  
A: `data/drug_master.yaml` にエントリを追加してください。プログラムの変更は不要です。

**Q: 麻酔料の点数を変更するには？**  
A: `data/anesthesia_fee.yaml` を編集してください。`time_fees` の `points_per_30min` や `positions` の `points_per_case` を変更できます。

**Q: イベントアイコンを変更するには？**  
A: `render_chart_erga()` の `event_icon_map` パラメータで辞書を渡してください。  
例: `event_icon_map={"anesthesia_start": "★", "intubation": "◎"}`

**Q: 薬剤の線色を変更するには？**  
A: `data/drug_master.yaml` の該当薬剤の `color` フィールドを変更してください。

**Q: フォント警告 "Failed to find font weight bold" が出る**  
A: IPAGothic フォントの制限です。表示に影響はありません。

**Q: 右軸のスケールが重なる**  
A: SpO2 と Temp の目盛りは自動的にオフセットされます。軸数が増えた場合は `axis_specs` で範囲を調整してください。
