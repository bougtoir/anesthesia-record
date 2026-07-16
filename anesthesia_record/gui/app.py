"""メインGUIアプリケーション: リアルタイム麻酔記録.

tkinter + matplotlib embed でライブチャートを表示し、
薬剤・イベント・出血/尿量の入力UIを提供する。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import threading
import queue

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from ..models import Delivery, MedEvent, OutputCategory, OutputEvent, Patient, Sex
from ..drug_master import load_drug_master, DrugMasterFile
from ..events import EventLog, ClinicalEvent
from ..vitals import VitalsTable, VitalSeries
from ..anesthesia_fee import AnesthesiaEvent, PositionEvent, load_anesthesia_fee
from .session import AnesthesiaSession
from .vitals_monitor import VitalsMonitor
from .chart_live import render_live_chart


_DEFAULT_REFRESH_MS = 5000  # 5秒ごとに再描画


class AnesthesiaApp:
    """リアルタイム麻酔記録GUIアプリケーション."""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self.root = tk.Tk()
        self.root.title("麻酔記録 v0.2 — リアルタイム")
        self.root.geometry("1600x900")
        self.root.minsize(1200, 700)

        # --- データ ---
        self._base_dir = Path(__file__).resolve().parent.parent.parent
        self.drug_master = load_drug_master(
            str(self._base_dir / "data" / "drug_master.yaml")
        )
        self.fee_config = load_anesthesia_fee(
            str(self._base_dir / "data" / "anesthesia_fee.yaml")
        )
        self.session = AnesthesiaSession()
        self.vitals_monitor: Optional[VitalsMonitor] = None
        self._refresh_job: Optional[str] = None
        self._event_queue: queue.Queue = queue.Queue()

        # --- UI構築 ---
        self._build_menu()
        self._build_layout()
        self._build_patient_panel()
        self._build_control_panel()
        self._build_drug_panel()
        self._build_event_panel()
        self._build_output_panel()
        self._build_chart_area()
        self._build_status_bar()

        # 初回描画
        self._schedule_refresh()

    # ===== レイアウト =====

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="新規症例", command=self._new_case)
        file_menu.add_command(label="症例読み込み...", command=self._load_case)
        file_menu.add_command(label="症例保存...", command=self._save_case)
        file_menu.add_separator()
        file_menu.add_command(label="チャート画像出力...", command=self._export_chart)
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self.root.quit)
        menubar.add_cascade(label="ファイル", menu=file_menu)

        vitals_menu = tk.Menu(menubar, tearoff=0)
        vitals_menu.add_command(label="CSV監視開始...", command=self._start_csv_watch)
        vitals_menu.add_command(label="CSV監視停止", command=self._stop_vitals)
        vitals_menu.add_separator()
        vitals_menu.add_command(label="VitalRecorder TCP接続...", command=self._connect_vitalrecorder)
        vitals_menu.add_command(label="接続切断", command=self._stop_vitals)
        menubar.add_cascade(label="バイタル", menu=vitals_menu)

        self.root.config(menu=menubar)

    def _build_layout(self) -> None:
        # 左パネル(入力) + 右(チャート)
        self.paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        self.left_frame = ttk.Frame(self.paned, width=380)
        self.right_frame = ttk.Frame(self.paned)
        self.paned.add(self.left_frame, weight=0)
        self.paned.add(self.right_frame, weight=1)

    def _build_patient_panel(self) -> None:
        lf = ttk.LabelFrame(self.left_frame, text="患者情報")
        lf.pack(fill=tk.X, padx=4, pady=2)

        row = ttk.Frame(lf)
        row.pack(fill=tk.X, padx=2, pady=1)
        ttk.Label(row, text="ID:").pack(side=tk.LEFT)
        self.var_patient_id = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_patient_id, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Label(row, text="年齢:").pack(side=tk.LEFT)
        self.var_age = tk.StringVar(value="50")
        ttk.Entry(row, textvariable=self.var_age, width=4).pack(side=tk.LEFT, padx=2)
        ttk.Label(row, text="性別:").pack(side=tk.LEFT)
        self.var_sex = tk.StringVar(value="male")
        ttk.Combobox(row, textvariable=self.var_sex, values=["male", "female"], width=6, state="readonly").pack(side=tk.LEFT, padx=2)

        row2 = ttk.Frame(lf)
        row2.pack(fill=tk.X, padx=2, pady=1)
        ttk.Label(row2, text="体重(kg):").pack(side=tk.LEFT)
        self.var_weight = tk.StringVar(value="60")
        ttk.Entry(row2, textvariable=self.var_weight, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(row2, text="身長(cm):").pack(side=tk.LEFT)
        self.var_height = tk.StringVar(value="165")
        ttk.Entry(row2, textvariable=self.var_height, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(row2, text="ASA-PS:").pack(side=tk.LEFT)
        self.var_asa = tk.StringVar(value="2")
        ttk.Combobox(row2, textvariable=self.var_asa, values=["1", "2", "3", "4", "5", "6"], width=3, state="readonly").pack(side=tk.LEFT, padx=2)

        ttk.Button(lf, text="患者情報を確定", command=self._confirm_patient).pack(pady=2)

    def _build_control_panel(self) -> None:
        lf = ttk.LabelFrame(self.left_frame, text="記録制御")
        lf.pack(fill=tk.X, padx=4, pady=2)
        row = ttk.Frame(lf)
        row.pack(fill=tk.X, padx=2, pady=2)
        self.btn_start = ttk.Button(row, text="麻酔開始", command=self._anesthesia_start)
        self.btn_start.pack(side=tk.LEFT, padx=2)
        self.btn_end = ttk.Button(row, text="麻酔終了", command=self._anesthesia_end, state=tk.DISABLED)
        self.btn_end.pack(side=tk.LEFT, padx=2)
        self.btn_surgery_start = ttk.Button(row, text="執刀", command=self._surgery_start)
        self.btn_surgery_start.pack(side=tk.LEFT, padx=2)
        self.btn_surgery_end = ttk.Button(row, text="閉創", command=self._surgery_end)
        self.btn_surgery_end.pack(side=tk.LEFT, padx=2)

        row2 = ttk.Frame(lf)
        row2.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(row2, text="更新間隔(秒):").pack(side=tk.LEFT)
        self.var_refresh = tk.StringVar(value="5")
        ttk.Entry(row2, textvariable=self.var_refresh, width=4).pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="適用", command=self._apply_refresh).pack(side=tk.LEFT)

    def _build_drug_panel(self) -> None:
        lf = ttk.LabelFrame(self.left_frame, text="薬剤投与")
        lf.pack(fill=tk.X, padx=4, pady=2)

        row1 = ttk.Frame(lf)
        row1.pack(fill=tk.X, padx=2, pady=1)
        ttk.Label(row1, text="薬剤:").pack(side=tk.LEFT)
        drug_names = [f"{d.generic_name} ({d.id})" for d in self.drug_master.drugs.values()]
        self.var_drug = tk.StringVar()
        self.cmb_drug = ttk.Combobox(row1, textvariable=self.var_drug, values=drug_names, width=25, state="readonly")
        self.cmb_drug.pack(side=tk.LEFT, padx=2)
        self.cmb_drug.bind("<<ComboboxSelected>>", self._on_drug_selected)

        row2 = ttk.Frame(lf)
        row2.pack(fill=tk.X, padx=2, pady=1)
        ttk.Label(row2, text="投与方式:").pack(side=tk.LEFT)
        self.var_delivery = tk.StringVar(value="bolus")
        ttk.Radiobutton(row2, text="ボーラス", variable=self.var_delivery, value="bolus").pack(side=tk.LEFT)
        ttk.Radiobutton(row2, text="持続", variable=self.var_delivery, value="infusion").pack(side=tk.LEFT)

        row3 = ttk.Frame(lf)
        row3.pack(fill=tk.X, padx=2, pady=1)
        ttk.Label(row3, text="用量/流量:").pack(side=tk.LEFT)
        self.var_dose = tk.StringVar()
        ttk.Entry(row3, textvariable=self.var_dose, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Label(row3, text="単位:").pack(side=tk.LEFT)
        self.var_dose_unit = tk.StringVar()
        self.cmb_dose_unit = ttk.Combobox(row3, textvariable=self.var_dose_unit, width=10)
        self.cmb_dose_unit.pack(side=tk.LEFT, padx=2)

        row4 = ttk.Frame(lf)
        row4.pack(fill=tk.X, padx=2, pady=1)
        ttk.Button(row4, text="投与記録", command=self._record_drug).pack(side=tk.LEFT, padx=2)
        ttk.Button(row4, text="持続終了", command=self._stop_infusion).pack(side=tk.LEFT, padx=2)

        # 輸液用残量入力
        row5 = ttk.Frame(lf)
        row5.pack(fill=tk.X, padx=2, pady=1)
        ttk.Label(row5, text="残量(ml):").pack(side=tk.LEFT)
        self.var_remaining = tk.StringVar()
        ttk.Entry(row5, textvariable=self.var_remaining, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Button(row5, text="残量記録", command=self._record_remaining).pack(side=tk.LEFT, padx=2)

    def _build_event_panel(self) -> None:
        lf = ttk.LabelFrame(self.left_frame, text="臨床イベント")
        lf.pack(fill=tk.X, padx=4, pady=2)

        row = ttk.Frame(lf)
        row.pack(fill=tk.X, padx=2, pady=1)
        ttk.Label(row, text="種類:").pack(side=tk.LEFT)
        event_types = [
            "挿管", "抜管", "体位変換", "特殊体位開始", "特殊体位終了",
            "A-line", "CV挿入", "神経ブロック", "カテコラミン開始",
            "輸血開始", "その他"
        ]
        self.var_event_type = tk.StringVar()
        ttk.Combobox(row, textvariable=self.var_event_type, values=event_types, width=15).pack(side=tk.LEFT, padx=2)

        row2 = ttk.Frame(lf)
        row2.pack(fill=tk.X, padx=2, pady=1)
        ttk.Label(row2, text="メモ:").pack(side=tk.LEFT)
        self.var_event_note = tk.StringVar()
        ttk.Entry(row2, textvariable=self.var_event_note, width=25).pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="記録", command=self._record_event).pack(side=tk.LEFT, padx=2)

    def _build_output_panel(self) -> None:
        lf = ttk.LabelFrame(self.left_frame, text="出血・尿量")
        lf.pack(fill=tk.X, padx=4, pady=2)

        row = ttk.Frame(lf)
        row.pack(fill=tk.X, padx=2, pady=1)
        ttk.Label(row, text="種類:").pack(side=tk.LEFT)
        self.var_output_cat = tk.StringVar(value="gauze")
        ttk.Combobox(
            row, textvariable=self.var_output_cat,
            values=["gauze (ガーゼg)", "suction (吸引cc)", "urine (尿量cc)"],
            width=18, state="readonly"
        ).pack(side=tk.LEFT, padx=2)

        row2 = ttk.Frame(lf)
        row2.pack(fill=tk.X, padx=2, pady=1)
        ttk.Label(row2, text="量:").pack(side=tk.LEFT)
        self.var_output_amount = tk.StringVar()
        ttk.Entry(row2, textvariable=self.var_output_amount, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="記録", command=self._record_output).pack(side=tk.LEFT, padx=2)

    def _build_chart_area(self) -> None:
        self.fig = Figure(figsize=(12, 7), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.right_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        toolbar = NavigationToolbar2Tk(self.canvas, self.right_frame)
        toolbar.update()

    def _build_status_bar(self) -> None:
        self.status_var = tk.StringVar(value="準備完了 — 患者情報を入力して麻酔を開始してください")
        status = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status.pack(fill=tk.X, side=tk.BOTTOM)

    # ===== コールバック =====

    def _confirm_patient(self) -> None:
        try:
            self.session.patient = Patient(
                patient_id=self.var_patient_id.get() or None,
                age_years=float(self.var_age.get()),
                sex=Sex(self.var_sex.get()),
                weight_kg=float(self.var_weight.get()),
                height_cm=float(self.var_height.get()),
                asa_ps=int(self.var_asa.get()),
            )
            self.status_var.set(f"患者確定: {self.session.patient.weight_kg}kg, {self.session.patient.age_years}歳")
        except (ValueError, KeyError) as e:
            messagebox.showerror("入力エラー", str(e))

    def _anesthesia_start(self) -> None:
        if self.session.patient is None:
            messagebox.showwarning("警告", "先に患者情報を確定してください")
            return
        now = datetime.now()
        self.session.anesthesia_start = now
        self.session.events.append(ClinicalEvent(time=now, type="anesthesia_start", label="麻酔開始"))
        self.btn_start.config(state=tk.DISABLED)
        self.btn_end.config(state=tk.NORMAL)
        self.status_var.set(f"麻酔開始: {now.strftime('%H:%M')}")
        self._refresh_chart()

    def _anesthesia_end(self) -> None:
        now = datetime.now()
        self.session.anesthesia_end = now
        self.session.events.append(ClinicalEvent(time=now, type="anesthesia_end", label="麻酔終了"))
        self.btn_end.config(state=tk.DISABLED)
        self.status_var.set(f"麻酔終了: {now.strftime('%H:%M')}")
        self._refresh_chart()

    def _surgery_start(self) -> None:
        now = datetime.now()
        self.session.events.append(ClinicalEvent(time=now, type="surgery_start", label="執刀"))
        self.status_var.set(f"執刀: {now.strftime('%H:%M')}")
        self._refresh_chart()

    def _surgery_end(self) -> None:
        now = datetime.now()
        self.session.events.append(ClinicalEvent(time=now, type="surgery_end", label="閉創"))
        self.status_var.set(f"閉創: {now.strftime('%H:%M')}")
        self._refresh_chart()

    def _on_drug_selected(self, event=None) -> None:
        sel = self.var_drug.get()
        if not sel:
            return
        drug_id = sel.split("(")[-1].rstrip(")")
        try:
            drug = self.drug_master.get(drug_id)
        except KeyError:
            return
        self.cmb_dose_unit["values"] = drug.dose_units
        if drug.dose_units:
            if self.var_delivery.get() == "bolus" and drug.unit_bolus:
                self.var_dose_unit.set(drug.unit_bolus)
            elif drug.unit_rate:
                self.var_dose_unit.set(drug.unit_rate)
            else:
                self.var_dose_unit.set(drug.dose_units[0])

    def _record_drug(self) -> None:
        sel = self.var_drug.get()
        if not sel:
            messagebox.showwarning("警告", "薬剤を選択してください")
            return
        drug_id = sel.split("(")[-1].rstrip(")")
        try:
            dose_val = float(self.var_dose.get())
        except ValueError:
            messagebox.showerror("入力エラー", "用量を数値で入力してください")
            return

        delivery = Delivery(self.var_delivery.get())
        now = datetime.now()
        med = MedEvent(
            drug_id=drug_id,
            start_time=now,
            delivery=delivery,
            dose=dose_val if delivery == Delivery.BOLUS else None,
            dose_unit=self.var_dose_unit.get() if delivery == Delivery.BOLUS else None,
            rate=dose_val if delivery == Delivery.INFUSION else None,
            rate_unit=self.var_dose_unit.get() if delivery == Delivery.INFUSION else None,
        )
        self.session.med_events.append(med)
        try:
            drug = self.drug_master.get(drug_id)
            name = drug.generic_name
        except KeyError:
            name = drug_id
        self.status_var.set(f"{name} {dose_val} {self.var_dose_unit.get()} @ {now.strftime('%H:%M:%S')}")
        self._refresh_chart()

    def _stop_infusion(self) -> None:
        sel = self.var_drug.get()
        if not sel:
            messagebox.showwarning("警告", "薬剤を選択してください")
            return
        drug_id = sel.split("(")[-1].rstrip(")")
        now = datetime.now()
        # 最後の持続イベントにend_timeを設定
        for med in reversed(self.session.med_events):
            if med.drug_id == drug_id and med.delivery == Delivery.INFUSION and med.end_time is None:
                med.end_time = now
                self.status_var.set(f"{drug_id} 持続終了 @ {now.strftime('%H:%M:%S')}")
                self._refresh_chart()
                return
        messagebox.showinfo("情報", f"{drug_id} の持続投与が見つかりません")

    def _record_remaining(self) -> None:
        sel = self.var_drug.get()
        if not sel:
            messagebox.showwarning("警告", "薬剤(輸液)を選択してください")
            return
        drug_id = sel.split("(")[-1].rstrip(")")
        try:
            remaining = float(self.var_remaining.get())
        except ValueError:
            messagebox.showerror("入力エラー", "残量を数値で入力してください")
            return
        now = datetime.now()
        # 最後の輸液イベントに残量を設定、または新規作成
        for med in reversed(self.session.med_events):
            if med.drug_id == drug_id and med.delivery == Delivery.INFUSION:
                if med.remaining_ml_end is None:
                    med.remaining_ml_end = remaining
                    med.end_time = now
                    self.status_var.set(f"{drug_id} 終了残量: {remaining}ml")
                    self._refresh_chart()
                    return
        # 新規開始(残量=開始時残量)
        med = MedEvent(
            drug_id=drug_id,
            start_time=now,
            delivery=Delivery.INFUSION,
            remaining_ml_start=remaining,
        )
        self.session.med_events.append(med)
        self.status_var.set(f"{drug_id} 開始残量: {remaining}ml")
        self._refresh_chart()

    def _record_event(self) -> None:
        etype = self.var_event_type.get()
        if not etype:
            messagebox.showwarning("警告", "イベント種類を選択してください")
            return
        now = datetime.now()
        # マッピング
        type_map = {
            "挿管": "intubation", "抜管": "extubation",
            "体位変換": "position_change", "特殊体位開始": "position_start",
            "特殊体位終了": "position_end", "A-line": "a_line",
            "CV挿入": "cv_insert", "神経ブロック": "nerve_block",
            "カテコラミン開始": "catecholamine_start", "輸血開始": "transfusion_start",
            "その他": "other",
        }
        event_id = type_map.get(etype, "other")
        note = self.var_event_note.get() or None
        ev = ClinicalEvent(time=now, type=event_id, label=etype, note=note)
        self.session.events.append(ev)
        self.status_var.set(f"イベント: {etype} @ {now.strftime('%H:%M:%S')}")
        self.var_event_note.set("")
        self._refresh_chart()

    def _record_output(self) -> None:
        cat_str = self.var_output_cat.get().split(" ")[0]
        try:
            cat = OutputCategory(cat_str)
        except ValueError:
            messagebox.showerror("エラー", "カテゴリが不正です")
            return
        try:
            amount = float(self.var_output_amount.get())
        except ValueError:
            messagebox.showerror("入力エラー", "量を数値で入力してください")
            return
        now = datetime.now()
        ev = OutputEvent(category=cat, time=now, amount=amount)
        self.session.output_events.append(ev)
        self.status_var.set(f"出力: {cat_str} +{amount} @ {now.strftime('%H:%M:%S')}")
        self.var_output_amount.set("")
        self._refresh_chart()

    # ===== バイタル監視 =====

    def _start_csv_watch(self) -> None:
        path = filedialog.askopenfilename(
            title="バイタルCSVファイルを選択",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return
        self._stop_vitals()
        self.vitals_monitor = VitalsMonitor(
            mode="csv", path=path,
            callback=self._on_vitals_update,
            interval_sec=2.0,
        )
        self.vitals_monitor.start()
        self.status_var.set(f"CSV監視開始: {Path(path).name}")

    def _connect_vitalrecorder(self) -> None:
        dialog = _VitalRecorderDialog(self.root)
        self.root.wait_window(dialog.top)
        if dialog.result:
            host, port = dialog.result
            self._stop_vitals()
            self.vitals_monitor = VitalsMonitor(
                mode="tcp", host=host, port=port,
                callback=self._on_vitals_update,
                interval_sec=1.0,
            )
            self.vitals_monitor.start()
            self.status_var.set(f"VitalRecorder接続: {host}:{port}")

    def _stop_vitals(self) -> None:
        if self.vitals_monitor:
            self.vitals_monitor.stop()
            self.vitals_monitor = None

    def _on_vitals_update(self, vitals: VitalsTable) -> None:
        self.session.vitals = vitals
        # GUIスレッドでの再描画はスケジュール済み

    # ===== チャート =====

    def _refresh_chart(self) -> None:
        self.fig.clear()
        render_live_chart(
            fig=self.fig,
            session=self.session,
            drug_master=self.drug_master,
            fee_config=self.fee_config,
        )
        self.canvas.draw_idle()

    def _schedule_refresh(self) -> None:
        self._refresh_chart()
        try:
            ms = int(float(self.var_refresh.get()) * 1000)
        except ValueError:
            ms = _DEFAULT_REFRESH_MS
        self._refresh_job = self.root.after(ms, self._schedule_refresh)

    def _apply_refresh(self) -> None:
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
        self._schedule_refresh()

    # ===== ファイル操作 =====

    def _new_case(self) -> None:
        if messagebox.askyesno("確認", "現在の症例データを破棄しますか？"):
            self.session = AnesthesiaSession()
            self.btn_start.config(state=tk.NORMAL)
            self.btn_end.config(state=tk.DISABLED)
            self.status_var.set("新規症例")
            self._refresh_chart()

    def _load_case(self) -> None:
        path = filedialog.askopenfilename(
            title="症例ファイル読み込み",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")]
        )
        if path:
            self.session.load_from_yaml(path, self.drug_master)
            self._sync_ui_from_session()
            self.status_var.set(f"読み込み完了: {Path(path).name}")
            self._refresh_chart()

    def _sync_ui_from_session(self) -> None:
        """セッションデータをUIフォームに反映."""
        if self.session.patient:
            p = self.session.patient
            self.var_patient_id.set(p.patient_id or "")
            self.var_age.set(str(int(p.age_years)))
            self.var_sex.set(p.sex.value)
            self.var_weight.set(str(p.weight_kg))
            self.var_height.set(str(p.height_cm))
            self.var_asa.set(str(p.asa_ps or 2))
        # ボタン状態同期
        if self.session.anesthesia_start:
            self.btn_start.config(state=tk.DISABLED)
            self.btn_end.config(state=tk.NORMAL if not self.session.anesthesia_end else tk.DISABLED)
        else:
            self.btn_start.config(state=tk.NORMAL)
            self.btn_end.config(state=tk.DISABLED)

    def _save_case(self) -> None:
        path = filedialog.asksaveasfilename(
            title="症例ファイル保存",
            defaultextension=".yaml",
            filetypes=[("YAML files", "*.yaml"), ("All files", "*.*")]
        )
        if path:
            self.session.save_to_yaml(path)
            self.status_var.set(f"保存完了: {Path(path).name}")

    def _export_chart(self) -> None:
        path = filedialog.asksaveasfilename(
            title="チャート画像出力",
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")]
        )
        if path:
            self.fig.savefig(path, dpi=150, bbox_inches="tight")
            self.status_var.set(f"画像出力: {Path(path).name}")

    # ===== 起動 =====

    def run(self) -> None:
        self.root.mainloop()
        self._stop_vitals()


class _VitalRecorderDialog:
    """VitalRecorder TCP接続先入力ダイアログ."""

    def __init__(self, parent: tk.Tk):
        self.result: Optional[tuple[str, int]] = None
        self.top = tk.Toplevel(parent)
        self.top.title("VitalRecorder TCP接続")
        self.top.geometry("300x120")
        self.top.transient(parent)
        self.top.grab_set()

        ttk.Label(self.top, text="ホスト:").pack(padx=10, pady=(10, 0), anchor=tk.W)
        self.var_host = tk.StringVar(value="127.0.0.1")
        ttk.Entry(self.top, textvariable=self.var_host).pack(fill=tk.X, padx=10)

        ttk.Label(self.top, text="ポート:").pack(padx=10, anchor=tk.W)
        self.var_port = tk.StringVar(value="8887")
        ttk.Entry(self.top, textvariable=self.var_port).pack(fill=tk.X, padx=10)

        ttk.Button(self.top, text="接続", command=self._ok).pack(pady=5)

    def _ok(self) -> None:
        try:
            port = int(self.var_port.get())
            self.result = (self.var_host.get(), port)
        except ValueError:
            pass
        self.top.destroy()
