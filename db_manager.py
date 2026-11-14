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

def normalize_tweet_url_and_id(url: str) -> tuple[str, str]:
    """
    X(Twitter)のポストURLを正規化し、(正規化URL, ツイートID) を返す。
    例:
    - "https://twitter.com/UserA/status/12345?s=20" -> ("https://x.com/UserA/status/12345", "12345")
    - "https://x.com/UserA/status/12345/photo/1" -> ("https://x.com/UserA/status/12345", "12345")
    - "https://x.com/i/web/status/12345" -> ("https://x.com/i/web/status/12345", "12345")
    """
    if not url:
        return "", ""
    raw = str(url).strip()
    m = re.search(r'(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/(?:i/web|([^/?#]+))/status/(\d+)', raw, re.IGNORECASE)
    if m:
        handle = (m.group(1) or "").strip().lstrip("@")
        tweet_id = m.group(2).strip()
        if handle and handle.lower() != "i":
            return f"https://x.com/{handle}/status/{tweet_id}", tweet_id
        return f"https://x.com/i/web/status/{tweet_id}", tweet_id
    return "", ""

def normalize_tweet_url(url: str) -> str:
    """正規化されたポストURLを返す（/status/を含まない場合は空文字）"""
    norm, _ = normalize_tweet_url_and_id(url)
    return norm

def normalize_action_type(action_type: str) -> str:
    a = str(action_type or "DM").strip().upper()
    if a in ("REPLY", "REPLY_AI"):
        return "REPLY"
    if a in ("QUOTE", "QUOTE_AI", "QUOTE_RT", "QUOTE_RT_AI"):
        return "QUOTE"
    if a in ("LIKE",):
        return "LIKE"
    if a in ("FOLLOW",):
        return "FOLLOW"
    if a in ("DM",):
        return "DM"
    return a

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
    """
    アカウント名.DB のSQLite3管理
    - アクションごとにテーブル分離:
      - dm_history     (ユーザー単位: @ユーザー名)
      - follow_history (ユーザー単位: @ユーザー名)
      - reply_history  (取得URL単位: 正規化ポストURL / tweet_id)
      - quote_history  (取得URL単位: 正規化ポストURL / tweet_id)
      - like_history   (取得URL単位: 正規化ポストURL / tweet_id)
    """
    USER_SCOPED_ACTIONS = {"DM", "FOLLOW"}
    URL_SCOPED_ACTIONS = {"REPLY", "QUOTE", "LIKE"}
    ACTION_TABLE_MAP = {
        "DM": "dm_history",
        "FOLLOW": "follow_history",
        "REPLY": "reply_history",
        "QUOTE": "quote_history",
        "LIKE": "like_history",
    }
    SUCCESS_STATUSES = (
        "SUCCESS",
        "ALREADY_FOLLOWING",
        "ALREADY_LIKED",
        "送信完了",
        "フォロー済",
        "いいね済",
    )

    def __init__(self, db_path: str = "アカウント名.db"):
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path, timeout=10.0)

    def _init_db(self):
        """アクション別テーブルの初期化と旧 sent_history からの自動移行"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS db_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            for table_name in self.ACTION_TABLE_MAP.values():
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS {table_name} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        target_key TEXT NOT NULL COLLATE NOCASE,
                        target_screen_name TEXT NOT NULL,
                        target_url TEXT DEFAULT '',
                        tweet_id TEXT DEFAULT '',
                        account_id_used TEXT,
                        action_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        snippet TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(target_key)
                    )
                """)
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{table_name}_key
                    ON {table_name}(target_key)
                """)
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{table_name}_tid
                    ON {table_name}(tweet_id)
                """)

            # 互換性および旧データの自動移行（1回のみ）
            cursor.execute("SELECT value FROM db_meta WHERE key = 'migrated_action_tables_v2'")
            migrated_row = cursor.fetchone()
            if not migrated_row:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sent_history'")
                if cursor.fetchone():
                    cursor.execute("""
                        SELECT target_screen_name, account_id_used, action_type, status, snippet, created_at
                        FROM sent_history
                        ORDER BY id ASC
                    """)
                    for row in cursor.fetchall():
                        raw_target, acc_used, raw_act, st, snip, created_at = row
                        act = normalize_action_type(raw_act)
                        table_name = self.ACTION_TABLE_MAP.get(act)
                        if not table_name:
                            continue
                        clean_name = normalize_screen_name(raw_target)
                        if not clean_name:
                            continue
                        if act in self.USER_SCOPED_ACTIONS:
                            t_key = clean_name.lower()
                            t_url = f"https://x.com/{clean_name}"
                            tid = ""
                        else:
                            norm_u, tid = normalize_tweet_url_and_id(raw_target)
                            if norm_u and tid:
                                t_key = norm_u.lower()
                                t_url = norm_u
                            else:
                                # 旧DBはユーザー名のみ記録だったため、履歴表示用に保持しつつ新ツイートURLをブロックしないよう tid='' とする
                                t_key = f"https://x.com/{clean_name.lower()}"
                                t_url = f"https://x.com/{clean_name}"
                                tid = ""
                        cursor.execute(f"""
                            INSERT OR IGNORE INTO {table_name}
                            (target_key, target_screen_name, target_url, tweet_id, account_id_used, action_type, status, snippet, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (t_key, clean_name, t_url, tid, acc_used, act, st, snip, created_at))

                cursor.execute("""
                    INSERT OR REPLACE INTO db_meta (key, value)
                    VALUES ('migrated_action_tables_v2', '1')
                """)

            conn.commit()

    def is_already_sent(self, target_screen_name: str, action_type: str = "DM", target_url: str = "") -> bool:
        """
        アクション種別に応じた重複実行チェック (成功ステータスのみ対象)
        - DM / FOLLOW: ユーザー単位 (@ユーザー名)
        - REPLY / QUOTE / LIKE: 取得URL単位 (正規化ポストURL / tweet_id)
        """
        act = normalize_action_type(action_type)
        table_name = self.ACTION_TABLE_MAP.get(act)
        if not table_name:
            return False

        placeholders = ",".join("?" for _ in self.SUCCESS_STATUSES)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if act in self.USER_SCOPED_ACTIONS:
                clean_name = normalize_screen_name(target_screen_name).lower()
                if not clean_name:
                    return False
                cursor.execute(f"""
                    SELECT id FROM {table_name}
                    WHERE LOWER(target_key) = ?
                      AND status IN ({placeholders})
                """, (clean_name, *self.SUCCESS_STATUSES))
                return cursor.fetchone() is not None
            else:
                norm_url, tweet_id = normalize_tweet_url_and_id(target_url)
                if not tweet_id and "/status/" in str(target_screen_name or ""):
                    norm_url, tweet_id = normalize_tweet_url_and_id(target_screen_name)
                if not tweet_id:
                    # ツイートURLが未確定の段階（ユーザー名のみの登録など）ではここではブロックせず、
                    # 実際の最新ツイートURLを取得したタイミングで再チェックする
                    return False
                cursor.execute(f"""
                    SELECT id FROM {table_name}
                    WHERE (tweet_id = ? OR LOWER(target_key) = ?)
                      AND status IN ({placeholders})
                """, (tweet_id, norm_url.lower(), *self.SUCCESS_STATUSES))
                return cursor.fetchone() is not None

    def record_sent(self, target_screen_name: str, account_id: str, action_type: str, status: str, snippet: str = "", target_url: str = ""):
        """送信・実行履歴をアクション別テーブルに登録（重複時は上書き更新）"""
        act = normalize_action_type(action_type)
        table_name = self.ACTION_TABLE_MAP.get(act)
        if not table_name:
            return

        norm_url, tweet_id = normalize_tweet_url_and_id(target_url)
        if not norm_url and "/status/" in str(target_screen_name or ""):
            norm_url, tweet_id = normalize_tweet_url_and_id(target_screen_name)

        clean_name = normalize_screen_name(target_screen_name) or normalize_screen_name(norm_url)
        if not clean_name and not norm_url:
            return

        if act in self.USER_SCOPED_ACTIONS:
            target_key = clean_name.lower()
            stored_url = norm_url or (f"https://x.com/{clean_name}" if clean_name else "")
            stored_tid = tweet_id
        else:
            if norm_url and tweet_id:
                target_key = norm_url.lower()
                stored_url = norm_url
                stored_tid = tweet_id
            else:
                target_key = f"https://x.com/{clean_name.lower()}"
                stored_url = f"https://x.com/{clean_name}"
                stored_tid = ""

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                INSERT INTO {table_name}
                (target_key, target_screen_name, target_url, tweet_id, account_id_used, action_type, status, snippet, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                ON CONFLICT(target_key)
                DO UPDATE SET
                    target_screen_name = excluded.target_screen_name,
                    target_url = excluded.target_url,
                    tweet_id = excluded.tweet_id,
                    status = excluded.status,
                    account_id_used = excluded.account_id_used,
                    snippet = excluded.snippet,
                    created_at = datetime('now', 'localtime')
            """, (target_key, clean_name, stored_url, stored_tid, account_id, act, status, snippet))
            conn.commit()

    def fetch_history(self, action_filter: str = "ALL", limit: int = 300) -> list[dict]:
        """GUI・管理用に履歴レコード一覧を取得（アクション絞込対応）"""
        f_act = str(action_filter or "ALL").strip().upper()
        if f_act != "ALL":
            f_act = normalize_action_type(f_act)

        target_tables = (
            [(act, tbl) for act, tbl in self.ACTION_TABLE_MAP.items() if f_act == "ALL" or act == f_act]
        )
        rows_out = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for act, tbl in target_tables:
                try:
                    cursor.execute(f"""
                        SELECT id, target_key, target_screen_name, target_url, account_id_used, action_type, status, created_at, snippet
                        FROM {tbl}
                        ORDER BY id DESC
                        LIMIT ?
                    """, (limit,))
                    for r in cursor.fetchall():
                        rows_out.append({
                            "id": r[0],
                            "target_key": r[1] or "",
                            "target_screen_name": r[2] or "",
                            "target_url": r[3] or "",
                            "account_id_used": r[4] or "",
                            "action_type": r[5] or act,
                            "status": r[6] or "",
                            "created_at": r[7] or "",
                            "snippet": r[8] or "",
                            "db_path": self.db_path,
                        })
                except Exception:
                    pass

        rows_out.sort(key=lambda x: (str(x.get("created_at", "")), int(x.get("id", 0))), reverse=True)
        return rows_out[:limit]

    def delete_record(self, action_type: str, record_id: int) -> bool:
        """指定アクションテーブルから特定IDの履歴を削除"""
        act = normalize_action_type(action_type)
        table_name = self.ACTION_TABLE_MAP.get(act)
        if not table_name:
            return False
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM {table_name} WHERE id = ?", (int(record_id),))
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted

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

