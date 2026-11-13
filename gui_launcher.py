"""
X(Twitter) 自動DMツール Pro - 統合単一ウィンドウ GUIアプリ (gui_launcher.py / gui_launcher.pyw)
・黒いコマンドプロンプト画面不要の1画面完結型
・「アカウント情報.xlsx」「アカウント名.xlsx」「アカウント名.db」を起動時に自動全読み込み
・各モード (検索最新/おすすめ/リプライURL/フォローURL) と実行設定をビジュアル表示
・初回セットアップ・単体テスト・本番実行・送信履歴管理まで全て1つのウィンドウで操作可能
"""

import os
import sys
import re
import subprocess
import threading
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog
from config_manager import ConfigManager

# Windows CP932 / Emoji UnicodeEncodeError 対策
if sys.platform == "win32":
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def normalize_screen_name(val: str) -> str:
    if not val:
        return ""
    s = str(val).strip().lstrip("@").strip()
    while True:
        m = re.match(r'^(?:https?://)?(?:www.)?(?:x|twitter).com/+@?', s, re.IGNORECASE)
        if m:
            s = s[m.end():].strip().lstrip("@").strip()
            continue
        if s.startswith("@"):
            s = s.lstrip("@").strip()
            continue
        if "x.com/" in s.lower() or "twitter.com/" in s.lower():
            parts = re.split(r'(?:https?://)?(?:www.)?(?:x|twitter).com/+@?', s, flags=re.IGNORECASE)
            s = parts[-1].strip().lstrip("@").strip()
            continue
        break
    s = s.split("?")[0].split("#")[0].rstrip("/")
    if "/" in s:
        s = s.split("/")[0]
    return s.lstrip("@").strip()

def sanitize_filename(name: str) -> str:
    """Windowsのファイル名・フォルダ名として使えない禁止文字 (\ / : * ? " < > | @) を _ に安全変換"""
    if not name:
        return "default"
    s = str(name).strip().lstrip("@")
    return re.sub(r'[\\/*?:"<>|]', '_', s)

try:
    import openpyxl
except ImportError:
    openpyxl = None

def get_db_dir(base_dir: str) -> str:
    """dbフォルダ (またはdb) のパスを取得し、無ければ自動作成"""
    for folder_name in ["dbフォルダ", "db", "dbs"]:
        p = os.path.join(base_dir, folder_name)
        if os.path.isdir(p):
            return p
    p = os.path.join(base_dir, "dbフォルダ")
    os.makedirs(p, exist_ok=True)
    return p

def get_log_dir(base_dir: str) -> str:
    """logフォルダ (またはlog) のパスを取得し、無ければ自動作成"""
    for folder_name in ["logフォルダ", "log", "logs"]:
        p = os.path.join(base_dir, folder_name)
        if os.path.isdir(p):
            return p
    p = os.path.join(base_dir, "logフォルダ")
    os.makedirs(p, exist_ok=True)
    return p

def get_account_db_path(base_dir: str, account_name: str) -> str:
    """アカウント名に応じた個別DBパス (dbフォルダ/アカウント名.DB) を取得"""
    db_dir = get_db_dir(base_dir)
    clean = normalize_screen_name(account_name) or str(account_name).strip()
    p_upper = os.path.join(db_dir, f"{clean}.DB")
    p_lower = os.path.join(db_dir, f"{clean}.db")
    return p_lower if os.path.exists(p_lower) else p_upper

def get_account_log_path(base_dir: str, account_name: str) -> str:
    """アカウント名に応じた個別ログファイルパス (logフォルダ/アカウント名.log) を取得"""
    log_dir = get_log_dir(base_dir)
    clean = normalize_screen_name(account_name) or str(account_name).strip()
    return os.path.join(log_dir, f"{clean}.log")

try:
    from db_manager import DatabaseManager
except Exception:
    DatabaseManager = None

class AppLauncherGUI:
    def get_db_dir(self) -> str:
        return get_db_dir(self.base_dir)

    def get_log_dir(self) -> str:
        return get_log_dir(self.base_dir)

    def __init__(self, root):
        self.root = root
        self.root.title("X(Twitter) 自動DM・リプライ・送信ツール Pro 【統合コントロールセンター】")
        self.root.geometry("1020x750")
        self.root.minsize(920, 620)

        if getattr(sys, 'frozen', False):
            self.base_dir = os.path.dirname(sys.executable)
        else:
            self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.running_process = None
        acc_path = os.path.join(self.base_dir, "アカウント情報.xlsx")
        name_path = os.path.join(self.base_dir, "アカウント名.xlsx")
        self.config_mgr = ConfigManager(acc_path, name_path, self.base_dir)

        # アプリ終了ハンドラー（ウィンドウの「×」で閉じたら裏の全プロセス・ブラウザを確実に全停止）
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)

        # 設定キャッシュ
        self.accounts_data = []
        self.targets_data = []
        self.keywords_data = []
        self.reply_urls_data = []
        self.follow_urls_data = []
        self.dm_templates = []
        self.reply_templates = []
        self.quote_templates = []
        self.prompt_setting = ""

        # UI構築
        self._create_widgets()

        # 起動時にExcel設定・アカウント・ターゲット・DBを自動全読み込み
        self.reload_all_configs()

    def _create_widgets(self):
        # 1. 最上部ヘッダー
        header_frame = tk.Frame(self.root, bg="#0F172A", padx=16, pady=10)
        header_frame.pack(fill=tk.X)

        title_box = tk.Frame(header_frame, bg="#0F172A")
        title_box.pack(side=tk.LEFT)

        title_label = tk.Label(
            title_box,
            text="X(Twitter) 自動DM・リプライ送信ツール Pro",
            font=("Meiryo", 13, "bold"),
            fg="white",
            bg="#0F172A"
        )
        title_label.pack(anchor="w")

        sub_label = tk.Label(
            title_box,
            text="💡 提案1: 1窓ローテーション巡回 (常に1画面のみ) | ⚡ 提案2: メディア遮断 (画像・動画ブロック・省メモリ50〜70%)",
            font=("Meiryo", 8),
            fg="#94A3B8",
            bg="#0F172A"
        )
        sub_label.pack(anchor="w")

        # ヘッダー右側: Excelファイル直接起動ボタン
        btn_bar = tk.Frame(header_frame, bg="#0F172A")
        btn_bar.pack(side=tk.RIGHT)

        btn_acc = tk.Button(btn_bar, text="📗 アカウント情報.xlsx", bg="#1E293B", fg="#38BDF8", font=("Meiryo", 8, "bold"), relief=tk.FLAT, padx=8, pady=4, cursor="hand2", command=lambda: self._open_file("アカウント情報.xlsx"))
        btn_acc.pack(side=tk.LEFT, padx=3)

        self.btn_name = tk.Button(btn_bar, text="📘 アカウント名.xlsx", bg="#1E293B", fg="#38BDF8", font=("Meiryo", 8, "bold"), relief=tk.FLAT, padx=8, pady=4, cursor="hand2", command=lambda: self._open_file(getattr(self, "current_target_file_name", "アカウント名.xlsx")))
        self.btn_name.pack(side=tk.LEFT, padx=3)

        btn_folder = tk.Button(btn_bar, text="📂 フォルダ", bg="#334155", fg="white", font=("Meiryo", 8), relief=tk.FLAT, padx=8, pady=4, cursor="hand2", command=lambda: os.startfile(self.base_dir))
        btn_folder.pack(side=tk.LEFT, padx=3)

        # 2. 自動読み込みステータスサマリーバー (上部カード)
        self.summary_frame = tk.Frame(self.root, bg="#1E293B", padx=14, pady=6)
        self.summary_frame.pack(fill=tk.X)

        self.lbl_summary_acc = tk.Label(self.summary_frame, text="アカウント: 読込中...", font=("Meiryo", 9, "bold"), fg="#F8FAFC", bg="#1E293B")
        self.lbl_summary_acc.pack(side=tk.LEFT, padx=6)

        self.lbl_summary_mode = tk.Label(self.summary_frame, text="取得モード: -", font=("Meiryo", 9), fg="#93C5FD", bg="#1E293B")
        self.lbl_summary_mode.pack(side=tk.LEFT, padx=6)

        self.lbl_summary_interval = tk.Label(self.summary_frame, text="待機間隔: -", font=("Meiryo", 9), fg="#FDE047", bg="#1E293B")
        self.lbl_summary_interval.pack(side=tk.LEFT, padx=6)

        self.lbl_summary_target = tk.Label(self.summary_frame, text="ターゲット: -", font=("Meiryo", 9, "bold"), fg="#4ADE80", bg="#1E293B")
        self.lbl_summary_target.pack(side=tk.LEFT, padx=6)

        btn_reload = tk.Button(self.summary_frame, text="🔄 設定を今すぐ再読込", bg="#2563EB", fg="white", font=("Meiryo", 8, "bold"), relief=tk.FLAT, padx=6, pady=2, cursor="hand2", command=self.reload_all_configs)
        btn_reload.pack(side=tk.RIGHT, padx=4)

        # 3. メインのタブコンテナ (ttk.Notebook)
        style = ttk.Style()
        style.configure("TNotebook.Tab", font=("Meiryo", 9, "bold"), padding=[10, 4])
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # --- タブ1: ⚡ 実行コントロール ＆ リアルタイム監視 ---
        self.tab_run = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_run, text="  ⚡ 実行＆監視  ")
        self._build_run_tab()

        # --- タブ2: 📋 アカウント設定 (アカウント情報.xlsx) ---
        self.tab_accounts = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_accounts, text="  📋 アカウント設定  ")
        self._build_accounts_tab()

        # --- タブ3: 🎯 ターゲット＆URL＆文面 (アカウント名.xlsx) ---
        self.tab_targets = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_targets, text="  🎯 ターゲット＆文面  ")
        self._build_targets_tab()

        # --- タブ4: 💾 送信履歴データベース (アカウント名.db) ---
        self.tab_db = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_db, text="  💾 送信履歴 (DB)  ")
        self._build_db_tab()

        # --- タブ5: 🛠️ システム診断＆単一EXE作成 ---
        self.tab_tools = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_tools, text="  🛠️ システム診断＆EXE化  ")
        self._build_tools_tab()

    def _build_run_tab(self):
        # アクションボタンエリア
        ctrl_box = tk.LabelFrame(self.tab_run, text=" 🚀 ツール実行コントロール（一時停止・強制停止） ", font=("Meiryo", 9, "bold"), padx=10, pady=8)
        ctrl_box.pack(fill=tk.X, padx=8, pady=4)

        # 1段目: 主要実行・操作バー (大ボタン・絶対に途切れない横配置)
        row1 = tk.Frame(ctrl_box)
        row1.pack(fill=tk.X, pady=2)

        self.btn_run_main = tk.Button(
            row1,
            text="🚀 本番自動実行 (全件)",
            font=("Meiryo", 10, "bold"),
            bg="#2563EB",
            fg="white",
            padx=14,
            pady=8,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._start_main_process
        )
        self.btn_run_main.pack(side=tk.LEFT, padx=3)

        self.btn_run_test = tk.Button(
            row1,
            text="🧪 安全テスト (1件のみ)",
            font=("Meiryo", 10, "bold"),
            bg="#3B82F6",
            fg="white",
            padx=12,
            pady=8,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._start_test_process
        )
        self.btn_run_test.pack(side=tk.LEFT, padx=3)

        self.btn_stop = tk.Button(
            row1,
            text="🛑 強制停止",
            font=("Meiryo", 10, "bold"),
            bg="#EF4444",
            fg="white",
            padx=12,
            pady=8,
            relief=tk.FLAT,
            state=tk.DISABLED,
            cursor="hand2",
            command=self._stop_process
        )
        self.btn_stop.pack(side=tk.RIGHT, padx=3)

        self.btn_pause = tk.Button(
            row1,
            text="⏸️ 一時停止 (ウィンドウ保持)",
            font=("Meiryo", 10, "bold"),
            bg="#F59E0B",
            fg="white",
            padx=16,
            pady=8,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._toggle_pause_process
        )
        self.btn_pause.pack(side=tk.RIGHT, padx=4)

        # 2段目: 補助ツール・パッケージ作成バー
        row2 = tk.Frame(ctrl_box)
        row2.pack(fill=tk.X, pady=(6, 2))

        self.btn_run_setup = tk.Button(
            row2,
            text="⚡ 初回セットアップ",
            font=("Meiryo", 8),
            bg="#10B981",
            fg="white",
            padx=8,
            pady=4,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._start_setup_process
        )
        self.btn_run_setup.pack(side=tk.LEFT, padx=3)

        self.btn_profile_launcher = tk.Button(
            row2,
            text="👤 Chromeプロファイル起動 (chrome_launcher)",
            font=("Meiryo", 8, "bold"),
            bg="#0284C7",
            fg="white",
            padx=10,
            pady=4,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._start_profile_launcher_process
        )
        self.btn_profile_launcher.pack(side=tk.LEFT, padx=3)

        self.btn_build_dist = tk.Button(
            row2,
            text="📦 他PC配布用パッケージ作成 (解凍するだけで動く完全版)",
            font=("Meiryo", 8, "bold"),
            bg="#8B5CF6",
            fg="white",
            padx=10,
            pady=4,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._start_build_dist_process
        )
        self.btn_build_dist.pack(side=tk.LEFT, padx=3)

        # 一時停止ステータスバナー（一時停止中に目立つ黄色・再開ボタン付き）
        self.pause_banner = tk.Frame(self.tab_run, bg="#FEF3C7", bd=1, relief=tk.SOLID, padx=10, pady=6)
        self.lbl_pause_banner = tk.Label(
            self.pause_banner,
            text="⏸️ 【一時停止中】ブラウザウィンドウを開いたまま保持しています。アカウント編集・設定確認後、「▶️ 処理を再開」ボタンを押してください。",
            font=("Meiryo", 9, "bold"),
            fg="#92400E",
            bg="#FEF3C7"
        )
        self.lbl_pause_banner.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.btn_resume_inline = tk.Button(
            self.pause_banner,
            text="▶️ 処理を再開する",
            font=("Meiryo", 9, "bold"),
            bg="#059669",
            fg="white",
            padx=12,
            pady=4,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._toggle_pause_process
        )
        self.btn_resume_inline.pack(side=tk.RIGHT, padx=4)

        # 実行オプション設定行
        self.opt_box = tk.Frame(self.tab_run, padx=4, pady=2)
        self.opt_box.pack(fill=tk.X, padx=8)

        self.var_concurrency = tk.IntVar(value=1)
        lbl_mode = tk.Label(self.opt_box, text="🪟 窓数 (1~5窓):", font=("Meiryo", 8, "bold"), fg="#1E293B")
        lbl_mode.pack(side=tk.LEFT, padx=3)
        cb_conc = ttk.Combobox(self.opt_box, textvariable=self.var_concurrency, values=[1, 2, 3, 4, 5], width=4, state="readonly")
        cb_conc.pack(side=tk.LEFT, padx=2)
        lbl_conc_sub = tk.Label(self.opt_box, text="窓 (1=超安全ローテ, 2〜5=同時並列)", font=("Meiryo", 8), fg="#475569")
        lbl_conc_sub.pack(side=tk.LEFT, padx=3)

        self.var_headless = tk.BooleanVar(value=False)
        chk_headless = tk.Checkbutton(self.opt_box, text="画面表示", variable=self.var_headless, font=("Meiryo", 8))
        chk_headless.pack(side=tk.LEFT, padx=6)

        lbl_tip = tk.Label(self.opt_box, text="※DM拒否アカウントは安全スキップされ、アカウント名.dbに自動登録されます", font=("Meiryo", 8), fg="#64748B")
        lbl_tip.pack(side=tk.RIGHT, padx=4)

        # リアルタイムログターミナル
        log_box = tk.LabelFrame(self.tab_run, text=" 📜 リアルタイム実行ログ ", font=("Meiryo", 9, "bold"), padx=6, pady=4)
        log_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        log_toolbar = tk.Frame(log_box)
        log_toolbar.pack(fill=tk.X, pady=2)

        btn_clear_log = ttk.Button(log_toolbar, text="ログをクリア", command=self._clear_log)
        btn_clear_log.pack(side=tk.RIGHT, padx=2)

        self.log_text = scrolledtext.ScrolledText(log_box, wrap=tk.WORD, font=("Consolas", 9), bg="#0F172A", fg="#F8FAFC", insertbackground="white")
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.log_text.tag_config("SUCCESS", foreground="#4ADE80")
        self.log_text.tag_config("ERROR", foreground="#F87171")
        self.log_text.tag_config("WARN", foreground="#FDE047")
        self.log_text.tag_config("INFO", foreground="#60A5FA")

        self.log("【準備完了】設定ファイルを読み込み中...")

    def _build_accounts_tab(self):
        top_bar = tk.Frame(self.tab_accounts, padx=8, pady=6)
        top_bar.pack(fill=tk.X)

        tk.Label(top_bar, text="「アカウント情報.xlsx」から自動読み込みされたアカウント一覧", font=("Meiryo", 9, "bold")).pack(side=tk.LEFT)
        ttk.Button(top_bar, text="📗 Excelを開く", command=lambda: self._open_file("アカウント情報.xlsx")).pack(side=tk.RIGHT, padx=3)
        ttk.Button(top_bar, text="🔄 再読込", command=self.reload_all_configs).pack(side=tk.RIGHT, padx=3)
        ttk.Button(top_bar, text="👤 Chromeプロファイル起動 (chrome_launcher)", command=self._start_profile_launcher_process).pack(side=tk.RIGHT, padx=3)
        ttk.Button(top_bar, text="🔑 DMパスコード設定 (初回保存)", command=self._set_passcode_dialog).pack(side=tk.RIGHT, padx=3)
        ttk.Button(top_bar, text="🌐 アカウント編集ブラウザ起動 (ウィンドウ保持)", command=self._start_edit_browser_process).pack(side=tk.RIGHT, padx=3)

        cols = ("アカウントID", "アカウントPW", "auth_token状態", "DMパスコード", "動作設定Excel", "設定モード", "ターゲット件数", "DM実行", "いいね実行", "フォロー実行", "リプライ実行", "引用RT実行")
        self.tree_acc = ttk.Treeview(self.tab_accounts, columns=cols, show="headings", height=8)
        for c in cols:
            self.tree_acc.heading(c, text=c)
            self.tree_acc.column(c, width=80, anchor=tk.CENTER)
        self.tree_acc.column("アカウントID", width=120, anchor=tk.W)
        self.tree_acc.column("アカウントPW", width=100, anchor=tk.CENTER)
        self.tree_acc.column("auth_token状態", width=95, anchor=tk.CENTER)
        self.tree_acc.column("DMパスコード", width=110, anchor=tk.CENTER)
        self.tree_acc.column("動作設定Excel", width=140, anchor=tk.W)
        self.tree_acc.column("設定モード", width=180, anchor=tk.W)
        self.tree_acc.column("ターゲット件数", width=85, anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(self.tab_accounts, orient=tk.VERTICAL, command=self.tree_acc.yview)
        self.tree_acc.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_acc.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.tree_acc.bind("<Double-1>", lambda e: self._set_passcode_dialog())

    def _set_passcode_dialog(self):
        selected = self.tree_acc.selection()
        default_acc = ""
        if selected:
            default_acc = str(self.tree_acc.item(selected[0])["values"][0]).strip().lstrip("@")
        elif self.accounts_data:
            default_acc = str(self.accounts_data[0].get("アカウントID", "")).strip().lstrip("@")
        
        acc_input = simpledialog.askstring("アカウント指定", "暗号化DMパスコードを設定するアカウントID (@不要):", initialvalue=default_acc, parent=self.root)
        if not acc_input:
            return
        clean_acc = acc_input.strip().lstrip("@")
        
        cur_p = ""
        try:
            from db_manager import get_saved_passcodes
            cur_p = get_saved_passcodes(self.base_dir).get(clean_acc, "")
        except Exception:
            pass
        
        pass_input = simpledialog.askstring("暗号化DMパスコード設定", f"アカウント @{clean_acc} の暗号化DMパスコード（数字4桁）を入力してください:\n\n※一度入力すると自動保存され、次回以降の入力は不要になります。", initialvalue=cur_p, parent=self.root)
        if pass_input is not None:
            clean_p = pass_input.strip()
            if clean_p:
                try:
                    from db_manager import save_account_passcode, update_excel_passcode
                    save_account_passcode(self.base_dir, clean_acc, clean_p)
                    update_excel_passcode(self.base_dir, clean_acc, clean_p)
                    messagebox.showinfo("保存完了", f"アカウント @{clean_acc} のDMパスコードを保存しました！\n次回以降は完全自動で解除されます。", parent=self.root)
                    self.reload_all_configs()
                except Exception as e:
                    messagebox.showerror("エラー", f"保存に失敗しました: {e}", parent=self.root)

    def _build_targets_tab(self):
        # ターゲット＆各シート用サブタブ
        sub_nb = ttk.Notebook(self.tab_targets)
        sub_nb.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # サブタブ1: ターゲット一覧
        frame_t = ttk.Frame(sub_nb)
        sub_nb.add(frame_t, text=" 🎯 ターゲット履歴・蓄積一覧 ")
        cols_t = ("アカウント名", "ステータス", "ターゲットURL", "処理日時", "備考")
        self.tree_targets = ttk.Treeview(frame_t, columns=cols_t, show="headings")
        for c in cols_t:
            self.tree_targets.heading(c, text=c)
            self.tree_targets.column(c, width=120)
        self.tree_targets.column("ターゲットURL", width=240)
        self.tree_targets.pack(fill=tk.BOTH, expand=True)

        # サブタブ2: 検索キーワード (最新/おすすめ)
        frame_kw = ttk.Frame(sub_nb)
        sub_nb.add(frame_kw, text=" 🔍 検索キーワード ")
        cols_kw = ("検索キーワード", "検索種別")
        self.tree_kw = ttk.Treeview(frame_kw, columns=cols_kw, show="headings")
        for c in cols_kw:
            self.tree_kw.heading(c, text=c)
            self.tree_kw.column(c, width=200)
        self.tree_kw.pack(fill=tk.BOTH, expand=True)

        # サブタブ3: リプライURL & フォローURL
        frame_url = ttk.Frame(sub_nb)
        sub_nb.add(frame_url, text=" 🌐 URLリスト (リプライ/フォロー) ")
        cols_u = ("種別", "URL", "備考")
        self.tree_urls = ttk.Treeview(frame_url, columns=cols_u, show="headings")
        for c in cols_u:
            self.tree_urls.heading(c, text=c)
            self.tree_urls.column(c, width=120)
        self.tree_urls.column("URL", width=400)
        self.tree_urls.pack(fill=tk.BOTH, expand=True)

        # サブタブ4: DM内容 & リプライ内容 & プロンプト
        frame_msg = ttk.Frame(sub_nb)
        sub_nb.add(frame_msg, text=" ✉️ 送信文面＆プロンプト ")
        self.txt_messages = scrolledtext.ScrolledText(frame_msg, wrap=tk.WORD, font=("Meiryo", 9))
        self.txt_messages.pack(fill=tk.BOTH, expand=True)

    def _open_db_folder(self):
        db_dir = get_db_dir(self.base_dir)
        os.makedirs(db_dir, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(db_dir)
        else:
            subprocess.run(["xdg-open", db_dir])

    def _open_log_folder(self):
        log_dir = get_log_dir(self.base_dir)
        os.makedirs(log_dir, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(log_dir)
        else:
            subprocess.run(["xdg-open", log_dir])

    def _build_db_tab(self):
        top_bar = tk.Frame(self.tab_db, padx=8, pady=6)
        top_bar.pack(fill=tk.X)

        tk.Label(top_bar, text="💾 送信履歴 (dbフォルダ/アカウント名.DB)", font=("Meiryo", 9, "bold")).pack(side=tk.LEFT)
        
        tk.Label(top_bar, text="表示DB:", font=("Meiryo", 9)).pack(side=tk.LEFT, padx=(12, 2))
        self.cmb_db_account = ttk.Combobox(top_bar, state="readonly", width=22)
        self.cmb_db_account.pack(side=tk.LEFT, padx=2)
        self.cmb_db_account.bind("<<ComboboxSelected>>", lambda e: self._reload_db())

        ttk.Button(top_bar, text="🔄 再読込", command=self._reload_db).pack(side=tk.LEFT, padx=3)
        ttk.Button(top_bar, text="📁 dbフォルダを開く", command=self._open_db_folder).pack(side=tk.RIGHT, padx=3)
        ttk.Button(top_bar, text="📁 logフォルダを開く", command=self._open_log_folder).pack(side=tk.RIGHT, padx=3)
        ttk.Button(top_bar, text="🗑️ 選択履歴を削除", command=self._delete_selected_db).pack(side=tk.RIGHT, padx=3)

        cols_db = ("ID", "ターゲット", "使用アカウント", "アクション", "ステータス", "送信日時", "文面抜粋")
        self.tree_db = ttk.Treeview(self.tab_db, columns=cols_db, show="headings")
        for c in cols_db:
            self.tree_db.heading(c, text=c)
            self.tree_db.column(c, width=90, anchor=tk.CENTER)
        self.tree_db.column("ID", width=50)
        self.tree_db.column("ターゲット", width=130, anchor=tk.W)
        self.tree_db.column("文面抜粋", width=260, anchor=tk.W)

        self.tree_db.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

    def _build_tools_tab(self):
        box = tk.LabelFrame(self.tab_tools, text=" 🛠️ システム診断 ＆ Python不要配布EXE作成 ", font=("Meiryo", 9, "bold"), padx=14, pady=12)
        box.pack(fill=tk.X, padx=14, pady=12)

        lbl = tk.Label(box, text="本ツールを他のPCに配布したい場合、ここで「単一EXEパッケージ」を作成できます。\nビルド完了後は Python 未インストールの Windows PC でもダブルクリックだけで動作します。", font=("Meiryo", 9), justify=tk.LEFT)
        lbl.pack(anchor="w", pady=4)

        btn_build = tk.Button(box, text="🔨 Python不要の単一EXE (x_auto_dm_gui.exe) を作成", font=("Meiryo", 10, "bold"), bg="#8B5CF6", fg="white", padx=16, pady=8, relief=tk.FLAT, cursor="hand2", command=self._start_build_process)
        btn_build.pack(anchor="w", pady=8)

    # ==========================
    # 設定ファイル自動全読み込み
    # ==========================
    def reload_all_configs(self):
        if not openpyxl:
            self.log("⚠️ openpyxl がインストールされていません。「初回セットアップ実行」を行ってください。")
            return

        acc_path = os.path.join(self.base_dir, "アカウント情報.xlsx")
        name_path = os.path.join(self.base_dir, "アカウント名.xlsx")

        # 1. アカウント情報.xlsx 読込
        self.accounts_data = []
        if os.path.exists(acc_path):
            try:
                wb = openpyxl.load_workbook(acc_path, data_only=True)
                sheet = wb.active
                rows = list(sheet.iter_rows(values_only=True))
                if rows and len(rows) > 1:
                    headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(rows[0])]
                    for r in rows[1:]:
                        if any(r):
                            d = dict(zip(headers, r))
                            has_acc = any(
                                k and d[k] is not None and str(d[k]).strip() != "" and
                                any(m in str(k).replace(" ", "").replace("　", "").replace("_", "").lower() for m in ["アカウントid", "アカウント名", "ユーザー名", "アカウント", "id"])
                                for k in d
                            )
                            if has_acc:
                                self.accounts_data.append(d)
                wb.close()
            except Exception as e:
                self.log(f"⚠️ アカウント情報.xlsx 読込エラー: {e}")

        # テーブル更新
        for item in self.tree_acc.get_children():
            self.tree_acc.delete(item)
        for acc in self.accounts_data:
            acc_id_str = str(acc.get("アカウントID", "") or acc.get("アカウント名", "") or acc.get("ユーザー名", "")).strip()
            clean_acc = normalize_screen_name(acc_id_str)
            prof_dir = os.path.join(self.base_dir, "browser_profiles", sanitize_filename(clean_acc)) if clean_acc else ""
            has_profile = bool(prof_dir and os.path.isdir(prof_dir))
            
            pw_val = str(acc.get("アカウントPW", "") or acc.get("パスワード", "") or acc.get("password", "") or "").strip()
            pw_display = "設定済 (●●●●)" if pw_val else "未入力 (Profile連携)"

            if has_profile:
                has_token = "✅ profile連携済"
            elif acc.get("auth_token"):
                has_token = "設定済 (Cookie)"
            elif pw_val:
                has_token = "設定済 (PW)"
            else:
                has_token = "👤 profile連携 (推奨)"

            # アカウント専用Excel (例: wqwq1320.xlsx) から個別動作設定 (動作設定シート・全体動作パラメータ) を読込
            try:
                acc_target_path = self.config_mgr.get_target_path_for_account(clean_acc)
                acc_set = self.config_mgr.load_account_settings(clean_acc, target_path_override=acc_target_path, fallback_acc=acc)
            except Exception:
                acc_target_path = ""
                acc_set = {}

            passcode_val = str(acc_set.get("dm_passcode", "") or "").strip()
            if not passcode_val:
                for k, v in acc.items():
                    if k and v is not None and str(v).strip():
                        k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
                        if any(m in k_clean for m in ["dmパスコード", "パスコード", "暗号化パスコード", "暗号化キー", "pin", "2fa"]):
                            raw = str(v).strip()
                            passcode_val = raw[:-2] if raw.endswith(".0") and raw[:-2].isdigit() else raw
                            break

            # 保存済みJSONからも確認（初回入力で記憶された値）
            if not passcode_val:
                try:
                    from db_manager import get_saved_passcodes
                    acc_clean = normalize_screen_name(str(acc.get("アカウントID", "") or acc.get("アカウント名", "")).strip())
                    saved_dict = get_saved_passcodes(self.base_dir)
                    passcode_val = saved_dict.get(acc_clean, "") or saved_dict.get(str(acc.get("アカウントID", "") or acc.get("アカウント名", "")).strip(), "")
                except Exception:
                    pass

            passcode_display = f"登録済 ({passcode_val})" if passcode_val else "未設定 (初回時に自動保存)"

            excel_name = os.path.basename(acc_target_path) if acc_target_path and os.path.exists(acc_target_path) else "アカウント名.xlsx"
            acts = acc_set.get("actions", {})
            enabled_modes = []
            for a_k, a_lbl in [("dm", "DM"), ("like", "いいね"), ("follow", "フォロー"), ("reply", "リプライ"), ("quote", "引用"), ("reply_ai", "リプライAI"), ("quote_ai", "引用AI")]:
                a_info = acts.get(a_k, {})
                if a_info.get("enabled") and a_info.get("mode"):
                    enabled_modes.append(f"{a_lbl}:{a_info.get('mode')}")
            target_mode_display = " / ".join(enabled_modes) if enabled_modes else (acc_set.get("target_mode") or "アクション別設定")
            t_cnt = acc_set.get("target_count", 15)

            is_dm = "ON" if acts.get("dm", {}).get("enabled") else "OFF"
            is_like = "ON" if acts.get("like", {}).get("enabled") else "OFF"
            is_follow = "ON" if acts.get("follow", {}).get("enabled") else "OFF"
            is_reply = "ON" if acts.get("reply", {}).get("enabled") or acts.get("reply_ai", {}).get("enabled") else "OFF"
            is_quote = "ON" if acts.get("quote", {}).get("enabled") or acts.get("quote_ai", {}).get("enabled") else "OFF"

            self.tree_acc.insert("", tk.END, values=(
                acc.get("アカウント名", "") or acc.get("アカウントID", ""),
                pw_display,
                has_token,
                passcode_display,
                excel_name,
                target_mode_display,
                f"{t_cnt}件",
                is_dm,
                is_like,
                is_follow,
                is_reply,
                is_quote,
            ))

        # 2. アカウント情報に登録されているアカウント名 (例: wqwq1320.xlsx) を最優先探索 & 自動複製
        primary_aid = ""
        for acc in self.accounts_data:
            aid = normalize_screen_name(str(acc.get("アカウントID", "") or acc.get("アカウント名", "") or acc.get("ユーザー名", "")).strip())
            if not aid:
                for k, v in acc.items():
                    if k and v and any(m in str(k).replace(" ", "").replace("　", "").replace("_", "").lower() for m in ["アカウントid", "アカウント名", "ユーザー名", "アカウント", "id"]):
                        aid = normalize_screen_name(str(v).strip())
                        if aid: break
            if aid:
                primary_aid = aid
                break

        template_xlsx = os.path.join(self.base_dir, "アカウント名.xlsx")
        if not os.path.exists(template_xlsx):
            template_xlsx = os.path.join(self.base_dir, "アカウント名情報.xlsx")

        # もしアカウント専用Excel ({primary_aid}.xlsx) が存在せず、テンプレート (アカウント名.xlsx) が存在する場合は自動複製
        if primary_aid and primary_aid != "アカウント名":
            primary_path = os.path.join(self.base_dir, f"{primary_aid}.xlsx")
            if not os.path.exists(primary_path) and os.path.exists(template_xlsx):
                try:
                    import shutil
                    shutil.copy2(template_xlsx, primary_path)
                except Exception:
                    pass

        target_name_candidates = []
        if primary_aid:
            target_name_candidates.extend([
                os.path.join(self.base_dir, f"{primary_aid}.xlsx"),
                os.path.join(self.base_dir, f"@{primary_aid}.xlsx"),
                os.path.join(self.base_dir, f"アカウント名_{primary_aid}.xlsx"),
                os.path.join(self.base_dir, f"アカウント_{primary_aid}.xlsx"),
            ])
        for acc in self.accounts_data:
            aid = normalize_screen_name(str(acc.get("アカウントID", "") or acc.get("アカウント名", "") or acc.get("ユーザー名", "")).strip())
            if aid:
                target_name_candidates.extend([
                    os.path.join(self.base_dir, f"{aid}.xlsx"),
                    os.path.join(self.base_dir, f"@{aid}.xlsx"),
                    os.path.join(self.base_dir, f"アカウント名_{aid}.xlsx"),
                    os.path.join(self.base_dir, f"アカウント_{aid}.xlsx"),
                ])
        # フォルダ内スキャン (大文字小文字・全角半角無視マッチ)
        if os.path.exists(self.base_dir):
            try:
                for fname in os.listdir(self.base_dir):
                    if fname.lower().endswith(".xlsx") and not fname.startswith("~$") and fname != "アカウント情報.xlsx":
                        stem = os.path.splitext(fname)[0].lstrip("@").strip().lower()
                        for acc in self.accounts_data:
                            aid = normalize_screen_name(str(acc.get("アカウントID", "") or acc.get("アカウント名", "") or "")).lower()
                            if aid and stem == aid:
                                target_name_candidates.insert(0, os.path.join(self.base_dir, fname))
            except Exception:
                pass

        target_name_candidates.extend([
            os.path.join(self.base_dir, "アカウント名.xlsx"),
            os.path.join(self.base_dir, "アカウント名情報.xlsx"),
            os.path.join(self.base_dir, "ターゲット.xlsx"),
            os.path.join(self.base_dir, "ターゲットリスト.xlsx"),
        ])

        name_path = os.path.join(self.base_dir, "アカウント名.xlsx")
        for cand in target_name_candidates:
            if cand and os.path.exists(cand):
                name_path = cand
                break

        self.current_target_file = name_path
        self.current_target_file_name = os.path.basename(name_path)
        if hasattr(self, "btn_name"):
            self.btn_name.config(text=f"📘 {self.current_target_file_name}", command=lambda: self._open_file(self.current_target_file_name))

        self.targets_data = []
        self.keywords_data = []
        self.reply_urls_data = []
        self.follow_urls_data = []
        self.dm_templates = []
        self.prompt_setting = ""

        if os.path.exists(name_path):
            try:
                wb = openpyxl.load_workbook(name_path, data_only=True)

                # ターゲット履歴
                target_sheet = wb["ターゲット履歴"] if "ターゲット履歴" in wb.sheetnames else wb.active
                t_rows = list(target_sheet.iter_rows(values_only=True))
                if t_rows and len(t_rows) > 1:
                    for r in t_rows[1:]:
                        if r and r[0]:
                            raw_t_name = str(r[0])
                            c_t_name = normalize_screen_name(raw_t_name)
                            if not c_t_name and len(r) > 1 and r[1]:
                                c_t_name = normalize_screen_name(str(r[1]))
                            disp_name = f"@{c_t_name}" if c_t_name else raw_t_name
                            t_url = f"https://x.com/{c_t_name}" if c_t_name else (str(r[1]) if len(r) > 1 and r[1] else "")
                            self.targets_data.append({
                                "name": disp_name,
                                "url": t_url,
                                "status": str(r[3]) if len(r) > 3 and r[3] else "未送信",
                                "date": str(r[4]) if len(r) > 4 and r[4] else "",
                                "note": str(r[5]) if len(r) > 5 and r[5] else ""
                            })

                # 検索キーワード
                if "検索キーワード" in wb.sheetnames:
                    kw_sheet = wb["検索キーワード"]
                    for r in kw_sheet.iter_rows(min_row=2, values_only=True):
                        if r and r[0]:
                            self.keywords_data.append({"keyword": str(r[0]), "type": str(r[1]) if len(r) > 1 and r[1] else "最新"})

                # URLリスト_リプライ
                if "URLリスト_リプライ" in wb.sheetnames:
                    for r in wb["URLリスト_リプライ"].iter_rows(min_row=2, values_only=True):
                        if r and r[0]:
                            self.reply_urls_data.append(str(r[0]))

                # URLリスト_フォロー
                if "URLリスト_フォロー" in wb.sheetnames:
                    for r in wb["URLリスト_フォロー"].iter_rows(min_row=2, values_only=True):
                        if r and r[0]:
                            self.follow_urls_data.append(str(r[0]))

                # DM内容
                if "DM内容" in wb.sheetnames:
                    for r in wb["DM内容"].iter_rows(min_row=2, values_only=True):
                        if r and r[0]:
                            self.dm_templates.append(str(r[0]))

                # リプライ内容
                if "リプライ内容" in wb.sheetnames:
                    for r in wb["リプライ内容"].iter_rows(min_row=2, values_only=True):
                        if r and r[0]:
                            self.reply_templates.append(str(r[0]))

                # 引用リツイート内容
                if "引用リツイート内容" in wb.sheetnames:
                    for r in wb["引用リツイート内容"].iter_rows(min_row=2, values_only=True):
                        if r and r[0]:
                            self.quote_templates.append(str(r[0]))

                # プロンプト設定
                if "プロンプト設定" in wb.sheetnames:
                    for r in wb["プロンプト設定"].iter_rows(min_row=2, values_only=True):
                        if r and r[0]:
                            self.prompt_setting = str(r[0])
                            break

                wb.close()
            except Exception as e:
                self.log(f"⚠️ アカウント名.xlsx 読込エラー: {e}")

        # サブテーブル反映
        for item in self.tree_targets.get_children():
            self.tree_targets.delete(item)
        for t in self.targets_data:
            self.tree_targets.insert("", tk.END, values=(t["name"], t["status"], t["url"], t["date"], t["note"]))

        for item in self.tree_kw.get_children():
            self.tree_kw.delete(item)
        for kw in self.keywords_data:
            self.tree_kw.insert("", tk.END, values=(kw["keyword"], kw["type"]))

        for item in self.tree_urls.get_children():
            self.tree_urls.delete(item)
        for u in self.reply_urls_data:
            self.tree_urls.insert("", tk.END, values=("リプライURL", u, ""))
        for u in self.follow_urls_data:
            self.tree_urls.insert("", tk.END, values=("フォローURL", u, ""))

        # 文面テキスト表示
        self.txt_messages.delete("1.0", tk.END)
        self.txt_messages.insert(tk.END, "=== ✉️ DM内容テンプレート ===\n")
        for i, dm in enumerate(self.dm_templates, 1):
            self.txt_messages.insert(tk.END, f"[{i}] {dm}\n")
        self.txt_messages.insert(tk.END, "\n=== 💬 リプライ内容テンプレート ===\n")
        for i, rep in enumerate(self.reply_templates, 1):
            self.txt_messages.insert(tk.END, f"[{i}] {rep}\n")
        self.txt_messages.insert(tk.END, "\n=== 🔁 引用リツイート内容テンプレート ===\n")
        for i, q in enumerate(self.quote_templates, 1):
            self.txt_messages.insert(tk.END, f"[{i}] {q}\n")
        self.txt_messages.insert(tk.END, "\n=== 🤖 プロンプト設定 ===\n")
        self.txt_messages.insert(tk.END, f"{self.prompt_setting or '未設定'}\n")

        # 3. データベース履歴読込
        self._reload_db()

        # 4. 最上部サマリーラベル更新
        if self.accounts_data:
            first_acc = self.accounts_data[0]
            acc_id = first_acc.get("アカウントID", "") or first_acc.get("アカウント名", "未設定")
            clean_first = normalize_screen_name(str(acc_id))
            try:
                first_path = self.config_mgr.get_target_path_for_account(clean_first)
                first_set = self.config_mgr.load_account_settings(clean_first, target_path_override=first_path, fallback_acc=first_acc)
            except Exception:
                first_set = {}
            first_acts = first_set.get("actions", {})
            first_modes = [f"{k}:{v.get('mode')}" for k, v in first_acts.items() if v.get("enabled") and v.get("mode")]
            mode = " / ".join(first_modes) if first_modes else "アクション別モード設定"
            if len(mode) > 42:
                mode = mode[:42] + "..."
            try:
                from main import extract_target_interval_seconds, extract_account_interval_seconds
                tgt_min_s, tgt_max_s = extract_target_interval_seconds(first_acc, 60.0, 150.0)
                acc_min_s, acc_max_s = extract_account_interval_seconds(first_acc, 300.0, 900.0)
            except Exception:
                tgt_min_s, tgt_max_s = 60.0, 150.0
                acc_min_s, acc_max_s = 300.0, 900.0

            pending = sum(1 for t in self.targets_data if t["status"] in ["未送信", "", "None"])
            self.lbl_summary_acc.config(text=f"アカウント: @{acc_id}")
            self.lbl_summary_mode.config(text=f"モード: {mode}")
            self.lbl_summary_interval.config(text=f"実行間隔: {acc_min_s:.0f}〜{acc_max_s:.0f}s | 送信待機: {tgt_min_s:.0f}〜{tgt_max_s:.0f}s")
            self.lbl_summary_target.config(text=f"ターゲット: {len(self.targets_data)}件 (未送信: {pending}件)")
        else:
            self.lbl_summary_acc.config(text="アカウント: 未設定")

        # 読込完了メッセージ (登録アカウントに応じたExcelファイル名を動的表示)
        if not hasattr(self, "_ready_logged_file") or self._ready_logged_file != self.current_target_file_name:
            self._ready_logged_file = self.current_target_file_name
            self.log(f"【準備完了】「アカウント情報.xlsx」「{self.current_target_file_name}」を読み込みました。")
            self.log("「本番自動実行を開始」または「1件のみ安全テスト」をクリックしてください。\n")

    def _reload_db(self):
        for item in self.tree_db.get_children():
            self.tree_db.delete(item)

        # dbフォルダ内の全DB一覧を取得してコンボボックスを更新
        db_dir = get_db_dir(self.base_dir)
        db_files = []
        if os.path.exists(db_dir):
            for f in os.listdir(db_dir):
                if f.lower().endswith(".db"):
                    db_files.append(f)
        db_files.sort()

        root_db = "アカウント名.db"
        if os.path.exists(os.path.join(self.base_dir, root_db)) and root_db not in db_files:
            db_files.append(root_db)

        combo_values = ["全アカウント (統合表示)"] + db_files
        self.cmb_db_account["values"] = combo_values
        selected = self.cmb_db_account.get()
        if not selected or selected not in combo_values:
            selected = combo_values[0]
            self.cmb_db_account.set(selected)

        # 読み込むDBファイルのリスト
        targets_to_read = []
        if selected == "全アカウント (統合表示)":
            for f in db_files:
                targets_to_read.append(os.path.join(db_dir, f) if f != root_db else os.path.join(self.base_dir, f))
        else:
            targets_to_read.append(os.path.join(db_dir, selected) if selected != root_db else os.path.join(self.base_dir, selected))

        all_rows = []
        for path in targets_to_read:
            if not os.path.exists(path):
                continue
            try:
                conn = sqlite3.connect(path)
                cursor = conn.cursor()
                cursor.execute("SELECT id, target_screen_name, account_id_used, action_type, status, created_at, snippet FROM sent_history ORDER BY id DESC LIMIT 200")
                rows = cursor.fetchall()
                conn.close()
                for r in rows:
                    all_rows.append(r)
            except Exception as e:
                self.log(f"⚠️ DB読込警告 ({os.path.basename(path)}): {e}")

        # 日時降順でソートして表示
        for r in all_rows[:300]:
            self.tree_db.insert("", tk.END, values=(r[0], r[1], r[2], r[3], r[4], r[5], r[6]))

    def _delete_selected_db(self):
        selected = self.tree_db.selection()
        if not selected:
            messagebox.showinfo("案内", "削除したい履歴を選択してください。")
            return
        rec_id = self.tree_db.item(selected[0])["values"][0]
        db_dir = get_db_dir(self.base_dir)
        selected_file = self.cmb_db_account.get()
        if selected_file and selected_file != "全アカウント (統合表示)":
            db_path = os.path.join(db_dir, selected_file)
            targets_to_del = [db_path]
        else:
            targets_to_del = [os.path.join(db_dir, f) for f in os.listdir(db_dir) if f.lower().endswith(".db")]
            if os.path.exists(os.path.join(self.base_dir, "アカウント名.db")):
                targets_to_del.append(os.path.join(self.base_dir, "アカウント名.db"))
        
        deleted = False
        for path in targets_to_del:
            try:
                conn = sqlite3.connect(path)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM sent_history WHERE id = ?", (rec_id,))
                if cursor.rowcount > 0:
                    conn.commit()
                    deleted = True
                conn.close()
            except Exception:
                pass
        
        if deleted:
            self._reload_db()
            self.log(f"🗑️ 履歴ID {rec_id} を削除しました（再送信可能になりました）。")
        else:
            messagebox.showwarning("案内", "該当レコードが見つかりませんでした。")

    def _start_main_process(self):
        script = os.path.join(self.base_dir, "main.py")
        self._run_script_in_thread(script, "【本番自動実行】")

    def _start_test_process(self):
        script = os.path.join(self.base_dir, "test_single.py")
        self._run_script_in_thread(script, "【単体テスト】")

    def _start_setup_process(self):
        self._run_command_in_thread([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], "【pip インストール】", next_cmd=[sys.executable, "-m", "playwright", "install", "chromium"])

    def _start_build_dist_process(self):
        script = os.path.join(self.base_dir, "build_dist_package.py")
        if not os.path.exists(script):
            messagebox.showinfo("案内", "build_dist_package.py が見つかりません。")
            return
        self._run_command_in_thread([sys.executable, script], "【他PC配布用パッケージ自動作成】")

    def _start_build_process(self):
        self._start_build_dist_process()

    def _run_script_in_thread(self, script_path, label):
        if self.running_process:
            messagebox.showinfo("実行中", "すでにプロセスが実行中です。")
            return

        self._set_ui_running(True)
        self.log(f"\n{'='*60}\n>>> {label} を開始します...\n{'='*60}")

        def worker():
            try:
                stop_file = os.path.join(self.base_dir, ".stop")
                if os.path.exists(stop_file):
                    try:
                        os.remove(stop_file)
                    except Exception:
                        pass

                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"
                env["LAUNCHER_PID"] = str(os.getpid())
                env["TOOL_MAX_CONCURRENT"] = str(getattr(self, "var_concurrency", tk.IntVar(value=1)).get())
                
                # 他PC実行（PyInstaller EXE化環境）自動判定
                if getattr(sys, 'frozen', False):
                    # 外部python.exeが存在しない他PCでは、自身のEXEを引数付きでバックグラウンド実行
                    if "test_single.py" in script_path:
                        cmd = [sys.executable, "--cli-test"]
                    elif "edit_account.py" in script_path:
                        cmd = [sys.executable, "--cli-edit"]
                    elif "profile_launcher.py" in script_path or "profile_luncher.py" in script_path:
                        cmd = [sys.executable, "--cli-profile"]
                    else:
                        cmd = [sys.executable, "--cli-main"]
                else:
                    python_exe = sys.executable
                    cmd = [python_exe, script_path]

                self.running_process = subprocess.Popen(
                    cmd,
                    cwd=self.base_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    env=env
                )

                for line in self.running_process.stdout:
                    self.log(line.rstrip())

                self.running_process.wait()
                self.log(f"\n<<< {label} が終了しました。\n")
            except Exception as e:
                self.log(f"❌ エラーが発生しました: {e}", tag="ERROR")
            finally:
                self.running_process = None
                self.root.after(0, lambda: self._set_ui_running(False))
                self.root.after(500, self.reload_all_configs)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _run_command_in_thread(self, cmd, label, next_cmd=None):
        if self.running_process:
            messagebox.showinfo("実行中", "すでにプロセスが実行中です。")
            return

        self._set_ui_running(True)
        self.log(f"\n{'='*60}\n>>> {label} を実行中...\n{'='*60}")

        def worker():
            try:
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"
                self.running_process = subprocess.Popen(
                    cmd,
                    cwd=self.base_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    env=env
                )
                for line in self.running_process.stdout:
                    self.log(line.rstrip())
                self.running_process.wait()

                if next_cmd:
                    self.log(f"\n>>> 次の処理を実行中: {' '.join(next_cmd)} ...")
                    self.running_process = subprocess.Popen(
                        next_cmd,
                        cwd=self.base_dir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                        env=env
                    )
                    for line in self.running_process.stdout:
                        self.log(line.rstrip())
                    self.running_process.wait()

                self.log(f"\n<<< {label} が正常に完了しました！\n", tag="SUCCESS")
            except Exception as e:
                self.log(f"❌ エラー: {e}", tag="ERROR")
            finally:
                self.running_process = None
                self.root.after(0, lambda: self._set_ui_running(False))
                self.root.after(500, self.reload_all_configs)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _toggle_pause_process(self):
        pause_file = os.path.join(self.base_dir, ".pause")
        if os.path.exists(pause_file):
            try:
                os.remove(pause_file)
            except Exception:
                pass
            self.btn_pause.config(text="⏸️ 一時停止 (ウィンドウ保持)", bg="#F59E0B")
            try:
                self.pause_banner.pack_forget()
            except Exception:
                pass
            self.log("▶️ 【処理再開】一時停止を解除しました。自動実行を再開します。", tag="SUCCESS")
        else:
            try:
                with open(pause_file, "w", encoding="utf-8") as f:
                    f.write("paused")
            except Exception as e:
                self.log(f"⚠️ 一時停止フラグ作成エラー: {e}", tag="ERROR")
            self.btn_pause.config(text="▶️ 処理を再開 (編集完了)", bg="#10B981")
            try:
                self.pause_banner.pack(fill=tk.X, padx=8, pady=(2, 4), before=self.opt_box)
            except Exception:
                pass
            self.log("⏸️ 【一時停止】ブラウザウィンドウを開いたまま待機状態に入りました。", tag="WARN")
            self.log("   ※ アカウント編集やプロフィールの確認、設定変更を行ってください。")
            self.log("   ※ 完了したら「▶️ 処理を再開 (編集完了)」ボタンをクリックすると処理を再開します。")

    def _start_edit_browser_process(self):
        script = os.path.join(self.base_dir, "edit_account.py")
        if not os.path.exists(script):
            messagebox.showinfo("案内", "edit_account.py が見つかりません。")
            return
        self._run_script_in_thread(script, "【アカウント編集用ブラウザ起動】")

    def _start_profile_launcher_process(self):
        """正規 Chrome カスタムプロファイル起動ツール (chrome_launcher / profile_launcher) を実行"""
        exe_launcher = os.path.join(self.base_dir, "chrome_launcher.exe")
        script = ""
        for cand in ("chrome_launcher.py", "profile_launcher.py", "profile_luncher.py"):
            p = os.path.join(self.base_dir, cand)
            if os.path.exists(p):
                script = p
                break
        bat_script = ""
        for cand in ("chrome_launcher.bat", "03_Chromeプロファイル起動_chrome_launcher.bat", "profile_launcher.bat", "profile_luncher.bat"):
            p = os.path.join(self.base_dir, cand)
            if os.path.exists(p):
                bat_script = p
                break

        default_name = ""
        try:
            if hasattr(self, "accounts_data") and self.accounts_data:
                default_name = normalize_screen_name(str(self.accounts_data[0].get("アカウントID", "")))
        except Exception:
            pass

        prof_name = simpledialog.askstring(
            "Chrome カスタムプロファイル起動 (chrome_launcher)",
            "作成・使用するプロファイル名を入力してください:\n(例: wqwq1320 / 空白のまま起動すると黒いコンソール画面で入力可能)",
            initialvalue=default_name,
            parent=self.root
        )
        if prof_name is None:
            return

        prof_name = prof_name.strip()
        if os.path.exists(exe_launcher):
            if prof_name:
                subprocess.Popen(f'start cmd /k ""{exe_launcher}" "{prof_name}""', shell=True, cwd=self.base_dir)
            else:
                subprocess.Popen(f'start cmd /k "{exe_launcher}"', shell=True, cwd=self.base_dir)
            return

        if getattr(sys, 'frozen', False):
            exe_path = sys.executable
            if prof_name:
                subprocess.Popen(f'start cmd /k ""{exe_path}" --cli-profile "{prof_name}""', shell=True, cwd=self.base_dir)
            else:
                subprocess.Popen(f'start cmd /k ""{exe_path}" --cli-profile"', shell=True, cwd=self.base_dir)
            return

        if prof_name:
            if bat_script:
                subprocess.Popen(f'start cmd /k ""{bat_script}" "{prof_name}""', shell=True, cwd=self.base_dir)
            elif script:
                subprocess.Popen(f'start cmd /k python "{script}" "{prof_name}"', shell=True, cwd=self.base_dir)
            else:
                messagebox.showerror("エラー", "chrome_launcher.py または chrome_launcher.bat が見つかりません。")
        else:
            if bat_script:
                subprocess.Popen(f'start cmd /k "{bat_script}"', shell=True, cwd=self.base_dir)
            elif script:
                subprocess.Popen(f'start cmd /k python "{script}"', shell=True, cwd=self.base_dir)
            else:
                messagebox.showerror("エラー", "chrome_launcher が見つかりません。")

    def _kill_process_tree(self, proc):
        """プロセスおよびその配下の全子プロセス（Playwright / Chrome / Node等）を根こそぎ完全終了"""
        try:
            stop_file = os.path.join(self.base_dir, ".stop")
            with open(stop_file, "w", encoding="utf-8") as f:
                f.write(str(time.time()))
        except Exception:
            pass

        if not proc:
            return
        try:
            pid = proc.pid
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=5)
            else:
                proc.terminate()
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def on_window_close(self):
        """アプリ終了ハンドラー: ウィンドウを閉じたら裏で動いている全処理・ブラウザ・サブプロセスを確実に全終了"""
        try:
            stop_file = os.path.join(self.base_dir, ".stop")
            try:
                with open(stop_file, "w", encoding="utf-8") as f:
                    f.write("stop")
            except Exception:
                pass
            if self.running_process:
                self._kill_process_tree(self.running_process)
                self.running_process = None
            pause_file = os.path.join(self.base_dir, ".pause")
            if os.path.exists(pause_file):
                try:
                    os.remove(pause_file)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass
            os._exit(0)

    def _stop_process(self):
        pause_file = os.path.join(self.base_dir, ".pause")
        if os.path.exists(pause_file):
            try:
                os.remove(pause_file)
            except Exception:
                pass
        stop_file = os.path.join(self.base_dir, ".stop")
        try:
            with open(stop_file, "w", encoding="utf-8") as f:
                f.write("stop")
        except Exception:
            pass
        if self.running_process:
            self._kill_process_tree(self.running_process)
            self.running_process = None
            self.log("\n🛑 プロセスおよびブラウザを完全に強制終了しました。", tag="WARN")
            self._set_ui_running(False)

    def _set_ui_running(self, running: bool):
        state_btn = tk.DISABLED if running else tk.NORMAL
        state_stop = tk.NORMAL if running else tk.DISABLED
        self.btn_run_main.config(state=state_btn)
        self.btn_run_test.config(state=state_btn)
        self.btn_run_setup.config(state=state_btn)
        self.btn_stop.config(state=state_stop)
        
        # 一時停止ボタンは常に有効（待機中もクリック可能で、事前に一時停止モード待機をセット可能）
        pause_file = os.path.join(self.base_dir, ".pause")
        is_paused = os.path.exists(pause_file)
        if is_paused:
            self.btn_pause.config(state=tk.NORMAL, text="▶️ 処理を再開 (編集完了)", bg="#10B981")
            try:
                self.pause_banner.pack(fill=tk.X, padx=8, pady=(2, 4), before=self.opt_box)
            except Exception:
                pass
        else:
            self.btn_pause.config(state=tk.NORMAL, text="⏸️ 一時停止 (ウィンドウ保持)", bg="#F59E0B")
            try:
                self.pause_banner.pack_forget()
            except Exception:
                pass

        if not running and not is_paused:
            if os.path.exists(pause_file):
                try:
                    os.remove(pause_file)
                except Exception:
                    pass

    def log(self, message, tag=None):
        if not tag:
            if "✅" in message or "SUCCESS" in message or "成功" in message:
                tag = "SUCCESS"
            elif "❌" in message or "ERROR" in message or "失敗" in message:
                tag = "ERROR"
            elif "⚠️" in message or "WARN" in message or "スキップ" in message:
                tag = "WARN"
            elif ">>>" in message or "ℹ️" in message:
                tag = "INFO"

        if tag:
            self.log_text.insert(tk.END, message + "\n", tag)
        else:
            self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    def _clear_log(self):
        self.log_text.delete("1.0", tk.END)

    def _open_file(self, filename):
        path = os.path.join(self.base_dir, filename)
        if os.path.exists(path):
            os.startfile(path)
        else:
            messagebox.showwarning("ファイル未発見", f"{filename} が見つかりません。")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli-main":
        import main
        main.main()
        sys.exit(0)
    elif len(sys.argv) > 1 and sys.argv[1] == "--cli-test":
        import test_single
        test_single.main()
        sys.exit(0)
    elif len(sys.argv) > 1 and sys.argv[1] == "--cli-edit":
        import edit_account
        edit_account.main()
        sys.exit(0)
    elif len(sys.argv) > 1 and sys.argv[1] == "--cli-profile":
        try:
            import chrome_launcher as _prof_mod
        except ImportError:
            import profile_launcher as _prof_mod
        if len(sys.argv) > 2:
            sys.argv = [sys.argv[0]] + sys.argv[2:]
        else:
            sys.argv = [sys.argv[0]]
        _prof_mod.main()
        sys.exit(0)
    else:
        try:
            root = tk.Tk()
            app = AppLauncherGUI(root)
            root.mainloop()
        except Exception as e:
            import traceback
            err_trace = traceback.format_exc()
            try:
                with open("launch_error.log", "w", encoding="utf-8") as f:
                    f.write(err_trace)
            except Exception:
                pass
            try:
                messagebox.showerror(
                    "起動エラー",
                    f"アプリケーション起動中にエラーが発生しました:\n\n{e}\n\n※ 詳細は launch_error.log または「【エラー診断】起動テスト_画面が消える時はこれ.bat」で確認できます。"
                )
            except Exception:
                pass
            sys.exit(1)
