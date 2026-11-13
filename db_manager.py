"""
SQLite3 データベース管理 (アカウント名.db)
- 送信済みアカウントの重複防止
- アカウント別・アクション別の履歴管理
"""

import sqlite3
import os
import sys
import re
import json
from datetime import datetime

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
    """Windowsで使用不可能な記号を安全なアンダースコアに置換"""
    clean = normalize_screen_name(name)
    if not clean:
        clean = re.sub(r'[\/*?:"<>|]', '_', str(name).strip())
    if not clean:
        clean = "unknown_account"
    return clean

def get_db_dir(base_dir: str) -> str:
    """dbフォルダ (またはdb) のパスを取得し、無ければ自動作成"""
    for folder_name in ["dbフォルダ", "db", "dbs"]:
        p = os.path.join(base_dir, folder_name)
        if os.path.isdir(p):
            return p
    default_p = os.path.join(base_dir, "dbフォルダ")
    os.makedirs(default_p, exist_ok=True)
    return default_p

def get_account_db_path(base_dir: str, account_name: str) -> str:
    """アカウント名に応じた個別DBパス (dbフォルダ/アカウント名.DB) を取得"""
    db_dir = get_db_dir(base_dir)
    safe_name = sanitize_filename(account_name)
    p_upper = os.path.join(db_dir, f"{safe_name}.DB")
    p_lower = os.path.join(db_dir, f"{safe_name}.db")
    if os.path.exists(p_lower):
        return p_lower
    return p_upper

def get_log_dir(base_dir: str) -> str:
    """logフォルダ (またはlog) のパスを取得し、無ければ自動作成"""
    for folder_name in ["logフォルダ", "log", "logs"]:
        p = os.path.join(base_dir, folder_name)
        if os.path.isdir(p):
            return p
    default_p = os.path.join(base_dir, "logフォルダ")
    os.makedirs(default_p, exist_ok=True)
    return default_p

def get_account_log_path(base_dir: str, account_name: str) -> str:
    """アカウント名に応じた個別ログファイルパス (logフォルダ/アカウント名.log) を取得"""
    log_dir = get_log_dir(base_dir)
    safe_name = sanitize_filename(account_name)
    return os.path.join(log_dir, f"{safe_name}.log")

def get_saved_passcodes(base_dir: str) -> dict:
    """保存されたアカウント別DMパスコード一覧を取得（初回入力のみで永続再利用・表記ゆれ完全吸収）"""
    db_dir = get_db_dir(base_dir)
    passcode_file = os.path.join(db_dir, "saved_passcodes.json")
    result = {}
    if os.path.exists(passcode_file):
        try:
            with open(passcode_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    result = data
        except Exception:
            pass
    return result

def save_account_passcode(base_dir: str, account_id: str, passcode: str) -> bool:
    """アカウントのDMパスコードを永続保存（初回入力のみで次回以降は完全自動）"""
    if not account_id or not passcode:
        return False
    clean_acc = normalize_screen_name(account_id)
    if not clean_acc:
        clean_acc = str(account_id).strip().lstrip("@")
    clean_p = str(passcode).strip().strip("'").strip('"')
    if clean_p.endswith(".0") and clean_p[:-2].isdigit():
        clean_p = clean_p[:-2]
    
    db_dir = get_db_dir(base_dir)
    passcode_file = os.path.join(db_dir, "saved_passcodes.json")
    try:
        data = {}
        if os.path.exists(passcode_file):
            try:
                with open(passcode_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data[clean_acc] = clean_p
        data[clean_acc.lower()] = clean_p
        data[str(account_id).strip()] = clean_p
        data[str(account_id).strip().lower()] = clean_p
        data[f"@{clean_acc}"] = clean_p
        data[f"@{clean_acc.lower()}"] = clean_p
        with open(passcode_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"⚠️ パスコード保存エラー: {e}")
        return False

def update_excel_passcode(base_dir: str, account_id: str, passcode: str) -> bool:
    """アカウント名.xlsx (<account_id>.xlsx) の「動作設定」シート【全体動作パラメータ】へDMパスコードを保存"""
    if not account_id or not passcode:
        return False
    clean_acc = normalize_screen_name(account_id) or str(account_id).strip().lstrip("@")
    candidates = [
        os.path.join(base_dir, f"{clean_acc}.xlsx"),
        os.path.join(base_dir, f"@{clean_acc}.xlsx"),
        os.path.join(base_dir, f"アカウント名_{clean_acc}.xlsx"),
        os.path.join(base_dir, "アカウント名.xlsx"),
    ]
    if os.path.isdir(base_dir):
        try:
            for fname in os.listdir(base_dir):
                if fname.lower().endswith(".xlsx") and not fname.startswith("~$") and fname != "アカウント情報.xlsx":
                    stem = os.path.splitext(fname)[0].lstrip("@").strip().lower()
                    if stem == clean_acc.lower():
                        candidates.insert(0, os.path.join(base_dir, fname))
        except Exception:
            pass

    seen = set()
    for excel_path in candidates:
        if not excel_path or excel_path in seen or not os.path.exists(excel_path):
            continue
        seen.add(excel_path)
        try:
            import openpyxl
            wb = openpyxl.load_workbook(excel_path)
            op_sheet = None
            for sname in ["動作設定", "設定", "アクション設定", "基本設定"]:
                if sname in wb.sheetnames:
                    op_sheet = wb[sname]
                    break
            if op_sheet is not None:
                found_row = None
                action_header_row = None
                for row_idx in range(1, op_sheet.max_row + 1):
                    col0 = str(op_sheet.cell(row=row_idx, column=1).value or "").strip()
                    col0_clean = col0.replace(" ", "").replace("　", "").replace("_", "").lower()
                    if any(k in col0_clean for k in ["dmパスコード", "パスコード", "暗号化パスコード", "暗号化キー", "dmpin", "passcode"]):
                        found_row = row_idx
                        break
                    if "【アクション別" in col0 and action_header_row is None:
                        action_header_row = row_idx
                if found_row is not None:
                    op_sheet.cell(row=found_row, column=2).value = str(passcode)
                else:
                    insert_at = action_header_row if action_header_row and action_header_row > 2 else (op_sheet.max_row + 1)
                    if action_header_row and action_header_row > 2:
                        op_sheet.insert_rows(insert_at)
                    op_sheet.cell(row=insert_at, column=1).value = "DMパスコード(不要時は空欄)"
                    op_sheet.cell(row=insert_at, column=2).value = str(passcode)
                    op_sheet.cell(row=insert_at, column=3).value = "暗号化DM・PIN自動入力用パスコード（数字4桁等・不要時は空欄）"
                wb.save(excel_path)
                wb.close()
                return True
            wb.close()
        except Exception:
            pass
    return False

class AccountLogger:
    """アカウントごとの個別ログ管理 (コンソール出力 ＋ logフォルダ/アカウント名.log への保存)"""
    def __init__(self, account_name: str, base_dir: str):
        self.account_name = account_name
        self.base_dir = base_dir
        self.log_path = get_account_log_path(base_dir, account_name)
        parent = os.path.dirname(os.path.abspath(self.log_path))
        if parent:
            os.makedirs(parent, exist_ok=True)

    def log(self, msg: str):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted = f"[{now}] {msg}"
        try:
            print(formatted, flush=True)
        except Exception:
            try:
                enc = sys.stdout.encoding or "utf-8"
                safe_msg = formatted.encode(enc, errors="replace").decode(enc)
                print(safe_msg, flush=True)
            except Exception:
                pass
        try:
            with open(self.log_path, "a", encoding="utf-8", errors="replace") as f:
                f.write(formatted + "\n")
        except Exception:
            pass

class DatabaseManager:
    def __init__(self, db_path: str = "アカウント名.db"):
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path, timeout=10.0)

    def _init_db(self):
        """テーブル初期化"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sent_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_screen_name TEXT NOT NULL,
                    account_id_used TEXT,
                    action_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    snippet TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(target_screen_name, action_type)
                )
            """)
            # 高速検索インデックス
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_target_action 
                ON sent_history(target_screen_name, action_type)
            """)
            conn.commit()

    def is_already_sent(self, target_screen_name: str, action_type: str = "DM") -> bool:
        """指定ユーザーへの送信履歴があるかチェック (成功ステータスのみ対象)"""
        clean_name = normalize_screen_name(target_screen_name).lower()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id FROM sent_history 
                WHERE LOWER(target_screen_name) = ? AND action_type = ?
                  AND status IN ('SUCCESS', 'ALREADY_FOLLOWING', 'ALREADY_LIKED', '送信完了', 'フォロー済', 'いいね済')
            """, (clean_name, action_type))
            row = cursor.fetchone()
            return row is not None

    def record_sent(self, target_screen_name: str, account_id: str, action_type: str, status: str, snippet: str = ""):
        """送信履歴を登録（重複時は上書き更新）"""
        clean_name = normalize_screen_name(target_screen_name)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sent_history (target_screen_name, account_id_used, action_type, status, snippet, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
                ON CONFLICT(target_screen_name, action_type) 
                DO UPDATE SET 
                    status = excluded.status,
                    account_id_used = excluded.account_id_used,
                    snippet = excluded.snippet,
                    created_at = datetime('now', 'localtime')
            """, (clean_name, account_id, action_type, status, snippet))
            conn.commit()

    @staticmethod
    def list_account_dbs(base_dir: str) -> list[str]:
        """dbフォルダ内の全アカウントDBファイル一覧を取得"""
        db_dir = get_db_dir(base_dir)
        if not os.path.exists(db_dir):
            return []
        files = []
        for f in os.listdir(db_dir):
            if f.lower().endswith(".db"):
                files.append(os.path.join(db_dir, f))
        return sorted(files)
