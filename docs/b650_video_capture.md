# B650 外部モニタ UVC ライブキャプチャ手引き

GE CARESCAPE B650 の DVI/HDMI 外部モニタ出力を HDMI キャプチャボードで取り込み、OpenCV + RapidOCR で数値を読み取る手順です。

## 必要なもの

- B650 の外部モニタ出力が有効になった DVI/HDMI 信号
- UVC 対応 HDMI キャプチャボード（例: UGREEN、Elgato Cam Link 等）
- Windows 11 + Python 3.10 以上
- 本リポジトリの `anesthesia-record` 環境

## 接続

```
B650 DVI-I/HDMI 出力 → HDMI ケーブル → キャプチャボード → PC USB
```

B650 の **Display / 外部モニタ** 設定で、ミラー/クローン出力が有効か確認してください。

## インストール

```powershell
cd anesthesia-record
pip install -r requirements.txt
```

主な追加依存: `opencv-python`, `rapidocr-onnxruntime`。

## デバイス番号の確認

PC に複数カメラがある場合は、まずこのスクリプトで B650 が割り当てられた番号を特定します。

```powershell
python - <<'PY'
import cv2
for i in range(10):
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if cap.isOpened():
        for _ in range(5):
            cap.read()
        ret, frame = cap.read()
        if ret and frame is not None and frame.sum() > 0:
            h, w = frame.shape[:2]
            cv2.imwrite(f"test_index_{i}_{w}x{h}.png", frame)
            print(f"index {i}: saved test_index_{i}_{w}x{h}.png")
    cap.release()
PY
```

B650 画面が写っている `test_index_X.png` の番号を使います。

## ライブキャプチャの実行

```powershell
python -m anesthesia_record.monitor_video `
  --config paperchart/b650_video.yaml `
  --device <インデックス> `
  --interval 1.0
```

`--device` を省略すると、フレームが取れる最初のカメラを自動探索します。

### 主なオプション

| オプション | 説明 |
|-----------|------|
| `--device N` | UVC キャプチャデバイス番号 |
| `--interval 1.0` | フレーム読み取り間隔（秒） |
| `--pvi-window 30.0` | PVI 計算窓（秒） |
| `--overlay out.png` | 1 枚だけ撮影して ROI 矩形を保存 |

## 出力例

```text
Opened camera 1: 640x480
{'time': '11:34', 'hr': 69, 'st': 0.0, 'spo2': 99, 'bis': None,
 'nibp': {'sys': 107, 'dia': 67, 'map': 83, 'measuring': False,
          'bar_present': True, 'progress': 0.702},
 'ecg_waveform': None,
 'spo2_waveform': {'pi': 13.46, 'pvi': None, 'ac': 21.19,
                   'ac_rms': 4.91, 'dc': 36.44, 'samples': 435}}
```

- `nibp.measuring=True` の間は表示されている SYS/DIA が古い値なので、記録しないでください。
- `nibp.progress` は緑プログレスバーの塗り率（0.0〜1.0）です。
- `bis` はセンサー未接続・レイアウトにない場合は `None` になります。
- `spo2_waveform` はトレンド画面等で Pleth 波形が隠れると `None` になります。

## 1 枚だけ保存して ROI を確認

```powershell
python -m anesthesia_record.monitor_video `
  --config paperchart/b650_video.yaml `
  --device 1 `
  --overlay roi_overlay.png

# スクリプトが 1 枚撮影して終了します
```

## トレンド画面でも動作させる

トレンド表示に切り替えても、右上の数値パネル（HR, ST, SpO2, BIS, NIBP）と
時刻は読み取り続けます。Pleth 波形が隠れている間は `spo2_waveform` が
`None` になります。元の波形表示に戻ると、自動的に PI/PVI が復帰します。

## よくあるトラブル

| 症状 | 対処 |
|------|------|
| 真っ黒 | B650 外部出力が有効か、ケーブル/キャプチャボードの USB 接続を確認 |
| カメラが認識されない | `--device` で番号を明示、別の USB ポートを試す |
| 解像度が違う | `paperchart/b650_video.yaml` の `source.width/height` を変更。座標は正規化済み |
| 数値が読みにくい | `--overlay` で ROI を確認し、必要に応じて config の `rois` を調整 |
| `ImportError: DLL load failed while importing onnxruntime_pybind11_state` | Anaconda 環境では `paperchart/fix_onnxruntime.bat` を実行。または `conda install conda-forge::vs2015_runtime -y -n base`。Microsoft 版 Python の場合は最新 VC++ Redistributable を再インストール |

## paperChart 連携手順

### 1. ライブラリとしてインポートする（Python 製 paperChart の場合）

`anesthesia_record.monitor_video.MonitorVideo` を paperChart プロジェクトに追加します。

```python
from anesthesia_record.monitor_video import MonitorVideo
from anesthesia_record.vitals import VitalsTable

def on_vitals(table: VitalsTable) -> None:
    """1 秒ごとに最新のバイタルが届く."""
    hr = table.parameters["HR"].values[-1]
    spo2 = table.parameters["SpO2"].values[-1]
    sbp = table.parameters["SBP"].values[-1]
    dbp = table.parameters["DBP"].values[-1]
    # paperChart の描画/記録処理へ渡す
    paperchart.update_vitals(hr=hr, spo2=spo2, sbp=sbp, dbp=dbp)

monitor = MonitorVideo(
    config_path="paperchart/b650_video.yaml",
    device_index=0,          # Windows の「カメラ」アプリで確認した番号
    callback=on_vitals,
    interval_sec=1.0,
    pvi_window_sec=30.0,
)
monitor.start()
# 終了時
monitor.stop()
```

`VitalsTable` に含まれるパラメータ名と意味:

| パラメータ | 内容 |
|------------|------|
| `HR`       | 心拍数 |
| `SpO2`     | 動脈血酸素飽和度 |
| `SBP`      | 収縮期血圧（NIBP） |
| `DBP`      | 拡張期血圧（NIBP） |
| `MAP`      | 平均血圧（NIBP。map が無い場合 `(SBP+2*DBP)/3`） |
| `BIS`      | BIS 指数（センサー未接続・BIS 行がない場合は `None`） |
| `ST`       | ST 値 |
| `PI`       | Perfusion Index プロキシ（Pleth 波形ありのとき） |
| `PVI`      | Pleth Variability Index（連続 30 秒以上の波形で計算） |

### 2. JSON 行プロセスとして呼び出す（別言語/既存 EXE 製 paperChart の場合）

paperChart 側から Python スクリプトを `subprocess` で起動し、標準出力を 1 行ずつ読みます。

```powershell
python -m anesthesia_record.monitor_video `
  --config paperchart/b650_video.yaml `
  --device 0 `
  --interval 1.0 `
  --json
```

- 1 秒ごとに 1 行の JSON が標準出力に出力されます。
- 終了はプロセスを kill します。
- 1 回だけ取得したい場合は `--once` を追加します。

JSON 出力例:

```json
{"time":"11:34","hr":69,"st":0.0,"spo2":99,"bis":null,
 "nibp":{"sys":107,"dia":67,"map":83,"measuring":false,
         "bar_present":true,"progress":0.702},
 "ecg_waveform":null,
 "spo2_waveform":{"pi":13.46,"pvi":null,"ac":21.19,
                  "ac_rms":4.91,"dc":36.44,"samples":435}}
```

paperChart 側の擬似コード例:

```python
import subprocess, json

proc = subprocess.Popen(
    [
        "python", "-m", "anesthesia_record.monitor_video",
        "--config", "paperchart/b650_video.yaml",
        "--device", "0",
        "--interval", "1.0",
        "--json",
    ],
    stdout=subprocess.PIPE,
    text=True,
)
for line in proc.stdout:
    data = json.loads(line)
    if data["nibp"]["measuring"]:
        # カフ充填中は古い NIBP 値なので無視
        continue
    paperchart.update_vitals(
        hr=data["hr"],
        spo2=data["spo2"],
        sbp=data["nibp"]["sys"],
        dbp=data["nibp"]["dia"],
        map=data["nibp"]["map"],
        bis=data["bis"],
        st=data["st"],
        pi=data["spo2_waveform"]["pi"] if data["spo2_waveform"] else None,
        pvi=data["spo2_waveform"]["pvi"] if data["spo2_waveform"] else None,
    )
```

### 3. 内蔵 GUI から使う

```powershell
python run_gui.py
```

メニュー「バイタル」→「外部モニタビデオキャプチャ...」でカメラ番号と取得間隔を指定すると、ライブチャートに HR/SpO2/NIBP/BIS/PI/PVI がリアルタイムで描画されます。

## paperChart モジュールとして使う

Windows 上の paperChart に直接数値を送るためのモジュールファイルを同梱しています。

### 同梱ファイル

| ファイル | 配置先（paperChart フォルダ） | 用途 |
|----------|------------------------------|------|
| `paperchart/BIN/monitors/B650Video.exe` | `BIN/monitors/B650Video.exe` | UVC+OCR を起動し、`PpcCtrl.dll` 経由で paperChart に送信 |
| `paperchart/BIN/monitors/PpcCtrl.dll` | `BIN/monitors/PpcCtrl.dll` | paperChart 連携 DLL（既存のものがあれば上書き不要） |
| `paperchart/CONF/monitors/B650Video.txt` | `CONF/monitors/B650Video.txt` | 取得間隔、Python 設定、paperChart 項目マッピング |

### インストール手順

1. `paperchart_install.bat` を実行し、paperChart フォルダを指定すると、
   `B650Video.exe` / `PpcCtrl.dll` / `B650Video.txt` がそれぞれ
   `BIN/monitors` / `CONF/monitors` にコピーされます。
2. `anesthesia-record` フォルダを paperChart フォルダ内（例: `C:\paperChart\anesthesia-record`）に
   配置します。別の場所に置く場合は、`CONF/monitors/B650Video.txt` の
   `@WorkingDir` をそのパスに変更してください。
3. paperChart の `CONF/dircnf.txt` の `command` セクション内、`new` および `append`
   ブロックに `module` 行を追加します。`paperchart_install.bat` を使えば自動で挿入されます。

```text
command
{
    new
    {
        ...
        module = monitors/B650Video.exe /std_arg/ ;
    }

    append
    {
        ...
        module = monitors/B650Video.exe /std_arg/ ;
    }

    ...
}
```

4. `B650Video.txt` の `@Device` を実際の UVC デバイス番号に合わせます。
5. paperChart を起動後、「モニタ開始」等で `B650Video` モジュールが起動し、
   1 秒ごとに HR/SpO2/NIBP/BIS/ST/PI/PVI が自動記録されます。

### `B650Video.txt` 設定

```text
@Python=python
@Module=anesthesia_record.monitor_video
@Config=paperchart\b650_video.yaml
@Device=0
@Interval=1.0
@WorkingDir=..\..\anesthesia-record

Startup=Hidden
SuppressZero=True

hr=HR,bpm
spo2=SpO2,%
nibp|sdm=NIBP,mmHg
bis=BIS,
st=ST,
spo2_waveform.pi=PI,%
spo2_waveform.pvi=PVI,%
```

- `nibp|sdm` は「収縮期 / 拡張期 / 平均」の 3 セットで送信します。
- `spo2_waveform.pi` / `spo2_waveform.pvi` は Pleth 波形から計算したプロキシ値です。
- `SuppressZero=True` の間、値が `0` の項目は送信しません。

### 注意

- NIBP 測定中（`nibp.measuring=true`）は、表示されている SYS/DIA が古い値のため
  送信しません。緑バーが満たされて測定完了後、新しい値が記録されます。
- BIS センサー未接続・BIS 行がないレイアウトでは `bis` は `null` になり、
  送信されません。
- Pleth 波形が隠れている画面（トレンド画面等）では `spo2_waveform` から
  値が得られません。通常波形画面に戻ると自動的に PI/PVI が復帰します。
