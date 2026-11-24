"""
Excel設定ファイル管理 (openpyxl)
- Windows環境での PermissionError (Excelが開かれている場合) 対策
  -> 一時ファイルへのコピー読み取り & 再試行リトライ機構
- 「アカウント情報.xlsx」と「アカウント名情報.xlsx」の読み書き
"""

import os
import sys
import re
import shutil
import tempfile
import time
from datetime import datetime
import openpyxl

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


def is_placeholder_target(val: str) -> bool:
    """テンプレート初期サンプル (@sample_user_01 等) のダミーアカウントを判定して実行除外する"""
    clean = normalize_screen_name(val).lower()
    if not clean:
        return True
    return bool(re.match(r'^(?:sample_user|sample_account|dummy_user|example_user|test_user)(?:_\d+|\d+)?$', clean))


_FULLWIDTH_TRANS = str.maketrans("０１２３４５６７８９：；〜～−‐—－、／", "0123456789:;------,/")


def parse_active_hours_spec(spec: str) -> list[tuple[int, int]]:
    """
    稼働時間帯文字列 (例: "09:00-17:00", "22:00-04:00", "07:00-10:00, 19:00-23:00", "9時-18時")
    を (開始分, 終了分) のタプルリスト (0〜1440分) にパース。
    空欄 / "24時間" / "常時" 等の場合は空リスト []（＝24時間常に稼働）を返す。
    """
    raw = str(spec or "").translate(_FULLWIDTH_TRANS).strip()
    if not raw:
        return []
    low = raw.replace(" ", "").replace("　", "").lower()
    if low in ("24", "24h", "24時間", "常時", "いつでも", "終日", "all", "always", "なし", "未設定", "-"):
        return []

    parts = [p.strip() for p in re.split(r"[,;/\n]+", raw) if p.strip()]
    windows: list[tuple[int, int]] = []
    time_pat = re.compile(
        r"(\d{1,2})(?::(\d{1,2})|時(\d{1,2})?分?)?\s*(?:-|to)\s*(\d{1,2})(?::(\d{1,2})|時(\d{1,2})?分?)?",
        re.IGNORECASE,
    )
    for part in parts:
        p_norm = part.replace("〜", "-").replace("~", "-").replace("から", "-")
        m = time_pat.search(p_norm)
        if not m:
            continue
        try:
            sh = int(m.group(1))
            sm = int(m.group(2) or m.group(3) or 0)
            eh = int(m.group(4))
            em = int(m.group(5) or m.group(6) or 0)
            if not (0 <= sh <= 24 and 0 <= sm < 60 and 0 <= eh <= 24 and 0 <= em < 60):
                continue
            start_min = min(1440, sh * 60 + sm)
            end_min = min(1440, eh * 60 + em)
            if start_min == end_min or (start_min == 0 and end_min == 1440):
                # 00:00-24:00 または 同一時刻は24時間扱い
                return []
            windows.append((start_min, end_min))
        except Exception:
            continue
    return windows


def is_within_active_hours(spec: str, now_dt: datetime = None) -> tuple[bool, int]:
    """
    現在時刻が稼働時間帯内かどうか判定する。
    戻り値: (is_active: bool, seconds_until_next_start: int)
    """
    windows = parse_active_hours_spec(spec)
    if not windows:
        return True, 0

    now = now_dt or datetime.now()
    cur_sec = now.hour * 3600 + now.minute * 60 + now.second
    cur_min = now.hour * 60 + now.minute

    for start_min, end_min in windows:
        if start_min < end_min:
            if start_min <= cur_min < end_min:
                return True, 0
        else:
            # 日跨ぎウィンドウ (例: 22:00-04:00)
            if cur_min >= start_min or cur_min < end_min:
                return True, 0

    # 時間外: 次に開始するウィンドウまでの最短秒数を計算
    min_wait = 86400
    for start_min, _ in windows:
        start_sec = start_min * 60
        diff = start_sec - cur_sec
        if diff <= 0:
            diff += 86400
        if diff < min_wait:
            min_wait = diff
    return False, max(1, int(min_wait))


def normalize_time_unit(unit_val: str, default_unit: str = "分") -> str:
    """時間単位文字列を '秒' | '分' | '時間' に正規化"""
    u = str(unit_val or "").strip().lower()
    if any(k in u for k in ("秒", "sec")) or u == "s":
        return "秒"
    if any(k in u for k in ("時", "hour", "hr")) or u == "h":
        return "時間"
    if any(k in u for k in ("分", "min")) or u == "m":
        return "分"
    du = str(default_unit or "分").strip().lower()
    if any(k in du for k in ("秒", "sec")) or du == "s":
        return "秒"
    if any(k in du for k in ("時", "hour", "hr")) or du == "h":
        return "時間"
    return "分"


def parse_delay_to_seconds(val_str, default_unit: str = "分") -> float:
    """
    遅延時間文字列（例: '60', '60分', '1.5時間', '30秒', '90m', '2h', '45s'）を秒数へ変換。
    数値に単位が明記されている場合はその単位を優先し、数値のみの場合は default_unit を適用する。
    """
    raw = str(val_str if val_str is not None else "").translate(_FULLWIDTH_TRANS).strip()
    if not raw:
        return 0.0
    m = re.search(r'(\d+(?:\.\d+)?)', raw)
    if not m:
        return 0.0
    try:
        num = float(m.group(1))
    except Exception:
        return 0.0
    low = raw.lower()
    if re.search(r'(?:秒|sec|s)\b', low) or "秒" in low:
        mult = 1.0
    elif re.search(r'(?:時間|時|hour|hrs|hr|h)\b', low) or "時間" in low or "時" in low:
        mult = 3600.0
    elif re.search(r'(?:分|min|mins|m)\b', low) or "分" in low:
        mult = 60.0
    else:
        norm_u = normalize_time_unit(default_unit, "分")
        mult = 1.0 if norm_u == "秒" else (3600.0 if norm_u == "時間" else 60.0)
    return max(0.0, num * mult)


def _default_auto_delete_settings() -> dict:
    return {
        "follow": {"enabled": False, "min_delay": 60.0, "max_delay": 180.0, "unit": "分", "min_seconds": 3600.0, "max_seconds": 10800.0, "max_per_run": 10},
        "like": {"enabled": False, "min_delay": 60.0, "max_delay": 180.0, "unit": "分", "min_seconds": 3600.0, "max_seconds": 10800.0, "max_per_run": 10},
        "reply": {"enabled": False, "min_delay": 120.0, "max_delay": 360.0, "unit": "分", "min_seconds": 7200.0, "max_seconds": 21600.0, "max_per_run": 10},
        "quote": {"enabled": False, "min_delay": 120.0, "max_delay": 360.0, "unit": "分", "min_seconds": 7200.0, "max_seconds": 21600.0, "max_per_run": 10},
    }


class ConfigManager:
    def __init__(self, account_config_path: str = "", target_list_path: str = "", base_dir: str = ""):
        # 1引数のみでディレクトリが渡された場合、または空引数に対応 (例: ConfigManager(self.base_dir))
        if account_config_path and os.path.isdir(account_config_path):
            base_dir = account_config_path
            account_config_path = ""

        if not base_dir:
            if account_config_path and os.path.isfile(account_config_path):
                base_dir = os.path.dirname(os.path.abspath(account_config_path))
            else:
                if getattr(sys, 'frozen', False):
                    base_dir = os.path.dirname(sys.executable)
                else:
                    env_dir = os.environ.get("X_AUTO_DM_BASE_DIR", "").strip().strip('"')
                    d = os.path.dirname(os.path.abspath(__file__))
                    base_dir = os.path.normpath(env_dir) if (env_dir and os.path.isdir(env_dir)) else (os.path.dirname(d) if os.path.basename(d) == "_app" else d)

        self.base_dir = base_dir
        self.account_config_path = account_config_path or os.path.join(self.base_dir, "アカウント情報.xlsx")
        self.target_list_path = target_list_path or os.path.join(self.base_dir, "アカウント名.xlsx")

    def get_target_path_for_account(self, account_id: str) -> str:
        """アカウント情報に登録されているアカウント名に応じた個別Excelファイル (例: reghv6.xlsx) を最優先で取得"""
        clean_id = normalize_screen_name(account_id)
        raw_clean = str(account_id).strip().lstrip("@")

        # 1. フォルダ内の大文字小文字・全角半角・@を吸収した完全スキャン探索 (最優先)
        if os.path.exists(self.base_dir):
            try:
                for fname in os.listdir(self.base_dir):
                    if fname.lower().endswith(".xlsx") and not fname.startswith("~$") and fname != "アカウント情報.xlsx":
                        stem = os.path.splitext(fname)[0].lstrip("@").strip().lower()
                        # アカウント名そのもの (例: reghv6.xlsx, Reghv6.xlsx, @reghv6.xlsx)
                        if clean_id and stem == clean_id.lower():
                            return os.path.join(self.base_dir, fname)
                        if raw_clean and stem == raw_clean.lower():
                            return os.path.join(self.base_dir, fname)
                        # 接頭辞付き (例: アカウント名_reghv6.xlsx, アカウント_reghv6.xlsx, target_reghv6.xlsx)
                        for prefix in ["アカウント名_", "アカウント_", "target_", "targets_"]:
                            if clean_id and stem == f"{prefix}{clean_id.lower()}":
                                return os.path.join(self.base_dir, fname)
            except Exception:
                pass

        # 2. 定番パス候補
        candidates = []
        if clean_id:
            candidates.extend([
                os.path.join(self.base_dir, f"{clean_id}.xlsx"),
                os.path.join(self.base_dir, f"@{clean_id}.xlsx"),
                os.path.join(self.base_dir, f"アカウント名_{clean_id}.xlsx"),
                os.path.join(self.base_dir, f"アカウント_{clean_id}.xlsx"),
                os.path.join(self.base_dir, f"{clean_id}_targets.xlsx"),
            ])
        if raw_clean and raw_clean != clean_id:
            candidates.append(os.path.join(self.base_dir, f"{raw_clean}.xlsx"))
            candidates.append(os.path.join(self.base_dir, f"@{raw_clean}.xlsx"))

        # アカウント固有ファイルが存在すればそれを即時返却
        for p in candidates:
            if p and os.path.exists(p):
                return p

        # もしアカウント専用Excel ({clean_id}.xlsx) が存在せず、テンプレート (アカウント名.xlsx等) が存在する場合は自動複製して返却
        if clean_id and clean_id != "アカウント名":
            target_account_file = os.path.join(self.base_dir, f"{clean_id}.xlsx")
            template_candidates = [
                os.path.join(self.base_dir, "アカウント名.xlsx"),
                self.target_list_path,
                os.path.join(self.base_dir, "アカウント名情報.xlsx"),
                os.path.join(self.base_dir, "ターゲット.xlsx"),
                os.path.join(self.base_dir, "ターゲットリスト.xlsx")
            ]
            for tmpl in template_candidates:
                if tmpl and os.path.exists(tmpl):
                    try:
                        import shutil
                        shutil.copy2(tmpl, target_account_file)
                        return target_account_file
                    except Exception:
                        pass

        candidates.extend([
            self.target_list_path,
            os.path.join(self.base_dir, "アカウント名.xlsx"),
            os.path.join(self.base_dir, "アカウント名情報.xlsx"),
            os.path.join(self.base_dir, "ターゲット.xlsx"),
            os.path.join(self.base_dir, "ターゲットリスト.xlsx"),
            os.path.join(self.base_dir, "targets.xlsx"),
        ])
        for p in candidates:
            if p and os.path.exists(p):
                return p
        return self.target_list_path

    def _safe_load_workbook(self, file_path: str, read_only: bool = True):
        """
        Excelファイルが別プロセスで開かれていても安全に読み取る機構
        PermissionError 発生時は一時フォルダに複製してロード
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"ファイルが見つかりません: {file_path}")

        for attempt in range(3):
            try:
                return openpyxl.load_workbook(file_path, data_only=True, read_only=read_only)
            except PermissionError:
                time.sleep(0.5)
                continue
            except Exception:
                break

        # ロック中のためテンポラリにコピーして読み取り
        temp_dir = tempfile.gettempdir()
        temp_file = os.path.join(temp_dir, f"temp_{os.path.basename(file_path)}")
        shutil.copy2(file_path, temp_file)
        return openpyxl.load_workbook(temp_file, data_only=True, read_only=read_only)

    def load_accounts(self) -> list[dict]:
        """「アカウント情報.xlsx」のシートからアカウント設定を読込"""
        wb = self._safe_load_workbook(self.account_config_path, read_only=True)
        # シート名探索（「X04_アカウント設定」または最初のシート）
        target_sheet_name = None
        for name in wb.sheetnames:
            if "アカウント設定" in name or "X04" in name:
                target_sheet_name = name
                break
        sheet = wb[target_sheet_name] if target_sheet_name else wb.active

        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return []

        headers = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(rows[0])]
        accounts = []

        ignore_ids = ["モード", "モード名", "モード名称", "番号", "1", "2", "3", "4", "5", "※備考", "※使い方", "※複数指定の例"]

        for row in rows[1:]:
            if not any(row):
                continue
            data = {}
            for h, val in zip(headers, row):
                data[h] = val

            raw_id = ""
            for k, val in data.items():
                if not k or val is None or str(val).strip() == "":
                    continue
                k_clean = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
                if any(m in k_clean for m in ["アカウントid", "アカウント名", "ユーザー名", "アカウント", "id"]):
                    raw_id = str(val).strip()
                    break

            if not raw_id:
                continue

            # 下部に追加されたモード説明行・注記行をアカウントとして誤認識しないよう確実にスキップ
            raw_id_clean = raw_id.replace(" ", "").replace("　", "").lower()
            if raw_id_clean in ignore_ids:
                continue
            if raw_id.startswith("【") or raw_id.startswith("※") or raw_id.startswith("(") or raw_id.startswith("（"):
                continue
            if any(term in raw_id for term in ["モード設定", "モード一覧", "設定説明", "入力例", "対象取得モード"]):
                continue

            accounts.append(data)

        wb.close()
        return accounts

    def load_max_concurrent(self) -> int:
        """アカウント情報.xlsx の「窓数」シート (または互換シート) から指定窓数 (1~5) を取得"""
        try:
            wb = self._safe_load_workbook(self.account_config_path, read_only=True)
            for sname in ["窓数", "窓数設定", "モード", "モード設定", "実行モード"]:
                if sname in wb.sheetnames:
                    sheet = wb[sname]
                    rows = list(sheet.iter_rows(values_only=True))
                    if rows:
                        headers = [str(c or "").strip().lower() for c in rows[0]]
                        win_col = None
                        for i, h in enumerate(headers):
                            if any(k in h for k in ["窓数", "窓", "最大同時", "同時", "並列", "concurrent", "slot"]):
                                win_col = i
                                break
                        if win_col is not None:
                            for r in rows[1:]:
                                if r and len(r) > win_col and r[win_col] is not None:
                                    try:
                                        val = int(float(str(r[win_col]).strip()))
                                        if 1 <= val <= 5:
                                            wb.close()
                                            return val
                                    except Exception:
                                        pass
                        # 「窓数」シートでヘッダー名が自由入力の場合でも先頭列の1〜5数値をフォールバック取得
                        if sname in ["窓数", "窓数設定"]:
                            for r in rows:
                                if r and len(r) > 0 and r[0] is not None:
                                    try:
                                        val = int(float(str(r[0]).strip()))
                                        if 1 <= val <= 5:
                                            wb.close()
                                            return val
                                    except Exception:
                                        pass
            for sname in wb.sheetnames:
                sheet = wb[sname]
                rows = list(sheet.iter_rows(values_only=True))
                if rows:
                    headers = [str(c or "").strip().lower() for c in rows[0]]
                    for i, h in enumerate(headers):
                        if any(k in h for k in ["窓数", "最大同時ブラウザ数", "同時ブラウザ数", "最大同時", "同時実行数", "max_concurrent"]):
                            for r in rows[1:]:
                                if r and len(r) > i and r[i] is not None:
                                    try:
                                        val = int(float(str(r[i]).strip()))
                                        if 1 <= val <= 5:
                                            wb.close()
                                            return val
                                    except Exception:
                                        pass
            wb.close()
        except Exception:
            pass
        return 1

    def load_account_settings(self, account_id: str, target_path_override: str = None, fallback_acc: dict = None) -> dict:
        """
        アカウント名.xlsx (例: wqwq1320.xlsx) の「動作設定」シートから
        全体設定および各アクション（リプライ、引用RT、いいね、DM、フォロー、AI生成）ごとの
        モード(,区切り複数可)・実行間隔最短/最長(秒)・連続処理件数最小/最大数を読込。
        """
        target_path = target_path_override or self.get_target_path_for_account(account_id)
        fallback = fallback_acc or {}
        
        res = {
            "target_mode": "",
            "target_min_sec": 60.0,
            "target_max_sec": 150.0,
            "scroll_limit": 15,
            "target_count": 15,
            "reply_url_loop_limit": 0,
            "reply_parent_hop_limit": 15,
            "ai_engine": "gemini",
            "ai_model": "",
            "ai_fallback_models": "",
            "ai_dedicated_window": True,
            "ai_api_key": "",
            "ai_prompt": "",
            "block_media": True,
            "dm_passcode": "",
            "active_hours": "",
            "actions": {
                "dm": {"enabled": True, "mode": "検索最新,URLリスト（リプライ）,URLリスト（フォロー）", "min_interval": 300.0, "max_interval": 900.0, "min_batch": 3, "max_batch": 8, "ai_gen": False, "like_enabled": False, "follow_enabled": False},
                "like": {"enabled": False, "mode": "検索最新,HOMEおすすめ", "min_interval": 30.0, "max_interval": 90.0, "min_batch": 5, "max_batch": 10, "ai_gen": False, "like_enabled": False, "follow_enabled": False},
                "follow": {"enabled": False, "mode": "検索最新,URLリスト（フォロー）", "min_interval": 60.0, "max_interval": 180.0, "min_batch": 3, "max_batch": 7, "ai_gen": False, "like_enabled": False, "follow_enabled": False},
                "reply": {"enabled": False, "mode": "検索最新,URLリスト（リプライ）", "min_interval": 180.0, "max_interval": 600.0, "min_batch": 2, "max_batch": 5, "ai_gen": False, "like_enabled": False, "follow_enabled": False},
                "quote": {"enabled": False, "mode": "話題の検索,トレンド", "min_interval": 300.0, "max_interval": 900.0, "min_batch": 1, "max_batch": 3, "ai_gen": False, "like_enabled": False, "follow_enabled": False},
                "reply_ai": {"enabled": False, "mode": "検索最新,HOMEおすすめ,トレンド", "min_interval": 180.0, "max_interval": 600.0, "min_batch": 2, "max_batch": 5, "ai_gen": True, "like_enabled": False, "follow_enabled": False},
                "quote_ai": {"enabled": False, "mode": "ニュース,トレンド", "min_interval": 300.0, "max_interval": 900.0, "min_batch": 1, "max_batch": 3, "ai_gen": True, "like_enabled": False, "follow_enabled": False},
            },
            "auto_delete": _default_auto_delete_settings(),
        }

        # 1. fallback_acc から初期ロード (互換性)
        if fallback:
            for k, v in fallback.items():
                if not k or v is None or str(v).strip() == "": continue
                kc = str(k).replace(" ", "").replace("　", "").replace("_", "").lower()
                if any(m in kc for m in ["dmパスコード", "パスコード", "暗号化パスコード", "暗号化キー", "暗号化dm", "dmpin", "dmpasscode", "passcode"]):
                    raw_p = str(v).strip()
                    res["dm_passcode"] = raw_p[:-2] if raw_p.endswith(".0") and raw_p[:-2].isdigit() else raw_p
                    continue
                if "取得モード" in kc or "対象取得モード" in kc or "モード" in kc:
                    res["target_mode"] = str(v).strip()
                elif "スクロール" in kc:
                    try: res["scroll_limit"] = int(float(str(v).strip()))
                    except: pass
                elif any(m in kc for m in ["ターゲット件数", "ターゲット数", "読み込み件数", "読込件数"]):
                    try: res["target_count"] = int(float(str(v).strip()))
                    except: pass
                elif "ホップ上限" in kc or "parenthop" in kc:
                    try: res["reply_parent_hop_limit"] = max(0, int(float(str(v).strip())))
                    except: pass
                elif "url巡回上限" in kc or "巡回上限" in kc or "urllooplimit" in kc:
                    try: res["reply_url_loop_limit"] = max(0, int(float(str(v).strip())))
                    except: pass
                elif "aiエンジン" in kc:
                    res["ai_engine"] = str(v).strip()
                elif "aiモデル" in kc or kc in ["モデル", "モデル名", "aimodel", "model"]:
                    res["ai_model"] = str(v).strip()
                elif any(m in kc for m in ["aiフォールバック", "フォールバック", "予備ai", "予備モデル", "切替ai", "aifallback", "fallback"]):
                    res["ai_fallback_models"] = str(v).strip()
                elif "メディア" in kc:
                    res["block_media"] = str(v).strip().lower() not in ["0", "false", "off", "無効"]
                elif any(m in kc for m in ["稼働時間帯", "稼働時間", "営業時間", "実行時間帯", "activehours", "activehour"]):
                    res["active_hours"] = str(v).strip()
                s_on = str(v).strip().lower() in ["1", "1.0", "true", "yes", "on", "実行", "有効", "○", "〇", "o"]
                if "命令実行dm" in kc or kc == "dm": res["actions"]["dm"]["enabled"] = s_on
                elif "命令実行いいね" in kc or kc == "いいね": res["actions"]["like"]["enabled"] = s_on
                elif "命令実行フォロー" in kc or kc == "フォロー": res["actions"]["follow"]["enabled"] = s_on
                elif "命令実行リプライ" in kc: res["actions"]["reply"]["enabled"] = s_on
                elif "命令実行引用" in kc: res["actions"]["quote"]["enabled"] = s_on
                elif "リプライai生成" in kc: res["actions"]["reply_ai"]["enabled"] = s_on
                elif "引用リツイートai生成" in kc: res["actions"]["quote_ai"]["enabled"] = s_on

        # 2. アカウント専用Excel (例: wqwq1320.xlsx) の「動作設定」シートから優先読込
        found_auto_delete_section = False
        if target_path and os.path.exists(target_path):
            try:
                wb = self._safe_load_workbook(target_path, read_only=True)
                op_sheet = None
                for sname in ["動作設定", "設定", "アクション設定", "基本設定"]:
                    if sname in wb.sheetnames:
                        op_sheet = wb[sname]
                        break
                if op_sheet:
                    rows = list(op_sheet.iter_rows(values_only=True))
                    col_map = None
                    auto_del_col_map = None
                    for r in rows:
                        if not r or not any(r): continue
                        col0 = str(r[0] or "").strip()
                        col0_clean = col0.replace(" ", "").replace("　", "").replace("_", "").lower()

                        if "自動削除" in col0 or "自動解除" in col0:
                            found_auto_delete_section = True

                        # 注記・セクション見出し行はアクション誤認を防ぐためスキップ
                        if col0.startswith("※") or col0.startswith("【"):
                            continue

                        # 自動削除表のヘッダー行を検出して列インデックスを動的マッピング
                        if col0_clean in ["自動削除アクション", "自動削除", "削除アクション", "自動解除アクション", "autodelete", "autodeleteaction"]:
                            found_auto_delete_section = True
                            auto_del_col_map = {}
                            for idx, h_cell in enumerate(r):
                                h_clean = str(h_cell or "").replace(" ", "").replace("　", "").replace("_", "").lower()
                                if any(k in h_clean for k in ["件数", "上限", "perrun", "maxper"]):
                                    auto_del_col_map["max_per_run"] = idx
                                elif any(k in h_clean for k in ["単位", "unit"]):
                                    auto_del_col_map["unit"] = idx
                                elif any(k in h_clean for k in ["最小", "最短", "mindelay"]):
                                    auto_del_col_map["min_delay"] = idx
                                elif any(k in h_clean for k in ["最大", "最長", "maxdelay"]):
                                    auto_del_col_map["max_delay"] = idx
                                elif idx > 0 and any(k in h_clean for k in ["自動削除", "自動解除", "on/off", "enabled", "実行", "有効"]):
                                    auto_del_col_map["enabled"] = idx
                            continue

                        # アクション表のヘッダー行を検出して列インデックスを動的マッピング
                        if col0_clean in ["アクション名", "アクション", "action", "actionname"]:
                            col_map = {}
                            for idx, h_cell in enumerate(r):
                                h_clean = str(h_cell or "").replace(" ", "").replace("　", "").replace("_", "").lower()
                                if "モード" in h_clean or "mode" in h_clean:
                                    col_map["mode"] = idx
                                elif "命令実行" in h_clean or "実行(" in h_clean or h_clean == "実行" or "enabled" in h_clean:
                                    col_map["enabled"] = idx
                                elif "実行間隔最短" in h_clean or "間隔最短" in h_clean or "mininterval" in h_clean:
                                    col_map["min_interval"] = idx
                                elif "実行間隔最長" in h_clean or "間隔最長" in h_clean or "maxinterval" in h_clean:
                                    col_map["max_interval"] = idx
                                elif "連続処理件数最小" in h_clean or "最小数" in h_clean or "minbatch" in h_clean:
                                    col_map["min_batch"] = idx
                                elif "連続処理件数最大" in h_clean or "最大数" in h_clean or "maxbatch" in h_clean:
                                    col_map["max_batch"] = idx
                                elif "ai生成" in h_clean or "aigen" in h_clean:
                                    col_map["ai_gen"] = idx
                                elif "いいね" in h_clean or "like" in h_clean:
                                    col_map["like_enabled"] = idx
                                elif "フォロー" in h_clean or "follow" in h_clean:
                                    col_map["follow_enabled"] = idx
                            continue

                        is_auto_delete_row = any(m in col0_clean for m in [
                            "自動削除", "自動解除", "フォロー解除", "いいね解除", "リプライ削除", "引用リツイート削除", "引用rt削除"
                        ])

                        if not is_auto_delete_row and len(r) > 1 and r[1] is not None and str(r[1]).strip() != "":
                            val1 = str(r[1]).strip()
                            if any(m in col0_clean for m in ["dmパスコード", "パスコード", "暗号化パスコード", "暗号化キー", "dmpin", "dmpasscode", "passcode"]):
                                res["dm_passcode"] = val1[:-2] if val1.endswith(".0") and val1[:-2].isdigit() else val1
                            elif "対象取得モード" in col0_clean:
                                res["target_mode"] = val1
                            elif "ランダムインターバル最小" in col0_clean:
                                try: res["target_min_sec"] = float(val1)
                                except: pass
                            elif "ランダムインターバル最大" in col0_clean:
                                try: res["target_max_sec"] = float(val1)
                                except: pass
                            elif "取得スクロール回数上限" in col0_clean:
                                try: res["scroll_limit"] = int(float(val1))
                                except: pass
                            elif "ターゲット件数" in col0_clean:
                                try: res["target_count"] = int(float(val1))
                                except: pass
                            elif "ホップ上限" in col0_clean or "parenthop" in col0_clean:
                                try: res["reply_parent_hop_limit"] = max(0, int(float(val1)))
                                except: pass
                            elif "url巡回上限" in col0_clean or "巡回上限" in col0_clean or "urllooplimit" in col0_clean:
                                try: res["reply_url_loop_limit"] = max(0, int(float(val1)))
                                except: pass
                            elif "aiエンジン" in col0_clean:
                                res["ai_engine"] = val1
                            elif "aiモデル" in col0_clean or col0_clean in ["モデル", "モデル名", "aimodel", "model"]:
                                res["ai_model"] = val1
                            elif any(m in col0_clean for m in ["aiフォールバック", "フォールバック", "予備ai", "予備モデル", "切替ai", "aifallback", "fallback"]):
                                res["ai_fallback_models"] = val1
                            elif "ai専用ウィンドウ" in col0_clean:
                                res["ai_dedicated_window"] = val1.lower() not in ["0", "false", "off", "無効"]
                            elif "aiapiキー" in col0_clean or "接続url" in col0_clean:
                                res["ai_api_key"] = val1
                            elif "aiプロンプト" in col0_clean:
                                res["ai_prompt"] = val1
                            elif "メディア読込ブロック" in col0_clean:
                                res["block_media"] = val1.lower() not in ["0", "false", "off", "無効"]
                            elif any(m in col0_clean for m in ["稼働時間帯", "稼働時間", "営業時間", "実行時間帯", "activehours", "activehour"]):
                                res["active_hours"] = val1

                        def _parse_row_cell(idx, def_val):
                            if idx is not None and len(r) > idx and r[idx] is not None and str(r[idx]).strip() != "":
                                return str(r[idx]).strip()
                            return def_val

                        if is_auto_delete_row and len(r) >= 3:
                            found_auto_delete_section = True
                            del_key = None
                            if "フォロー" in col0_clean or "follow" in col0_clean:
                                del_key = "follow"
                            elif "いいね" in col0_clean or "like" in col0_clean:
                                del_key = "like"
                            elif "リプライ" in col0_clean or "reply" in col0_clean:
                                del_key = "reply"
                            elif "引用" in col0_clean or "quote" in col0_clean:
                                del_key = "quote"

                            if del_key:
                                d_cfg = res["auto_delete"][del_key]
                                if auto_del_col_map is not None:
                                    idx_en = auto_del_col_map.get("enabled", 2)
                                    idx_min = auto_del_col_map.get("min_delay", 3)
                                    idx_max = auto_del_col_map.get("max_delay", 4)
                                    idx_max_run = auto_del_col_map.get("max_per_run", 5)
                                    idx_unit = auto_del_col_map.get("unit", 6)
                                else:
                                    idx_en, idx_min, idx_max, idx_max_run, idx_unit = 2, 3, 4, 5, 6

                                flag_str = _parse_row_cell(idx_en, "0").lower()
                                d_cfg["enabled"] = flag_str in ["1", "1.0", "true", "yes", "on", "実行", "有効", "○", "〇", "o"]

                                raw_unit = _parse_row_cell(idx_unit, d_cfg["unit"])
                                norm_unit = normalize_time_unit(raw_unit, d_cfg["unit"])
                                d_cfg["unit"] = norm_unit

                                raw_min = _parse_row_cell(idx_min, str(d_cfg["min_delay"]))
                                raw_max = _parse_row_cell(idx_max, str(d_cfg["max_delay"]))
                                min_sec = parse_delay_to_seconds(raw_min, default_unit=norm_unit)
                                max_sec = parse_delay_to_seconds(raw_max, default_unit=norm_unit)

                                try:
                                    m_min = re.search(r'(\d+(?:\.\d+)?)', str(raw_min).translate(_FULLWIDTH_TRANS))
                                    min_num = float(m_min.group(1)) if m_min else float(d_cfg["min_delay"])
                                except Exception:
                                    min_num = float(d_cfg["min_delay"])
                                try:
                                    m_max = re.search(r'(\d+(?:\.\d+)?)', str(raw_max).translate(_FULLWIDTH_TRANS))
                                    max_num = float(m_max.group(1)) if m_max else float(d_cfg["max_delay"])
                                except Exception:
                                    max_num = float(d_cfg["max_delay"])

                                if min_sec > max_sec:
                                    min_sec, max_sec = max_sec, min_sec
                                    min_num, max_num = max_num, min_num

                                d_cfg["min_delay"] = min_num
                                d_cfg["max_delay"] = max_num
                                d_cfg["min_seconds"] = min_sec
                                d_cfg["max_seconds"] = max_sec

                                raw_mpr = _parse_row_cell(idx_max_run, str(d_cfg["max_per_run"]))
                                try:
                                    d_cfg["max_per_run"] = max(0, int(float(str(raw_mpr).translate(_FULLWIDTH_TRANS))))
                                except Exception:
                                    pass
                            continue

                        is_passcode_row = any(m in col0_clean for m in ["パスコード", "passcode", "暗号化", "pin"])
                        act_key = None
                        if not is_passcode_row and not is_auto_delete_row:
                            if "dm" in col0_clean: act_key = "dm"
                            elif "いいね" in col0_clean: act_key = "like"
                            elif "フォロー" in col0_clean: act_key = "follow"
                            elif "リプライai" in col0_clean: act_key = "reply_ai"
                            elif "引用" in col0_clean and "ai" in col0_clean: act_key = "quote_ai"
                            elif "リプライ" in col0_clean: act_key = "reply"
                            elif "引用" in col0_clean: act_key = "quote"

                        if act_key and len(r) >= 2:
                            if col_map is not None:
                                idx_mode = col_map.get("mode")
                                idx_en = col_map.get("enabled", 2 if idx_mode == 1 else 1)
                                idx_min_i = col_map.get("min_interval", idx_en + 1)
                                idx_max_i = col_map.get("max_interval", idx_en + 2)
                                idx_min_b = col_map.get("min_batch", idx_en + 3)
                                idx_max_b = col_map.get("max_batch", idx_en + 4)
                                idx_ai = col_map.get("ai_gen", idx_en + 5)
                                idx_like = col_map.get("like_enabled")
                                idx_follow = col_map.get("follow_enabled")
                            else:
                                # ヘッダー未検出時：新11列フォーマット / 旧9列・旧8列フォーマットを自動判別
                                has_mode_col = False
                                if len(r) >= 8:
                                    has_mode_col = True
                                elif len(r) >= 3:
                                    c1_str = str(r[1] or "").strip()
                                    if any(mk in c1_str for mk in ["検索", "HOME", "URLリスト", "ニュース", "トレンド", ","]):
                                        has_mode_col = True
                                if has_mode_col:
                                    idx_mode, idx_en, idx_min_i, idx_max_i, idx_min_b, idx_max_b, idx_ai = 1, 2, 3, 4, 5, 6, 7
                                    idx_like = 8 if len(r) >= 10 else None
                                    idx_follow = 9 if len(r) >= 10 else None
                                else:
                                    idx_mode, idx_en, idx_min_i, idx_max_i, idx_min_b, idx_max_b, idx_ai = None, 1, 2, 3, 4, 5, 6
                                    idx_like, idx_follow = None, None

                            if idx_mode is not None:
                                m_val = _parse_row_cell(idx_mode, "")
                                if m_val:
                                    res["actions"][act_key]["mode"] = m_val
                            elif res["target_mode"]:
                                res["actions"][act_key]["mode"] = res["target_mode"]

                            flag_str = _parse_row_cell(idx_en, "1" if res["actions"][act_key]["enabled"] else "0").lower()
                            res["actions"][act_key]["enabled"] = flag_str in ["1", "1.0", "true", "yes", "on", "実行", "有効", "○", "〇", "o"]
                            
                            try: res["actions"][act_key]["min_interval"] = float(_parse_row_cell(idx_min_i, res["actions"][act_key]["min_interval"]))
                            except: pass
                            try: res["actions"][act_key]["max_interval"] = float(_parse_row_cell(idx_max_i, res["actions"][act_key]["max_interval"]))
                            except: pass
                            try: res["actions"][act_key]["min_batch"] = int(float(_parse_row_cell(idx_min_b, res["actions"][act_key]["min_batch"])))
                            except: pass
                            try: res["actions"][act_key]["max_batch"] = int(float(_parse_row_cell(idx_max_b, res["actions"][act_key]["max_batch"])))
                            except: pass
                            ai_str = _parse_row_cell(idx_ai, "1" if res["actions"][act_key]["ai_gen"] else "0").lower()
                            res["actions"][act_key]["ai_gen"] = ai_str in ["1", "1.0", "true", "yes", "on", "実行", "有効", "○", "〇", "o"]
                            like_str = _parse_row_cell(idx_like, "1" if res["actions"][act_key].get("like_enabled") else "0").lower()
                            res["actions"][act_key]["like_enabled"] = like_str in ["1", "1.0", "true", "yes", "on", "実行", "有効", "○", "〇", "o"]
                            follow_str = _parse_row_cell(idx_follow, "1" if res["actions"][act_key].get("follow_enabled") else "0").lower()
                            res["actions"][act_key]["follow_enabled"] = follow_str in ["1", "1.0", "true", "yes", "on", "実行", "有効", "○", "〇", "o"]
                wb.close()
            except Exception:
                pass

            if not found_auto_delete_section:
                try:
                    self.ensure_auto_delete_section(target_path)
                except Exception:
                    pass

        return res

    def ensure_auto_delete_section(self, target_path: str) -> bool:
        """
        既存の「<アカウント名>.xlsx」の「動作設定」シートに
        【実行後の自動削除・解除設定】セクションが無い場合のみ、初期値(すべてOFF=0)で自動追記する。
        Excel編集中(PermissionError)の場合は安全にスキップする。
        """
        if not target_path or not os.path.exists(target_path):
            return False
        try:
            from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

            wb = openpyxl.load_workbook(target_path)
            op_sheet = None
            for sname in ["動作設定", "設定", "アクション設定", "基本設定"]:
                if sname in wb.sheetnames:
                    op_sheet = wb[sname]
                    break
            if op_sheet is None:
                wb.close()
                return False

            for r_idx in range(1, op_sheet.max_row + 1):
                c0 = str(op_sheet.cell(row=r_idx, column=1).value or "").strip()
                if "自動削除" in c0 or "自動解除" in c0 or "フォロー解除" in c0:
                    wb.close()
                    return False

            last_row = 0
            for r_idx in range(1, op_sheet.max_row + 1):
                if any(op_sheet.cell(row=r_idx, column=c_idx).value not in (None, "") for c_idx in range(1, 12)):
                    last_row = r_idx

            spacer_row = last_row + 1
            sec_row = spacer_row + 1
            hdr_row = sec_row + 1
            data_start = hdr_row + 1

            thin = Side(style="thin", color="94A3B8")
            border_std = Border(top=thin, left=thin, bottom=thin, right=thin)
            rose_side = Side(style="thin", color="9F1239")
            border_sec = Border(top=rose_side, left=rose_side, bottom=rose_side, right=rose_side)
            note_side = Side(style="thin", color="FDA4AF")
            border_note = Border(top=note_side, left=note_side, bottom=note_side, right=note_side)

            op_sheet.row_dimensions[spacer_row].height = 12
            op_sheet.row_dimensions[sec_row].height = 26
            op_sheet.cell(row=sec_row, column=1, value="【実行後の自動削除・解除設定（指定ランダム時間経過後に自動実行）】")
            for c_idx in range(1, 12):
                cell = op_sheet.cell(row=sec_row, column=c_idx)
                cell.fill = PatternFill(start_color="9F1239", end_color="9F1239", fill_type="solid")
                cell.font = Font(name="Meiryo UI", size=11, bold=True, color="FFFFFF")
                cell.alignment = Alignment(horizontal="left", vertical="center")
                cell.border = border_sec

            headers = [
                ("自動削除アクション", "334155", "FFF1F2", "left", True),
                ("対象説明", "475569", "F8FAFC", "left", False),
                ("自動削除(1=ON/0=OFF)", "BE123C", "FFE4E6", "right", True),
                ("削除までの最小時間", "0F766E", "CCFBF1", "right", True),
                ("削除までの最大時間", "0F766E", "CCFBF1", "right", True),
                ("1回の最大削除件数(0=全件)", "0369A1", "E0F2FE", "right", True),
                ("時間単位(分/秒/時間)", "B45309", "FEF3C7", "right", True),
                ("備考", "475569", "F8FAFC", "left", False),
                ("", "475569", "F8FAFC", "left", False),
                ("", "475569", "F8FAFC", "left", False),
                ("安全仕様・補足", "475569", "F8FAFC", "left", False),
            ]
            op_sheet.row_dimensions[hdr_row].height = 28
            for c_idx, (h_lbl, h_fill, _, h_align, _) in enumerate(headers, start=1):
                cell = op_sheet.cell(row=hdr_row, column=c_idx, value=h_lbl)
                cell.fill = PatternFill(start_color=h_fill, end_color=h_fill, fill_type="solid")
                cell.font = Font(name="Meiryo UI", size=10, bold=True, color="FFFFFF")
                cell.alignment = Alignment(horizontal=h_align, vertical="center", wrap_text=True)
                cell.border = border_std

            matrix = [
                ["自動削除 フォロー解除", "ツールでフォローした相手を指定ランダム時間後に自動フォロー解除", 0, 60, 180, 10, "分", "ツール実行分のみ対象", "", "", "既存フォローは解除せずツール履歴(follow)のみ解除（1=ON / 0=OFF）"],
                ["自動削除 いいね解除", "ツールでいいねした投稿を指定ランダム時間後に自動いいね解除", 0, 60, 180, 10, "分", "ツール実行分のみ対象", "", "", "連動いいね・単体いいね両方を対象に指定時間後に取り消し"],
                ["自動削除 リプライ削除", "ツールが送信した自分のリプライを指定ランダム時間後に自動削除", 0, 120, 360, 10, "分", "自分の送信リプライのみ", "", "", "AI生成リプライ・定型リプライ両方の自ポストのみを削除（重複防止DBは維持）"],
                ["自動削除 引用リツイート削除", "ツールが投稿した自分の引用RTを指定ランダム時間後に自動削除", 0, 120, 360, 10, "分", "自分の投稿引用RTのみ", "", "", "AI生成引用RT・定型引用RT両方の自ポストのみを削除（重複防止DBは維持）"],
            ]
            for r_off, row_vals in enumerate(matrix):
                r_num = data_start + r_off
                op_sheet.row_dimensions[r_num].height = 28
                for c_idx, val in enumerate(row_vals, start=1):
                    _, _, c_fill, c_align, c_bold = headers[c_idx - 1]
                    cell = op_sheet.cell(row=r_num, column=c_idx, value=val)
                    cell.fill = PatternFill(start_color=c_fill, end_color=c_fill, fill_type="solid")
                    cell.font = Font(name="Meiryo UI", size=10, bold=c_bold, color="0F172A")
                    cell.alignment = Alignment(horizontal=c_align, vertical="center", wrap_text=(c_idx in (2, 11)))
                    cell.border = border_std

            note_spacer = data_start + len(matrix)
            note_row = note_spacer + 1
            op_sheet.row_dimensions[note_spacer].height = 10
            op_sheet.row_dimensions[note_row].height = 26
            op_sheet.cell(row=note_row, column=1, value="※時間単位の指定方法")
            op_sheet.cell(row=note_row, column=2, value="G列に「分」「秒」「時間」を指定可（例: 30秒〜60秒、60分〜180分、2時間〜6時間 / 最小〜最大の間でランダム決定）")
            for c_idx in range(1, 12):
                cell = op_sheet.cell(row=note_row, column=c_idx)
                cell.fill = PatternFill(start_color="FFF1F2", end_color="FFF1F2", fill_type="solid")
                cell.font = Font(name="Meiryo UI", size=10, bold=(c_idx == 1), color="881337")
                cell.alignment = Alignment(horizontal="left", vertical="center")
                cell.border = border_note

            wb.save(target_path)
            wb.close()
            return True
        except PermissionError:
            return False
        except Exception:
            return False

    def _extract_sheet_messages(self, sheet) -> list[str]:
        """シートから登録文章（全文）を確実に抽出。列順（タイトル先頭等）や空行に柔軟対応"""
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return []
        
        # 1行目がヘッダーか判定
        header_row = [str(c or "").strip().lower() for c in rows[0]]
        content_keywords = ["dm内容", "本文", "メッセージ", "内容", "文章", "テキスト", "content", "message", "text", "送信文"]
        title_keywords = ["タイトル", "title", "件名", "見出し", "概要", "区分", "カテゴリ", "category", "id", "no", "番号", "項目"]
        
        target_col = None
        # ヘッダーから本文列を特定
        for col_idx, h in enumerate(header_row):
            h_clean = h.replace(" ", "").replace("　", "").replace("_", "")
            if any(k in h_clean for k in content_keywords) and not any(tk in h_clean for tk in title_keywords):
                target_col = col_idx
                break
        
        data_rows = rows[1:] if any(any(k in h for k in content_keywords + title_keywords) for h in header_row) else rows
        
        if target_col is None:
            # ヘッダーが見当たらない場合、各列の平均文字列長を比較して本文列（一番文章が長い列）を判定
            best_col = 0
            max_len = -1
            if data_rows:
                num_cols = max((len(r) for r in data_rows if r), default=1)
                for c in range(num_cols):
                    col_texts = [str(r[c]).strip() for r in data_rows if r and len(r) > c and r[c] is not None]
                    avg_len = sum(len(t) for t in col_texts) / (len(col_texts) or 1)
                    if avg_len > max_len:
                        max_len = avg_len
                        best_col = c
            target_col = best_col

        templates = []
        for row in data_rows:
            if not row or len(row) <= target_col:
                continue
            val = row[target_col]
            if val is not None:
                s = str(val).strip()
                # ヘッダー文字列そのものの混入を除外
                if s and s.lower() not in content_keywords and s.lower() not in title_keywords:
                    templates.append(s)
        return templates

    def load_dm_templates(self, target_path_override: str = None) -> list[str]:
        """「アカウント名.xlsx」の「DM内容」シートから送信テキスト一覧を読込（文章全体の完全抽出対応）"""
        templates = []
        target_file = target_path_override or self.target_list_path
        paths = [p for p in [target_file, self.target_list_path, self.account_config_path] if p]
        seen_paths = set()
        for path in paths:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            if os.path.exists(path):
                wb = self._safe_load_workbook(path, read_only=True)
                for sname in ["DM内容", "dm内容", "DM", "dm", "メッセージ内容", "DM文面"]:
                    if sname in wb.sheetnames:
                        sheet = wb[sname]
                        extracted = self._extract_sheet_messages(sheet)
                        if extracted:
                            templates.extend(extracted)
                            break
                wb.close()
                if templates:
                    break
        return templates

    def load_reply_templates(self, target_path_override: str = None) -> list[str]:
        """「アカウント名.xlsx」の「リプライ内容」シートから送信テキスト一覧を読込"""
        templates = []
        target_file = target_path_override or self.target_list_path
        paths = [p for p in [target_file, self.target_list_path, self.account_config_path] if p]
        seen_paths = set()
        for path in paths:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            if os.path.exists(path):
                wb = self._safe_load_workbook(path, read_only=True)
                for sname in ["リプライ内容", "リプライ文面", "リプライ"]:
                    if sname in wb.sheetnames:
                        sheet = wb[sname]
                        extracted = self._extract_sheet_messages(sheet)
                        if extracted:
                            templates.extend(extracted)
                            break
                wb.close()
                if templates:
                    break
        return templates

    def load_quote_templates(self, target_path_override: str = None) -> list[str]:
        """「アカウント名.xlsx」の「引用リツイート内容」シートから送信テキスト一覧を読込"""
        templates = []
        target_file = target_path_override or self.target_list_path
        paths = [p for p in [target_file, self.target_list_path, self.account_config_path] if p]
        seen_paths = set()
        for path in paths:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            if os.path.exists(path):
                wb = self._safe_load_workbook(path, read_only=True)
                for sname in ["引用リツイート内容", "引用RT内容", "引用リツイート"]:
                    if sname in wb.sheetnames:
                        sheet = wb[sname]
                        extracted = self._extract_sheet_messages(sheet)
                        if extracted:
                            templates.extend(extracted)
                            break
                wb.close()
                if templates:
                    break
        return templates

    def load_search_keywords(self, target_path_override: str = None) -> list[dict]:
        """「アカウント名.xlsx」の「検索キーワード」シートからキーワード一覧を読込 (最新 / おすすめ)"""
        keywords = []
        target_file = target_path_override or self.target_list_path
        paths = [p for p in [target_file, self.target_list_path, self.account_config_path] if p]
        seen_paths = set()
        for path in paths:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            if os.path.exists(path):
                wb = self._safe_load_workbook(path, read_only=True)
                if "検索キーワード" in wb.sheetnames:
                    sheet = wb["検索キーワード"]
                    for row in sheet.iter_rows(min_row=2, values_only=True):
                        if row and row[0]:
                            kw = str(row[0]).strip()
                            search_type = str(row[1]).strip() if len(row) > 1 and row[1] else "最新"
                            keywords.append({"keyword": kw, "search_type": search_type})
                wb.close()
                if keywords:
                    break
        return keywords

    def load_reply_urls(self, target_path_override: str = None) -> list[str]:
        """「アカウント名.xlsx」の「URLリスト_リプライ」シートからリプライURL一覧を読込"""
        urls = []
        target_file = target_path_override or self.target_list_path
        paths = [p for p in [target_file, self.target_list_path, self.account_config_path] if p]
        seen_paths = set()
        for path in paths:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            if os.path.exists(path):
                wb = self._safe_load_workbook(path, read_only=True)
                for sname in ["URLリスト_リプライ", "リスト_リプライ"]:
                    if sname in wb.sheetnames:
                        sheet = wb[sname]
                        for row in sheet.iter_rows(min_row=2, values_only=True):
                            if row and row[0]:
                                urls.append(str(row[0]).strip())
                        break
                wb.close()
                if urls:
                    break
        return urls

    def load_follow_urls(self, target_path_override: str = None) -> list[str]:
        """「アカウント名.xlsx」の「URLリスト_フォロー」シートからフォローURL一覧を読込"""
        urls = []
        target_file = target_path_override or self.target_list_path
        paths = [p for p in [target_file, self.target_list_path, self.account_config_path] if p]
        seen_paths = set()
        for path in paths:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            if os.path.exists(path):
                wb = self._safe_load_workbook(path, read_only=True)
                if "URLリスト_フォロー" in wb.sheetnames:
                    sheet = wb["URLリスト_フォロー"]
                    for row in sheet.iter_rows(min_row=2, values_only=True):
                        if row and row[0]:
                            urls.append(str(row[0]).strip())
                wb.close()
                if urls:
                    break
        return urls

    def load_ai_prompt_config(self, target_path_override: str = None, legacy_extra_prompt: str = "") -> dict:
        """
        アカウント名.xlsx (例: wqwq1320.xlsx) の「AIプロンプト」シートから
        キャラ設定 / 口調・文体ルール / 取得URL・投稿情報 / プロンプト / お手本サンプル / NGルール / 出力ルール を読込。
        旧構成（プロンプト設定 / プロンプト枠組み / AIプロンプト追加指示）も自動フォールバック対応。
        """
        default_cfg = {
            "character": "あなたはX(Twitter)の親しみやすく礼儀正しいユーザーです。",
            "tone_style": "",
            "post_format": "",
            "instruction": "取得する文章はXのポストです。相手に寄り添って文章を生成して 140文字以内で",
            "examples": "",
            "ng_rules": "",
            "output_rule": (
                "※出力は送信するメッセージ本文のみを1〜2行（140文字以内）で返してください。"
                "挨拶や前置き（例: 「了解しました」「返信案:」）や引用符などの余計な注釈は一切含めないでください。"
            ),
            "has_sheet": False,
        }
        target_file = target_path_override or self.target_list_path
        paths = [p for p in [target_file, self.target_list_path, self.account_config_path] if p]
        seen_paths = set()

        for path in paths:
            if path in seen_paths or not os.path.exists(path):
                continue
            seen_paths.add(path)
            try:
                wb = self._safe_load_workbook(path, read_only=True)
                ai_sheet = None
                for sname in wb.sheetnames:
                    sc = sname.replace(" ", "").replace("　", "").lower()
                    if sc in ["aiプロンプト", "aiプロンプト設定", "プロンプトai"]:
                        ai_sheet = wb[sname]
                        break
                if ai_sheet is not None:
                    res = {
                        "character": "",
                        "tone_style": "",
                        "post_format": "",
                        "instruction": "",
                        "examples": "",
                        "ng_rules": "",
                        "output_rule": "",
                        "has_sheet": True,
                    }
                    rows = list(ai_sheet.iter_rows(values_only=True))
                    for row in rows:
                        if not row:
                            continue
                        k_raw = str(row[0] or "").strip()
                        if not k_raw:
                            continue
                        k_clean = k_raw.replace(" ", "").replace("　", "").replace("_", "").lower()
                        if k_clean in ["項目", "設定項目", "キー", "key", "item"]:
                            continue
                        val = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
                        if any(m in k_clean for m in ["キャラ", "役割", "立場", "前提", "冒頭", "persona", "character"]):
                            res["character"] = val
                        elif any(m in k_clean for m in ["口調", "文体", "トーン", "語り口", "一人称", "絵文字", "tonestyle", "tone"]):
                            res["tone_style"] = val
                        elif any(m in k_clean for m in ["お手本", "サンプル", "返信例", "例文", "fewshot", "example", "sample"]):
                            res["examples"] = val
                        elif any(m in k_clean for m in ["ngルール", "ng表現", "禁止ワード", "禁止表現", "避ける", "ngrule", "ng"]):
                            res["ng_rules"] = val
                        elif any(m in k_clean for m in ["取得url", "投稿情報", "ポスト情報", "投稿フォーマット", "urlフォーマット", "postformat"]):
                            res["post_format"] = val
                        elif any(m in k_clean for m in ["出力ルール", "出力形式", "禁止", "末尾", "outputrule", "output"]):
                            res["output_rule"] = val
                        elif any(m in k_clean for m in ["プロンプト", "指示", "instruction", "prompt"]):
                            res["instruction"] = val
                    wb.close()
                    return res

                # 旧シート（プロンプト設定）互換フォールバック
                if "プロンプト設定" in wb.sheetnames:
                    sheet = wb["プロンプト設定"]
                    legacy_instr = ""
                    for row in sheet.iter_rows(min_row=2, values_only=True):
                        if row and row[0]:
                            legacy_instr = str(row[0]).strip()
                            break
                    wb.close()
                    res = dict(default_cfg)
                    extra = (legacy_extra_prompt or "").strip()
                    if extra and legacy_instr:
                        res["instruction"] = f"{legacy_instr}\n{extra}"
                    elif extra:
                        res["instruction"] = extra
                    elif legacy_instr:
                        res["instruction"] = legacy_instr
                    return res
                wb.close()
            except Exception:
                pass

        res = dict(default_cfg)
        if legacy_extra_prompt and str(legacy_extra_prompt).strip():
            res["instruction"] = str(legacy_extra_prompt).strip()
        return res

    def load_prompt_settings(self, target_path_override: str = None) -> str:
        """「アカウント名.xlsx」の「AIプロンプト」（または旧「プロンプト設定」）シートから指示プロンプトを読込"""
        cfg = self.load_ai_prompt_config(target_path_override=target_path_override)
        if cfg.get("has_sheet"):
            return str(cfg.get("instruction") or "").strip()
        return str(cfg.get("instruction") or "").strip() or "取得する文章はXのポストです。相手に寄り添って文章を生成して 140文字以内で"

    def _get_target_sheet(self, wb):
        """ターゲット蓄積シート（ターゲット履歴／ターゲット一覧／ターゲット等）を判定（存在しない場合はNone）"""
        for sname in ["ターゲット履歴", "ターゲット一覧", "ターゲット", "リスト", "ターゲットリスト"]:
            if sname in wb.sheetnames:
                return wb[sname]
        for name in wb.sheetnames:
            sheet = wb[name]
            try:
                first_row = [str(cell.value or "") for cell in sheet[1]]
                if any("アカウント名" in h or "ユーザー名" in h or "ターゲットURL" in h or "screen_name" in h for h in first_row):
                    return sheet
            except Exception:
                continue
        return None

    def append_discovered_target(self, screen_name: str, source_url: str = "", target_path_override: str = None, tweet_url: str = "", display_name: str = ""):
        """旧「ターゲット履歴」シートが存在するExcelのみ追記（シート削除済みならDB管理のみとしExcelへ新規作成しない）"""
        clean_name = normalize_screen_name(screen_name)
        if not clean_name:
            return

        target_file = target_path_override or self.target_list_path
        if not os.path.exists(target_file):
            return

        for attempt in range(5):
            try:
                wb = openpyxl.load_workbook(target_file)
                sheet = None
                for sname in ["ターゲット履歴", "ターゲット一覧", "ターゲットリスト"]:
                    if sname in wb.sheetnames:
                        sheet = wb[sname]
                        break
                if sheet is None:
                    wb.close()
                    return

                # アカウント名列の特定
                name_col = 1
                for c_idx, cell in enumerate(sheet[1], start=1):
                    val = str(cell.value or "")
                    if "アカウント名" in val or "ユーザー名" in val or "screen_name" in val:
                        name_col = c_idx
                        break

                # 既存存在チェック
                exists = False
                for row_idx in range(2, sheet.max_row + 1):
                    val = normalize_screen_name(str(sheet.cell(row=row_idx, column=name_col).value or "")).lower()
                    if val == clean_name.lower():
                        exists = True
                        break

                if not exists:
                    dest_url = tweet_url if tweet_url else f"https://x.com/{clean_name}"
                    source_label = f"自動抽出: {source_url}" + (f" (表示名: {display_name})" if display_name else "")
                    new_row = [f"@{clean_name}", dest_url, "", "未送信", "", source_label]
                    sheet.append(new_row)
                    wb.save(target_file)

                wb.close()
                return
            except PermissionError:
                time.sleep(1.0)
            except Exception as e:
                print(f"⚠️ 新規ターゲット追記エラー: {e}")
                break

    def load_targets(self, limit: int = 0, target_path_override: str = None) -> list[dict]:
        """「アカウント名.xlsx」のターゲットシートが存在する場合のみ未処理ターゲット一覧を読込"""
        target_file = target_path_override or self.target_list_path
        if not os.path.exists(target_file):
            return []
        try:
            wb = self._safe_load_workbook(target_file, read_only=True)
            sheet = self._get_target_sheet(wb)
            if sheet is None:
                wb.close()
                return []
            rows = list(sheet.iter_rows(values_only=True))
            wb.close()
        except Exception as e:
            print(f"⚠️ ターゲット読み込み警告 ({target_file}): {e}")
            return []

        if not rows or len(rows) < 2:
            return []

        headers = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(rows[0])]
        
        name_idx = 0
        display_idx = None
        msg_idx = None
        status_idx = None
        url_idx = 1 if len(headers) > 1 else None

        for idx, h in enumerate(headers):
            h_clean = h.replace(" ", "").replace("　", "").replace("_", "").lower()
            if any(k in h_clean for k in ["表示名", "お名前", "名前"]):
                display_idx = idx
            elif any(k in h_clean for k in ["アカウント名", "ユーザー名", "screenname", "ターゲット", "target", "id"]):
                name_idx = idx
            elif any(k in h_clean for k in ["個別メッセージ", "メッセージ", "dm内容", "dm本文", "dm文面", "文面", "dm", "message", "content", "送信内容"]):
                msg_idx = idx
            elif any(k in h_clean for k in ["ステータス", "状態", "status"]):
                status_idx = idx
            elif any(k in h_clean for k in ["url", "link"]):
                url_idx = idx

        targets = []
        for row in rows[1:]:
            if not row or not any(row):
                continue
            raw_name = str(row[name_idx] or "").strip()
            clean_name = normalize_screen_name(raw_name)
            if not clean_name and url_idx is not None and len(row) > url_idx:
                clean_name = normalize_screen_name(str(row[url_idx] or ""))
            if not clean_name or is_placeholder_target(clean_name):
                continue

            status = str(row[status_idx] or "").strip() if status_idx is not None and len(row) > status_idx else "未送信"
            custom_msg = str(row[msg_idx] or "").strip() if msg_idx is not None and len(row) > msg_idx else ""
            raw_url = str(row[url_idx] or "").strip() if url_idx is not None and len(row) > url_idx else ""
            target_url = raw_url if ("/status/" in raw_url or "x.com" in raw_url or "twitter.com" in raw_url) else f"https://x.com/{clean_name}"
            tweet_url = raw_url if "/status/" in raw_url else ""

            display_name = str(row[display_idx] or "").strip() if display_idx is not None and len(row) > display_idx else ""
            if not display_name and raw_name and not raw_name.startswith("@") and normalize_screen_name(raw_name).lower() != clean_name.lower():
                display_name = raw_name

            if status in ["", "未送信", "None", "none", "未処理"]:
                targets.append({
                    "アカウント名": clean_name,
                    "ターゲットURL": target_url,
                    "ツイートURL": tweet_url,
                    "個別メッセージ": custom_msg,
                    "ステータス": status,
                    "表示名": display_name
                })
                if limit > 0 and len(targets) >= limit:
                    break

        return targets

    def update_target_status(self, screen_name: str, status: str, detail: str = "", target_path_override: str = None):
        """「アカウント名.xlsx」の該当ユーザーのステータス列・処理日時を更新"""
        clean_target = normalize_screen_name(screen_name).lower()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        target_file = target_path_override or self.target_list_path
        if not os.path.exists(target_file):
            return

        for attempt in range(5):
            try:
                wb = openpyxl.load_workbook(target_file)
                sheet = self._get_target_sheet(wb)
                
                # ヘッダー特定
                name_col = 1
                status_col = None
                date_col = None
                detail_col = None

                for col_idx, cell in enumerate(sheet[1], start=1):
                    val = str(cell.value or "").strip()
                    if "アカウント名" in val or "ユーザー名" in val or "screen_name" in val:
                        name_col = col_idx
                    elif "ステータス" in val:
                        status_col = col_idx
                    elif "日時" in val:
                        date_col = col_idx
                    elif "詳細" in val or "備考" in val:
                        detail_col = col_idx

                if not status_col:
                    status_col = sheet.max_column + 1
                    sheet.cell(row=1, column=status_col, value="ステータス")

                for row_idx in range(2, sheet.max_row + 1):
                    val = normalize_screen_name(str(sheet.cell(row=row_idx, column=name_col).value or "")).lower()
                    if val == clean_target:
                        sheet.cell(row=row_idx, column=status_col, value=status)
                        if date_col:
                            sheet.cell(row=row_idx, column=date_col, value=now_str)
                        if detail_col and detail:
                            sheet.cell(row=row_idx, column=detail_col, value=detail)
                        break

                wb.save(target_file)
                wb.close()
                return
            except PermissionError:
                time.sleep(1.0)
            except Exception as e:
                print(f"⚠️ Excelステータス更新警告: {e}")
                break

    def load_target_limit(self, default: int = 15, target_path_override: str = None) -> int:
        """「アカウント情報.xlsx」または「アカウント名.xlsx」からターゲット件数設定を柔軟に取得"""
        target_file = target_path_override or self.target_list_path
        paths = [p for p in [self.account_config_path, target_file, self.target_list_path] if p]
        seen_paths = set()
        for path in paths:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            if not os.path.exists(path):
                continue
            try:
                wb = self._safe_load_workbook(path, read_only=True)
                for sname in wb.sheetnames:
                    sheet = wb[sname]
                    rows = list(sheet.iter_rows(values_only=True))
                    if not rows:
                        continue
                    # 1. ヘッダー行から「ターゲット件数」「送信件数」列を探索
                    if len(rows) >= 2:
                        headers = [str(h or "").replace(" ", "").replace("　", "") for h in rows[0]]
                        for col_idx, h in enumerate(headers):
                            if any(k in h for k in ["ターゲット件数", "ターゲット数", "送信件数", "目標件数", "上限件数", "処理件数", "目標上限", "送信上限", "件数"]):
                                for r in rows[1:]:
                                    if r and len(r) > col_idx and r[col_idx] is not None:
                                        try:
                                            val = int(float(str(r[col_idx]).strip()))
                                            if val > 0:
                                                wb.close()
                                                return val
                                        except:
                                            pass
                    # 2. 縦並びのキー・バリュー設定行を探索
                    for r in rows:
                        if r and len(r) >= 2 and r[0] and r[1] is not None:
                            k_str = str(r[0]).replace(" ", "").replace("　", "")
                            if any(k in k_str for k in ["ターゲット件数", "送信件数", "目標件数", "処理件数"]):
                                try:
                                    val = int(float(str(r[1]).strip()))
                                    if val > 0:
                                        wb.close()
                                        return val
                                except:
                                    pass
                wb.close()
            except Exception:
                pass
        return default
