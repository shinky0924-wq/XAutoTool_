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
        try:
            from ai_generator import AIDedicatedWindowManager
            AIDedicatedWindowManager.close_ai_window()
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

        auth_token = get_acc_val(acc, ["auth_token", "authtoken", "セッションid", "session_id", "token", "クッキー", "cookie"], "").strip()
        password = get_acc_val(acc, ["アカウントpw", "アカウントパスワード", "パスワード", "password", "pass", "pw"], "").strip()

        # アカウント専用Excel (例: wqwq1320.xlsx) から個別動作設定 (動作設定シート・全体動作パラメータ) を読込
        acc_target_path = config_mgr.get_target_path_for_account(clean_account_id)
        acc_settings = config_mgr.load_account_settings(clean_account_id, target_path_override=acc_target_path, fallback_acc=acc)

        dm_passcode = str(acc_settings.get("dm_passcode", "") or "").strip()
        if not dm_passcode:
            dm_passcode = get_acc_val(acc, ["dmパスコード", "パスコード", "暗号化パスコード", "暗号化キー", "暗号化dm", "pin", "dmpin", "復号", "2fa", "認証コード", "dmpasscode", "passcode"], "").strip()
        if not dm_passcode:
            try:
                from db_manager import get_saved_passcodes
                saved_passes = get_saved_passcodes(base_dir)
                dm_passcode = saved_passes.get(clean_account_id, "") or saved_passes.get(normalize_screen_name(account_id), "") or saved_passes.get(str(account_id).strip(), "")
            except Exception:
                pass

        target_mode = acc_settings.get("target_mode") or get_acc_val(acc, ["取得モード", "対象取得モード", "ターゲットモード", "検索モード", "モード", "抽出モード"], "").strip()
        scroll_limit = acc_settings.get("scroll_limit", 5)
        target_read_limit = acc_settings.get("target_count", 15)
        block_media = acc_settings.get("block_media", True)
        ai_engine = acc_settings.get("ai_engine", "gemini")
        ai_dedicated_window = bool(acc_settings.get("ai_dedicated_window", True))
        ai_api_key = acc_settings.get("ai_api_key", "")
        ai_prompt_extra = acc_settings.get("ai_prompt", "")

        target_min_sec = float(acc_settings.get("target_min_sec", 60.0))
        target_max_sec = float(acc_settings.get("target_max_sec", 150.0))

        # アクション個別設定の取得 (DM, いいね, フォロー, リプライ, 引用RT, リプライAI生成, 引用RT AI生成)
        actions_cfg = acc_settings.get("actions", {})
        dm_cfg = actions_cfg.get("dm", {})
        like_cfg = actions_cfg.get("like", {})
        follow_cfg = actions_cfg.get("follow", {})
        reply_cfg = actions_cfg.get("reply", {})
        quote_cfg = actions_cfg.get("quote", {})
        reply_ai_cfg = actions_cfg.get("reply_ai", {})
        quote_ai_cfg = actions_cfg.get("quote_ai", {})

        def parse_mode_list(raw_mode_val: str) -> list[tuple[str, str]]:
            raw_s = str(raw_mode_val or "").strip()
            mode_clean = raw_s.replace(chr(10), ",").replace(chr(13), ",").replace("　", " ")
            parts = [p.strip() for p in re.split(r'[,、+/|]', mode_clean) if p.strip()]
            parsed = []
            for part in parts:
                p_clean = part.replace(" ", "").replace("（", "(").replace("）", ")").lower()
                if any(k in p_clean for k in ["リプライ", "reply"]):
                    parsed.append(("URL_REPLY", f"URLリスト（リプライ） ({part})"))
                elif ("home" in p_clean or "ホーム" in p_clean) and any(k in p_clean for k in ["フォロー中", "following"]):
                    parsed.append(("HOME_FOLLOWING", f"HOMEフォロー中 ({part})"))
                elif "home" in p_clean or "ホーム" in p_clean:
                    parsed.append(("HOME_FOR_YOU", f"HOMEおすすめ ({part})"))
                elif any(k in p_clean for k in ["フォロー", "follow"]) and "相互" not in p_clean:
                    parsed.append(("URL_FOLLOW", f"URLリスト（フォロー） ({part})"))
                elif any(k in p_clean for k in ["話題", "top", "人気"]):
                    parsed.append(("SEARCH_TOP", f"検索話題のポスト検索 ({part})"))
                elif any(k in p_clean for k in ["ニュース", "news"]):
                    parsed.append(("NEWS", f"ニュース ({part})"))
                elif any(k in p_clean for k in ["トレンド", "trend"]):
                    parsed.append(("TRENDS", f"トレンド ({part})"))
                elif p_clean in ["1", "1番", "１", "１番", "①", "01"] or any(k in p_clean for k in ["検索", "キーワード", "search", "keyword", "クエリ", "最新"]):
                    parsed.append(("SEARCH_LATEST", f"検索最新 ({part})"))
                elif "おすすめ" in p_clean:
                    parsed.append(("HOME_FOR_YOU", f"HOMEおすすめ ({part})"))
                elif p_clean in ["2", "2番", "２", "２番", "②", "02"]:
                    parsed.append(("URL_REPLY", f"URLリスト（リプライ） ({part})"))
                elif p_clean in ["3", "3番", "３", "３番", "③", "03"]:
                    parsed.append(("URL_FOLLOW", f"URLリスト（フォロー） ({part})"))
                elif p_clean in ["4", "4番", "４", "４番", "④", "04"] or any(k in p_clean for k in ["リスト", "ターゲット", "履歴", "list"]):
                    parsed.append(("LIST", f"リスト送信 ({part})"))
                elif p_clean in ["5", "5番", "５", "５番", "⑤", "05"] or any(k in p_clean for k in ["相互フォロー", "相互", "mutual"]):
                    parsed.append(("MUTUAL", f"相互フォロー検索 ({part})"))
                else:
                    parsed.append(("SEARCH_LATEST", f"検索最新 ({part})"))
            if not parsed:
                parsed = [("SEARCH_LATEST", "検索最新 (デフォルト)")]
            return parsed

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
        acc_prompt_setting = config_mgr.load_prompt_settings(target_path_override=acc_target_path)

        log(f"\n{'='*58}")
        log(f"🚀 アカウント [{account_id}] ({acc_idx}/{total_acc_count}件目) のスロット{active_slot}セッションを開始します")
        log(f"   📂 ターゲット/設定ファイル: {os.path.basename(acc_target_path)} (個別動作設定シート適用)")
        log(f"   ⚡ メディア読込ブロック: {'ON (省メモリ・超高速)' if block_media else 'OFF (通常)'}")
        log(f"   スクロール上限: {scroll_limit}回 / 読み込みターゲット件数上限: {target_read_limit}件")
        log(f"   AIエンジン: {ai_engine}")
        log(f"   AI専用ウィンドウ連携: {'ON (専用ウィンドウ起動・連携)' if ai_dedicated_window else 'OFF (API直接生成)'}")
        log(f"   📋 アクション個別設定 (アカウント名.xlsx「動作設定」シート準拠):")
        action_label_map = {
            "dm": "命令実行 DM",
            "like": "命令実行 いいね",
            "follow": "命令実行 フォロー",
            "reply": "命令実行 リプライ",
            "quote": "命令実行 引用リツイート",
            "reply_ai": "リプライAI生成",
            "quote_ai": "引用リツイートAI生成",
        }
        active_action_jobs = []
        for act_key in ["follow", "like", "reply", "reply_ai", "dm", "quote", "quote_ai"]:
            a_cfg = actions_cfg.get(act_key, {})
            if a_cfg.get("enabled"):
                act_mode_str = a_cfg.get("mode") or target_mode or "検索最新"
                act_title = action_label_map.get(act_key, act_key.upper())
                active_action_jobs.append((act_key, act_title, a_cfg))
                log(f"      ・{act_title}: モード=[{act_mode_str}], 間隔={a_cfg.get('min_interval', 0):.0f}〜{a_cfg.get('max_interval', 0):.0f}秒, 連続={a_cfg.get('min_batch', 1)}〜{a_cfg.get('max_batch', 1)}件, AI生成={'ON' if (a_cfg.get('ai_gen') or act_key.endswith('_ai')) else 'OFF'}")
        if not active_action_jobs:
            log("      ・⚠️ 有効なアクションがありません（すべてOFF設定）")
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
                log(f"⏳ 【並列上限到達】アカウント [{clean_account_id}] の待機時間が到来しましたが、現在上限枠が稼働中です。空き枠が出るまで待機キューでスタンバイします...")
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

            # AIアクションが有効な場合、専用ウィンドウ連携ONまたはWeb AIエンジン/URL指定時は即座に専用ウィンドウを起動・遷移しておく
            uses_ai_in_jobs = any((a_cfg.get("ai_gen") or a_key.endswith("_ai")) for a_key, _, a_cfg in active_action_jobs)
            if uses_ai_in_jobs and ai_engine and str(ai_engine).strip().lower() not in ["none", "off", "無効"]:
                try:
                    from ai_generator import AIGenerator, AIDedicatedWindowManager
                    if AIGenerator.should_launch_ai_window(ai_engine, ai_api_key, ai_dedicated_window):
                        eff_eng, eff_url = AIGenerator.resolve_engine_and_url(ai_engine, ai_api_key)
                        log(f"🪟 [AI専用ウィンドウ] AI生成専用ブラウザウィンドウを起動・準備しています (エンジン: {eff_eng}" + (f" / URL: {eff_url}" if eff_url else "") + ")...")
                        AIDedicatedWindowManager.get_or_create_ai_page(
                            browser_ctrl.playwright,
                            headless=False,
                            base_dir=base_dir,
                            initial_engine=ai_engine,
                            has_api_key=AIGenerator.has_api_key(ai_engine, ai_api_key),
                            slot_id=active_slot,
                            connection_url=ai_api_key
                        )
                except Exception as ai_win_e:
                    log(f"   ⚠️ AI専用ウィンドウ先行起動通知: {ai_win_e}")

            # --- モード別ターゲット収集 (同一アカウント内キャッシュあり) ---
            mode_targets_cache = {}

            def append_collected_items(found_items, source_label: str, dest_list: list):
                for item in found_items:
                    if isinstance(item, dict):
                        user = item.get("screen_name") or item.get("username") or ""
                        t_url = item.get("tweet_url", "")
                        d_name = item.get("display_name", "")
                        t_text = item.get("tweet_text", "")
                    else:
                        user = str(item)
                        t_url = ""
                        d_name = ""
                        t_text = ""
                    config_mgr.append_discovered_target(user, source_label, target_path_override=acc_target_path, tweet_url=t_url, display_name=d_name)
                    clean_u = normalize_screen_name(user)
                    if clean_u and not any(t.get("アカウント名") == clean_u for t in dest_list):
                        dest_list.append({
                            "アカウント名": clean_u,
                            "ターゲットURL": t_url if t_url else f"https://x.com/{clean_u}",
                            "ツイートURL": t_url,
                            "ツイート本文": t_text,
                            "個別メッセージ": "",
                            "ステータス": "未送信",
                            "表示名": d_name
                        })

            def collect_targets_for_mode(m_type: str, m_label: str) -> list[dict]:
                if m_type in mode_targets_cache:
                    cached = mode_targets_cache[m_type]
                    log(f"   ♻️ [{m_label}] 収集済みキャッシュから {len(cached)} 件のターゲットを再利用します")
                    return cached

                check_pause_state(f"モード [{m_label}] ターゲット収集前")
                collected = []

                if m_type in ["SEARCH_LATEST", "SEARCH_TOP"]:
                    stype_override = "おすすめ" if m_type == "SEARCH_TOP" else "最新"
                    if acc_search_keywords:
                        log(f"🔍 [{m_label}] ターゲット収集開始 (登録キーワード: {len(acc_search_keywords)}件 / 上限: {target_read_limit}件)...")
                        for kw_item in acc_search_keywords:
                            kw = kw_item.get("keyword")
                            found = browser_ctrl.collect_targets_from_search(kw, search_type=stype_override, scroll_limit=scroll_limit, max_targets=target_read_limit)
                            append_collected_items(found, f"{m_label}:{kw}", collected)
                            if target_read_limit > 0 and len(collected) >= target_read_limit:
                                break
                    else:
                        log(f"   ℹ️ [{m_label}] 「検索キーワード」シートにキーワード未登録のため、標準「相互フォロー」で探索します。")
                        found = browser_ctrl.collect_targets_from_search("相互フォロー", search_type=stype_override, scroll_limit=scroll_limit, max_targets=target_read_limit)
                        append_collected_items(found, f"{m_label}:相互フォロー", collected)

                elif m_type == "HOME_FOR_YOU":
                    log(f"🏠 [HOMEおすすめ] タイムラインからターゲット収集開始 (上限: {target_read_limit}件)...")
                    found = browser_ctrl.collect_targets_from_home(tab_type="おすすめ", scroll_limit=scroll_limit, max_targets=target_read_limit)
                    append_collected_items(found, "HOMEおすすめ", collected)

                elif m_type == "HOME_FOLLOWING":
                    log(f"🏠 [HOMEフォロー中] タイムラインからターゲット収集開始 (上限: {target_read_limit}件)...")
                    found = browser_ctrl.collect_targets_from_home(tab_type="フォロー中", scroll_limit=scroll_limit, max_targets=target_read_limit)
                    append_collected_items(found, "HOMEフォロー中", collected)

                elif m_type == "URL_REPLY":
                    if acc_reply_urls:
                        log(f"💬 [URLリスト（リプライ）] ターゲット収集開始 (登録URL: {len(acc_reply_urls)}件 / 上限: {target_read_limit}件)...")
                        for rurl in acc_reply_urls:
                            found = browser_ctrl.collect_targets_from_replies(rurl, scroll_limit=scroll_limit, max_targets=target_read_limit)
                            append_collected_items(found, f"URLリスト（リプライ）:{rurl}", collected)
                            if target_read_limit > 0 and len(collected) >= target_read_limit:
                                break
                    else:
                        log(f"   ℹ️ [URLリスト（リプライ）] 「URLリスト_リプライ」シートに対象URLが未登録のためスキップします。")

                elif m_type == "URL_FOLLOW":
                    if acc_follow_urls:
                        log(f"👥 [URLリスト（フォロー）] ターゲット収集開始 (登録URL: {len(acc_follow_urls)}件 / 上限: {target_read_limit}件)...")
                        for furl in acc_follow_urls:
                            found = browser_ctrl.collect_targets_from_follow(furl, scroll_limit=scroll_limit, max_targets=target_read_limit)
                            append_collected_items(found, f"URLリスト（フォロー）:{furl}", collected)
                            if target_read_limit > 0 and len(collected) >= target_read_limit:
                                break
                    else:
                        log(f"   ℹ️ [URLリスト（フォロー）] 「URLリスト_フォロー」シートに対象URLが未登録のためスキップします。")

                elif m_type == "NEWS":
                    log(f"📰 [ニュース] 探索タブからターゲット収集開始 (上限: {target_read_limit}件)...")
                    found = browser_ctrl.collect_targets_from_explore(tab_type="ニュース", scroll_limit=scroll_limit, max_targets=target_read_limit)
                    append_collected_items(found, "ニュース", collected)

                elif m_type == "TRENDS":
                    log(f"📈 [トレンド] 探索タブからターゲット収集開始 (上限: {target_read_limit}件)...")
                    found = browser_ctrl.collect_targets_from_explore(tab_type="トレンド", scroll_limit=scroll_limit, max_targets=target_read_limit)
                    append_collected_items(found, "トレンド", collected)

                elif m_type == "LIST":
                    log(f"📑 [リスト送信] Excel「{os.path.basename(acc_target_path)}」の未送信ターゲットを読込中...")
                    collected = config_mgr.load_targets(limit=target_read_limit if target_read_limit > 0 else 0, target_path_override=acc_target_path)

                elif m_type == "MUTUAL":
                    log(f"🤝 [相互フォロー検索] ターゲット収集開始 (上限: {target_read_limit}件)...")
                    found = browser_ctrl.collect_targets_from_search("相互フォロー", search_type="最新", scroll_limit=scroll_limit, max_targets=target_read_limit)
                    append_collected_items(found, "相互フォロー検索", collected)

                mode_targets_cache[m_type] = collected
                return collected

            def generate_ai_or_template_text(kind: str, target_screen_name: str, resolved_disp: str, tweet_url: str, use_ai: bool, cached_tweet_text: str = "") -> str:
                if use_ai and ai_engine and str(ai_engine).strip().lower() not in ["none", "off", "無効"]:
                    try:
                        from ai_generator import AIGenerator, AIDedicatedWindowManager
                        post_txt = (cached_tweet_text or "").strip()
                        if not post_txt and tweet_url:
                            try:
                                post_txt = browser_ctrl.get_tweet_text_from_url(tweet_url)
                            except Exception:
                                post_txt = ""
                        if not post_txt and target_screen_name:
                            try:
                                fetched_url, fetched_txt = browser_ctrl.get_latest_tweet_info(target_screen_name)
                                if fetched_txt:
                                    post_txt = fetched_txt
                                if fetched_url and not tweet_url:
                                    tweet_url = fetched_url
                            except Exception:
                                pass

                        custom_p = ai_prompt_extra or acc_prompt_setting
                        use_win_flow = AIGenerator.should_launch_ai_window(ai_engine, ai_api_key, ai_dedicated_window)
                        if use_win_flow:
                            ok_ai, ai_res = AIDedicatedWindowManager.generate_and_copy_via_ai_window(
                                playwright=browser_ctrl.playwright,
                                post_url=tweet_url,
                                post_text=post_txt,
                                engine=ai_engine,
                                action_type="reply" if kind == "reply" else "quote",
                                target_name=resolved_disp or target_screen_name,
                                api_key=ai_api_key,
                                custom_prompt=custom_p,
                                headless=False,
                                base_dir=base_dir,
                                slot_id=active_slot
                            )
                        else:
                            ok_ai, ai_res = AIGenerator.generate_comment(
                                engine=ai_engine,
                                post_url=tweet_url,
                                post_text=post_txt,
                                action_type="reply" if kind == "reply" else "quote",
                                target_name=resolved_disp or target_screen_name,
                                api_key=ai_api_key,
                                custom_prompt=custom_p,
                                page=browser_ctrl.page
                            )
                        if ok_ai and ai_res and ai_res.strip():
                            prefix_icon = "🪟 [AI専用ウィンドウ生成成功" if use_win_flow else "🤖 [AI生成成功"
                            log(f"   {prefix_icon}/{ai_engine}] 「{ai_res.strip()[:45]}...」")
                            return ai_res.strip()
                        else:
                            log(f"   ⚠️ AI生成フォールバック (理由: {ai_res}) → 定型テンプレートを使用します")
                    except Exception as ai_e:
                        log(f"   ⚠️ AI生成通知 ({ai_e}) → 定型テンプレートを使用します")

                if kind == "reply":
                    raw_msg = random.choice(acc_reply_templates) if acc_reply_templates else (random.choice(reply_templates) if reply_templates else "大変参考になります！有益な情報をありがとうございます✨")
                else:
                    raw_msg = random.choice(acc_quote_templates) if acc_quote_templates else (random.choice(quote_templates) if quote_templates else "大変有益なポストです！📌")
                return format_template_message(raw_msg, resolved_disp, target_screen_name, preserve_unresolved=True)

            # 単一アクションをターゲット1件に実行
            def execute_single_action_on_target(act_key: str, target_item: dict, target_idx: int, total_in_mode: int, current_success_count: int, target_batch_goal: int, use_ai: bool = False) -> tuple[bool, str]:
                nonlocal processed_count
                check_pause_state(f"{act_key.upper()} ターゲット [{target_idx}/{total_in_mode}] 実行前")
                target_screen_name = normalize_screen_name(target_item.get("アカウント名") or target_item.get("ターゲットURL") or target_item.get("ユーザー名") or "")
                if not target_screen_name:
                    return False, "INVALID_TARGET"

                target_tweet_url = str(target_item.get("ツイートURL") or target_item.get("tweet_url") or "").strip()
                raw_target_url = str(target_item.get("ターゲットURL") or target_item.get("url") or "").strip()
                if not target_tweet_url and "/status/" in raw_target_url:
                    target_tweet_url = raw_target_url

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

                db_action_map = {
                    "follow": "FOLLOW",
                    "like": "LIKE",
                    "reply": "REPLY",
                    "reply_ai": "REPLY",
                    "dm": "DM",
                    "quote": "QUOTE",
                    "quote_ai": "QUOTE",
                }
                db_action_type = db_action_map.get(act_key, act_key.upper())

                if db_mgr.is_already_sent(target_screen_name, action_type=db_action_type):
                    log(f"   ⏩ [{db_action_type}] @{target_screen_name} は過去に実行済みのためスキップ")
                    return False, "ALREADY_SENT"

                log("")
                if target_tweet_url:
                    log(f"🎯 [{act_key.upper()} {target_idx}/{total_in_mode}] ターゲット: @{target_screen_name} (取得ツイート: {target_tweet_url}) 実行開始 (成功: {current_success_count}/{target_batch_goal}件)...")
                else:
                    log(f"🎯 [{act_key.upper()} {target_idx}/{total_in_mode}] ターゲット: @{target_screen_name} 実行開始 (成功: {current_success_count}/{target_batch_goal}件)...")

                browser_ctrl.ensure_page()

                try:
                    if act_key == "follow":
                        st, det = browser_ctrl.follow_user(target_screen_name)
                        log(f"   👤 [フォロー結果] {st} ({det})")
                        get_target_name()
                        db_mgr.record_sent(target_screen_name, account_id, "FOLLOW", st, det)
                        status_label = "フォロー済" if st in ["SUCCESS", "ALREADY_FOLLOWING"] else f"フォロー:{st}"
                        config_mgr.update_target_status(target_screen_name, status_label, target_path_override=acc_target_path)
                        if st in ["SUCCESS", "ALREADY_FOLLOWING"]:
                            processed_count += 1
                            return True, "SUCCESS"
                        return False, st

                    elif act_key == "like":
                        st, det = browser_ctrl.like_recent_post(target_screen_name, tweet_url=target_tweet_url)
                        log(f"   ❤️ [いいね結果] {st} ({det})")
                        get_target_name()
                        db_mgr.record_sent(target_screen_name, account_id, "LIKE", st, det)
                        status_label = "いいね済" if st in ["SUCCESS", "ALREADY_LIKED"] else f"いいね:{st}"
                        config_mgr.update_target_status(target_screen_name, status_label, target_path_override=acc_target_path)
                        if st in ["SUCCESS", "ALREADY_LIKED"]:
                            processed_count += 1
                            return True, "SUCCESS"
                        return False, st

                    elif act_key in ["reply", "reply_ai"]:
                        resolved_disp = get_target_name()
                        target_tweet_text = str(target_item.get("ツイート本文") or target_item.get("tweet_text") or "").strip()
                        rep_msg = generate_ai_or_template_text("reply", target_screen_name, resolved_disp, target_tweet_url, use_ai=use_ai, cached_tweet_text=target_tweet_text)
                        preview_name = resolved_disp or "相手のアカウント名"
                        preview_log = format_template_message(rep_msg, preview_name, target_screen_name, preserve_unresolved=False)
                        log(f"   💬 [リプライ送信中] 「{preview_log[:35]}...」")
                        st, det = browser_ctrl.send_reply(target_screen_name, rep_msg, tweet_url=target_tweet_url)
                        log(f"   💬 [リプライ結果] {st} ({det})")
                        db_mgr.record_sent(target_screen_name, account_id, "REPLY", st, rep_msg[:40])
                        status_label = "リプライ完了" if st == "SUCCESS" else f"リプライ:{st}"
                        config_mgr.update_target_status(target_screen_name, status_label, target_path_override=acc_target_path)
                        if st == "SUCCESS":
                            processed_count += 1
                            return True, "SUCCESS"
                        return False, st

                    elif act_key == "dm":
                        dm_msg = target_item.get("個別メッセージ") or ""
                        if not dm_msg and acc_dm_templates:
                            dm_msg = random.choice(acc_dm_templates)
                        elif not dm_msg and dm_templates:
                            dm_msg = random.choice(dm_templates)
                        if not dm_msg:
                            dm_msg = "{name}さん、はじめまして！ご連絡ありがとうございます。今後ともよろしくお願いいたします。"

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
                            config_mgr.update_target_status(target_screen_name, "DM送信完了", target_path_override=acc_target_path)
                            processed_count += 1
                            return True, "SUCCESS"
                        elif st in ["INBOX_CLOSED", "DM_REJECTED", "LOCKED_ACCOUNT"]:
                            is_inbox_closed = (st == "INBOX_CLOSED") or ("受信トレイ" in det) or ("Xナンバー" in det)
                            status_label = "受信トレイ閉鎖(DM不可)" if is_inbox_closed else f"DMスキップ({det})"
                            config_mgr.update_target_status(target_screen_name, status_label, target_path_override=acc_target_path)
                            log(f"   ⏩ 【送信カウント除外】@{target_screen_name} は送信不可（{det}）のためスキップします（現在の成功: {current_success_count}/{target_batch_goal}件）")
                            time.sleep(random.uniform(1.8, 3.2))
                            return False, "SKIPPED_INBOX_CLOSED"
                        else:
                            config_mgr.update_target_status(target_screen_name, f"DMエラー({det})", target_path_override=acc_target_path)
                            return False, st

                    elif act_key in ["quote", "quote_ai"]:
                        resolved_disp = get_target_name()
                        target_tweet_text = str(target_item.get("ツイート本文") or target_item.get("tweet_text") or "").strip()
                        q_msg = generate_ai_or_template_text("quote", target_screen_name, resolved_disp, target_tweet_url, use_ai=use_ai, cached_tweet_text=target_tweet_text)
                        preview_name = resolved_disp or "相手のアカウント名"
                        preview_log = format_template_message(q_msg, preview_name, target_screen_name, preserve_unresolved=False)
                        log(f"   🔁 [引用RT実行中] 「{preview_log[:35]}...」")
                        st, det = browser_ctrl.quote_tweet(target_screen_name, q_msg, tweet_url=target_tweet_url)
                        log(f"   🔁 [引用RT結果] {st} ({det})")
                        db_mgr.record_sent(target_screen_name, account_id, "QUOTE", st, q_msg[:40])
                        status_label = "引用RT完了" if st == "SUCCESS" else f"引用RT:{st}"
                        config_mgr.update_target_status(target_screen_name, status_label, target_path_override=acc_target_path)
                        if st == "SUCCESS":
                            processed_count += 1
                            return True, "SUCCESS"
                        return False, st

                except Exception as target_err:
                    log(f"   ⚠️ ターゲット @{target_screen_name} の処理中にエラーが発生したためスキップします: {target_err}")
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

                return False, "NO_ACTION"

            def wait_between_targets(wait_time: float, label_prefix: str, cur_cnt: int, goal_cnt: int, act_min: float, act_max: float):
                log(f"☕ [{label_prefix}] 次のターゲットまで {wait_time:.1f} 秒待機します（実行間隔 {act_min:.0f}秒〜{act_max:.0f}秒 / 進捗: {cur_cnt}/{goal_cnt}件）...")
                start_wait = time.time()
                close_during_wait = wait_time >= 300.0
                if close_during_wait:
                    log(f"🚪 [超長時間待機] 待機時間（{wait_time:.1f}秒）のため、ブラウザウィンドウを一旦安全に閉じます...")
                    try:
                        from ai_generator import AIDedicatedWindowManager
                        AIDedicatedWindowManager.close_ai_window()
                    except Exception:
                        pass
                    try:
                        browser_ctrl.close()
                    except Exception as ce:
                        log(f"   (ブラウザ一時終了通知: {ce})")

                while time.time() - start_wait < wait_time:
                    check_app_termination(base_dir, browser_ctrl)
                    check_pause_state("ターゲット間待機中", browser_ctrl)
                    time.sleep(min(0.5, max(0.1, wait_time - (time.time() - start_wait))))

                if close_during_wait:
                    log("🚀 待機時間が終了しました。ブラウザウィンドウを再開して次の処理を再開します...")
                    try:
                        browser_ctrl.start()
                    except Exception as se:
                        log(f"   ⚠️ ブラウザ再起動エラー: {se}")

            total_actions_performed = 0

            # --- 各アクションを「アクションごとに設定されたモード」で順番実行 ---
            for act_key, act_title, a_cfg in active_action_jobs:
                act_modes = parse_mode_list(a_cfg.get("mode") or target_mode or "検索最新")
                act_min_int = float(a_cfg.get("min_interval", 60.0))
                act_max_int = float(a_cfg.get("max_interval", 180.0))
                if act_min_int > act_max_int:
                    act_min_int, act_max_int = act_max_int, act_min_int
                act_min_b = max(1, int(a_cfg.get("min_batch", 1)))
                act_max_b = max(act_min_b, int(a_cfg.get("max_batch", act_min_b)))
                use_ai = bool(a_cfg.get("ai_gen", False)) or act_key.endswith("_ai")

                log("")
                log(f"{'='*58}")
                log(f"⚡ 【アクション実行開始: {act_title}】")
                log(f"   設定モード ({len(act_modes)}個): {', '.join([lbl for _, lbl in act_modes])}")
                log(f"   実行間隔: {act_min_int:.0f}〜{act_max_int:.0f}秒 / 連続処理目標: 最小{act_min_b}〜最大{act_max_b}件 / AI生成: {'ON' if use_ai else 'OFF'}")
                log(f"{'='*58}")

                for mode_idx, (m_type, m_label) in enumerate(act_modes, start=1):
                    check_pause_state(f"{act_title} モード [{mode_idx}/{len(act_modes)}: {m_label}] 開始前")
                    mode_targets = collect_targets_for_mode(m_type, m_label)
                    if not mode_targets:
                        log(f"ℹ️ [{act_title} / {m_label}] 有効なターゲットが0件でした。次のモードへ進みます。")
                        continue

                    batch_target_count = random.randint(act_min_b, act_max_b)
                    log(f"📋 [{act_title} / {m_label}] 取得ターゲット: {len(mode_targets)}件 / 目標連続処理数: {batch_target_count}件")

                    mode_success_count = 0
                    for t_idx, target in enumerate(mode_targets, start=1):
                        if mode_success_count >= batch_target_count:
                            log(f"🎯 [{act_title} / {m_label}] 目標件数 ({batch_target_count}件) の処理を完了しました！")
                            break
                        is_ok, _ = execute_single_action_on_target(
                            act_key,
                            target,
                            t_idx,
                            len(mode_targets),
                            current_success_count=mode_success_count,
                            target_batch_goal=batch_target_count,
                            use_ai=use_ai
                        )
                        if is_ok:
                            mode_success_count += 1
                            total_actions_performed += 1
                            if mode_success_count < batch_target_count and t_idx < len(mode_targets):
                                wait_time = random.uniform(act_min_int, act_max_int)
                                wait_between_targets(wait_time, act_title, mode_success_count, batch_target_count, act_min_int, act_max_int)

                    log(f"✅ [{act_title} / {m_label}] 完了 (成功: {mode_success_count}件 / 目標: {batch_target_count}件)")

            # --- 全アクション終了後のフォールバック＆新規アカウント救済 ---
            if total_actions_performed == 0 and active_action_jobs:
                log("")
                log("⚠️ 【全モード完了確認】設定されたモードによる実行件数が0件でした。Excel内の未送信ターゲットまたは自動救済を確認します...")
                existing_targets = config_mgr.load_targets(limit=target_read_limit if target_read_limit > 0 else 0, target_path_override=acc_target_path)
                if not existing_targets:
                    log("💡 【新規アカウント救済モード】標準キーワード「相互フォロー (最新)」からアクティブユーザーを探索します...")
                    auto_found = browser_ctrl.collect_targets_from_search("相互フォロー", search_type="最新", scroll_limit=scroll_limit, max_targets=target_read_limit)
                    append_collected_items(auto_found, "自動救済:相互フォロー(最新)", existing_targets)

                if existing_targets:
                    act_key, act_title, a_cfg = active_action_jobs[0]
                    act_min_int = float(a_cfg.get("min_interval", 60.0))
                    act_max_int = float(a_cfg.get("max_interval", 180.0))
                    act_min_b = max(1, int(a_cfg.get("min_batch", 1)))
                    act_max_b = max(act_min_b, int(a_cfg.get("max_batch", act_min_b)))
                    batch_target_count = random.randint(act_min_b, act_max_b)
                    use_ai = bool(a_cfg.get("ai_gen", False)) or act_key.endswith("_ai")
                    fb_success_count = 0
                    for t_idx, target in enumerate(existing_targets, start=1):
                        if fb_success_count >= batch_target_count:
                            break
                        is_ok, _ = execute_single_action_on_target(act_key, target, t_idx, len(existing_targets), fb_success_count, batch_target_count, use_ai=use_ai)
                        if is_ok:
                            fb_success_count += 1
                            total_actions_performed += 1
                            if fb_success_count < batch_target_count and t_idx < len(existing_targets):
                                wait_between_targets(random.uniform(act_min_int, act_max_int), act_title, fb_success_count, batch_target_count, act_min_int, act_max_int)
                else:
                    log("ℹ️ 有効なターゲットが見つかりませんでした。安全に今回のアカウント処理を終了します。")

        except Exception as e:
            log(f"⚠️ アカウント [{account_id}] の処理中に予期せぬエラーが発生しました: {e}。このアカウントを安全にスキップし、次のアカウントへ進みます。")
            traceback.print_exc()
        finally:
            check_pause_state("ブラウザ終了前")
            try:
                from ai_generator import AIDedicatedWindowManager
                AIDedicatedWindowManager.close_ai_window()
            except Exception:
                pass
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
