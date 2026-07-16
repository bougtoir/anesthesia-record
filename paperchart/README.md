# paperChart 設定ファイル (GE CARESCAPE B650)

paperChart（岩瀬良範先生・斎藤智彦先生らによるフリー自動麻酔記録ソフト）で
**GE CARESCAPE B650** のバイタルを自動取り込みするための設定ファイルと、
その内容を検証・構造化するローダを収録する。

| ファイル | 用途 |
| --- | --- |
| `b650.txt` | paperChart に読み込ませる本体（**Shift-JIS / CRLF**） |
| `b650.src.txt` | 上記の UTF-8 / LF 編集用ソース（GitHub 上で差分が読める） |
| `dri_viewer.py` | DRI バイナリログの 2 ペイン・ビューア（左:生16進 / 右:解析） |
| `dri_request.py` | シリアル DRI の送信リクエスト送出＋受信保存（要 pyserial） |
| `pcap_dri.py` | ネットワークキャプチャ(pcap/pcapng)から S/5(DRI) フロー抽出・解析 |
| `../anesthesia_record/paperchart_config.py` | 設定ファイルのローダ・整合性検証 |
| `../anesthesia_record/dri_decode.py` | DRI フレーム分解・チェックサム検証・数値デコード |
| `../tests/test_paperchart_config.py` / `test_dri_decode.py` | pytest |

`b650.src.txt` を編集したら、次で `b650.txt` を再生成する:

```bash
sed 's/$/\r/' b650.src.txt | iconv -f UTF-8 -t CP932 > b650.txt
```

## なぜ s5.txt がそのまま B650 に効くのか

paperChart の Datex-Ohmeda S/5 用設定ファイル `s5.txt` は、DRI
(Datex Record Interface) の**物理データレコード** `dri_phdb` 上での
バイトオフセット・スケール・ラベルを定義している（原典:
*DatexOhmeda S/5 Computer Interface Specification*, Doc. No. M1017617-01,
March 2004）。

物理データレコードのレイアウト（subrecord class / group_hdr / physdata の
オフセット・スケール）は S/5 と共通であるため、`b650.txt` の
`numerics` / `waves` / `labels` は `s5.txt` と同一の DRI レイアウトに基づく。

すなわち本ファイルは **S/5 の s5.txt を B650 向けに派生**させたものであり、
差分は主に (1) ヘッダ/エラーメッセージの文言、(2) 実際に得られる
パラメータの選択、(3) 接続方法の注意書き、である。DRI レコードの
レイアウトは、**シリアルでもネットワークでも同じ**なので、どちらの経路で
取得しても数値マップは流用できる。

## ⚠️ B650 の DRI 取得経路（実機写真で確認済み）

実機(SW 2.0.8, 2018年製)の背面と設定画面を確認した結果、**当初想定していた
「シリアル(RS-232)で DRI を取る」経路はこの機種では成立しない**ことが判明した。

- 背面コネクタは **USB×4 / DVI-I / ePort(DB9) / MC・IX ネットワーク / X3** のみ。
  **ネイティブ RS-232 端子は存在しない**。
- **DB9 は「ePort」= PDM 専用**。しかも Licensing 画面が "PDM NOT CONNECTED"
  で、ePort には何も接続されていない。ここに USB-シリアル変換器を挿しても
  意味のある信号は出ない（ボーレートを変えても全キャプチャがゴミだった原因）。
- 背面 USB は**ホスト側**（キーボード/バーコード等の入力機器用）で、DRI の
  データ出力ではない。
- Configuration → Network に **`Select Network Type: [ ] CARESCAPE Network
  [ ] S/5`** の切替がある。→ **B650 の DRI(S/5) データはネットワーク経由**で
  出す設計。paperChart が扱う S/5 レコードは MC ネットワーク上を流れる。

### ネットワーク(S/5)キャプチャ手順

> ⚠️ **安全**: 臨床稼働中のモニタで Network Type や設定を変更しない
> （セントラルモニタ連携が切れる等の重大リスク）。検証機、または臨床工学部の
> 管理下で行うこと。受動キャプチャ（下記）はモニタ設定を変えないので比較的安全。

1. PC を **MC ネットワーク**（例 172.16.x/16）と同一セグメントに接続する
   （検証機なら小型スイッチで PC とモニタを直結）。
2. Wireshark 等でモニタ IP（例 172.16.12.143）発の UDP/TCP を数分キャプチャし、
   `.pcapng` で保存する。
3. `pcap_dri.py` で S/5(DRI) 候補フローを抽出・デコードする:

   ```bash
   python paperchart/pcap_dri.py capture.pcapng
   python paperchart/pcap_dri.py capture.pcapng --ip 172.16.12.143 --decode
   ```

   `0x7E`（DRI フレーム区切り）を含むフローが候補。`--decode` で checksum OK と
   数値が出れば取得成功。CARESCAPE Network モード時は S/5 とは別プロトコルの
   ため、`0x7E` が見えない場合は Network Type を S/5 に切り替えて再取得する
   （※上記の安全注意に従うこと）。

## 実機確認が必要な項目（未検証）

このファイルは仕様・公開資料からの導出であり、実際の B650 の DRI 出力
キャプチャでは未検証。運用前に少なくとも次を実機確認すること。

1. **ボーレート**: VitalDB は Bx50=9600 baud と記載。paperChart の S/5
   ドライバが 19200 固定の場合は通信不可のため、B650 側 DRI 出力の
   ボーレート設定と一致させる必要がある。
2. **ポート名**: `rs232c_port = com1:` は各 PC の実際の COM 番号に合わせる。
3. **モジュール依存パラメータ**: 呼吸/ガス系（CO2・O2・N2O・麻酔ガス濃度、
   気道圧、換気量）は CARESCAPE 呼吸/ガスモジュール装着時のみ DRI に出る。
   B650+PDM 単体では循環・SpO2・体温系のみ。

## 参考資料

- `s5.txt`（本リポジトリに同梱の paperChart S/5 用設定）— 派生元。
- DatexOhmeda S/5 Computer Interface Specification (M1017617-01) — DRI 原典。
- Datex-Ohmeda COM Output Protocol, Carestation 600 Series (Rev.5) —
  パラメータ意味/単位/スケールのクロスリファレンス（COM 1.0/1.2 は
  別系統の ASCII プロトコルで本ファイルの書式とは異なる）。
- paperChart workshop（日本歯科麻酔学会）: works5.html に「paperChart と
  接続した GE CARESCAPE B650 の特徴」の解説あり。

## ローダの使い方

```python
from anesthesia_record.paperchart_config import load_paperchart_config

cfg = load_paperchart_config("paperchart/b650.txt")
print(cfg.rs232c_port)              # 'com1:'
print(len(cfg.numerics))            # 取り込みパラメータ数
print(cfg.validate())               # [] なら内部整合性OK
hr = cfg.numeric("HR")              # 個別パラメータの定義
```

`validate()` は、未定義ラベルセットの参照や、`initial_waves` が `waves` に
存在しない波形を指していないか等の整合性を検査する。

## DRI バイナリログの 2 ペイン・ビューア

TeraTerm 等で B650 のシリアル(DRI)出力を**バイナリ**保存したログを、
左に生バイナリ(16進)、右に解析結果を並べて確認できる。

```bash
python paperchart/dri_viewer.py capture.bin          # GUI (tkinter)
python paperchart/dri_viewer.py capture.bin --text   # 端末に2ペイン出力
python paperchart/dri_viewer.py --demo --text        # 合成データでデモ
```

DISPLAY の無い環境では自動的に端末出力(`--text`)にフォールバックする。
デコード器 `anesthesia_record/dri_decode.py` は次を行う。

- `0x7E` 区切りフレームの分解と `0x7D` エスケープの復元
- 末尾 1 バイトのチェックサム検証（総和の下位 8bit）
- 40 バイトの datex ヘッダ解析（時刻・レコード種別・サブレコード記述子）
- 生理データ表示(DISPL)の physdata を **b650.txt の numerics 定義**で数値化
  （16bit 符号付き LE、`-32001` 以下は無効値）

> TeraTerm 側は「ファイル > ログ」で**バイナリ**チェックを有効にして保存する
> こと（テキストモードだと 0x0A/0x0D 変換でフレームが壊れる）。
> なお本デコーダは公開仕様からの実装で、実機 B650 の出力では未検証。

## データが出てこない場合（送信リクエストの送出）

DRI では**収集側ホストが送信リクエストを送らないとモニタは生理データを
ストリームしない**。TeraTerm でポートを受け身に開くだけだと何も来ず、
キャプチャが全て `0x00` になる（フレーム区切り `0x7E` が 0 個）。

`dri_request.py` は送信リクエストを送ってから受信・保存する（要 `pyserial`）。

```bash
pip install pyserial
# CARESCAPE Bx50 (DRI v6): 115200 8E1
python paperchart/dri_request.py --port COM5 --baud 115200 --parity even --seconds 30 --out capture.bin
# 旧 Datex S/5: 19200 8E1
python paperchart/dri_request.py --port COM5 --baud 19200  --parity even --seconds 30 --out capture.bin
python paperchart/dri_viewer.py capture.bin --text   # 右ペインに checksum OK が出れば成功
```

### シリアル設定（重要）

DRI は**リクエスト駆動**で、モニタに「DRI ON」トグルは無い。ホストが送信
リクエストを送ることで初めてストリームが始まる（`dri_request.py` が送出）。
VSCapture の S/5 設定（`Class1.cs`）に一致する既定値:

| 項目 | 値 |
| --- | --- |
| Baud | **115200**（CARESCAPE B450/B650/B850 = DRI v6）／旧 S/5 は 19200 |
| Data bits | 8 |
| Parity | **even** |
| Stop bits | 1 |
| Handshake | RTS（`--rtscts`。まず flow=none + RTS/DTR assert で可） |

全て `0x00`（+ 1ビットだけの値）になる場合の切り分け:

1. **ボーレート不一致**（最有力）。Bx50 は 115200。9600/19200 で取ると
   115200 ストリームを大幅にアンダーサンプリングして `0x00`＋1ビット雑音の
   ゴミになる（実測で確認）。まず **115200 8E1** を試す。
2. 送信リクエスト未送出（`dri_request.py` で解消）。
3. **配線**: B650 背面の DB9 は **ePort(Tram-Net)** で、独自バス（電源＋
   Tram-Net）と DRI 用 RS-232 が同居する。RS-232 の TX/GND ピンに正しく
   当たっているか要確認。USB ポートはホスト（入力機器用）で DRI 出力ではない。
