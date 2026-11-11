# -*- coding: utf-8 -*-
"""
X(Twitter) 自動DM・リプライ・送信ツール Pro
メイン実行エントリポイント
Windows環境 (デスクトップ配下 x_auto_dm_tool フォルダ) 想定
"""

import os
import sys
import re
import time
import random
import traceback
import threading
import concurrent.futures
from datetime import datetime

# Windows CP932 / Emoji UnicodeEncodeError 対策 (標準入出力をUTF-8化)
if sys.platform == "win32":
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config_manager import ConfigManager
from db_manager import (
    DatabaseManager,
    get_db_dir,
    get_account_db_path,
    get_log_dir,
    get_account_log_path,
    AccountLogger,
    sanitize_filename
)
from browser_controller import XBrowserController

def get_base_dir() -> str:
    """PyInstaller EXE化実行環境および通常Python実行環境の両方で、ルート実行ディレクトリを安全に取得"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

PAUSE_FILE = os.path.join(get_base_dir(), ".pause")
STOP_FILE = os.path.join(get_base_dir(), ".stop")

def check_app_termination(base_dir: str = "", browser_ctrl = None) -> bool:
    """アプリ終了（ウィンドウ閉鎖または強制停止命令）を検知した場合、ブラウザを安全に閉じて即座に強制終了"""
    base = base_dir or get_base_dir()
    should_stop = False

    # 1. 停止ファイル (.stop) 検知
    stop_file = os.path.join(base, ".stop")
    if os.path.exists(stop_file):
        should_stop = True

    # 2. 親プロセス (GUIランチャー) 消失検知
    launcher_pid_str = os.environ.get("LAUNCHER_PID", "").strip()
    if not should_stop and launcher_pid_str and launcher_pid_str.isdigit():
        try:
            l_pid = int(launcher_pid_str)
            if sys.platform == "win32":
                import ctypes
                SYNCHRONIZE = 0x00100000
                process = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, l_pid)
                if not process:
                    should_stop = True
                else:
                    ctypes.windll.kernel32.CloseHandle(process)
            else:
                try:
                    os.kill(l_pid, 0)
                except OSError:
                    should_stop = True
        except Exception:
            pass

    # 3. stdout パイプ閉鎖検知 (親プロセス終了時にパイプ切断)
    if not should_stop and launcher_pid_str:
        try:
            if sys.stdout is None or sys.stdout.closed:
                should_stop = True
        except Exception:
            pass

    if should_stop:
        try:
            print("🛑 アプリ画面の閉鎖または停止命令を検知しました。裏の実行プロセスおよびブラウザを即座に完全停止します...", flush=True)
        except Exception:
            pass
        if browser_ctrl:
            try:
                browser_ctrl.close()
            except Exception:
                pass
        os._exit(0)
    return False

log_lock = threading.Lock()
_thread_local = threading.local()

def set_current_logger(logger):
    _thread_local.logger = logger

def get_current_logger():
    return getattr(_thread_local, 'logger', None)

def log(msg: str, logger=None):
    check_app_termination(get_base_dir())
    active_logger = logger or get_current_logger()
    with log_lock:
        if active_logger:
            active_logger.log(msg)
        else:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                print(f"[{now}] {msg}", flush=True)
            except (BrokenPipeError, OSError):
                check_app_termination(get_base_dir())
            except Exception:
                try:
                    enc = sys.stdout.encoding or "utf-8"
                    safe_msg = msg.encode(enc, errors="replace").decode(enc)
                    print(f"[{now}] {safe_msg}", flush=True)
                except Exception:
                    pass

def check_pause_state(stage: str = "", browser_ctrl = None):
    """
    一時停止フラグ (.pause) が存在する場合、ブラウザ画面を閉じることなく開いたまま一時停止する。
    アカウント編集や確認を行いたいユーザー向けの機能。
    """
    check_app_termination(get_base_dir(), browser_ctrl)
    if os.path.exists(PAUSE_FILE):
        log("")
        log(f"⏸️ 【一時停止中】ブラウザ画面を開いたまま処理を一時停止しています ({stage})。")
        log("   💡 アカウントの編集・確認や設定変更を行ってください（※ブラウザウィンドウは開いたまま維持されます）。")
        log("   ▶️ 編集完了後、GUIの「▶️ 処理を再開」ボタンを押すか、.pause ファイルを削除すると自動で再開します。")
        while os.path.exists(PAUSE_FILE):
            check_app_termination(get_base_dir(), browser_ctrl)
            time.sleep(0.5)
        log("▶️ 【処理を再開しました】一時停止が解除されたため、次の処理を継続します。")
        log("")

def auto_rectify_interval(min_val: float, max_val: float) -> tuple[float, float]:
    """最小値と最大値が逆転していた場合に自動で整流する"""
    if min_val > max_val:
        log(f"⚠️ [警告] 待機間隔の最小値({min_val}秒)が最大値({max_val}秒)より大きいため、自動入れ替え整流を行いました。")
        return max_val, min_val
    return min_val, max_val

def extract_target_interval_seconds(acc_dict, default_min: float = 60.0, default_max: float = 120.0) -> tuple[float, float]:
    """
    Excel「アカウント情報.xlsx」から【ターゲット間BAN対策ランダムインターバル】(1件送信ごとの待機秒数) を確実に抽出
    優先探索カラム名:
    - 「ランダムインターバル最小(秒)」「ランダムインターバル最大(秒)」
    - 「BAN対策ランダムインターバル最小(秒)」「BAN対策ランダムインターバル最大(秒)」
    - 「ランダムインターバル最小」「ランダムインターバル最大」
    - 「送信間隔最小(秒)」「送信間隔最大(秒)」「送信最小」「送信最大」
    - 「BAN対策最小」「ターゲット待機最小」「target_interval_min」「target_interval_max」
    - 単一カラムでの「60-120」「60〜150」等の範囲指定
    ※ もし上記がなく「実行間隔最短(秒)」のみでかつ値が300秒以下の場合はフォールバックとして採用
    """
    if not isinstance(acc_dict, dict):
        return auto_rectify_interval(default_min, default_max)

    found_min = None
    found_max = None

    # 1. 単一列での「60〜120」「60-150」等の範囲入力チェック
    for k, val in acc_dict.items():
        if not k or val is None or str(val).strip() == "":
            continue
        k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
        if any(w in k_clean for w in ["ランダムインターバル", "ban対策", "送信間隔", "送信待機", "targetinterval"]):
            val_str = str(val).strip()
            match = re.search(r'(d+(?:.d+)?)s*(?:〜|~|-|,|:|to)s*(d+(?:.d+)?)', val_str)
            if match:
                try:
                    v1 = float(match.group(1))
                    v2 = float(match.group(2))
                    return auto_rectify_interval(v1, v2)
                except Exception:
                    pass

    # 2. 最小秒 / 最短秒カラムの探索 (ターゲット間ランダムインターバル)
    min_keywords = [
        "ランダムインターバル最小", "ban対策ランダムインターバル最小", "ban対策最小", 
        "送信間隔最小", "送信待機最小", "送信最小", "ターゲット待機最小", "ターゲット最小",
        "targetintervalmin", "target_min_sec", "target_min"
    ]
    for k, val in acc_dict.items():
        if not k or val is None or str(val).strip() == "":
            continue
        k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
        is_min_col = any(kw in k_clean for kw in min_keywords)
        if is_min_col and "最大" not in k_clean and "最長" not in k_clean and "max" not in k_clean:
            try:
                num_match = re.search(r'd+(?:.d+)?', str(val).strip())
                if num_match:
                    found_min = float(num_match.group(0))
                    break
            except Exception:
                pass

    # 3. 最大秒 / 最長秒カラムの探索 (ターゲット間ランダムインターバル)
    max_keywords = [
        "ランダムインターバル最大", "ban対策ランダムインターバル最大", "ban対策最大", 
        "送信間隔最大", "送信待機最大", "送信最大", "ターゲット待機最大", "ターゲット最大",
        "targetintervalmax", "target_max_sec", "target_max"
    ]
    for k, val in acc_dict.items():
        if not k or val is None or str(val).strip() == "":
            continue
        k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
        is_max_col = any(kw in k_clean for kw in max_keywords)
        if is_max_col and "最小" not in k_clean and "最短" not in k_clean and "min" not in k_clean:
            try:
                num_match = re.search(r'd+(?:.d+)?', str(val).strip())
                if num_match:
                    found_max = float(num_match.group(0))
                    break
            except Exception:
                pass

    # 4. 旧Excelとの後方互換性フォールバック（ランダムインターバル列が存在せず実行間隔のみが記載されている場合）
    if found_min is None or found_max is None:
        for k, val in acc_dict.items():
            if not k or val is None or str(val).strip() == "":
                continue
            k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
            if found_min is None and any(w in k_clean for w in ["実行間隔最短", "間隔最短", "最短秒数"]):
                try:
                    num_match = re.search(r'd+(?:.d+)?', str(val).strip())
                    if num_match and float(num_match.group(0)) <= 300.0:
                        found_min = float(num_match.group(0))
                except Exception:
                    pass
            if found_max is None and any(w in k_clean for w in ["実行間隔最長", "間隔最長", "最長秒数"]):
                try:
                    num_match = re.search(r'd+(?:.d+)?', str(val).strip())
                    if num_match and float(num_match.group(0)) <= 600.0:
                        found_max = float(num_match.group(0))
                except Exception:
                    pass

    final_min = found_min if found_min is not None else default_min
    final_max = found_max if found_max is not None else default_max
    return auto_rectify_interval(final_min, final_max)

def extract_account_interval_seconds(acc_dict, default_min: float = 300.0, default_max: float = 900.0) -> tuple[float, float]:
    """
    Excel「アカウント情報.xlsx」から【実行間隔】(全件完了後、次回このアカウントが周回起動するまでの待機時間) を確実に抽出
    優先探索カラム名:
    - 「実行間隔最短(秒)」「実行間隔最長(秒)」
    - 「実行間隔最短」「実行間隔最長」
    - 「周回間隔最短(秒)」「周回間隔最長(秒)」「アカウント間隔最短」「アカウント間隔最長」
    - 「間隔最短」「間隔最長」「account_interval_min」「account_interval_max」
    - 単一カラムでの「300-900」「600〜1800」等の範囲指定
    """
    if not isinstance(acc_dict, dict):
        return auto_rectify_interval(default_min, default_max)

    found_min = None
    found_max = None

    # 1. 単一列での「300〜900」「600-1800」等の範囲入力チェック
    for k, val in acc_dict.items():
        if not k or val is None or str(val).strip() == "":
            continue
        k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
        if any(w in k_clean for w in ["実行間隔", "周回間隔", "アカウント間隔", "サイクル間隔", "accountinterval"]):
            val_str = str(val).strip()
            match = re.search(r'(d+(?:.d+)?)s*(?:〜|~|-|,|:|to)s*(d+(?:.d+)?)', val_str)
            if match:
                try:
                    v1 = float(match.group(1))
                    v2 = float(match.group(2))
                    return auto_rectify_interval(v1, v2)
                except Exception:
                    pass

    # 2. 実行間隔最短 / 最小カラムの探索
    min_keywords = [
        "実行間隔最短", "実行間隔最小", "周回間隔最短", "周回間隔最小", 
        "アカウント間隔最短", "アカウント間隔最小", "サイクル間隔最短", "周回最短", 
        "account_interval_min", "cycle_min_sec", "acc_interval_min", "間隔最短"
    ]
    for k, val in acc_dict.items():
        if not k or val is None or str(val).strip() == "":
            continue
        k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
        # 「ランダムインターバル」「ban」を含まない実行間隔列を優先
        if "ランダム" in k_clean or "ban" in k_clean:
            continue
        is_min_col = any(kw in k_clean for kw in min_keywords) or (
            any(m in k_clean for m in ["最短", "最小", "min"]) and any(w in k_clean for w in ["実行", "周回", "アカウント", "間隔"])
        )
        if is_min_col and "最大" not in k_clean and "最長" not in k_clean and "max" not in k_clean:
            try:
                num_match = re.search(r'd+(?:.d+)?', str(val).strip())
                if num_match:
                    found_min = float(num_match.group(0))
                    break
            except Exception:
                pass

    # 3. 実行間隔最長 / 最大カラムの探索
    max_keywords = [
        "実行間隔最長", "実行間隔最大", "周回間隔最長", "周回間隔最大", 
        "アカウント間隔最長", "アカウント間隔最大", "サイクル間隔最長", "周回最長", 
        "account_interval_max", "cycle_max_sec", "acc_interval_max", "間隔最長"
    ]
    for k, val in acc_dict.items():
        if not k or val is None or str(val).strip() == "":
            continue
        k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
        if "ランダム" in k_clean or "ban" in k_clean:
            continue
        is_max_col = any(kw in k_clean for kw in max_keywords) or (
            any(m in k_clean for m in ["最長", "最大", "max"]) and any(w in k_clean for w in ["実行", "周回", "アカウント", "間隔"])
        )
        if is_max_col and "最小" not in k_clean and "最短" not in k_clean and "min" not in k_clean:
            try:
                num_match = re.search(r'd+(?:.d+)?', str(val).strip())
                if num_match:
                    found_max = float(num_match.group(0))
                    break
            except Exception:
                pass

    final_min = found_min if found_min is not None else default_min
    final_max = found_max if found_max is not None else default_max
    return auto_rectify_interval(final_min, final_max)

def extract_interval_seconds(acc_dict, default_min: float = 60.0, default_max: float = 120.0) -> tuple[float, float]:
    """後方互換用エイリアス: ターゲット間ランダムインターバルを返却"""
    return extract_target_interval_seconds(acc_dict, default_min, default_max)

def normalize_screen_name(val: str) -> str:
    """
    URLや@が含まれていても、純粋なX(Twitter)スクリーン名（ユーザーID）のみを完全抽出
    対応例:
    - "https://x.com/elonmusk" -> "elonmusk"
    - "https://x.com/https://x.com/elonmusk" -> "elonmusk"
    - "@https://x.com/elonmusk" -> "elonmusk"
    - "https://x.com/@elonmusk" -> "elonmusk"
    - "https://twitter.com/elonmusk/" -> "elonmusk"
    - "https://x.com/elonmusk/status/12345678" -> "elonmusk"
    - "https://x.com/elonmusk?s=20" -> "elonmusk"
    - "@elonmusk" -> "elonmusk"
    - "elonmusk" -> "elonmusk"
    """
    if not val:
        return ""
    s = str(val).strip().lstrip("@").strip()
    # URLプレフィックスの多重剥ぎ取り
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

    # クエリパラメータ・ハッシュ・末尾スラッシュの除去
    s = s.split("?")[0].split("#")[0].rstrip("/")
    if "/" in s:
        s = s.split("/")[0]
    return s.lstrip("@").strip()

def extract_display_name_from_text(raw_name: str, target_screen_name: str = "", fallback_to_id: bool = False) -> str:
    """
    アカウントの表示名（アカウント名）を抽出し、{name}さん に適した自然な呼称に整形
    例:
    - "限界OLちゃん" -> "限界OLちゃん"
    - "山田 太郎" -> "山田 太郎"
    - "山田太郎@副業マーケター" -> "山田太郎"
    - "田中｜SNS運用代行" -> "田中"
    - "佐藤 | AIエンジニア" -> "佐藤"
    - "@nxs7m" -> "" (fallback_to_id=False時) または "nxs7m" (fallback_to_id=True時)
    """
    clean_target = target_screen_name.lstrip("@").strip().lower()
    val = str(raw_name or "").strip()
    if not val:
        return target_screen_name.lstrip("@").strip() if fallback_to_id else ""
    
    # もしIDそのもの (@nxs7m または nxs7m) の場合
    if val.lstrip("@").strip().lower() == clean_target:
        return target_screen_name.lstrip("@").strip() if fallback_to_id else ""
    
    # 先頭の@や余分な空白を除去
    if val.startswith("@") and not any(sep in val for sep in ["｜", "|", "【", "◆", "★", "■", "／", "/"]):
        val = val.lstrip("@").strip()

    # 表示名の肩書き（@や|以降）をきれいに除去して呼称として自然にする
    for sep in ["@", "＠", "｜", "|", "【", "◆", "★", "■", "／", "/", "🔻", "⚡", "📌"]:
        if sep in val:
            prefix = val.split(sep)[0].strip()
            if len(prefix) >= 1:
                val = prefix
                break

    return val.strip()

def format_template_message(template: str, display_name: str = "", screen_name: str = "", preserve_unresolved: bool = False) -> str:
    """
    メッセージテンプレートの変数を置換:
    - {name}, {名前}, {アカウント名}: 自然な表示名（例: "限界OLちゃん", "山田太郎", "田中"）※IDではなく表示名
    - {full_name}, {表示名}: 肩書き付きの完全な表示名（例: "山田太郎@副業マーケター"）
    - {id}, {ユーザー名}: ユーザーID（@なし、例: "nxs7m"）
    - {mention}: メンション（例: "@nxs7m"）
    - preserve_unresolved: Trueの場合、display_nameが未確定なら{name}等を置換せずに保持（プロフィール画面で後から本物の表示名を取得して置換）
    """
    if not template:
        return ""
    clean_id = screen_name.lstrip("@").strip()
    clean_name = extract_display_name_from_text(display_name, clean_id, fallback_to_id=not preserve_unresolved)
    
    full_disp = str(display_name or "").strip()
    if full_disp.lstrip("@").lower() == clean_id.lower():
        full_disp = "" if preserve_unresolved else clean_id

    res = template
    # {id}, {ユーザー名}, {mention} は常に即時置換
    res = res.replace("{id}", clean_id)
    res = res.replace("{ユーザー名}", clean_id)
    res = res.replace("{mention}", f"@{clean_id}")

    if clean_name:
        res = res.replace("{name}", clean_name)
        res = res.replace("{名前}", clean_name)
        res = res.replace("{アカウント名}", clean_name)
    elif not preserve_unresolved:
        res = res.replace("{name}", clean_id)
        res = res.replace("{名前}", clean_id)
        res = res.replace("{アカウント名}", clean_id)

    if full_disp:
        res = res.replace("{full_name}", full_disp)
        res = res.replace("{表示名}", full_disp)
    elif not preserve_unresolved:
        res = res.replace("{full_name}", clean_id)
        res = res.replace("{表示名}", clean_id)

    # 「さんさん」や「様様」などの重複敬称を自動防止
    res = re.sub(r'さんさん(?=[^w]|$)', 'さん', res)
    res = re.sub(r'様様(?=[^w]|$)', '様', res)
    return res

def sanitize_filename(name: str) -> str:
    """Windowsで使用不可能な記号を安全なアンダースコアに置換"""
    clean = normalize_screen_name(name)
    if not clean:
        clean = re.sub(r'[\/*?:"<>|]', '_', str(name).strip())
    if not clean:
        clean = "unknown_account"
    return clean

def wait_for_account_interval(target_account_name: str, wait_seconds: float, min_sec: float, max_sec: float, base_dir: str, context_label: str = ""):
    """アカウントごとに設定された待機時間をカウントダウンし、到来したら実行を許可する"""
    if wait_seconds <= 0:
        return
    log("")
    log(f"{'-'*60}")
    log(f"⏰ 【アカウント別待機タイマー】 アカウント [{target_account_name}]")
    log(f"   設定待機間隔: {min_sec:.0f}秒 〜 {max_sec:.0f}秒 （アカウント情報.xlsx 個別設定）")
    log(f"   待機時間: {wait_seconds:.1f} 秒 ({context_label})")
    log(f"   ☕ アカウント [{target_account_name}] の待機時間が来たら自動でブラウザを起動します...")
    log(f"{'-'*60}")
    
    start_t = time.time()
    last_log_t = start_t
    
    while True:
        elapsed = time.time() - start_t
        remaining = wait_seconds - elapsed
        if remaining <= 0:
            break
            
        check_app_termination(base_dir)
        check_pause_state(f"アカウント [{target_account_name}] 待機中")
        
        now_t = time.time()
        # 10秒毎にログ進捗、残り5秒以下は毎秒カウントダウン表示
        if (remaining > 5.0 and now_t - last_log_t >= 10.0) or (remaining <= 5.0 and now_t - last_log_t >= 1.0):
            log(f"⏳ アカウント [{target_account_name}] 起動まで あと {max(1, int(round(remaining)))} 秒...")
            last_log_t = now_t
            
        time.sleep(min(0.25, max(0.05, remaining)))
        
    log(f"✨ アカウント [{target_account_name}] の待機時間が到来しました！")
    log(f"🚀 アカウント [{target_account_name}] のブラウザを起動します。")

def run_single_cycle(config_mgr, global_db_mgr, base_dir, cycle_count: int = 1, account_ready_times: dict = None) -> tuple[float, float]:
    if account_ready_times is None:
        account_ready_times = {}
    cycle_min_sec = 60.0
    cycle_max_sec = 120.0

    try:
        log(f"1. 設定ファイル (アカウント情報.xlsx) を読み込んでいます... (第 {cycle_count} 周回)")
        accounts = config_mgr.load_accounts()
        if not accounts:
            log("❌ アカウント設定が1件も取得できませんでした。Excelを確認してください。")
            return cycle_min_sec, cycle_max_sec

        dm_templates = config_mgr.load_dm_templates()
        reply_templates = config_mgr.load_reply_templates()
        quote_templates = config_mgr.load_quote_templates()
        log(f"   DMテンプレート: {len(dm_templates)}件 / リプライテンプレート: {len(reply_templates)}件 / 引用RT: {len(quote_templates)}件")

        search_keywords = config_mgr.load_search_keywords()
        reply_urls = config_mgr.load_reply_urls()
        follow_urls = config_mgr.load_follow_urls()
        log(f"   読込設定: 検索キーワード {len(search_keywords)}件 / リプライURL {len(reply_urls)}件 / フォローURL {len(follow_urls)}件")

    except Exception as e:
        log(f"❌ 設定ファイル読み込みエラー: {e}")
        traceback.print_exc()
        return cycle_min_sec, cycle_max_sec

    # 2. アカウント設定ヘルパー関数
    def get_acc_val(acc_dict, keywords, default=""):
        for k, val in acc_dict.items():
            if not k or val is None or str(val).strip() == "":
                continue
            k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
            if any(kw.lower() in k_clean for kw in keywords):
                s = str(val).strip()
                if s.endswith(".0") and s[:-2].isdigit():
                    return s[:-2]
                return s
        return default

    def get_acc_num(acc_dict, keywords, default_val=60.0):
        for k, val in acc_dict.items():
            if not k or val is None or str(val).strip() == "":
                continue
            k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
            if any(kw in k_clean for kw in keywords):
                try:
                    return float(str(val).strip())
                except:
                    pass
        return default_val

    # 3. 各アカウント実行関数 (1窓ローテーション & 2窓同時並列モード両対応)
    total_acc_count = len(accounts)

    def process_single_account(acc, acc_idx: int, total_acc_count: int, active_slot: int = 1, slot_semaphore=None):
        def get_acc_val(acc_dict, keywords, default=""):
            for k, val in acc_dict.items():
                if not k or val is None or str(val).strip() == "":
                    continue
                k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
                if any(kw.lower() in k_clean for kw in keywords):
                    s = str(val).strip()
                    if s.endswith(".0") and s[:-2].isdigit():
                        return s[:-2]
                    return s
            return default

        raw_account_id = get_acc_val(acc, ["アカウントid", "アカウント名", "ユーザー名", "id"], "").strip() or f"アカウント#{acc_idx}"
        
        # モード説明行や注記行の確実なスキップ判定
        raw_id_clean = raw_account_id.replace(" ", "").replace("　", "").lower()
        if raw_id_clean in ["モード", "モード名", "モード名称", "番号", "1", "2", "3", "4", "5", "※備考", "※使い方", "※複数指定の例"]:
            return min_sec if 'min_sec' in locals() else 60.0, max_sec if 'max_sec' in locals() else 120.0
        if raw_account_id.startswith("【") or raw_account_id.startswith("※") or raw_account_id.startswith("(") or raw_account_id.startswith("（"):
            return min_sec if 'min_sec' in locals() else 60.0, max_sec if 'max_sec' in locals() else 120.0

        clean_account_id = normalize_screen_name(raw_account_id) or f"acc_{acc_idx}"
        account_id = clean_account_id

        # ★ 要件対応: 実行されたアカウントごとに logフォルダ/アカウント名.log と dbフォルダ/アカウント名.DB を生成
        acc_logger = AccountLogger(clean_account_id, base_dir)
        acc_db_path = get_account_db_path(base_dir, clean_account_id)
        db_mgr = DatabaseManager(acc_db_path)

        set_current_logger(acc_logger)

        auth_token = get_acc_val(acc, ["auth_token", "authtoken", "token", "クッキー"], "").strip()
        password = get_acc_val(acc, ["パスワード", "password", "pass", "アカウントpw", "pw"], "").strip()
        dm_passcode = get_acc_val(acc, ["dmパスコード", "パスコード", "暗号化パスコード", "暗号化キー", "暗号化dm", "pin", "dmpin", "復号", "2fa", "認証コード", "dmpasscode", "passcode"], "").strip()
        if not dm_passcode:
            try:
                from db_manager import get_saved_passcodes
                saved_passes = get_saved_passcodes(base_dir)
                dm_passcode = saved_passes.get(clean_account_id, "") or saved_passes.get(normalize_screen_name(account_id), "") or saved_passes.get(str(account_id).strip(), "")
            except Exception:
                pass

        # アカウント専用Excel (例: wqwq1320.xlsx) から個別動作設定 (動作設定シート) を読込
        acc_target_path = config_mgr.get_target_path_for_account(clean_account_id)
        acc_settings = config_mgr.load_account_settings(clean_account_id, target_path_override=acc_target_path, fallback_acc=acc)

        target_mode = acc_settings.get("target_mode") or get_acc_val(acc, ["取得モード", "対象取得モード", "ターゲットモード", "検索モード", "モード", "抽出モード"], "").strip()
        scroll_limit = acc_settings.get("scroll_limit", 5)
        target_read_limit = acc_settings.get("target_count", 15)
        block_media = acc_settings.get("block_media", True)
        ai_engine = acc_settings.get("ai_engine", "grok")

        target_min_sec = float(acc_settings.get("target_min_sec", 60.0))
        target_max_sec = float(acc_settings.get("target_max_sec", 150.0))

        # アクション個別設定の取得 (リプライ, 引用RT, いいね, DM, フォロー, AI生成)
        actions_cfg = acc_settings.get("actions", {})
        dm_cfg = actions_cfg.get("dm", {})
        like_cfg = actions_cfg.get("like", {})
        follow_cfg = actions_cfg.get("follow", {})
        reply_cfg = actions_cfg.get("reply", {})
        quote_cfg = actions_cfg.get("quote", {})
        reply_ai_cfg = actions_cfg.get("reply_ai", {})
        quote_ai_cfg = actions_cfg.get("quote_ai", {})

        cmd_dm = dm_cfg.get("enabled", False)
        cmd_like = like_cfg.get("enabled", False)
        cmd_follow = follow_cfg.get("enabled", False)
        cmd_reply = reply_cfg.get("enabled", False) or reply_ai_cfg.get("enabled", False)
        cmd_quote = quote_cfg.get("enabled", False) or quote_ai_cfg.get("enabled", False)

        reply_use_ai = reply_ai_cfg.get("enabled", False) or reply_cfg.get("ai_gen", False)
        quote_use_ai = quote_ai_cfg.get("enabled", False) or quote_cfg.get("ai_gen", False)

        active_actions = []
        if cmd_follow: active_actions.append(f"フォロー(最小{follow_cfg.get('min_batch', 3)}〜最大{follow_cfg.get('max_batch', 7)}件/間隔{follow_cfg.get('min_interval', 60):.0f}〜{follow_cfg.get('max_interval', 180):.0f}秒)")
        if cmd_like: active_actions.append(f"いいね(最小{like_cfg.get('min_batch', 5)}〜最大{like_cfg.get('max_batch', 10)}件/間隔{like_cfg.get('min_interval', 30):.0f}〜{like_cfg.get('max_interval', 90):.0f}秒)")
        if cmd_reply: active_actions.append(f"リプライ{'[AI生成]' if reply_use_ai else ''}(最小{reply_cfg.get('min_batch', 2)}〜最大{reply_cfg.get('max_batch', 5)}件/間隔{reply_cfg.get('min_interval', 180):.0f}〜{reply_cfg.get('max_interval', 600):.0f}秒)")
        if cmd_dm: active_actions.append(f"DM送信(最小{dm_cfg.get('min_batch', 3)}〜最大{dm_cfg.get('max_batch', 8)}件/間隔{dm_cfg.get('min_interval', 300):.0f}〜{dm_cfg.get('max_interval', 900):.0f}秒)")
        if cmd_quote: active_actions.append(f"引用RT{'[AI生成]' if quote_use_ai else ''}(最小{quote_cfg.get('min_batch', 1)}〜最大{quote_cfg.get('max_batch', 3)}件/間隔{quote_cfg.get('min_interval', 300):.0f}〜{quote_cfg.get('max_interval', 900):.0f}秒)")

        # 有効化されているアクションの実行間隔・連続件数から全体の基準値を算出
        enabled_intervals_min = [cfg.get("min_interval", 300.0) for cfg in actions_cfg.values() if cfg.get("enabled")]
        enabled_intervals_max = [cfg.get("max_interval", 900.0) for cfg in actions_cfg.values() if cfg.get("enabled")]
        account_min_sec = min(enabled_intervals_min) if enabled_intervals_min else 300.0
        account_max_sec = max(enabled_intervals_max) if enabled_intervals_max else 900.0

        enabled_batch_min = [cfg.get("min_batch", 3) for cfg in actions_cfg.values() if cfg.get("enabled")]
        enabled_batch_max = [cfg.get("max_batch", 8) for cfg in actions_cfg.values() if cfg.get("enabled")]
        min_batch = min(enabled_batch_min) if enabled_batch_min else 3
        max_batch = max(enabled_batch_max) if enabled_batch_max else 8

        min_sec, max_sec = target_min_sec, target_max_sec
        cycle_min_sec, cycle_max_sec = account_min_sec, account_max_sec
        processed_count = 0

        acc_dm_templates = config_mgr.load_dm_templates(target_path_override=acc_target_path) or dm_templates
        acc_reply_templates = config_mgr.load_reply_templates(target_path_override=acc_target_path) or reply_templates
        acc_quote_templates = config_mgr.load_quote_templates(target_path_override=acc_target_path) or quote_templates
        acc_search_keywords = config_mgr.load_search_keywords(target_path_override=acc_target_path) or search_keywords
        acc_reply_urls = config_mgr.load_reply_urls(target_path_override=acc_target_path) or reply_urls
        acc_follow_urls = config_mgr.load_follow_urls(target_path_override=acc_target_path) or follow_urls

        log(f"\n{'='*58}")
        log(f"🚀 アカウント [{account_id}] ({acc_idx}/{total_acc_count}件目) のスロット{active_slot}セッションを開始します")
        log(f"   📂 ターゲット/設定ファイル: {os.path.basename(acc_target_path)} (個別動作設定シート適用)")
        log(f"   ⚡ メディア読込ブロック: {'ON (省メモリ・超高速)' if block_media else 'OFF (通常)'}")
        log(f"   取得モード: {target_mode or '指定なし'} (スクロール上限: {scroll_limit}回)")
        log(f"   AIエンジン: {ai_engine}")
        log(f"   📋 アクション個別設定 (アカウント名.xlsx「動作設定」シート準拠):")
        for act_name, a_cfg in actions_cfg.items():
            if a_cfg.get("enabled"):
                log(f"      ・{act_name.upper()}: 実行=ON, 実行間隔={a_cfg.get('min_interval', 0):.0f}〜{a_cfg.get('max_interval', 0):.0f}秒, 連続処理={a_cfg.get('min_batch', 1)}〜{a_cfg.get('max_batch', 1)}件, AI生成={'ON' if a_cfg.get('ai_gen') else 'OFF'}")
        log(f"      ・読み込み指定 (ターゲット件数): {target_read_limit} 件")
        log(f"      ・DMパスコード: {'登録済み (暗号化DM自動認証有効)' if dm_passcode else '⚠️ 未登録'}")
        log(f"      ・アカウント次回周回待機: {account_min_sec:.0f}秒 〜 {account_max_sec:.0f}秒")
        log(f"      ・ターゲット間ランダム待機: {target_min_sec:.0f}秒 〜 {target_max_sec:.0f}秒")
        log(f"{'='*58}")

        # ★ 起動時の待機なし＆周回時個別待機制御（アプリ起動時は待機なしで即座にブラウザを起動）
        if clean_account_id in account_ready_times:
            ready_at = account_ready_times[clean_account_id]
            remaining_wait = ready_at - time.time()
            if remaining_wait > 0:
                wait_for_account_interval(clean_account_id, remaining_wait, account_min_sec, account_max_sec, base_dir, f"第{cycle_count}周回 個別実行間隔")
            else:
                log(f"✅ アカウント [{clean_account_id}] の設定実行間隔（{account_min_sec:.0f}秒 〜 {account_max_sec:.0f}秒）は既に経過しています。待ち時間0秒で直ちにブラウザを起動します！")
        else:
            # アプリ起動直後・初回実行時: 待機時間なしで即座にブラウザを起動
            log(f"⚡ アカウント [{clean_account_id}] を待ち時間なしで直ちに起動します（初回即時実行）")

        # 2窓並列モードの場合、セマフォで最大2ブラウザに制御
        acquired_semaphore = False
        if slot_semaphore is not None:
            if not slot_semaphore.acquire(blocking=False):
                log(f"⏳ 【2窓上限到達】アカウント [{clean_account_id}] の待機時間が到来しましたが、現在上限の2窓が稼働中です。空き枠が出るまで待機キューでスタンバイします...")
                slot_semaphore.acquire(blocking=True)
                log(f"✨ 実行枠が空きました！ アカウント [{clean_account_id}] のブラウザを直ちに起動します（スロット{active_slot}取得完了）。")
            acquired_semaphore = True

        profile_dir = os.path.join(base_dir, "browser_profiles", sanitize_filename(clean_account_id))
        browser_channel = get_acc_val(acc, ["ブラウザ", "使用ブラウザ", "browser", "channel"], "").strip().lower()
        browser_ctrl = XBrowserController(headless=False, user_data_dir=profile_dir, base_dir=base_dir, block_media=block_media, preferred_channel=browser_channel)
        try:
            browser_ctrl.start()
            browser_ctrl.current_account_id = clean_account_id
            if dm_passcode:
                browser_ctrl.current_passcode = dm_passcode
            
            # ログイン (Cookie優先・パスコード自動対応)
            login_success = browser_ctrl.login(account_id=clean_account_id, auth_token=auth_token, password=password, passcode=dm_passcode)
            if not login_success:
                log(f"❌ アカウント [{account_id}] のログインに失敗したため、次のアカウントへ移ります。")
                browser_ctrl.close()
                return min_sec, max_sec

            # アカウントログイン直後の一時停止チェック (アカウント編集したい場合に対応)
            check_pause_state(f"アカウント @{account_id} ログイン直後")

            # --- 複数モードの解析 (カンマ・句読点区切りで設定された順序を完全保持して順番処理) ---
            check_pause_state(f"アカウント @{account_id} ターゲット収集前")
            raw_mode_str = target_mode.strip()
            # 改行や全角空白・区切り文字を正規化して分割 (TypeScriptテンプレート展開時のシンタックスエラーを防止)
            mode_clean = raw_mode_str.replace(chr(10), ",").replace(chr(13), ",").replace("　", " ")
            mode_parts = [p.strip() for p in re.split(r'[,、+/|]', mode_clean) if p.strip()]
            
            # 各要素がどのモードに対応するか判定してリスト化 (指定された順番を厳密に保持)
            # 1〜5番の数字指定、全角数字（１〜５）、丸数字（①〜⑤）にも完全対応！
            parsed_modes = []
            for part in mode_parts:
                p_clean = part.strip().lower()
                if p_clean in ["1", "1番", "１", "１番", "①", "01"] or any(k in p_clean for k in ["検索", "キーワード", "search", "keyword", "クエリ"]):
                    parsed_modes.append(("SEARCH", f"1. 検索キーワード ({part})"))
                elif p_clean in ["2", "2番", "２", "２番", "②", "02"] or any(k in p_clean for k in ["リプライ", "reply"]):
                    parsed_modes.append(("REPLY", f"2. URLリスト_リプライ ({part})"))
                elif p_clean in ["3", "3番", "３", "３番", "③", "03"] or any(k in p_clean for k in ["フォロー", "follow"]):
                    parsed_modes.append(("FOLLOW", f"3. URLリスト_フォロー ({part})"))
                elif p_clean in ["4", "4番", "４", "４番", "④", "04"] or any(k in p_clean for k in ["リスト", "ターゲット", "履歴", "list"]):
                    parsed_modes.append(("LIST", f"4. リスト送信 ({part})"))
                elif p_clean in ["5", "5番", "５", "５番", "⑤", "05"] or any(k in p_clean for k in ["相互フォロー", "相互", "mutual"]):
                    parsed_modes.append(("MUTUAL", f"5. 相互フォロー検索 ({part})"))
                else:
                    parsed_modes.append(("SEARCH", f"検索キーワード ({part})"))

            # モード指定が空だった場合はリスト送信モード
            if not parsed_modes:
                parsed_modes = [("LIST", "4. ターゲット一覧 (リスト送信)")]

            # ターゲット1件に対するアクション実行関数 (いいね/フォロー/リプライ/DM/引用RT)
            # ★ 戻り値: (is_success: bool, detail: str) - 送信成功時のみTrue、閉鎖トレイ・スキップ時はFalseを返却
            def execute_target_action(target_item, target_idx, total_in_mode, current_success_count: int = 0, target_batch_goal: int = 1) -> tuple[bool, str]:
                nonlocal processed_count
                check_pause_state(f"ターゲット [{target_idx}/{total_in_mode}] 実行前")
                target_screen_name = normalize_screen_name(target_item.get("アカウント名") or target_item.get("ターゲットURL") or target_item.get("ユーザー名") or "")
                if not target_screen_name:
                    return False, "INVALID_TARGET"

                # ★ ユーザー要件対応: 取得した特定のツイートURL（検索結果やリプライ一覧で取得したツイート自身）
                target_tweet_url = str(target_item.get("ツイートURL") or target_item.get("tweet_url") or "").strip()
                raw_target_url = str(target_item.get("ターゲットURL") or target_item.get("url") or "").strip()
                if not target_tweet_url and "/status/" in raw_target_url:
                    target_tweet_url = raw_target_url

                # ターゲットのアカウント名（表示名）解決 (Excel設定 または 探索結果 または プロフィール画面から自動取得)
                raw_acc_entry = str(target_item.get("アカウント名") or "").strip()
                excel_disp = str(target_item.get("表示名") or target_item.get("お名前") or target_item.get("名前") or target_item.get("display_name") or "").strip()
                if not excel_disp and raw_acc_entry and not raw_acc_entry.startswith("@") and normalize_screen_name(raw_acc_entry).lower() != target_screen_name.lower():
                    excel_disp = raw_acc_entry

                target_display_name = excel_disp

                def get_target_name() -> str:
                    nonlocal target_display_name
                    if not target_display_name:
                        try:
                            p_name = browser_ctrl.get_profile_display_name(target_screen_name)
                            if p_name and p_name.lstrip("@").lower() != target_screen_name.lower():
                                target_display_name = p_name
                                log(f"   👤 [アカウント名取得] 表示名: 「{target_display_name}」 (ID: @{target_screen_name})")
                        except Exception:
                            pass
                    return target_display_name

                log("")
                if target_tweet_url:
                    log(f"🎯 [{target_idx}/{total_in_mode}] ターゲット: @{target_screen_name} (取得ツイート: {target_tweet_url}) を実行開始 (現在の送信成功: {current_success_count}/{target_batch_goal}件)...")
                else:
                    log(f"🎯 [{target_idx}/{total_in_mode}] ターゲット: @{target_screen_name} を上から順番に実行開始 (現在の送信成功: {current_success_count}/{target_batch_goal}件)...")
                target_actions_done = 0
                status_list = []

                # ブラウザウィンドウが閉じている場合は自動起動・ページ確保
                browser_ctrl.ensure_page()

                try:
                    # 1. フォロー実行 (Excel「命令実行 フォロー」がONの場合)
                    if cmd_follow:
                        if db_mgr.is_already_sent(target_screen_name, action_type="FOLLOW"):
                            log(f"   ⏩ [フォロー] @{target_screen_name} は過去に実行済みのためスキップ")
                        else:
                            st, det = browser_ctrl.follow_user(target_screen_name)
                            log(f"   👤 [フォロー結果] {st} ({det})")
                            get_target_name()  # 画面読み込み後に表示名を即時抽出・キャッシュ
                            db_mgr.record_sent(target_screen_name, account_id, "FOLLOW", st, det)
                            if st in ["SUCCESS", "ALREADY_FOLLOWING"]:
                                target_actions_done += 1
                                status_list.append("フォロー済")
                            else:
                                status_list.append(f"フォロー:{st}")
                            time.sleep(random.uniform(2.0, 4.0))
                    else:
                        log(f"   ⏩ [フォロー] Excel設定「命令実行 フォロー」がOFFのためスキップ")

                    # 2. いいね実行 (Excel「命令実行 いいね」がONの場合)
                    # ★ 取得したツイートURLがある場合はトップツイートではなくその取得ツイートにいいね
                    if cmd_like:
                        if db_mgr.is_already_sent(target_screen_name, action_type="LIKE"):
                            log(f"   ⏩ [いいね] @{target_screen_name} は過去に実行済みのためスキップ")
                        else:
                            st, det = browser_ctrl.like_recent_post(target_screen_name, tweet_url=target_tweet_url)
                            log(f"   ❤️ [いいね結果] {st} ({det})")
                            get_target_name()  # 表示名のキャッシュ
                            db_mgr.record_sent(target_screen_name, account_id, "LIKE", st, det)
                            if st in ["SUCCESS", "ALREADY_LIKED"]:
                                target_actions_done += 1
                                status_list.append("いいね済")
                            else:
                                status_list.append(f"いいね:{st}")
                            time.sleep(random.uniform(2.0, 4.0))
                    else:
                        log(f"   ⏩ [いいね] Excel設定「命令実行 いいね」がOFFのためスキップ")

                    # 3. リプライ実行 (Excel「命令実行 リプライ」がONの場合)
                    # ★ 取得したツイートURLがある場合はトップツイートではなくその取得ツイートにリプライ
                    if cmd_reply:
                        if db_mgr.is_already_sent(target_screen_name, action_type="REPLY"):
                            log(f"   ⏩ [リプライ] @{target_screen_name} は過去に実行済みのためスキップ")
                        else:
                            rep_msg = random.choice(acc_reply_templates) if acc_reply_templates else (random.choice(reply_templates) if reply_templates else "大変参考になります！有益な情報をありがとうございます✨")
                            resolved_disp = get_target_name()
                            rep_msg = format_template_message(rep_msg, resolved_disp, target_screen_name, preserve_unresolved=True)
                            preview_name = resolved_disp or "相手のアカウント名"
                            preview_log = format_template_message(rep_msg, preview_name, target_screen_name, preserve_unresolved=False)
                            log(f"   💬 [リプライ送信中] 「{preview_log[:35]}...」")
                            st, det = browser_ctrl.send_reply(target_screen_name, rep_msg, tweet_url=target_tweet_url)
                            log(f"   💬 [リプライ結果] {st} ({det})")
                            db_mgr.record_sent(target_screen_name, account_id, "REPLY", st, rep_msg[:40])
                            if st == "SUCCESS":
                                target_actions_done += 1
                                status_list.append("リプライ完了")
                            else:
                                status_list.append(f"リプライ:{st}")
                            time.sleep(random.uniform(3.0, 5.5))

                    # 4. DM送信実行 (Excel「命令実行 DM」がONの場合)
                    if cmd_dm:
                        if db_mgr.is_already_sent(target_screen_name, action_type="DM"):
                            log(f"   ⏩ [DM] @{target_screen_name} は過去に実行済みのためスキップ")
                        else:
                            dm_msg = target_item.get("個別メッセージ") or ""
                            if not dm_msg and acc_dm_templates:
                                dm_msg = random.choice(acc_dm_templates)
                            elif not dm_msg and dm_templates:
                                dm_msg = random.choice(dm_templates)
                            if not dm_msg:
                                dm_msg = "{name}さん、はじめまして！ご連絡ありがとうございます。今後ともよろしくお願いいたします。"
                            
                            # IDではなく自然なアカウント名（表示名）を展開して送信
                            # 表示名が未確定の場合は {name} を維持したまま send_direct_message に渡し、
                            # 相手のプロフィール画面で確実に取得した本物の表示名（アカウント名）を展開
                            resolved_disp = get_target_name()
                            dm_msg = format_template_message(dm_msg, resolved_disp, target_screen_name, preserve_unresolved=True)
                            preview_name = resolved_disp or "相手のアカウント名"
                            preview_log = format_template_message(dm_msg, preview_name, target_screen_name, preserve_unresolved=False)

                            log(f"   ✉️ [DM送信中] 「{preview_log[:35]}...」")
                            effective_pass = dm_passcode or browser_ctrl.current_passcode
                            if effective_pass:
                                log(f"      🔑 登録済みDMパスコード (**** {len(effective_pass)}桁) を適用して暗号化解除・送信を実行します")
                            st, det = browser_ctrl.send_direct_message(target_screen_name, dm_msg, passcode=effective_pass)
                            log(f"   ✉️ [DM結果] {st} ({det})")
                            db_mgr.record_sent(target_screen_name, account_id, "DM", st, dm_msg[:40])
                            
                            if st == "SUCCESS":
                                target_actions_done += 1
                                status_list.append("DM送信完了")
                            elif st in ["INBOX_CLOSED", "DM_REJECTED", "LOCKED_ACCOUNT"]:
                                is_inbox_closed = (st == "INBOX_CLOSED") or ("受信トレイ" in det) or ("Xナンバー" in det)
                                status_label = "受信トレイ閉鎖(DM不可)" if is_inbox_closed else f"DMスキップ({det})"
                                status_list.append(status_label)
                                final_status = " / ".join(status_list) if status_list else status_label
                                config_mgr.update_target_status(target_screen_name, final_status, target_path_override=acc_target_path)
                                
                                if is_inbox_closed:
                                    log(f"   ⚠️ @{target_screen_name} は受信トレイが閉じられています（相手のXナンバー要求）")
                                    log(f"   👉 モーダルの「今はしない」をクリックして閉じました。")
                                else:
                                    log(f"   ⏩ 送信不可（{det}）を検知しました。")
                                
                                # ★ ユーザー要件対応: 連続処理件数 (例: 最小2件〜最大5件) の送信カウントに含めずにスキップ
                                log(f"   ⏩ 【送信カウント除外】このターゲットは送信件数カウントに含めずスキップします（現在の送信成功: {current_success_count}/{target_batch_goal}件）")
                                short_wait = random.uniform(1.8, 3.2)
                                log(f"   ⏩ 次のアカウントへ短縮スキップ待機中 ({short_wait:.1f}秒)...")
                                time.sleep(short_wait)
                                return False, "SKIPPED_INBOX_CLOSED"
                            else:
                                status_list.append(f"DMエラー({det})")
                            time.sleep(random.uniform(3.0, 5.5))

                    # 5. 引用リツイート実行 (Excel「命令実行 引用リツイート」がONの場合)
                    # ★ 取得したツイートURLがある場合はトップツイートではなくその取得ツイートに引用RT
                    if cmd_quote:
                        if db_mgr.is_already_sent(target_screen_name, action_type="QUOTE"):
                            log(f"   ⏩ [引用RT] @{target_screen_name} は過去に実行済みのためスキップ")
                        else:
                            q_msg = random.choice(acc_quote_templates) if acc_quote_templates else (random.choice(quote_templates) if quote_templates else "大変有益なポストです！📌")
                            resolved_disp = get_target_name()
                            q_msg = format_template_message(q_msg, resolved_disp, target_screen_name, preserve_unresolved=True)
                            preview_name = resolved_disp or "相手のアカウント名"
                            preview_log = format_template_message(q_msg, preview_name, target_screen_name, preserve_unresolved=False)
                            log(f"   🔁 [引用RT実行中] 「{preview_log[:35]}...」")
                            st, det = browser_ctrl.quote_tweet(target_screen_name, q_msg, tweet_url=target_tweet_url)
                            log(f"   🔁 [引用RT結果] {st} ({det})")
                            db_mgr.record_sent(target_screen_name, account_id, "QUOTE", st, q_msg[:40])
                            if st == "SUCCESS":
                                target_actions_done += 1
                                status_list.append("引用RT完了")
                            else:
                                status_list.append(f"引用RT:{st}")
                            time.sleep(random.uniform(3.0, 5.5))

                    # 最終ステータス更新
                    final_status = " / ".join(status_list) if status_list else "処理済"
                    config_mgr.update_target_status(target_screen_name, final_status, target_path_override=acc_target_path)

                    if target_actions_done > 0:
                        processed_count += 1
                        log(f"   🎉 【送信完了】ターゲット @{target_screen_name} への送信が成功しました！（今回の連続処理進捗: {current_success_count + 1}/{target_batch_goal}件）")
                        return True, "SUCCESS"
                    else:
                        short_wait = random.uniform(2.0, 3.5)
                        log(f"   ⏩ アクション未実行のため短縮待機 ({short_wait:.1f}秒) で次のアカウントへ進みます...")
                        time.sleep(short_wait)
                        return False, "NO_ACTION"

                except Exception as target_err:
                    log(f"   ⚠️ ターゲット @{target_screen_name} の処理中にエラーが発生したためスキップし、次のターゲットへ進みます: {target_err}")
                    try:
                        config_mgr.update_target_status(target_screen_name, f"エラースキップ({target_err})", target_path_override=acc_target_path)
                    except Exception:
                        pass
                    try:
                        browser_ctrl.page.keyboard.press("Escape")
                    except Exception:
                        pass
                    time.sleep(1.5)
                    return False, f"ERROR:{target_err}"

            total_modes = len(parsed_modes)
            log("")
            log(f"📋 【モード設定確認】合計 {total_modes} 個のモードが設定されています（指定された順序で順番に実行）:")
            for m_i, (m_type, m_label) in enumerate(parsed_modes, start=1):
                log(f"   [{m_i}/{total_modes}] {m_label}")

            total_actions_performed = 0

            # 設定された各モードを順番に実行
            for mode_idx, (m_type, m_label) in enumerate(parsed_modes, start=1):
                check_pause_state(f"モード [{mode_idx}/{total_modes}: {m_label}] 開始前")
                log("")
                log(f"{'='*58}")
                log(f"🔄 【モード {mode_idx}/{total_modes}: {m_label}】の順番処理を開始します")
                log(f"{'='*58}")

                mode_targets = []

                if m_type == "SEARCH":
                    if acc_search_keywords:
                        log(f"🔍 [検索キーワード] ターゲット収集を開始 (登録キーワード: {len(acc_search_keywords)}件 / 上限: {target_read_limit}件)...")
                        for kw_item in acc_search_keywords:
                            kw = kw_item.get("keyword")
                            stype = kw_item.get("search_type", "最新")
                            found = browser_ctrl.collect_targets_from_search(kw, search_type=stype, scroll_limit=scroll_limit, max_targets=target_read_limit)
                            for item in found:
                                if isinstance(item, dict):
                                    user = item.get("screen_name") or item.get("username") or ""
                                    t_url = item.get("tweet_url", "")
                                    d_name = item.get("display_name", "")
                                else:
                                    user = str(item)
                                    t_url = ""
                                    d_name = ""
                                config_mgr.append_discovered_target(user, f"検索:{kw}({stype})", target_path_override=acc_target_path, tweet_url=t_url, display_name=d_name)
                                clean_u = normalize_screen_name(user)
                                if clean_u and not any(t.get("アカウント名") == clean_u for t in mode_targets):
                                    mode_targets.append({
                                        "アカウント名": clean_u,
                                        "ターゲットURL": t_url if t_url else f"https://x.com/{clean_u}",
                                        "ツイートURL": t_url,
                                        "個別メッセージ": "",
                                        "ステータス": "未送信",
                                        "表示名": d_name
                                    })
                            if target_read_limit > 0 and len(mode_targets) >= target_read_limit:
                                break
                    else:
                        log(f"   ℹ️ [検索キーワード] 「検索キーワード」シートに対象キーワードが未登録のため、このモードをスキップして次のモードへ進みます。")

                elif m_type == "REPLY":
                    if acc_reply_urls:
                        log(f"💬 [URLリスト_リプライ] ターゲット収集を開始 (登録URL: {len(acc_reply_urls)}件 / 上限: {target_read_limit}件)...")
                        for rurl in acc_reply_urls:
                            found = browser_ctrl.collect_targets_from_replies(rurl, scroll_limit=scroll_limit, max_targets=target_read_limit)
                            for item in found:
                                if isinstance(item, dict):
                                    user = item.get("screen_name") or item.get("username") or ""
                                    t_url = item.get("tweet_url", "")
                                    d_name = item.get("display_name", "")
                                else:
                                    user = str(item)
                                    t_url = ""
                                    d_name = ""
                                config_mgr.append_discovered_target(user, f"リプライ:{rurl}", target_path_override=acc_target_path, tweet_url=t_url, display_name=d_name)
                                clean_u = normalize_screen_name(user)
                                if clean_u and not any(t.get("アカウント名") == clean_u for t in mode_targets):
                                    mode_targets.append({
                                        "アカウント名": clean_u,
                                        "ターゲットURL": t_url if t_url else f"https://x.com/{clean_u}",
                                        "ツイートURL": t_url,
                                        "個別メッセージ": "",
                                        "ステータス": "未送信",
                                        "表示名": d_name
                                    })
                            if target_read_limit > 0 and len(mode_targets) >= target_read_limit:
                                break
                    else:
                        log(f"   ℹ️ [URLリスト_リプライ] 「URLリスト_リプライ」シートに対象URLが未登録のため、このモードをスキップして次のモードへ進みます。")

                elif m_type == "FOLLOW":
                    if acc_follow_urls:
                        log(f"👥 [URLリスト_フォロー] ターゲット収集を開始 (登録URL: {len(acc_follow_urls)}件 / 上限: {target_read_limit}件)...")
                        for furl in acc_follow_urls:
                            found = browser_ctrl.collect_targets_from_follow(furl, scroll_limit=scroll_limit, max_targets=target_read_limit)
                            for item in found:
                                if isinstance(item, dict):
                                    user = item.get("screen_name") or item.get("username") or ""
                                    t_url = item.get("tweet_url", "")
                                    d_name = item.get("display_name", "")
                                else:
                                    user = str(item)
                                    t_url = ""
                                    d_name = ""
                                config_mgr.append_discovered_target(user, f"フォロー:{furl}", target_path_override=acc_target_path, tweet_url=t_url, display_name=d_name)
                                clean_u = normalize_screen_name(user)
                                if clean_u and not any(t.get("アカウント名") == clean_u for t in mode_targets):
                                    mode_targets.append({
                                        "アカウント名": clean_u,
                                        "ターゲットURL": t_url if t_url else f"https://x.com/{clean_u}",
                                        "ツイートURL": t_url,
                                        "個別メッセージ": "",
                                        "ステータス": "未送信",
                                        "表示名": d_name
                                    })
                            if target_read_limit > 0 and len(mode_targets) >= target_read_limit:
                                break
                    else:
                        log(f"   ℹ️ [URLリスト_フォロー] 「URLリスト_フォロー」シートに対象URLが未登録のため、このモードをスキップして次のモードへ進みます。")

                elif m_type == "LIST":
                    log(f"📑 [リスト送信] Excel「{os.path.basename(acc_target_path)}」の未送信ターゲットを読込中...")
                    list_targets = config_mgr.load_targets(limit=target_read_limit if target_read_limit > 0 else 0, target_path_override=acc_target_path)
                    if list_targets:
                        mode_targets = list_targets
                        log(f"   ✨ 未送信ターゲット {len(mode_targets)} 件を読み込みました。")
                    else:
                        log(f"   ℹ️ 未送信のターゲットが0件です。")

                elif m_type == "MUTUAL":
                    log(f"🤝 [相互フォロー検索] ターゲット収集を開始 (キーワード: 「相互フォロー」 / 上限: {target_read_limit}件)...")
                    found = browser_ctrl.collect_targets_from_search("相互フォロー", search_type="最新", scroll_limit=scroll_limit, max_targets=target_read_limit)
                    for item in found:
                        if isinstance(item, dict):
                            user = item.get("screen_name") or item.get("username") or ""
                            t_url = item.get("tweet_url", "")
                            d_name = item.get("display_name", "")
                        else:
                            user = str(item)
                            t_url = ""
                            d_name = ""
                        config_mgr.append_discovered_target(user, "相互フォロー検索:相互フォロー(最新)", target_path_override=acc_target_path, tweet_url=t_url, display_name=d_name)
                        clean_u = normalize_screen_name(user)
                        if clean_u and not any(t.get("アカウント名") == clean_u for t in mode_targets):
                            mode_targets.append({
                                "アカウント名": clean_u,
                                "ターゲットURL": t_url if t_url else f"https://x.com/{clean_u}",
                                "ツイートURL": t_url,
                                "個別メッセージ": "",
                                "ステータス": "未送信",
                                "表示名": d_name
                            })
                        if target_read_limit > 0 and len(mode_targets) >= target_read_limit:
                            break

                # このモードで取得されたターゲットのアクション実行
                # ★ 連続処理件数範囲: 最小 2 件 〜 最大 5 件 を厳密に適用
                # 受信トレイ閉鎖時はカウントに含めず、目標の送信成功数に達するまでスキップして次のターゲットへ継続
                if mode_targets:
                    if active_actions:
                        batch_target_count = random.randint(min_batch, max_batch)
                        log("")
                        log(f"📋 【{m_label} - 実行決定】 取得ターゲット: {len(mode_targets)}件 / 今回の目標連続実行数: {batch_target_count}件 (連続処理範囲: 最小{min_batch}件 〜 最大{max_batch}件)")
                        log(f"   📌 受信トレイ閉鎖（Xナンバー要求等）や送信不可時はカウントせず、目標達成まで順次スキップして次のターゲットを処理します。")

                        mode_success_count = 0
                        for t_idx, target in enumerate(mode_targets, start=1):
                            if mode_success_count >= batch_target_count:
                                log(f"🎯 今回の連続処理目標件数 ({batch_target_count}件) の送信を無事達成・完了しました！")
                                break

                            is_ok, res_detail = execute_target_action(target, t_idx, len(mode_targets), current_success_count=mode_success_count, target_batch_goal=batch_target_count)
                            if is_ok:
                                mode_success_count += 1
                                total_actions_performed += 1

                                # 目標未達かつ次のターゲットが存在する場合のみ、BAN対策待機
                                if mode_success_count < batch_target_count and t_idx < len(mode_targets):
                                    wait_time = random.uniform(min_sec, max_sec)
                                    log(f"☕ 次のターゲットまで {wait_time:.1f} 秒待機します（BAN対策ランダムインターバル: 設定範囲 {min_sec:.0f}秒〜{max_sec:.0f}秒 から自動決定 / 今回の進捗: {mode_success_count}/{batch_target_count}件）...")
                                    start_wait = time.time()
                                    
                                    # 同一アカウントの連続送信中はブラウザを開いたまま自然に待機（不要な開閉を防止）
                                    # ※ 待機時間が300秒(5分)以上の超長時間待機の場合のみ、省メモリのため一旦閉じる
                                    close_during_wait = wait_time >= 300.0
                                    if close_during_wait:
                                        log(f"🚪 [超長時間待機] 待機時間（{wait_time:.1f}秒）のため、ブラウザウィンドウを一旦安全に閉じます...")
                                        try:
                                            browser_ctrl.close()
                                        except Exception as ce:
                                            log(f"   (ブラウザ一時終了通知: {ce})")

                                    while time.time() - start_wait < wait_time:
                                        check_app_termination(base_dir, browser_ctrl)
                                        check_pause_state("ターゲット間待機中", browser_ctrl)
                                        time.sleep(min(0.5, max(0.1, wait_time - (time.time() - start_wait))))

                                    if close_during_wait:
                                        log("🚀 待機時間が終了しました。ブラウザウィンドウを再開して次のターゲット処理を再開します...")
                                        try:
                                            browser_ctrl.start()
                                        except Exception as se:
                                            log(f"   ⚠️ ブラウザ再起動エラー: {se}")

                        log("")
                        log(f"✅ 【モード {mode_idx}/{total_modes}: {m_label}】の処理が完了しました (実送信成功: {mode_success_count}件 / 目標: {batch_target_count}件)")
                    else:
                        log(f"ℹ️ アクション命令（DM/いいね/フォロー等）がすべてOFFのため、【{m_label}】はターゲット収集のみ完了しました。")
                else:
                    log(f"ℹ️ 【モード {mode_idx}/{total_modes}: {m_label}】では有効なターゲットが0件でした。次の設定モードへ進みます。")

            # --- 全モード終了後のフォールバック＆新規アカウント救済 ---
            if total_actions_performed == 0:
                log("")
                log("⚠️ 【全モード完了確認】設定されたモードによる実行件数が0件でした。Excel内の登録ターゲットまたは自動救済を確認します...")
                
                # 1. Excel登録済み未送信ターゲット確認
                existing_targets = config_mgr.load_targets(limit=target_read_limit if target_read_limit > 0 else 0, target_path_override=acc_target_path)
                if existing_targets:
                    log(f"🔄 【自動切り替え】Excel「{os.path.basename(acc_target_path)}」の未送信登録ターゲット（{len(existing_targets)}件）に自動切り替えて実行します。")
                    batch_target_count = random.randint(min_batch, max_batch)
                    log(f"📋 【未送信ターゲット実行】 今回の目標連続実行数: {batch_target_count}件 (連続処理範囲: 最小{min_batch}件 〜 最大{max_batch}件)")
                    fb_success_count = 0
                    for t_idx, target in enumerate(existing_targets, start=1):
                        if fb_success_count >= batch_target_count:
                            log(f"🎯 目標件数 ({batch_target_count}件) の送信を完了しました！")
                            break
                        is_ok, _ = execute_target_action(target, t_idx, len(existing_targets), current_success_count=fb_success_count, target_batch_goal=batch_target_count)
                        if is_ok:
                            fb_success_count += 1
                            total_actions_performed += 1
                            if fb_success_count < batch_target_count and t_idx < len(existing_targets):
                                wait_time = random.uniform(min_sec, max_sec)
                                log(f"☕ 次のターゲットまで {wait_time:.1f} 秒待機します（BAN対策ランダムインターバル: 設定範囲 {min_sec:.0f}秒〜{max_sec:.0f}秒 から自動決定 / 今回の進捗: {fb_success_count}/{batch_target_count}件）...")
                                start_wait = time.time()
                                while time.time() - start_wait < wait_time:
                                    check_app_termination(base_dir, browser_ctrl)
                                    check_pause_state("ターゲット間待機中", browser_ctrl)
                                    time.sleep(min(0.5, max(0.1, wait_time - (time.time() - start_wait))))
                else:
                    # 2. 救済モード (相互フォロー最新検索)
                    log(f"💡 【新規アカウント救済モード】設定モードおよび登録ターゲットが未設定のため、自動で標準キーワード「相互フォロー (最新)」からアクティブユーザーを探索・収集します...")
                    auto_found = browser_ctrl.collect_targets_from_search("相互フォロー", search_type="最新", scroll_limit=scroll_limit, max_targets=target_read_limit)
                    if auto_found:
                        rescue_targets = []
                        for item in auto_found:
                            if isinstance(item, dict):
                                user = item.get("screen_name") or item.get("username") or ""
                                t_url = item.get("tweet_url", "")
                                d_name = item.get("display_name", "")
                            else:
                                user = str(item)
                                t_url = ""
                                d_name = ""
                            config_mgr.append_discovered_target(user, "自動救済:相互フォロー(最新)", target_path_override=acc_target_path, tweet_url=t_url, display_name=d_name)
                            clean_u = normalize_screen_name(user)
                            if clean_u and not any(t.get("アカウント名") == clean_u for t in rescue_targets):
                                rescue_targets.append({
                                    "アカウント名": clean_u,
                                    "ターゲットURL": t_url if t_url else f"https://x.com/{clean_u}",
                                    "ツイートURL": t_url,
                                    "個別メッセージ": "",
                                    "ステータス": "未送信",
                                    "表示名": d_name
                                })
                        if rescue_targets:
                            batch_target_count = random.randint(min_batch, max_batch)
                            log(f"✨ 【救済収集完了】新規アクティブターゲット {len(rescue_targets)} 件を取得しました。目標連続実行数: {batch_target_count}件")
                            rs_success_count = 0
                            for t_idx, target in enumerate(rescue_targets, start=1):
                                if rs_success_count >= batch_target_count:
                                    log(f"🎯 目標件数 ({batch_target_count}件) の送信を完了しました！")
                                    break
                                is_ok, _ = execute_target_action(target, t_idx, len(rescue_targets), current_success_count=rs_success_count, target_batch_goal=batch_target_count)
                                if is_ok:
                                    rs_success_count += 1
                                    total_actions_performed += 1
                                    if rs_success_count < batch_target_count and t_idx < len(rescue_targets):
                                        wait_time = random.uniform(min_sec, max_sec)
                                        log(f"☕ 次のターゲットまで {wait_time:.1f} 秒待機します（BAN対策ランダムインターバル: 設定範囲 {min_sec:.0f}秒〜{max_sec:.0f}秒 から自動決定 / 今回の進捗: {rs_success_count}/{batch_target_count}件）...")
                                        start_wait = time.time()
                                        while time.time() - start_wait < wait_time:
                                            check_app_termination(base_dir, browser_ctrl)
                                            check_pause_state("ターゲット間待機中", browser_ctrl)
                                            time.sleep(min(0.5, max(0.1, wait_time - (time.time() - start_wait))))
                        else:
                            log(f"ℹ️ 救済探索でもターゲットが取得できませんでした。安全にセッションを終了します。")
                    else:
                        log(f"ℹ️ 有効なターゲットが見つかりませんでした。安全に今回のアカウント処理を終了します。")

        except Exception as e:
            log(f"⚠️ アカウント [{account_id}] の処理中に予期せぬエラーが発生しました: {e}。このアカウントを安全にスキップし、次のアカウントへ進みます。")
            traceback.print_exc()
        finally:
            check_pause_state("ブラウザ終了前")
            if browser_ctrl:
                try:
                    browser_ctrl.close()
                except Exception:
                    pass
            if acquired_semaphore and slot_semaphore is not None:
                try:
                    slot_semaphore.release()
                except Exception:
                    pass
            log(f"🏁 アカウント [{account_id}] のブラウザを完全に閉じました (ログ: {acc_logger.log_path})")
            log(f"   ✨ メモリ完全開放完了（スロット{active_slot}解放 / 低スペックPC対応）")
            set_current_logger(None)

            # このアカウントの次回実行可能時刻（アカウント固有の実行間隔後）を記録
            next_interval = random.uniform(account_min_sec, account_max_sec)
            account_ready_times[clean_account_id] = time.time() + next_interval
            log(f"⏰ 次回のアカウント [{account_id}] 実行可能時間: 約{next_interval:.1f}秒後 (実行間隔設定: {account_min_sec:.0f}秒 〜 {account_max_sec:.0f}秒)")

        # ★ 提案1: 「1窓・待機時間ごとにローテーション」 次のアカウント案内
        if acc_idx < total_acc_count:
            next_acc_obj = accounts[acc_idx]
            next_raw_id = get_acc_val(next_acc_obj, ["アカウントid", "アカウント名", "ユーザー名", "id"], "").strip() or f"アカウント#{acc_idx+1}"
            next_name = normalize_screen_name(next_raw_id) or f"acc_{acc_idx+1}"
            next_min, next_max = extract_account_interval_seconds(next_acc_obj, 300.0, 900.0)
            log("")
            log(f"{'-'*58}")
            log(f"🔒 【提案1: 1窓ローテーション切替】アカウント [{account_id}] 終了 → 次のアカウント [{next_name}] ({acc_idx + 1}/{total_acc_count}件目) へ")
            log(f"   💡 同一PC・同一IPからの同時アクセスをゼロ化し、アカウント凍結・制限リスクを最小化")
            if next_name in account_ready_times and account_ready_times[next_name] > time.time():
                wait_left = account_ready_times[next_name] - time.time()
                log(f"   ⏰ アカウント [{next_name}] の次回待機時間: あと{wait_left:.0f}秒後に自動起動")
            else:
                log(f"   🚀 アカウント [{next_name}] を待ち時間なしで直ちに起動します")
            log(f"{'-'*58}")

    # === 実行モード判定 (1窓〜最大5窓同時並列実行) ===
    env_concurrent = os.environ.get("TOOL_MAX_CONCURRENT", "").strip()
    global_max_concurrent = 1
    if env_concurrent.isdigit() and 1 <= int(env_concurrent) <= 5:
        global_max_concurrent = int(env_concurrent)
    else:
        global_max_concurrent = config_mgr.load_max_concurrent()

    log("")
    log(f"🪟 【ブラウザ起動モード】: {f'⚡ {global_max_concurrent}窓同時並列実行モード (最大{global_max_concurrent}アカウント同時進行)' if global_max_concurrent > 1 else '🔒 1窓完全ローテーションモード (1画面ずつ順番・超安全・省メモリ推奨)'}")

    if global_max_concurrent == 1:
        log("📋 【1窓順次ローテーション】全アカウントを1画面ずつ順番に待機時間を確認しながら実行します。")
        for acc_idx, acc in enumerate(accounts, start=1):
            process_single_account(acc, acc_idx, total_acc_count, active_slot=1, slot_semaphore=None)
    else:
        log(f"⚡ 【{global_max_concurrent}窓同時並列実行】時間が被ったアカウントは最大{global_max_concurrent}画面まで同時に独立ブラウザ（独立Cookie/独立DB/独立ログ）で並行稼働します。")
        log(f"   ※{global_max_concurrent}枠が埋まっている間に待機時間が到来した場合は、待機キューでスタンバイし、空き枠が出次第0秒即時起動します。")
        slot_semaphore = threading.Semaphore(global_max_concurrent)
        threads = []
        for acc_idx, acc in enumerate(accounts, start=1):
            t = threading.Thread(
                target=process_single_account,
                args=(acc, acc_idx, total_acc_count, ((acc_idx - 1) % global_max_concurrent) + 1, slot_semaphore),
                name=f"Thread-Acc-{acc_idx}"
            )
            threads.append(t)
            t.start()
            time.sleep(0.5)

        for t in threads:
            t.join()

    log(f"\n🏁 【第 {cycle_count} 周回 完了】 今回の巡回対象アカウント処理がすべて完了しました。")
    return cycle_min_sec, cycle_max_sec

def main():
    print("=" * 65)
    print("  X (Twitter) 自動DM・リプライ・送信ツール Pro - Windows版")
    print("  Playwright x 人間らしいタイピング x ベジェ曲線 x BAN対策")
    print("  ★ 無限ループ巡回モード: 1周終了後に待機秒数待機し、自動継続実行")
    print("=" * 65)

    base_dir = get_base_dir()
    account_config_path = os.path.join(base_dir, "アカウント情報.xlsx")
    default_target_path = os.path.join(base_dir, "アカウント名.xlsx")
    if not os.path.exists(default_target_path):
        default_target_path = os.path.join(base_dir, "アカウント名情報.xlsx")
    config_mgr = ConfigManager(account_config_path, default_target_path, base_dir=base_dir)

    # アカウント情報に登録されているアカウント名.xlsx (例: wqwq1320.xlsx) を最優先で探索・設定
    target_list_path = default_target_path
    try:
        accs = config_mgr.load_accounts()
        for a in accs:
            aid = normalize_screen_name(str(a.get("アカウントID", "") or a.get("アカウント名", "") or a.get("ユーザー名", "") or ""))
            if aid:
                p = config_mgr.get_target_path_for_account(aid)
                if p and os.path.exists(p):
                    target_list_path = p
                    config_mgr.target_list_path = p
                    break
    except Exception:
        pass
    db_path = os.path.join(base_dir, "アカウント名.db")
    db_mgr = DatabaseManager(db_path)

    account_ready_times = {}
    cycle_count = 0
    while True:
        cycle_count += 1
        log("")
        log(f"{'#'*65}")
        log(f"🔄 【第 {cycle_count} 周回 巡回開始】 設定ファイル・ターゲットを読み込んで実行します (無限ループ稼働中)")
        log(f"   💡 提案1: 常に1画面のみ / 提案2: メディア通信遮断 / アカウント別待機タイマー制御")
        log(f"{'#'*65}")

        try:
            cycle_min_sec, cycle_max_sec = run_single_cycle(config_mgr, db_mgr, base_dir, cycle_count, account_ready_times)
        except Exception as cycle_err:
            log(f"⚠️ 第 {cycle_count} 周回中に予期せぬエラーが発生しました: {cycle_err}。")
            traceback.print_exc()

        log("")
        log(f"{'='*65}")
        log(f"🏁 【第 {cycle_count} 周回 完了】 すべてのアカウントの処理が一巡しました。")
        log(f"💡 ※ 自動で無限ループ巡回を継続します。各アカウントの設定待機時間が来たら順次自動起動します。")
        log(f"{'='*65}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 ユーザー操作（Ctrl+C または停止命令）により自動実行を停止しました。")
    except Exception as e:
        print(f"\n致命的なエラーが発生しました: {e}")
        traceback.print_exc()
    finally:
        # コマンドプロンプト直接起動時のみキー待ち、GUI・パイプ実行時は即座に終了してEOFErrorを回避
        try:
            if sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
                print("\n" + "=" * 65)
                input("処理が終了しました。Enterキーを押すとウィンドウを閉じます...")
        except (EOFError, KeyboardInterrupt, Exception):
            pass
