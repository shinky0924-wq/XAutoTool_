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

from config_manager import ConfigManager, parse_active_hours_spec, is_within_active_hours, is_placeholder_target
from db_manager import (
    DatabaseManager,
    AccountHealthManager,
    get_db_dir,
    get_account_db_path,
    get_log_dir,
    get_account_log_path,
    AccountLogger,
    sanitize_filename,
    normalize_tweet_url
)
from browser_controller import XBrowserController


def is_low_spec_mode_enabled() -> bool:
    return os.environ.get("TOOL_LOW_SPEC_MODE", "").strip().lower() in ("1", "true", "yes", "on")

def get_base_dir() -> str:
    """PyInstaller EXE化実行環境・_app/ 実行環境・通常Python実行環境でルートディレクトリを取得"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    env_dir = os.environ.get("X_AUTO_DM_BASE_DIR", "").strip().strip('"')
    if env_dir and os.path.isdir(env_dir):
        return os.path.normpath(env_dir)
    d = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(d) == "_app":
        return os.path.dirname(d)
    return d

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


def select_ready_first_candidate(candidates: list[tuple[float, int, dict]], now_ts: float):
    """
    (ready_at, acc_idx, item) の候補リストから、
    1) ready_at <= now_ts の準備完了済みアカウントを最優先（その中で最も早く準備できていた順）
    2) 全て待機中なら最も早く ready_at が到来するアカウントを優先
    """
    if not candidates:
        return None
    return min(candidates, key=lambda x: (0 if x[0] <= now_ts else 1, x[0], x[1]))


def compute_effective_ready_at(
    normal_ready_at: float,
    auto_delete_cfg: dict,
    acc_db_mgr: DatabaseManager,
    now_ts: float = None,
) -> tuple[float, bool]:
    """
    通常周回予定時刻(normal_ready_at)と自動削除キュー最短予定時刻を比較し、
    (実効起動予定時刻, 削除専用早期起動フラグ) を返す。
    """
    now = float(now_ts) if now_ts is not None else time.time()
    if not isinstance(auto_delete_cfg, dict) or acc_db_mgr is None:
        return normal_ready_at, False
    key_map = (("follow", "FOLLOW"), ("like", "LIKE"), ("reply", "REPLY"), ("quote", "QUOTE"))
    enabled_acts = [db_act for cfg_k, db_act in key_map if (auto_delete_cfg.get(cfg_k) or {}).get("enabled")]
    if not enabled_acts:
        return normal_ready_at, False
    earliest_del = acc_db_mgr.get_earliest_pending_delete_ts(enabled_actions=enabled_acts)
    if earliest_del is not None and normal_ready_at > now and earliest_del < normal_ready_at:
        return earliest_del, True
    return normal_ready_at, False


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
    health_mgr = AccountHealthManager(base_dir)
    low_spec_mode = is_low_spec_mode_enabled()
    cycle_executed_count = 0
    cycle_executed_lock = threading.Lock()

    def _extract_clean_id_from_row(acc_dict: dict, idx: int) -> tuple[str, str]:
        raw_id = get_acc_val(acc_dict, ["アカウントid", "アカウント名", "ユーザー名", "id"], "").strip() or f"アカウント#{idx}"
        raw_clean = raw_id.replace(" ", "").replace("　", "").lower()
        if raw_clean in ["モード", "モード名", "モード名称", "番号", "1", "2", "3", "4", "5", "※備考", "※使い方", "※複数指定の例"]:
            return "", raw_id
        if raw_id.startswith("【") or raw_id.startswith("※") or raw_id.startswith("(") or raw_id.startswith("（"):
            return "", raw_id
        return (normalize_screen_name(raw_id) or f"acc_{idx}"), raw_id

    def process_single_account(acc, acc_idx: int, total_acc_count: int, active_slot: int = 1, slot_semaphore=None, skip_interval_wait: bool = False, delete_only_mode: bool = False):
        nonlocal cycle_executed_count
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
        reply_url_loop_limit = max(0, int(acc_settings.get("reply_url_loop_limit", 0)))
        reply_parent_hop_limit = max(0, int(acc_settings.get("reply_parent_hop_limit", 15)))
        block_media = True if low_spec_mode else acc_settings.get("block_media", True)
        active_hours_spec = str(acc_settings.get("active_hours", "") or get_acc_val(acc, ["稼働時間帯", "稼働時間", "営業時間", "実行時間帯", "activehours"], "") or "").strip()
        ai_engine = acc_settings.get("ai_engine", "gemini")
        ai_model = str(acc_settings.get("ai_model", "") or "").strip()
        ai_fallback_models = str(acc_settings.get("ai_fallback_models", "") or "").strip()
        ai_dedicated_window = bool(acc_settings.get("ai_dedicated_window", True))
        ai_api_key = acc_settings.get("ai_api_key", "")
        ai_prompt_extra = acc_settings.get("ai_prompt", "")

        # サーキットブレーカー / 稼働時間帯の事前チェック (Chrome起動前に判定して省メモリ)
        is_paused, pause_info = health_mgr.is_paused(clean_account_id)
        if is_paused:
            rem_min = max(1, int((float(pause_info.get("paused_until") or 0) - time.time()) // 60) + 1)
            log(f"⛔ [自動休止スキップ] アカウント [@{clean_account_id}] は一時休止中のためスキップします（残り約{rem_min}分 / 理由: {pause_info.get('pause_reason', '異常検知')}）")
            set_current_logger(None)
            return min_sec if 'min_sec' in locals() else 60.0, max_sec if 'max_sec' in locals() else 120.0

        within_hours, _until_sec = is_within_active_hours(active_hours_spec)
        if not within_hours:
            hrs_min = max(1, int(_until_sec // 60))
            log(f"🌙 [稼働時間外スキップ] アカウント [@{clean_account_id}] は稼働時間帯 ({active_hours_spec}) 外のためブラウザを起動せずスキップします（次回開始まで約{hrs_min}分）")
            set_current_logger(None)
            return min_sec if 'min_sec' in locals() else 60.0, max_sec if 'max_sec' in locals() else 120.0

        target_min_sec = float(acc_settings.get("target_min_sec", 60.0))
        target_max_sec = float(acc_settings.get("target_max_sec", 150.0))
        if target_min_sec > target_max_sec:
            target_min_sec, target_max_sec = target_max_sec, target_min_sec

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
                elif any(k in p_clean for k in ["話題の検索", "話題を検索"]) or p_clean in ["話題", "top", "人気", "foryou", "for_you"]:
                    parsed.append(("EXPLORE_TOPICS", f"話題の検索 ({part})"))
                elif "検索話題" in p_clean or (("検索" in p_clean or "キーワード" in p_clean) and any(k in p_clean for k in ["話題", "top", "人気", "おすすめ"])):
                    parsed.append(("SEARCH_TOP", f"検索話題 ({part})"))
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
        if account_min_sec > account_max_sec:
            account_min_sec, account_max_sec = account_max_sec, account_min_sec

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
        acc_ai_prompt_cfg = config_mgr.load_ai_prompt_config(target_path_override=acc_target_path, legacy_extra_prompt=ai_prompt_extra)
        acc_prompt_setting = str(acc_ai_prompt_cfg.get("instruction") or "").strip()

        log(f"\n{'='*58}")
        log(f"🚀 アカウント [{account_id}] ({acc_idx}/{total_acc_count}件目) のスロット{active_slot}セッションを開始します")
        log(f"   📂 ターゲット/設定ファイル: {os.path.basename(acc_target_path)} (個別動作設定シート適用)")
        log(f"   ⏰ 稼働時間帯: {active_hours_spec or '24時間 (終日稼働)'}")
        log(f"   ⚡ メディア読込ブロック: {'ON (省メモリ・超高速)' if block_media else 'OFF (通常)'}" + (" | 🪶 低スペ超軽量モード: ON" if low_spec_mode else ""))
        log(f"   スクロール上限: {scroll_limit}回 / 読み込みターゲット件数上限: {target_read_limit}件 / URLリプライ巡回上限: {'全件' if reply_url_loop_limit == 0 else str(reply_url_loop_limit) + '件'} / 親ホップ上限: {reply_parent_hop_limit}回")
        log(f"   AIエンジン: {ai_engine}" + (f" (固定モデル: {ai_model})" if ai_model else " (モデル: 既定自動)") + (f" | フォールバック: {ai_fallback_models}" if ai_fallback_models else " | フォールバック: 自動切替ON"))
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
        for act_key in ["dm", "reply", "reply_ai", "quote", "quote_ai", "follow", "like"]:
            a_cfg = actions_cfg.get(act_key, {})
            if a_cfg.get("enabled"):
                act_mode_str = a_cfg.get("mode") or target_mode or "検索最新"
                act_title = action_label_map.get(act_key, act_key.upper())
                active_action_jobs.append((act_key, act_title, a_cfg))
                ai_flag_lbl = 'ON' if (a_cfg.get('ai_gen') or act_key.endswith('_ai')) else 'OFF'
                like_flag_lbl = 'ON' if a_cfg.get('like_enabled') else 'OFF'
                follow_flag_lbl = 'ON' if a_cfg.get('follow_enabled') else 'OFF'
                log(f"      ・{act_title}: モード=[{act_mode_str}], 間隔={a_cfg.get('min_interval', 0):.0f}〜{a_cfg.get('max_interval', 0):.0f}秒, 連続={a_cfg.get('min_batch', 1)}〜{a_cfg.get('max_batch', 1)}件, AI生成={ai_flag_lbl}, いいね={like_flag_lbl}, フォロー={follow_flag_lbl}")
        if not active_action_jobs:
            log("      ・⚠️ 有効なアクションがありません（すべてOFF設定）")
        auto_delete_cfg = acc_settings.get("auto_delete", {}) or {}
        try:
            db_mgr.sync_pending_auto_delete_schedules(auto_delete_cfg)
        except Exception:
            pass
        auto_del_labels = [
            ("follow", "FOLLOW", "フォロー解除"),
            ("like", "LIKE", "いいね解除"),
            ("reply", "REPLY", "リプライ削除"),
            ("quote", "QUOTE", "引用RT削除"),
        ]
        enabled_del_summaries = []
        for d_key, d_act, d_lbl in auto_del_labels:
            dc = auto_delete_cfg.get(d_key) or {}
            if dc.get("enabled"):
                u_lbl = dc.get("unit", "分")
                m_lim = int(dc.get("max_per_run", 10) or 0)
                lim_str = "全件" if m_lim == 0 else f"最大{m_lim}件/回"
                pend_cnt = db_mgr.count_pending_auto_deletes(d_act)
                enabled_del_summaries.append(
                    f"{d_lbl}({dc.get('min_delay', 0):g}〜{dc.get('max_delay', 0):g}{u_lbl}/{lim_str}/待機:{pend_cnt}件)"
                )
        if enabled_del_summaries:
            log(f"      ・自動削除・解除: {' | '.join(enabled_del_summaries)}")
        else:
            log("      ・自動削除・解除: すべてOFF")
        log(f"      ・DMパスコード: {'登録済み (暗号化DM自動認証有効)' if dm_passcode else '⚠️ 未登録'}")
        log(f"      ・アカウント次回周回待機: {account_min_sec:.0f}秒 〜 {account_max_sec:.0f}秒")
        log(f"      ・ターゲット間ランダム待機: {target_min_sec:.0f}秒 〜 {target_max_sec:.0f}秒")
        if delete_only_mode:
            log("      ・🧹 [起動モード] 自動削除・解除 期限到来のため削除処理のみ先行実行します")
        log(f"{'='*58}")

        # ★ 起動時の待機なし＆周回時個別待機制御（1窓スケジューラ時は待機完了後に呼ばれるためスキップ可）
        if not skip_interval_wait:
            if clean_account_id in account_ready_times:
                ready_at = account_ready_times[clean_account_id]
                remaining_wait = ready_at - time.time()
                if remaining_wait > 0:
                    wait_for_account_interval(clean_account_id, remaining_wait, account_min_sec, account_max_sec, base_dir, f"第{cycle_count}周回 個別実行間隔")
                else:
                    log(f"✅ アカウント [{clean_account_id}] の設定実行間隔（{account_min_sec:.0f}秒 〜 {account_max_sec:.0f}秒）は既に経過しています。待ち時間0秒で直ちにブラウザを起動します！")
            else:
                log(f"⚡ アカウント [{clean_account_id}] を待ち時間なしで直ちに起動します（初回即時実行）")

        # 2窓並列モードの場合、セマフォで最大Nブラウザに制御
        acquired_semaphore = False
        if slot_semaphore is not None:
            if not slot_semaphore.acquire(blocking=False):
                log(f"⏳ 【並列上限到達】アカウント [{clean_account_id}] の待機時間が到来しましたが、現在上限枠が稼働中です。空き枠が出るまで待機キューでスタンバイします...")
                slot_semaphore.acquire(blocking=True)
                log(f"✨ 実行枠が空きました！ アカウント [{clean_account_id}] のブラウザを直ちに起動します（スロット{active_slot}取得完了）。")
            acquired_semaphore = True

        with cycle_executed_lock:
            cycle_executed_count += 1

        profile_dir = os.path.join(base_dir, "browser_profiles", sanitize_filename(clean_account_id))
        browser_channel = get_acc_val(acc, ["ブラウザ", "使用ブラウザ", "browser", "channel"], "").strip().lower()
        browser_ctrl = XBrowserController(
            headless=False,
            user_data_dir=profile_dir,
            base_dir=base_dir,
            block_media=block_media,
            preferred_channel=browser_channel,
            low_spec_mode=low_spec_mode,
            auto_cache_cleanup=True,
        )
        session_failed = False
        try:
            browser_ctrl.start()
            browser_ctrl.current_account_id = clean_account_id
            if dm_passcode:
                browser_ctrl.current_passcode = dm_passcode
            
            # ログイン (Cookie優先・パスコード自動対応)
            login_success = browser_ctrl.login(account_id=clean_account_id, auth_token=auth_token, password=password, passcode=dm_passcode)
            if not login_success:
                session_failed = True
                browser_ctrl.capture_error_screenshot("login_failed", clean_account_id)
                fail_reason = getattr(browser_ctrl, "last_login_failure_reason", "") or "ログイン認証失敗"
                is_crit = bool(getattr(browser_ctrl, "last_login_failure_critical", False))
                res_h = health_mgr.record_failure(
                    clean_account_id,
                    reason=fail_reason,
                    pause_hours=24.0 if is_crit else 2.0,
                    immediate_pause=is_crit,
                    threshold=2,
                )
                if res_h.get("paused_until", 0) > time.time():
                    log(f"⛔ 【自動休止(サーキットブレーカー)】アカウント [{account_id}] を自動一時休止しました (連続失敗: {res_h.get('consecutive_failures', 1)}回 / 理由: {fail_reason})")
                log(f"❌ アカウント [{account_id}] のログインに失敗したため、次のアカウントへ移ります。")
                browser_ctrl.close()
                return min_sec, max_sec

            # アカウントログイン直後の一時停止チェック (アカウント編集したい場合に対応)
            check_pause_state(f"アカウント @{account_id} ログイン直後")

            def _format_delay_human(delay_sec: float) -> str:
                sec = max(0, int(round(delay_sec)))
                if sec < 60:
                    return f"約{sec}秒後"
                mins = sec // 60
                if sec < 3600:
                    return f"約{mins}分 ({sec}秒) 後"
                hrs = mins // 60
                rem_m = mins % 60
                return f"約{mins}分 ({hrs}時間{rem_m}分 / {sec}秒) 後"

            def enqueue_delete_if_applicable(act_type: str, target_screen_name: str, target_url: str = "", own_post_url: str = "", snippet: str = ""):
                act_u = str(act_type or "").upper()
                cfg_key = {"FOLLOW": "follow", "LIKE": "like", "REPLY": "reply", "QUOTE": "quote"}.get(act_u)
                if not cfg_key:
                    return None
                d_cfg = auto_delete_cfg.get(cfg_key) or {}
                if not d_cfg.get("enabled"):
                    return None
                try:
                    q_info = db_mgr.enqueue_auto_delete(
                        action_type=act_u,
                        target_screen_name=target_screen_name,
                        account_id=account_id,
                        min_seconds=float(d_cfg.get("min_seconds", 0.0)),
                        max_seconds=float(d_cfg.get("max_seconds", 0.0)),
                        target_url=target_url,
                        own_post_url=own_post_url,
                        snippet=snippet,
                    )
                    if q_info:
                        act_jp = {"FOLLOW": "フォロー解除", "LIKE": "いいね解除", "REPLY": "リプライ削除", "QUOTE": "引用RT削除"}.get(act_u, act_u)
                        exec_dt_str = datetime.fromtimestamp(q_info.get("executed_at", time.time())).strftime("%Y/%m/%d %H:%M:%S")
                        sched_dt_str = datetime.fromtimestamp(q_info["scheduled_delete_at"]).strftime("%Y/%m/%d %H:%M:%S")
                        delay_label = _format_delay_human(q_info["delay_seconds"])
                        target_ref = q_info.get("own_post_url") or q_info.get("target_url") or f"@{q_info['target_screen_name']}"
                        log(f"   🕒 [自動削除予定/{act_jp}] @{q_info['target_screen_name']} | 実行: {exec_dt_str} → 削除予定: {sched_dt_str} ({delay_label}) | 対象: {target_ref}")
                        db_mgr.mark_history_auto_delete_scheduled(
                            act_u,
                            q_info["target_key"],
                            q_info["scheduled_delete_at"],
                            is_unlink=(act_u in ("FOLLOW", "LIKE")),
                        )
                    return q_info
                except Exception as q_err:
                    log(f"   ⚠️ [自動削除予約通知/{act_type}] 予約登録エラー: {q_err}")
                    return None

            def process_due_auto_deletes(stage_label: str = "") -> int:
                handled = 0
                del_specs = [
                    ("follow", "FOLLOW", "フォロー解除", "[自動解除済]"),
                    ("like", "LIKE", "いいね解除", "[自動解除済]"),
                    ("reply", "REPLY", "リプライ削除", "[自動削除済]"),
                    ("quote", "QUOTE", "引用RT削除", "[自動削除済]"),
                ]
                for d_key, db_act, del_lbl, hist_tag in del_specs:
                    d_cfg = auto_delete_cfg.get(d_key) or {}
                    if not d_cfg.get("enabled"):
                        continue
                    max_run = max(0, int(d_cfg.get("max_per_run", 10) or 0))
                    due_tasks = db_mgr.get_due_auto_delete_tasks(action_type=db_act, now_ts=time.time(), limit=max_run)
                    if not due_tasks:
                        continue
                    log(f"🧹 [自動{del_lbl}] {stage_label}: 期限到来 {len(due_tasks)}件の処理を開始します...")
                    for task_idx, task in enumerate(due_tasks, start=1):
                        check_pause_state(f"自動{del_lbl} ({task_idx}/{len(due_tasks)}) 実行前", browser_ctrl)
                        t_name = task.get("target_screen_name") or ""
                        t_url = task.get("target_url") or ""
                        own_u = task.get("own_post_url") or ""
                        snip = task.get("snippet") or ""
                        try:
                            if db_act == "FOLLOW":
                                d_st, d_det = browser_ctrl.unfollow_user(t_name)
                            elif db_act == "LIKE":
                                d_st, d_det = browser_ctrl.unlike_post(t_name, tweet_url=t_url)
                            else:
                                d_st, d_det = browser_ctrl.delete_reply_or_quote(
                                    db_act,
                                    clean_account_id,
                                    own_post_url=own_u,
                                    target_url=t_url,
                                    snippet=snip,
                                )
                        except Exception as del_err:
                            d_st, d_det = "FAILED", f"例外: {del_err}"

                        db_mgr.mark_auto_delete_result(task["id"], d_st, error_msg=d_det, retry_delay_sec=180.0)
                        if d_st in ("DELETED", "ALREADY_REMOVED"):
                            db_mgr.mark_history_auto_deleted(db_act, task["target_key"], tag=hist_tag)
                            handled += 1
                            target_desc = own_u or t_url or f"@{t_name}"
                            log(f"   ✅ [自動{del_lbl} {d_st}] {target_desc} ({d_det}) [重複防止DBは維持]")
                        else:
                            log(f"   ⚠️ [自動{del_lbl}保留] @{t_name} ({d_det}) → 次回以降に安全リトライ")
                        if task_idx < len(due_tasks):
                            time.sleep(random.uniform(1.2, 2.5))
                return handled

            # ログイン直後に期限到来済みの自動削除・解除を実行
            process_due_auto_deletes("ログイン直後")

            if delete_only_mode:
                log("🧹 [自動削除専用セッション] 削除処理のみ完了したため、通常アクションをスキップして終了します。")
                health_mgr.record_success(clean_account_id)
                return min_sec, max_sec

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

            # --- モード別ターゲット収集 (アクションDB単位のキャッシュあり) ---
            mode_targets_cache = {}
            db_action_map = {
                "follow": "FOLLOW",
                "like": "LIKE",
                "reply": "REPLY",
                "reply_ai": "REPLY",
                "dm": "DM",
                "quote": "QUOTE",
                "quote_ai": "QUOTE",
            }

            def append_collected_items(found_items, source_label: str, dest_list: list):
                for item in found_items:
                    if isinstance(item, dict):
                        user = item.get("screen_name") or item.get("username") or ""
                        t_url = item.get("tweet_url", "")
                        d_name = item.get("display_name", "")
                        t_text = item.get("tweet_text", "")
                        p_text = item.get("parent_tweet_text", "")
                        is_rep = bool(item.get("is_reply", False))
                    else:
                        user = str(item)
                        t_url = ""
                        d_name = ""
                        t_text = ""
                        p_text = ""
                        is_rep = False
                    config_mgr.append_discovered_target(user, source_label, target_path_override=acc_target_path, tweet_url=t_url, display_name=d_name)
                    clean_u = normalize_screen_name(user)
                    norm_t_url = normalize_tweet_url(t_url) if t_url else ""
                    eff_t_url = norm_t_url or t_url
                    if norm_t_url:
                        is_dup_in_list = any(normalize_tweet_url(t.get("ツイートURL") or "").lower() == norm_t_url.lower() for t in dest_list)
                    else:
                        is_dup_in_list = any(t.get("アカウント名", "").lower() == clean_u.lower() and not t.get("ツイートURL") for t in dest_list)
                    if clean_u and not is_dup_in_list:
                        dest_list.append({
                            "アカウント名": clean_u,
                            "ターゲットURL": eff_t_url if eff_t_url else f"https://x.com/{clean_u}",
                            "ツイートURL": eff_t_url,
                            "ツイート本文": t_text,
                            "親ツイート本文": p_text,
                            "is_reply": is_rep,
                            "個別メッセージ": "",
                            "ステータス": "未送信",
                            "表示名": d_name
                        })

            def collect_targets_for_mode(m_type: str, m_label: str, act_key: str = "dm", force_refresh: bool = False) -> list[dict]:
                db_action_type = db_action_map.get(act_key, act_key.upper())
                cache_key = (m_type, db_action_type)
                if not force_refresh and cache_key in mode_targets_cache:
                    cached = mode_targets_cache[cache_key]
                    log(f"   ♻️ [{m_label}/{db_action_type}] 収集済みキャッシュから {len(cached)} 件の完全新規ターゲットを再利用します")
                    return cached

                check_pause_state(f"モード [{m_label}] ターゲット収集前")
                collected = []

                def should_skip_target(cand_user: str, cand_tweet_url: str = "") -> bool:
                    clean_u = normalize_screen_name(cand_user)
                    if not clean_u or is_placeholder_target(clean_u):
                        return True
                    if db_mgr.is_user_unavailable(clean_u):
                        return True
                    if db_mgr.is_registered_in_db(clean_u, action_type=db_action_type, target_url=cand_tweet_url):
                        return True
                    norm_u = normalize_tweet_url(cand_tweet_url) if cand_tweet_url else ""
                    if norm_u:
                        if any((t.get("ツイートURL") or "").lower() == norm_u.lower() for t in collected):
                            return True
                    if any((t.get("アカウント名") or "").lower() == clean_u.lower() for t in collected):
                        return True
                    return False

                def remaining_limit() -> int:
                    if target_read_limit <= 0:
                        return 0
                    return max(0, target_read_limit - len(collected))

                def _defer_last_used(items: list, last_val: str, key_fn, mode_lbl: str, item_type_lbl: str) -> list:
                    norm_last = str(last_val or "").strip().rstrip("/").lower()
                    if not norm_last or len(items) < 2:
                        return items
                    front = [it for it in items if str(key_fn(it) or "").strip().rstrip("/").lower() != norm_last]
                    tail = [it for it in items if str(key_fn(it) or "").strip().rstrip("/").lower() == norm_last]
                    if front and tail:
                        log(f"   ⏭️ [{mode_lbl}] 前回実行{item_type_lbl}「{last_val}」を後回しにしました (候補末尾へ移動)")
                        return front + tail
                    return items

                if m_type in ["SEARCH_LATEST", "SEARCH_TOP"]:
                    stype_override = "話題" if m_type == "SEARCH_TOP" else "最新"
                    if acc_search_keywords:
                        kw_candidates = list(acc_search_keywords)
                        random.shuffle(kw_candidates)
                        last_kw = db_mgr.get_meta("last_used_search_keyword", "")
                        kw_candidates = _defer_last_used(
                            kw_candidates,
                            last_kw,
                            lambda x: x.get("keyword", ""),
                            m_label,
                            "キーワード",
                        )
                        log(f"🔍 [{m_label}] 完全新規ターゲット収集開始 (登録キーワード: {len(kw_candidates)}件 / 目標人数: {target_read_limit if target_read_limit > 0 else '無制限'}件)...")
                        last_yielded_kw = ""
                        first_accessed_kw = ""
                        for kw_idx, kw_item in enumerate(kw_candidates, start=1):
                            rem = remaining_limit()
                            if target_read_limit > 0 and rem <= 0:
                                break
                            kw = kw_item.get("keyword")
                            if not first_accessed_kw and kw:
                                first_accessed_kw = str(kw).strip()
                            before_kw_cnt = len(collected)
                            stype = "話題" if m_type == "SEARCH_TOP" else kw_item.get("search_type", stype_override)
                            found = browser_ctrl.collect_targets_from_search(kw, search_type=stype, scroll_limit=scroll_limit, max_targets=rem, should_skip_fn=should_skip_target)
                            append_collected_items(found, f"{m_label}:{kw}", collected)
                            if len(collected) > before_kw_cnt and kw:
                                last_yielded_kw = str(kw).strip()
                            if target_read_limit > 0:
                                if len(collected) >= target_read_limit:
                                    log(f"   ✅ [{m_label}] 目標人数 ({len(collected)}/{target_read_limit}件) に達したため探索を終了します。")
                                    break
                                elif kw_idx < len(kw_candidates):
                                    log(f"   ℹ️ [{m_label}] キーワード「{kw}」終了時点で {len(collected)}/{target_read_limit}件 のため、別ワードへ移動して目標人数まで取得を続けます...")
                            else:
                                if len(collected) > 0:
                                    break
                                elif kw_idx < len(kw_candidates):
                                    log(f"   ℹ️ [{m_label}] キーワード「{kw}」は新規0件だったため、別ワードへフォールバックします...")
                        save_kw = last_yielded_kw or first_accessed_kw
                        if save_kw:
                            db_mgr.set_meta("last_used_search_keyword", save_kw)
                    else:
                        log(f"   ℹ️ [{m_label}] 「検索キーワード」シートにキーワード未登録のため、「相互フォロー」で探索します。")
                        found = browser_ctrl.collect_targets_from_search("相互フォロー", search_type=stype_override, scroll_limit=scroll_limit, max_targets=target_read_limit, should_skip_fn=should_skip_target)
                        append_collected_items(found, f"{m_label}:相互フォロー", collected)

                elif m_type == "EXPLORE_TOPICS":
                    log(f"🔥 [話題の検索] X探索「話題を検索」タブからランダムにワードを選び、DB未登録の話題のポスト(Top)を収集開始 (上限: {target_read_limit}件)...")
                    last_explore_kw = db_mgr.get_meta("last_used_explore_話題を検索", "")
                    used_explore_kws = []
                    found = browser_ctrl.collect_targets_from_explore(
                        tab_type="話題を検索",
                        scroll_limit=scroll_limit,
                        max_targets=target_read_limit,
                        should_skip_fn=should_skip_target,
                        last_used_topic=last_explore_kw,
                        out_used_topics=used_explore_kws,
                    )
                    append_collected_items(found, "話題の検索", collected)
                    if used_explore_kws:
                        db_mgr.set_meta("last_used_explore_話題を検索", used_explore_kws[-1])

                elif m_type == "HOME_FOR_YOU":
                    log(f"🏠 [HOMEおすすめ] タイムラインからDB未登録ターゲット収集開始 (上限: {target_read_limit}件)...")
                    found = browser_ctrl.collect_targets_from_home(tab_type="おすすめ", scroll_limit=scroll_limit, max_targets=target_read_limit, should_skip_fn=should_skip_target)
                    append_collected_items(found, "HOMEおすすめ", collected)

                elif m_type == "HOME_FOLLOWING":
                    log(f"🏠 [HOMEフォロー中] タイムラインからDB未登録ターゲット収集開始 (上限: {target_read_limit}件)...")
                    found = browser_ctrl.collect_targets_from_home(tab_type="フォロー中", scroll_limit=scroll_limit, max_targets=target_read_limit, should_skip_fn=should_skip_target)
                    append_collected_items(found, "HOMEフォロー中", collected)

                elif m_type == "URL_REPLY":
                    if acc_reply_urls:
                        shuffled_reply_urls = list(acc_reply_urls)
                        random.shuffle(shuffled_reply_urls)
                        last_reply_url = db_mgr.get_meta("last_used_reply_url", "")
                        shuffled_reply_urls = _defer_last_used(
                            shuffled_reply_urls,
                            last_reply_url,
                            lambda u: u,
                            "URLリスト（リプライ）",
                            "URL",
                        )
                        single_url_mode = (reply_url_loop_limit == 0)
                        urls_to_visit = list(shuffled_reply_urls)
                        log(f"💬 [URLリスト（リプライ）] DB未登録ターゲット収集開始 (登録URL: {len(acc_reply_urls)}件 / {'目標人数到達まで巡回' if single_url_mode else f'巡回上限: {reply_url_loop_limit}件'} / 目標人数: {target_read_limit if target_read_limit > 0 else '無制限'}件 / 親ホップ上限: {reply_parent_hop_limit}回)...")
                        visited_reply_accounts = {clean_account_id.lower()} if clean_account_id else set()
                        visited_parent_urls = set()
                        pending_parent_urls = []
                        pending_hop_accounts = []
                        last_yielded_reply_url = ""
                        first_accessed_reply_url = ""

                        for r_idx, rurl in enumerate(urls_to_visit, start=1):
                            if not single_url_mode and r_idx > reply_url_loop_limit and len(collected) > 0:
                                break
                            rem = remaining_limit()
                            if target_read_limit > 0 and rem <= 0:
                                break
                            if not first_accessed_reply_url and rurl:
                                first_accessed_reply_url = str(rurl).strip()
                            before_rurl_cnt = len(collected)
                            owner_cand = normalize_screen_name(rurl).lower()
                            if "/status/" not in rurl and owner_cand:
                                visited_reply_accounts.add(owner_cand)
                            discovered_parents = []
                            log(f"   📍 登録URL [{r_idx}/{len(urls_to_visit)}]: {rurl}")
                            allow_deleted_parent_profile_target = db_action_type in ("DM", "FOLLOW")
                            found = browser_ctrl.collect_targets_from_replies(
                                rurl,
                                scroll_limit=scroll_limit,
                                max_targets=rem,
                                should_skip_fn=should_skip_target,
                                exclude_owner=True,
                                out_parent_candidates=discovered_parents,
                                include_reply_to_mentions=allow_deleted_parent_profile_target,
                            )
                            append_collected_items(found, f"URLリスト（リプライ）:{rurl}", collected)
                            if len(collected) > before_rurl_cnt and rurl:
                                last_yielded_reply_url = str(rurl).strip()
                            for pu in discovered_parents:
                                p_norm = (normalize_tweet_url(pu) or pu).lower()
                                if p_norm and p_norm not in visited_parent_urls and pu not in pending_parent_urls:
                                    pending_parent_urls.append(pu)
                            if target_read_limit > 0 and len(collected) >= target_read_limit:
                                log(f"   ✅ [URLリスト（リプライ）] 目標人数 ({len(collected)}/{target_read_limit}件) に達したため登録URL探索を完了します。")
                                break
                            if single_url_mode:
                                if target_read_limit <= 0 and len(collected) > 0:
                                    break
                                if r_idx < len(urls_to_visit):
                                    log(f"   ℹ️ [URLリスト（リプライ）] 現時点 {len(collected)}/{target_read_limit}件 のため、別URLへ移動して取得を続けます...")
                            else:
                                if r_idx >= reply_url_loop_limit and len(collected) > 0:
                                    break
                                if len(collected) == 0 and r_idx < len(urls_to_visit):
                                    log(f"   ℹ️ [URLリスト（リプライ）] 新規0件だったため、別URLへフォールバックします...")

                        save_reply_url = last_yielded_reply_url or first_accessed_reply_url
                        if save_reply_url:
                            db_mgr.set_meta("last_used_reply_url", save_reply_url)

                        need_more = (target_read_limit > 0 and len(collected) < target_read_limit) or (target_read_limit <= 0 and len(collected) == 0)
                        if need_more and reply_parent_hop_limit > 0 and pending_parent_urls:
                            log(f"   🔄 [URLリスト（リプライ）] 登録URLで目標未達 ({len(collected)}/{target_read_limit}件) のため、親ポスト経由ホップ探索（最大{reply_parent_hop_limit}ホップ / 一時巡回）を開始します...")
                            hop_count = 0
                            while hop_count < reply_parent_hop_limit:
                                if target_read_limit > 0 and len(collected) >= target_read_limit:
                                    break
                                check_pause_state(f"親ポスト連鎖ホップ ({hop_count + 1}/{reply_parent_hop_limit}) 前")

                                while not pending_hop_accounts and pending_parent_urls:
                                    p_url = pending_parent_urls.pop(0)
                                    p_norm = (normalize_tweet_url(p_url) or p_url).lower()
                                    if p_norm in visited_parent_urls:
                                        continue
                                    visited_parent_urls.add(p_norm)
                                    repliers = browser_ctrl.collect_repliers_from_parent_post(
                                        p_url,
                                        exclude_users=visited_reply_accounts,
                                        max_candidates=12,
                                    )
                                    for ru in repliers:
                                        ru_low = normalize_screen_name(ru).lower()
                                        if not ru_low or ru_low in visited_reply_accounts or db_mgr.is_user_unavailable(ru_low):
                                            continue
                                        if all(ru_low != normalize_screen_name(x).lower() for x in pending_hop_accounts):
                                            pending_hop_accounts.append(ru)

                                if not pending_hop_accounts:
                                    log(f"   ℹ️ [親ポスト連鎖ホップ] これ以上辿れる未訪問の親ポスト・別リプライ主が無いため終了します ({hop_count}/{reply_parent_hop_limit}ホップ)。")
                                    break

                                next_u = pending_hop_accounts.pop(0)
                                nu_low = normalize_screen_name(next_u).lower()
                                if not nu_low or nu_low in visited_reply_accounts or db_mgr.is_user_unavailable(nu_low):
                                    continue
                                visited_reply_accounts.add(nu_low)
                                hop_count += 1
                                rem = remaining_limit()
                                hop_url = f"https://x.com/{next_u}/with_replies"
                                log(f"   🔁 [親ポスト連鎖ホップ {hop_count}/{reply_parent_hop_limit}] @{next_u}/with_replies から親ポスト側ターゲットを探索中...")
                                hop_parents = []
                                allow_deleted_parent_profile_target = db_action_type in ("DM", "FOLLOW")
                                hop_found = browser_ctrl.collect_targets_from_replies(
                                    hop_url,
                                    scroll_limit=scroll_limit,
                                    max_targets=rem,
                                    should_skip_fn=should_skip_target,
                                    exclude_owner=True,
                                    out_parent_candidates=hop_parents,
                                    include_reply_to_mentions=allow_deleted_parent_profile_target,
                                )
                                append_collected_items(hop_found, f"親ホップ{hop_count}:@{next_u}", collected)
                                for hpu in hop_parents:
                                    hp_norm = (normalize_tweet_url(hpu) or hpu).lower()
                                    if hp_norm and hp_norm not in visited_parent_urls and hpu not in pending_parent_urls:
                                        pending_parent_urls.append(hpu)

                                if target_read_limit > 0 and len(collected) >= target_read_limit:
                                    log(f"   🎯 [親ポスト連鎖ホップ] 新規ターゲット上限 ({target_read_limit}件) に達したため、ホップ探索を完了します ({hop_count}/{reply_parent_hop_limit}ホップ)。")
                                    break
                                if target_read_limit <= 0 and len(collected) > 0:
                                    break
                    else:
                        log(f"   ℹ️ [URLリスト（リプライ）] 「URLリスト_リプライ」シートに対象URLが未登録のためスキップします。")

                elif m_type == "URL_FOLLOW":
                    if acc_follow_urls:
                        follow_candidates = list(acc_follow_urls)
                        random.shuffle(follow_candidates)
                        last_follow_url = db_mgr.get_meta("last_used_follow_url", "")
                        follow_candidates = _defer_last_used(
                            follow_candidates,
                            last_follow_url,
                            lambda u: u,
                            "URLリスト（フォロー）",
                            "URL",
                        )
                        log(f"👥 [URLリスト（フォロー）] DB未登録ターゲット収集開始 (登録URL: {len(follow_candidates)}件 / 目標人数: {target_read_limit if target_read_limit > 0 else '無制限'}件)...")
                        last_yielded_follow_url = ""
                        first_accessed_follow_url = ""
                        for f_idx, furl in enumerate(follow_candidates, start=1):
                            rem = remaining_limit()
                            if target_read_limit > 0 and rem <= 0:
                                break
                            if not first_accessed_follow_url and furl:
                                first_accessed_follow_url = str(furl).strip()
                            before_furl_cnt = len(collected)
                            found = browser_ctrl.collect_targets_from_follow(furl, scroll_limit=scroll_limit, max_targets=rem, should_skip_fn=should_skip_target)
                            append_collected_items(found, f"URLリスト（フォロー）:{furl}", collected)
                            if len(collected) > before_furl_cnt and furl:
                                last_yielded_follow_url = str(furl).strip()
                            if target_read_limit > 0:
                                if len(collected) >= target_read_limit:
                                    log(f"   ✅ [URLリスト（フォロー）] 目標人数 ({len(collected)}/{target_read_limit}件) に達したため探索を終了します。")
                                    break
                                elif f_idx < len(follow_candidates):
                                    log(f"   ℹ️ [URLリスト（フォロー）] {furl} 終了時点で {len(collected)}/{target_read_limit}件 のため、別URLへ移動して目標人数まで取得を続けます...")
                            else:
                                if len(collected) > 0:
                                    break
                                elif f_idx < len(follow_candidates):
                                    log(f"   ℹ️ [URLリスト（フォロー）] {furl} は新規0件だったため、別URLへフォールバックします...")
                        save_follow_url = last_yielded_follow_url or first_accessed_follow_url
                        if save_follow_url:
                            db_mgr.set_meta("last_used_follow_url", save_follow_url)
                    else:
                        log(f"   ℹ️ [URLリスト（フォロー）] 「URLリスト_フォロー」シートに対象URLが未登録のためスキップします。")

                elif m_type == "NEWS":
                    log(f"📰 [ニュース] 探索タブからランダムにワードを選び、DB未登録の話題のポスト(Top)を収集開始 (上限: {target_read_limit}件)...")
                    last_news_kw = db_mgr.get_meta("last_used_explore_ニュース", "")
                    used_news_kws = []
                    found = browser_ctrl.collect_targets_from_explore(
                        tab_type="ニュース",
                        scroll_limit=scroll_limit,
                        max_targets=target_read_limit,
                        should_skip_fn=should_skip_target,
                        last_used_topic=last_news_kw,
                        out_used_topics=used_news_kws,
                    )
                    append_collected_items(found, "ニュース", collected)
                    if used_news_kws:
                        db_mgr.set_meta("last_used_explore_ニュース", used_news_kws[-1])

                elif m_type == "TRENDS":
                    log(f"📈 [トレンド] 探索タブからランダムにワードを選び、DB未登録の話題のポスト(Top)を収集開始 (上限: {target_read_limit}件)...")
                    last_trend_kw = db_mgr.get_meta("last_used_explore_トレンド", "")
                    used_trend_kws = []
                    found = browser_ctrl.collect_targets_from_explore(
                        tab_type="トレンド",
                        scroll_limit=scroll_limit,
                        max_targets=target_read_limit,
                        should_skip_fn=should_skip_target,
                        last_used_topic=last_trend_kw,
                        out_used_topics=used_trend_kws,
                    )
                    append_collected_items(found, "トレンド", collected)
                    if used_trend_kws:
                        db_mgr.set_meta("last_used_explore_トレンド", used_trend_kws[-1])

                elif m_type == "LIST":
                    log(f"📑 [リスト送信] Excel「{os.path.basename(acc_target_path)}」からDB未登録ターゲットを読込中...")
                    raw_list = config_mgr.load_targets(limit=0, target_path_override=acc_target_path)
                    for item in raw_list:
                        cu = normalize_screen_name(item.get("アカウント名") or item.get("ターゲットURL") or "")
                        tu = normalize_tweet_url(item.get("ツイートURL") or item.get("ターゲットURL") or "")
                        if should_skip_target(cu, tu):
                            continue
                        collected.append(item)
                        if target_read_limit > 0 and len(collected) >= target_read_limit:
                            break

                elif m_type == "MUTUAL":
                    log(f"🤝 [相互フォロー検索] DB未登録ターゲット収集開始 (上限: {target_read_limit}件)...")
                    found = browser_ctrl.collect_targets_from_search("相互フォロー", search_type="最新", scroll_limit=scroll_limit, max_targets=target_read_limit, should_skip_fn=should_skip_target)
                    append_collected_items(found, "相互フォロー検索", collected)

                mode_targets_cache[cache_key] = collected
                return collected

            def pick_next_template(kind: str, candidates: list[str], fallback_default: str) -> str:
                unique_cands = []
                for c in (candidates or []):
                    cs = str(c or "").strip()
                    if cs and cs not in unique_cands:
                        unique_cands.append(cs)
                if not unique_cands:
                    return fallback_default
                meta_key = f"last_used_template_{kind}"
                last_used = (db_mgr.get_meta(meta_key, "") or "").strip()
                if last_used and last_used in unique_cands:
                    next_idx = (unique_cands.index(last_used) + 1) % len(unique_cands)
                    chosen = unique_cands[next_idx]
                else:
                    chosen = unique_cands[0]
                db_mgr.set_meta(meta_key, chosen)
                return chosen

            def generate_ai_or_template_text(kind: str, target_screen_name: str, resolved_disp: str, tweet_url: str, use_ai: bool, cached_tweet_text: str = "", cached_parent_text: str = "", is_reply_target: bool = False) -> str:
                if use_ai and ai_engine and str(ai_engine).strip().lower() not in ["none", "off", "無効"]:
                    try:
                        from ai_generator import AIGenerator, AIDedicatedWindowManager
                        post_txt = (cached_tweet_text or "").strip()
                        parent_txt = (cached_parent_text or "").strip()

                        need_ctx_fetch = False
                        if tweet_url and "/status/" in tweet_url:
                            if not post_txt:
                                need_ctx_fetch = True
                            elif (is_reply_target or post_txt.lstrip().startswith("@")) and not parent_txt:
                                need_ctx_fetch = True

                        if need_ctx_fetch:
                            try:
                                ctx = browser_ctrl.get_tweet_context_from_url(tweet_url)
                                if isinstance(ctx, dict):
                                    if not post_txt and ctx.get("tweet_text"):
                                        post_txt = ctx["tweet_text"].strip()
                                    if not parent_txt and ctx.get("parent_tweet_text"):
                                        parent_txt = ctx["parent_tweet_text"].strip()
                            except Exception:
                                if not post_txt:
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

                        if parent_txt:
                            log(f"   🧵 [会話コンテキスト取得] 元ポスト: 「{parent_txt[:35]}...」 / 対象返信: 「{post_txt[:35]}...」")

                        custom_p = acc_prompt_setting if acc_ai_prompt_cfg.get("has_sheet") else (acc_prompt_setting or ai_prompt_extra)

                        def _run_ai_once():
                            return AIGenerator.generate_comment_with_fallback(
                                engine=ai_engine,
                                post_url=tweet_url,
                                post_text=post_txt,
                                action_type="reply" if kind == "reply" else "quote",
                                target_name=resolved_disp or target_screen_name,
                                api_key=ai_api_key,
                                custom_prompt=custom_p,
                                playwright=browser_ctrl.playwright,
                                page=browser_ctrl.page,
                                use_dedicated_window=ai_dedicated_window,
                                base_dir=base_dir,
                                slot_id=active_slot,
                                headless=False,
                                parent_post_text=parent_txt,
                                prompt_config=acc_ai_prompt_cfg,
                                model_name=ai_model,
                                fallback_spec=ai_fallback_models,
                                logger=log
                            )

                        ok_ai, ai_res, used_ai_label = _run_ai_once()
                        use_win_flow = AIGenerator.should_launch_ai_window(ai_engine, ai_api_key, ai_dedicated_window)
                        if (not ok_ai or not (ai_res or "").strip() or AIGenerator.is_loading_placeholder(ai_res)) and use_win_flow:
                            log(f"   ⏳ [AI出力確認リトライ] ラグ等により本文未確認のため、AI専用ウィンドウ状態を確認して1回再生成します...")
                            try:
                                st_ai = AIDedicatedWindowManager._get_state()
                                cur_ai_p = getattr(st_ai, "ai_page", None)
                                if cur_ai_p is None or cur_ai_p.is_closed():
                                    AIDedicatedWindowManager.close_ai_window()
                            except Exception:
                                pass
                            time.sleep(1.5)
                            ok_ai, ai_res, used_ai_label = _run_ai_once()

                        if ok_ai and ai_res and ai_res.strip():
                            out_cand = ai_res.strip()
                            if AIGenerator.is_prompt_echo(out_cand, ""):
                                log(f"   ⚠️ [AI出力異常検知] プロンプト本文の誤取得をブロックしました → 定型テンプレートへフォールバックします")
                            elif AIGenerator.is_skip_token_prefix(out_cand) or AIGenerator.detect_skip_response(out_cand)[0]:
                                skip_rsn = AIGenerator.extract_skip_reason(out_cand)
                                return f"{AIGenerator.SKIP_SIGNAL_PREFIX}{skip_rsn}"
                            elif AIGenerator.is_loading_placeholder(out_cand):
                                log(f"   ⚠️ [AI出力未完了検知] 生成中プレースホルダ（「{out_cand[:20]}」）のままだったため投稿を保留しフォールバックします")
                            else:
                                prim_spec_eng, prim_spec_mod, prim_spec_ov = AIGenerator.parse_engine_list(ai_engine, ai_model)[0]
                                prim_norm, _ = AIGenerator.resolve_engine_and_url(prim_spec_eng, prim_spec_ov or ai_api_key)
                                prim_lbl = f"{prim_norm} ({prim_spec_mod})" if prim_spec_mod else prim_norm
                                if used_ai_label != prim_lbl:
                                    prefix_icon = "🔄 [AI自動切替生成成功"
                                else:
                                    prefix_icon = "🪟 [AI専用ウィンドウ生成成功" if use_win_flow else "🤖 [AI生成成功"
                                log(f"   {prefix_icon}/{used_ai_label}] 「{out_cand[:45]}...」")
                                log(f"   ✅ [AI本文出力確認完了/{used_ai_label}] 本文({len(out_cand)}文字)の出力を確認しました → X投稿処理へ進みます")
                                return out_cand
                        else:
                            log(f"   ⚠️ AI生成フォールバック (全候補失敗 / 最終理由: {ai_res}) → 定型テンプレートを使用します")
                    except Exception as ai_e:
                        log(f"   ⚠️ AI生成通知 ({ai_e}) → 定型テンプレートを使用します")

                if kind == "reply":
                    raw_msg = pick_next_template(
                        "reply",
                        acc_reply_templates or reply_templates,
                        "大変参考になります！有益な情報をありがとうございます✨",
                    )
                else:
                    raw_msg = pick_next_template(
                        "quote",
                        acc_quote_templates or quote_templates,
                        "大変有益なポストです！📌",
                    )
                return format_template_message(raw_msg, resolved_disp, target_screen_name, preserve_unresolved=True)

            # 単一アクションをターゲット1件に実行
            def execute_single_action_on_target(act_key: str, target_item: dict, target_idx: int, total_in_mode: int, current_success_count: int, target_batch_goal: int, use_ai: bool = False, do_like: bool = False, do_follow: bool = False) -> tuple[bool, str]:
                nonlocal processed_count
                check_pause_state(f"{act_key.upper()} ターゲット [{target_idx}/{total_in_mode}] 実行前")
                target_screen_name = normalize_screen_name(target_item.get("アカウント名") or target_item.get("ターゲットURL") or target_item.get("ユーザー名") or "")
                if not target_screen_name:
                    return False, "INVALID_TARGET"

                target_tweet_url = str(target_item.get("ツイートURL") or target_item.get("tweet_url") or "").strip()
                raw_target_url = str(target_item.get("ターゲットURL") or target_item.get("url") or "").strip()
                if not target_tweet_url and "/status/" in raw_target_url:
                    target_tweet_url = raw_target_url
                target_tweet_url = normalize_tweet_url(target_tweet_url) or target_tweet_url

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

                db_action_type = db_action_map.get(act_key, act_key.upper())
                is_url_scoped_action = db_action_type in ("REPLY", "QUOTE", "LIKE")

                if db_mgr.is_user_unavailable(target_screen_name):
                    log(f"   ⏩ [{db_action_type}] @{target_screen_name} は凍結・存在しない(削除済み)アカウントとして共通ブラックリスト登録済みのためページを開かず0秒でスキップ")
                    return False, "UNAVAILABLE_USER"

                if is_url_scoped_action:
                    if target_tweet_url and "/status/" in target_tweet_url:
                        if db_mgr.is_registered_in_db(target_screen_name, action_type=db_action_type, target_url=target_tweet_url):
                            log(f"   ⏩ [{db_action_type}] 取得ポスト ({target_tweet_url}) はDB登録済みのためスキップ")
                            return False, "ALREADY_REGISTERED"
                else:
                    if db_mgr.is_registered_in_db(target_screen_name, action_type=db_action_type):
                        log(f"   ⏩ [{db_action_type}] @{target_screen_name} はDB登録済みのためスキップ")
                        return False, "ALREADY_REGISTERED"

                log("")
                if target_tweet_url:
                    log(f"🎯 [{act_key.upper()} {target_idx}/{total_in_mode}] ターゲット: @{target_screen_name} (取得ツイート: {target_tweet_url}) 実行開始 (成功: {current_success_count}/{target_batch_goal}件)...")
                else:
                    log(f"🎯 [{act_key.upper()} {target_idx}/{total_in_mode}] ターゲット: @{target_screen_name} 実行開始 (成功: {current_success_count}/{target_batch_goal}件)...")

                browser_ctrl.ensure_page()

                # ユーザー名のみ登録（ツイートURL未指定）の場合、最新ポストURLを取得してURL単位DBで重複判定する
                if is_url_scoped_action and (not target_tweet_url or "/status/" not in target_tweet_url):
                    try:
                        fetched_u, fetched_txt = browser_ctrl.get_latest_tweet_info(target_screen_name)
                        unavail_reason = getattr(browser_ctrl, "last_unavailable_reason", "")
                        if unavail_reason:
                            log(f"   🚫 [{db_action_type}] @{target_screen_name} は {unavail_reason} のため共通ブラックリストへ登録してスキップ")
                            db_mgr.record_sent(target_screen_name, account_id, db_action_type, "SUSPENDED", unavail_reason, target_url=target_tweet_url)
                            return False, "SUSPENDED"
                        if fetched_u:
                            target_tweet_url = normalize_tweet_url(fetched_u) or fetched_u
                            if fetched_txt and not target_item.get("ツイート本文"):
                                target_item["ツイート本文"] = fetched_txt
                            if db_mgr.is_registered_in_db(target_screen_name, action_type=db_action_type, target_url=target_tweet_url):
                                log(f"   ⏩ [{db_action_type}] @{target_screen_name} の最新ポスト ({target_tweet_url}) はDB登録済みのためスキップ")
                                return False, "ALREADY_REGISTERED"
                    except Exception:
                        pass

                def run_paired_like_and_follow():
                    if db_mgr.is_user_unavailable(target_screen_name):
                        log(f"   ⏩ [連動] @{target_screen_name} は凍結・存在しないアカウントのため連動処理をスキップ")
                        return
                    if do_like:
                        try:
                            like_url = normalize_tweet_url(getattr(browser_ctrl, "last_acted_tweet_url", "") or target_tweet_url) or target_tweet_url
                            if not like_url or "/status/" not in like_url:
                                try:
                                    f_like_u, _ = browser_ctrl.get_latest_tweet_info(target_screen_name)
                                    if getattr(browser_ctrl, "last_unavailable_reason", ""):
                                        db_mgr.record_sent(target_screen_name, account_id, "LIKE", "SUSPENDED", browser_ctrl.last_unavailable_reason)
                                        return
                                    if f_like_u:
                                        like_url = normalize_tweet_url(f_like_u) or f_like_u
                                except Exception:
                                    pass
                            if like_url and "/status/" in like_url and db_mgr.is_already_sent(target_screen_name, action_type="LIKE", target_url=like_url):
                                log(f"   ⏩ [連動いいね] 対象ポスト ({like_url}) は過去にいいね済みのためスキップ")
                            else:
                                check_pause_state(f"連動いいね @{target_screen_name} 実行前")
                                l_st, l_det = browser_ctrl.like_recent_post(target_screen_name, tweet_url=like_url)
                                log(f"   ❤️ [連動いいね結果] {l_st} ({l_det})")
                                eff_like_url = normalize_tweet_url(getattr(browser_ctrl, "last_acted_tweet_url", "") or like_url) or like_url
                                db_mgr.record_sent(target_screen_name, account_id, "LIKE", l_st, l_det, target_url=eff_like_url)
                                if l_st == "SUCCESS":
                                    enqueue_delete_if_applicable("LIKE", target_screen_name, target_url=eff_like_url)
                        except Exception as l_err:
                            log(f"   ⚠️ [連動いいね通知] @{target_screen_name} のいいね実行中にエラー: {l_err}")
                    if do_follow and not db_mgr.is_user_unavailable(target_screen_name):
                        try:
                            if db_mgr.is_already_sent(target_screen_name, action_type="FOLLOW"):
                                log(f"   ⏩ [連動フォロー] @{target_screen_name} は過去にフォロー済みのためスキップ")
                            else:
                                check_pause_state(f"連動フォロー @{target_screen_name} 実行前")
                                f_st, f_det = browser_ctrl.follow_user(target_screen_name)
                                log(f"   👤 [連動フォロー結果] {f_st} ({f_det})")
                                db_mgr.record_sent(target_screen_name, account_id, "FOLLOW", f_st, f_det)
                                if f_st == "SUCCESS":
                                    enqueue_delete_if_applicable("FOLLOW", target_screen_name)
                        except Exception as f_err:
                            log(f"   ⚠️ [連動フォロー通知] @{target_screen_name} のフォロー実行中にエラー: {f_err}")

                try:
                    if act_key == "follow":
                        st, det = browser_ctrl.follow_user(target_screen_name)
                        log(f"   👤 [フォロー結果] {st} ({det})")
                        get_target_name()
                        db_mgr.record_sent(target_screen_name, account_id, "FOLLOW", st, det, target_url=target_tweet_url)
                        if st == "SUCCESS":
                            enqueue_delete_if_applicable("FOLLOW", target_screen_name, target_url=target_tweet_url)
                        status_label = "フォロー済" if st in ["SUCCESS", "ALREADY_FOLLOWING"] else f"フォロー:{st}"
                        config_mgr.update_target_status(target_screen_name, status_label, target_path_override=acc_target_path)
                        if st in ["SUCCESS", "ALREADY_FOLLOWING"]:
                            processed_count += 1
                            return True, "SUCCESS"
                        if st == "FAILED":
                            browser_ctrl.capture_error_screenshot("follow_failed", clean_account_id, target_screen_name)
                        return False, st

                    elif act_key == "like":
                        st, det = browser_ctrl.like_recent_post(target_screen_name, tweet_url=target_tweet_url)
                        log(f"   ❤️ [いいね結果] {st} ({det})")
                        get_target_name()
                        eff_like_url = normalize_tweet_url(getattr(browser_ctrl, "last_acted_tweet_url", "") or target_tweet_url) or target_tweet_url
                        db_mgr.record_sent(target_screen_name, account_id, "LIKE", st, det, target_url=eff_like_url)
                        if st == "SUCCESS":
                            enqueue_delete_if_applicable("LIKE", target_screen_name, target_url=eff_like_url)
                        status_label = "いいね済" if st in ["SUCCESS", "ALREADY_LIKED"] else f"いいね:{st}"
                        config_mgr.update_target_status(target_screen_name, status_label, target_path_override=acc_target_path)
                        if st in ["SUCCESS", "ALREADY_LIKED"]:
                            processed_count += 1
                            return True, "SUCCESS"
                        if st == "FAILED":
                            browser_ctrl.capture_error_screenshot("like_failed", clean_account_id, target_screen_name)
                        return False, st

                    elif act_key in ["reply", "reply_ai"]:
                        resolved_disp = get_target_name()
                        target_tweet_text = str(target_item.get("ツイート本文") or target_item.get("tweet_text") or "").strip()
                        target_parent_text = str(target_item.get("親ツイート本文") or target_item.get("parent_tweet_text") or "").strip()
                        is_rep_tgt = bool(target_item.get("is_reply", False)) or target_tweet_text.lstrip().startswith("@")
                        rep_msg = generate_ai_or_template_text("reply", target_screen_name, resolved_disp, target_tweet_url, use_ai=use_ai, cached_tweet_text=target_tweet_text, cached_parent_text=target_parent_text, is_reply_target=is_rep_tgt)
                        if not rep_msg or not rep_msg.strip() or rep_msg.startswith("__AI_SKIP_RULE__:"):
                            skip_reason = rep_msg.split(":", 1)[1].strip() if ":" in (rep_msg or "") else "空欄/出力ルール対象外"
                            log(f"   ⏭️ [リプライスキップ] @{target_screen_name}: AIが「{skip_reason}」と判定したため投稿を中止します（連動処理もスキップ）")
                            db_mgr.record_sent(target_screen_name, account_id, "REPLY", "SKIPPED_AI_RULE", f"AIスキップ({skip_reason[:30]})", target_url=target_tweet_url)
                            config_mgr.update_target_status(target_screen_name, f"AIスキップ({skip_reason[:20]})", target_path_override=acc_target_path)
                            return False, "SKIPPED_AI_RULE"
                        preview_name = resolved_disp or "相手のアカウント名"
                        preview_log = format_template_message(rep_msg, preview_name, target_screen_name, preserve_unresolved=False)
                        log(f"   💬 [リプライ送信中] 「{preview_log[:35]}...」")
                        st, det = browser_ctrl.send_reply(target_screen_name, rep_msg, tweet_url=target_tweet_url)
                        log(f"   💬 [リプライ結果] {st} ({det})")
                        eff_rep_url = normalize_tweet_url(getattr(browser_ctrl, "last_acted_tweet_url", "") or target_tweet_url) or target_tweet_url
                        own_rep_url = normalize_tweet_url(getattr(browser_ctrl, "last_created_post_url", "") or "")
                        if eff_rep_url:
                            target_tweet_url = eff_rep_url
                        db_mgr.record_sent(target_screen_name, account_id, "REPLY", st, rep_msg[:40] if st == "SUCCESS" else det, target_url=eff_rep_url)
                        if st == "SUCCESS":
                            enqueue_delete_if_applicable("REPLY", target_screen_name, target_url=eff_rep_url, own_post_url=own_rep_url, snippet=preview_log[:120])
                        status_label = "リプライ完了" if st == "SUCCESS" else f"リプライ:{st}"
                        config_mgr.update_target_status(target_screen_name, status_label, target_path_override=acc_target_path)
                        if st == "SUCCESS":
                            run_paired_like_and_follow()
                            processed_count += 1
                            return True, "SUCCESS"
                        if st == "FAILED":
                            browser_ctrl.capture_error_screenshot("reply_failed", clean_account_id, target_screen_name)
                        return False, st

                    elif act_key == "dm":
                        dm_msg = target_item.get("個別メッセージ") or ""
                        if not dm_msg:
                            dm_msg = pick_next_template(
                                "dm",
                                acc_dm_templates or dm_templates,
                                "{name}さん、はじめまして！ご連絡ありがとうございます。今後ともよろしくお願いいたします。",
                            )

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
                        db_mgr.record_sent(target_screen_name, account_id, "DM", st, dm_msg[:40] if st == "SUCCESS" else det, target_url=target_tweet_url)

                        if st == "SUCCESS":
                            config_mgr.update_target_status(target_screen_name, "DM送信完了", target_path_override=acc_target_path)
                            run_paired_like_and_follow()
                            processed_count += 1
                            return True, "SUCCESS"
                        elif st in ["INBOX_CLOSED", "DM_REJECTED", "LOCKED_ACCOUNT", "SUSPENDED"]:
                            is_inbox_closed = (st == "INBOX_CLOSED") or ("受信トレイ" in det) or ("Xナンバー" in det)
                            status_label = "アカウント凍結/削除済み" if st == "SUSPENDED" else ("受信トレイ閉鎖(DM不可)" if is_inbox_closed else f"DMスキップ({det})")
                            config_mgr.update_target_status(target_screen_name, status_label, target_path_override=acc_target_path)
                            log(f"   ⏩ 【送信カウント除外】@{target_screen_name} は送信不可（{det}）のためスキップします（現在の成功: {current_success_count}/{target_batch_goal}件）")
                            time.sleep(random.uniform(1.8, 3.2))
                            return False, "SKIPPED_INBOX_CLOSED"
                        else:
                            browser_ctrl.capture_error_screenshot("dm_failed", clean_account_id, target_screen_name)
                            config_mgr.update_target_status(target_screen_name, f"DMエラー({det})", target_path_override=acc_target_path)
                            return False, st

                    elif act_key in ["quote", "quote_ai"]:
                        resolved_disp = get_target_name()
                        target_tweet_text = str(target_item.get("ツイート本文") or target_item.get("tweet_text") or "").strip()
                        target_parent_text = str(target_item.get("親ツイート本文") or target_item.get("parent_tweet_text") or "").strip()
                        is_rep_tgt = bool(target_item.get("is_reply", False)) or target_tweet_text.lstrip().startswith("@")
                        q_msg = generate_ai_or_template_text("quote", target_screen_name, resolved_disp, target_tweet_url, use_ai=use_ai, cached_tweet_text=target_tweet_text, cached_parent_text=target_parent_text, is_reply_target=is_rep_tgt)
                        if not q_msg or not q_msg.strip() or q_msg.startswith("__AI_SKIP_RULE__:"):
                            skip_reason = q_msg.split(":", 1)[1].strip() if ":" in (q_msg or "") else "空欄/出力ルール対象外"
                            log(f"   ⏭️ [引用RTスキップ] @{target_screen_name}: AIが「{skip_reason}」と判定したため投稿を中止します（連動処理もスキップ）")
                            db_mgr.record_sent(target_screen_name, account_id, "QUOTE", "SKIPPED_AI_RULE", f"AIスキップ({skip_reason[:30]})", target_url=target_tweet_url)
                            config_mgr.update_target_status(target_screen_name, f"AIスキップ({skip_reason[:20]})", target_path_override=acc_target_path)
                            return False, "SKIPPED_AI_RULE"
                        preview_name = resolved_disp or "相手のアカウント名"
                        preview_log = format_template_message(q_msg, preview_name, target_screen_name, preserve_unresolved=False)
                        log(f"   🔁 [引用RT実行中] 「{preview_log[:35]}...」")
                        st, det = browser_ctrl.quote_tweet(target_screen_name, q_msg, tweet_url=target_tweet_url)
                        log(f"   🔁 [引用RT結果] {st} ({det})")
                        eff_quote_url = normalize_tweet_url(getattr(browser_ctrl, "last_acted_tweet_url", "") or target_tweet_url) or target_tweet_url
                        own_quote_url = normalize_tweet_url(getattr(browser_ctrl, "last_created_post_url", "") or "")
                        if eff_quote_url:
                            target_tweet_url = eff_quote_url
                        db_mgr.record_sent(target_screen_name, account_id, "QUOTE", st, q_msg[:40] if st == "SUCCESS" else det, target_url=eff_quote_url)
                        if st == "SUCCESS":
                            enqueue_delete_if_applicable("QUOTE", target_screen_name, target_url=eff_quote_url, own_post_url=own_quote_url, snippet=preview_log[:120])
                        status_label = "引用RT完了" if st == "SUCCESS" else f"引用RT:{st}"
                        config_mgr.update_target_status(target_screen_name, status_label, target_path_override=acc_target_path)
                        if st == "SUCCESS":
                            run_paired_like_and_follow()
                            processed_count += 1
                            return True, "SUCCESS"
                        if st == "FAILED":
                            browser_ctrl.capture_error_screenshot("quote_failed", clean_account_id, target_screen_name)
                        return False, st

                except Exception as target_err:
                    log(f"   ⚠️ ターゲット @{target_screen_name} の処理中にエラーが発生したためスキップします: {target_err}")
                    try:
                        browser_ctrl.capture_error_screenshot(f"{act_key}_exception", clean_account_id, target_screen_name)
                    except Exception:
                        pass
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

            def wait_between_targets(wait_time: float, label_prefix: str, cur_cnt: int, goal_cnt: int, act_min: float, act_max: float, wait_kind: str = "ランダムインターバル"):
                log(f"☕ [{label_prefix}] 次のターゲットまで {wait_time:.1f} 秒待機します（{wait_kind} {act_min:.0f}秒〜{act_max:.0f}秒 / 進捗: {cur_cnt}/{goal_cnt}件）...")
                start_wait = time.time()
                close_threshold = 60.0 if low_spec_mode else 300.0
                close_during_wait = wait_time >= close_threshold
                if close_during_wait:
                    log(f"🚪 [{'省メモリ待機' if low_spec_mode else '超長時間待機'}] 待機時間（{wait_time:.1f}秒 >= {close_threshold:.0f}秒）のため、ブラウザウィンドウを一旦安全に閉じてメモリを解放します...")
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

                try:
                    process_due_auto_deletes("待機終了後")
                except Exception:
                    pass

            total_actions_performed = 0
            batch_cooldown_pending = None
            target_wait_pending = False

            # --- 各アクションを「アクションごとに設定されたモード」で順番実行 ---
            for act_key, act_title, a_cfg in active_action_jobs:
                act_modes = parse_mode_list(a_cfg.get("mode") or target_mode or "検索最新")
                act_min_int = float(a_cfg.get("min_interval", 300.0))
                act_max_int = float(a_cfg.get("max_interval", 900.0))
                if act_min_int > act_max_int:
                    act_min_int, act_max_int = act_max_int, act_min_int
                act_min_b = max(1, int(a_cfg.get("min_batch", 1)))
                act_max_b = max(act_min_b, int(a_cfg.get("max_batch", act_min_b)))
                use_ai = bool(a_cfg.get("ai_gen", False)) or act_key.endswith("_ai")
                do_like = bool(a_cfg.get("like_enabled", False))
                do_follow = bool(a_cfg.get("follow_enabled", False))

                log("")
                log(f"{'='*58}")
                log(f"⚡ 【アクション実行開始: {act_title}】")
                log(f"   設定モード ({len(act_modes)}個): {', '.join([lbl for _, lbl in act_modes])}")
                log(f"   連続処理目標: 最小{act_min_b}〜最大{act_max_b}件 / 1件ごと待機(ランダムインターバル): {target_min_sec:.0f}〜{target_max_sec:.0f}秒 / 連続処理後・実行間隔: {act_min_int:.0f}〜{act_max_int:.0f}秒 / AI生成: {'ON' if use_ai else 'OFF'} / いいね: {'ON' if do_like else 'OFF'} / フォロー: {'ON' if do_follow else 'OFF'}")
                log(f"{'='*58}")

                for mode_idx, (m_type, m_label) in enumerate(act_modes, start=1):
                    check_pause_state(f"{act_title} モード [{mode_idx}/{len(act_modes)}: {m_label}] 開始前")
                    mode_targets = collect_targets_for_mode(m_type, m_label, act_key=act_key)
                    if not mode_targets and m_type != "LIST":
                        log(f"🔄 [{act_title} / {m_label}] ターゲットを取得できなかったため、ページを再読み込みして再探索します...")
                        wait_with_abort(3.0)
                        try:
                            browser_ctrl.recover_if_stuck("", f"{m_label}ターゲット未取得のため再読み込み")
                        except Exception:
                            pass
                        mode_targets = collect_targets_for_mode(m_type, m_label, act_key=act_key, force_refresh=True)
                    if not mode_targets:
                        log(f"ℹ️ [{act_title} / {m_label}] 完全新規の有効ターゲットが0件でした。次のモードへ進みます。")
                        continue

                    batch_target_count = random.randint(act_min_b, act_max_b)
                    if batch_cooldown_pending is not None:
                        cd_title, cd_min, cd_max, cd_done, cd_goal = batch_cooldown_pending
                        batch_wait = random.uniform(cd_min, cd_max)
                        log(f"⏸️ [{cd_title}] 連続処理 {cd_done}件完了のため、次の処理前に実行間隔 {batch_wait:.1f} 秒待機します（実行間隔 {cd_min:.0f}秒〜{cd_max:.0f}秒）...")
                        wait_between_targets(batch_wait, cd_title, cd_done, cd_goal, cd_min, cd_max, wait_kind="連続処理後・実行間隔")
                        batch_cooldown_pending = None
                        target_wait_pending = False
                    elif target_wait_pending:
                        t_wait = random.uniform(target_min_sec, target_max_sec)
                        wait_between_targets(t_wait, act_title, 0, batch_target_count, target_min_sec, target_max_sec, wait_kind="ランダムインターバル")
                        target_wait_pending = False

                    log(f"📋 [{act_title} / {m_label}] 取得新規ターゲット: {len(mode_targets)}件 / 目標連続処理数: {batch_target_count}件")

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
                            use_ai=use_ai,
                            do_like=do_like,
                            do_follow=do_follow
                        )
                        if is_ok:
                            mode_success_count += 1
                            total_actions_performed += 1
                            if mode_success_count < batch_target_count and t_idx < len(mode_targets):
                                wait_time = random.uniform(target_min_sec, target_max_sec)
                                wait_between_targets(wait_time, act_title, mode_success_count, batch_target_count, target_min_sec, target_max_sec, wait_kind="ランダムインターバル")

                    if mode_success_count >= batch_target_count:
                        batch_cooldown_pending = (act_title, act_min_int, act_max_int, mode_success_count, batch_target_count)
                        target_wait_pending = False
                    elif mode_success_count > 0:
                        target_wait_pending = True

                    log(f"✅ [{act_title} / {m_label}] 完了 (成功: {mode_success_count}件 / 目標: {batch_target_count}件)")

            # --- 全アクション終了後のフォールバック＆新規アカウント救済 ---
            if total_actions_performed == 0 and active_action_jobs:
                configured_mode_types = {
                    mt
                    for (_, _, acfg) in active_action_jobs
                    for (mt, _) in parse_mode_list(acfg.get("mode") or target_mode or "検索最新")
                }
                if configured_mode_types and configured_mode_types.issubset({"URL_REPLY", "URL_FOLLOW"}):
                    log("")
                    log("ℹ️ 【URLリスト専用終了】URLリスト系モードのみが指定されており、新規ターゲットが見つからなかったため（相互フォロー検索等の自動救済は行わず）このまま安全終了します。")
                else:
                    log("")
                    log("⚠️ 【全モード完了確認】設定されたモードによる実行件数が0件でした。Excel内の未送信・DB未登録ターゲットまたは自動救済を確認します...")
                    act_key_fb = active_action_jobs[0][0]
                    fb_db_act = db_action_map.get(act_key_fb, "DM")
                    fb_skip_fn = lambda u, tu="": (not u) or is_placeholder_target(u) or db_mgr.is_user_unavailable(u) or db_mgr.is_registered_in_db(u, action_type=fb_db_act, target_url=tu)
                    raw_existing = config_mgr.load_targets(limit=0, target_path_override=acc_target_path)
                    existing_targets = [
                        it for it in raw_existing
                        if not fb_skip_fn(normalize_screen_name(it.get("アカウント名") or it.get("ターゲットURL") or ""), normalize_tweet_url(it.get("ツイートURL") or it.get("ターゲットURL") or ""))
                    ]
                    if target_read_limit > 0:
                        existing_targets = existing_targets[:target_read_limit]
                    allow_mutual_rescue = bool(configured_mode_types & {"SEARCH_LATEST", "SEARCH_TOP", "LIST", "MUTUAL"})
                    if not existing_targets and allow_mutual_rescue:
                        log("💡 【新規アカウント救済モード】標準キーワード「相互フォロー (最新)」からDB未登録のアクティブユーザーを探索します...")
                        auto_found = browser_ctrl.collect_targets_from_search("相互フォロー", search_type="最新", scroll_limit=scroll_limit, max_targets=target_read_limit, should_skip_fn=fb_skip_fn)
                        append_collected_items(auto_found, "自動救済:相互フォロー(最新)", existing_targets)

                    if existing_targets:
                        act_key, act_title, a_cfg = active_action_jobs[0]
                        act_min_int = float(a_cfg.get("min_interval", 300.0))
                        act_max_int = float(a_cfg.get("max_interval", 900.0))
                        act_min_b = max(1, int(a_cfg.get("min_batch", 1)))
                        act_max_b = max(act_min_b, int(a_cfg.get("max_batch", act_min_b)))
                        batch_target_count = random.randint(act_min_b, act_max_b)
                        use_ai = bool(a_cfg.get("ai_gen", False)) or act_key.endswith("_ai")
                        do_like = bool(a_cfg.get("like_enabled", False))
                        do_follow = bool(a_cfg.get("follow_enabled", False))
                        fb_success_count = 0
                        for t_idx, target in enumerate(existing_targets, start=1):
                            if fb_success_count >= batch_target_count:
                                break
                            is_ok, _ = execute_single_action_on_target(act_key, target, t_idx, len(existing_targets), fb_success_count, batch_target_count, use_ai=use_ai, do_like=do_like, do_follow=do_follow)
                            if is_ok:
                                fb_success_count += 1
                                total_actions_performed += 1
                                if fb_success_count < batch_target_count and t_idx < len(existing_targets):
                                    wait_between_targets(random.uniform(target_min_sec, target_max_sec), act_title, fb_success_count, batch_target_count, target_min_sec, target_max_sec, wait_kind="ランダムインターバル")
                    else:
                        log("ℹ️ 有効なターゲットが見つかりませんでした（サンプル/ダミーターゲットは除外）。再読み込み後も取得できなかったため安全に終了します。")

            if not session_failed:
                try:
                    process_due_auto_deletes("セッション終了前")
                except Exception:
                    pass
                health_mgr.record_success(clean_account_id)

        except Exception as e:
            session_failed = True
            log(f"⚠️ アカウント [{account_id}] の処理中に予期せぬエラーが発生しました: {e}。このアカウントを安全にスキップし、次のアカウントへ進みます。")
            try:
                if browser_ctrl:
                    browser_ctrl.capture_error_screenshot("account_exception", clean_account_id)
            except Exception:
                pass
            try:
                res_h = health_mgr.record_failure(clean_account_id, reason=str(e), pause_hours=1.0, immediate_pause=False, threshold=3)
                if res_h.get("newly_paused"):
                    log(f"⛔ 【自動休止(サーキットブレーカー)】アカウント [{account_id}] が連続エラー ({res_h.get('consecutive_failures')}回) のため一時休止されました。")
            except Exception:
                pass
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

            # 通常実行完了時のみ次回周回待機時刻を更新（自動削除専用起動では本来の通常周回時刻を維持）
            if not delete_only_mode:
                next_interval = random.uniform(account_min_sec, account_max_sec)
                account_ready_times[clean_account_id] = time.time() + next_interval
                log(f"⏰ 次回のアカウント [{account_id}] 実行可能時間: 約{next_interval:.1f}秒後 (実行間隔設定: {account_min_sec:.0f}秒 〜 {account_max_sec:.0f}秒)")

    # === 実行モード判定 (1窓〜最大5窓同時並列実行 / 低スペPC超軽量モード時は1窓固定) ===
    env_concurrent = os.environ.get("TOOL_MAX_CONCURRENT", "").strip()
    global_max_concurrent = 1
    if low_spec_mode:
        global_max_concurrent = 1
    elif env_concurrent.isdigit() and 1 <= int(env_concurrent) <= 5:
        global_max_concurrent = int(env_concurrent)
    else:
        global_max_concurrent = config_mgr.load_max_concurrent()

    log("")
    if low_spec_mode:
        log("🪶 【低スペPC超軽量モード ON】 1窓固定・メディア通信遮断・軽量Chromiumフラグ・キャッシュ自動掃除・60秒以上待機時ブラウザ自動終了を有効化")
    log(f"🪟 【ブラウザ起動モード】: {f'⚡ {global_max_concurrent}窓同時並列実行モード (最大{global_max_concurrent}アカウント同時進行)' if global_max_concurrent > 1 else '🔒 1窓完全ローテーションモード (1画面ずつ順番・最短待ち優先スケジューラ)'}")

    if global_max_concurrent == 1:
        log("📋 【非ブロッキング・最短待ち優先スケジューラ】稼働時間帯＆健全性を確認し、待機完了済みのアカウントから即座に順次実行します。")
        pending = []
        for acc_idx, acc in enumerate(accounts, start=1):
            clean_id, _ = _extract_clean_id_from_row(acc, acc_idx)
            if not clean_id:
                continue
            acc_t_path = config_mgr.get_target_path_for_account(clean_id)
            acc_s = config_mgr.load_account_settings(clean_id, target_path_override=acc_t_path, fallback_acc=acc)
            ah_spec = str(acc_s.get("active_hours", "") or get_acc_val(acc, ["稼働時間帯", "稼働時間", "営業時間", "実行時間帯", "activehours"], "") or "").strip()
            e_mins = [cfg.get("min_interval", 300.0) for cfg in (acc_s.get("actions") or {}).values() if cfg.get("enabled")]
            e_maxs = [cfg.get("max_interval", 900.0) for cfg in (acc_s.get("actions") or {}).values() if cfg.get("enabled")]
            pending.append({
                "acc": acc,
                "acc_idx": acc_idx,
                "clean_id": clean_id,
                "active_hours": ah_spec,
                "min_sec": min(e_mins) if e_mins else 300.0,
                "max_sec": max(e_maxs) if e_maxs else 900.0,
                "auto_delete": acc_s.get("auto_delete", {}),
                "acc_db": DatabaseManager(get_account_db_path(base_dir, clean_id)),
            })

        while pending:
            check_app_termination(base_dir)
            check_pause_state("スケジューラ待機")
            now_ts = time.time()
            eligible = []
            still_pending = []
            delete_only_flags = {}
            for item in pending:
                cid = item["clean_id"]
                is_paused, p_info = health_mgr.is_paused(cid, now_ts=now_ts)
                if is_paused:
                    rem_m = max(1, int((float(p_info.get("paused_until") or 0) - now_ts) // 60) + 1)
                    log(f"⛔ [自動休止スキップ] アカウント [@{cid}] は一時休止中のため今回スキップします（残り約{rem_m}分 / 理由: {p_info.get('pause_reason', '異常検知')}）")
                    continue
                ok_hr, wait_hr = is_within_active_hours(item["active_hours"])
                if not ok_hr:
                    log(f"🌙 [稼働時間外スキップ] アカウント [@{cid}] は稼働時間帯 ({item['active_hours']}) 外のため今回スキップします（次回開始まで約{max(1, wait_hr // 60)}分）")
                    continue
                normal_ready_at = account_ready_times.get(cid, 0.0)
                eff_ready_at, is_del_only = compute_effective_ready_at(
                    normal_ready_at,
                    item.get("auto_delete") or {},
                    item.get("acc_db"),
                    now_ts=now_ts,
                )
                delete_only_flags[(cid, item["acc_idx"])] = is_del_only
                eligible.append((eff_ready_at, item["acc_idx"], item))
                still_pending.append(item)

            pending = still_pending
            if not eligible:
                break

            # 準備完了済み (ready_at <= now) を最優先。無ければ最短待ち時刻のアカウントを選択
            chosen_ready_at, chosen_idx, chosen_item = select_ready_first_candidate(eligible, now_ts)
            chosen_is_del_only = bool(delete_only_flags.get((chosen_item["clean_id"], chosen_idx), False))
            remaining_wait = chosen_ready_at - time.time()
            if remaining_wait > 0:
                wait_ctx = f"第{cycle_count}周回 自動削除期限待機" if chosen_is_del_only else f"第{cycle_count}周回 最短待ち優先"
                log(f"⏳ [{'自動削除期限待機' if chosen_is_del_only else '最短待ち優先'}] 実行可能な他アカウントがないため、最短待機アカウント [@{chosen_item['clean_id']}] の{'自動削除予定時刻' if chosen_is_del_only else '準備完了'}まで {remaining_wait:.1f} 秒待機します...")
                wait_for_account_interval(chosen_item["clean_id"], remaining_wait, chosen_item["min_sec"], chosen_item["max_sec"], base_dir, wait_ctx)
            else:
                log(f"⚡ [{'自動削除即時起動' if chosen_is_del_only else '即時起動'}] アカウント [@{chosen_item['clean_id']}] は準備完了済みのため待ち時間0秒で開始します（残り待機キュー: {len(pending) - 1}件）")

            if not chosen_is_del_only:
                pending = [it for it in pending if it["clean_id"] != chosen_item["clean_id"] or it["acc_idx"] != chosen_idx]
            process_single_account(
                chosen_item["acc"],
                chosen_idx,
                total_acc_count,
                active_slot=1,
                slot_semaphore=None,
                skip_interval_wait=True,
                delete_only_mode=chosen_is_del_only,
            )
    else:
        log(f"⚡ 【{global_max_concurrent}窓同時並列実行】時間が被ったアカウントは最大{global_max_concurrent}画面まで同時に独立ブラウザ（独立Cookie/独立DB/独立ログ）で並行稼働します。")
        log(f"   ※{global_max_concurrent}枠が埋まっている間に待機時間が到来した場合は、待機キューでスタンバイし、空き枠が出次第0秒即時起動します。")
        slot_semaphore = threading.Semaphore(global_max_concurrent)
        # 待機時間到来順（準備完了済み優先）でスレッド投入
        ordered_accs = sorted(
            enumerate(accounts, start=1),
            key=lambda pair: account_ready_times.get(_extract_clean_id_from_row(pair[1], pair[0])[0], 0.0),
        )
        threads = []
        for acc_idx, acc in ordered_accs:
            t = threading.Thread(
                target=process_single_account,
                args=(acc, acc_idx, total_acc_count, ((acc_idx - 1) % global_max_concurrent) + 1, slot_semaphore, False),
                name=f"Thread-Acc-{acc_idx}"
            )
            threads.append(t)
            t.start()
            time.sleep(0.5)

        for t in threads:
            t.join()

    account_ready_times["__last_cycle_executed__"] = cycle_executed_count
    log(f"\n🏁 【第 {cycle_count} 周回 完了】 今回の巡回対象アカウント処理がすべて完了しました (実行: {cycle_executed_count}件)。")
    return cycle_min_sec, cycle_max_sec

def main():
    print("=" * 65)
    print("  X (Twitter) 自動DM・リプライ・送信ツール Pro - Windows版")
    print("  Playwright x 人間らしいタイピング x ベジェ曲線 x BAN対策")
    print("  ★ 無限ループ巡回モード: 1周終了後に待機秒数待機し、自動継続実行")
    print("=" * 65)

    base_dir = get_base_dir()
    os.makedirs(os.path.join(base_dir, "スクショフォルダ"), exist_ok=True)
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
        log(f"   💡 提案1: 最短待ち優先1窓ローテ / 提案2: メディア通信遮断 / 稼働時間帯・自動休止対応")
        log(f"{'#'*65}")

        try:
            cycle_min_sec, cycle_max_sec = run_single_cycle(config_mgr, db_mgr, base_dir, cycle_count, account_ready_times)
        except Exception as cycle_err:
            log(f"⚠️ 第 {cycle_count} 周回中に予期せぬエラーが発生しました: {cycle_err}。")
            traceback.print_exc()

        executed_cnt = int(account_ready_times.pop("__last_cycle_executed__", 0) or 0)
        if executed_cnt == 0:
            idle_sec = 60.0
            log(f"💤 現在稼働可能なアカウントがない（稼働時間外または自動休止中）ため、CPU負荷0%で {idle_sec:.0f}秒後に再チェックします...")
            idle_start = time.time()
            while time.time() - idle_start < idle_sec:
                check_app_termination(base_dir)
                check_pause_state("アイドル待機中")
                time.sleep(1.0)
        else:
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
