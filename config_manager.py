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
                base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

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
        """アカウント情報.xlsx の「モード」シート (またはアカウント設定) から指定窓数 (1~5) を取得"""
        try:
            wb = self._safe_load_workbook(self.account_config_path, read_only=True)
            for sname in ["モード", "モード設定", "実行モード"]:
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
        実行間隔最短/最長(秒)・連続処理件数最小/最大数を読込。
        ※「動作設定」シート未存在時はアカウント情報.xlsxの旧行から自動フォールバック。
        """
        target_path = target_path_override or self.get_target_path_for_account(account_id)
        fallback = fallback_acc or {}
        
        res = {
            "target_mode": "検索キーワード,URLリスト_リプライ,URLリスト_フォロー",
            "target_min_sec": 60.0,
            "target_max_sec": 150.0,
            "scroll_limit": 15,
            "target_count": 15,
            "ai_engine": "grok",
            "block_media": True,
            "dm_passcode": "",
            "actions": {
                "dm": {"enabled": True, "min_interval": 300.0, "max_interval": 900.0, "min_batch": 3, "max_batch": 8, "ai_gen": False},
                "like": {"enabled": True, "min_interval": 30.0, "max_interval": 90.0, "min_batch": 5, "max_batch": 10, "ai_gen": False},
                "follow": {"enabled": True, "min_interval": 60.0, "max_interval": 180.0, "min_batch": 3, "max_batch": 7, "ai_gen": False},
                "reply": {"enabled": False, "min_interval": 180.0, "max_interval": 600.0, "min_batch": 2, "max_batch": 5, "ai_gen": False},
                "quote": {"enabled": False, "min_interval": 300.0, "max_interval": 900.0, "min_batch": 1, "max_batch": 3, "ai_gen": False},
                "reply_ai": {"enabled": False, "min_interval": 180.0, "max_interval": 600.0, "min_batch": 2, "max_batch": 5, "ai_gen": True},
                "quote_ai": {"enabled": False, "min_interval": 300.0, "max_interval": 900.0, "min_batch": 1, "max_batch": 3, "ai_gen": True},
            }
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
                elif "aiエンジン" in kc:
                    res["ai_engine"] = str(v).strip()
                elif "メディア" in kc:
                    res["block_media"] = str(v).strip().lower() not in ["0", "false", "off", "無効"]
                s_on = str(v).strip().lower() in ["1", "1.0", "true", "yes", "on", "実行", "有効", "○", "〇", "o"]
                if "命令実行dm" in kc or kc == "dm": res["actions"]["dm"]["enabled"] = s_on
                elif "命令実行いいね" in kc or kc == "いいね": res["actions"]["like"]["enabled"] = s_on
                elif "命令実行フォロー" in kc or kc == "フォロー": res["actions"]["follow"]["enabled"] = s_on
                elif "命令実行リプライ" in kc: res["actions"]["reply"]["enabled"] = s_on
                elif "命令実行引用" in kc: res["actions"]["quote"]["enabled"] = s_on
                elif "リプライai生成" in kc: res["actions"]["reply_ai"]["enabled"] = s_on
                elif "引用リツイートai生成" in kc: res["actions"]["quote_ai"]["enabled"] = s_on

        # 2. アカウント専用Excel (例: wqwq1320.xlsx) の「動作設定」シートから優先読込
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
                    for r in rows:
                        if not r or not any(r): continue
                        col0 = str(r[0] or "").strip()
                        col0_clean = col0.replace(" ", "").replace("　", "").replace("_", "").lower()
                        
                        if len(r) > 1 and r[1] is not None and str(r[1]).strip() != "":
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
                            elif "aiエンジン" in col0_clean:
                                res["ai_engine"] = val1
                            elif "メディア読込ブロック" in col0_clean:
                                res["block_media"] = val1.lower() not in ["0", "false", "off", "無効"]

                        is_passcode_row = any(m in col0_clean for m in ["パスコード", "passcode", "暗号化", "pin"])
                        act_key = None
                        if not is_passcode_row:
                            if "dm" in col0_clean: act_key = "dm"
                            elif "いいね" in col0_clean: act_key = "like"
                            elif "フォロー" in col0_clean: act_key = "follow"
                            elif "リプライai" in col0_clean: act_key = "reply_ai"
                            elif "引用" in col0_clean and "ai" in col0_clean: act_key = "quote_ai"
                            elif "リプライ" in col0_clean: act_key = "reply"
                            elif "引用" in col0_clean: act_key = "quote"

                        if act_key and len(r) >= 2:
                            def _parse_cell(idx, def_val):
                                if len(r) > idx and r[idx] is not None and str(r[idx]).strip() != "":
                                    return str(r[idx]).strip()
                                return def_val

                            flag_str = _parse_cell(1, "1" if res["actions"][act_key]["enabled"] else "0").lower()
                            res["actions"][act_key]["enabled"] = flag_str in ["1", "1.0", "true", "yes", "on", "実行", "有効", "○", "〇", "o"]
                            
                            try: res["actions"][act_key]["min_interval"] = float(_parse_cell(2, res["actions"][act_key]["min_interval"]))
                            except: pass
                            try: res["actions"][act_key]["max_interval"] = float(_parse_cell(3, res["actions"][act_key]["max_interval"]))
                            except: pass
                            try: res["actions"][act_key]["min_batch"] = int(float(_parse_cell(4, res["actions"][act_key]["min_batch"])))
                            except: pass
                            try: res["actions"][act_key]["max_batch"] = int(float(_parse_cell(5, res["actions"][act_key]["max_batch"])))
                            except: pass
                            ai_str = _parse_cell(6, "1" if res["actions"][act_key]["ai_gen"] else "0").lower()
                            res["actions"][act_key]["ai_gen"] = ai_str in ["1", "1.0", "true", "yes", "on", "実行", "有効", "○", "〇", "o"]
                wb.close()
            except Exception:
                pass

        return res

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

    def load_prompt_settings(self, target_path_override: str = None) -> str:
        """「アカウント名.xlsx」の「プロンプト設定」シートからプロンプトを読込"""
        prompt = ""
        target_file = target_path_override or self.target_list_path
        paths = [p for p in [target_file, self.target_list_path, self.account_config_path] if p]
        seen_paths = set()
        for path in paths:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            if os.path.exists(path):
                wb = self._safe_load_workbook(path, read_only=True)
                if "プロンプト設定" in wb.sheetnames:
                    sheet = wb["プロンプト設定"]
                    for row in sheet.iter_rows(min_row=2, values_only=True):
                        if row and row[0]:
                            prompt = str(row[0]).strip()
                            break
                wb.close()
                if prompt:
                    break
        return prompt or "取得する文章はXのポストです。相手に寄り添って文章を生成して 140文字以内で"

    def _get_target_sheet(self, wb):
        """ターゲット蓄積シート（ターゲット履歴／ターゲット一覧／ターゲット等）を自動判定"""
        for sname in ["ターゲット履歴", "ターゲット一覧", "ターゲット", "リスト", "ターゲットリスト"]:
            if sname in wb.sheetnames:
                return wb[sname]
        for name in wb.sheetnames:
            sheet = wb[name]
            first_row = [str(cell.value or "") for cell in sheet[1]]
            if any("アカウント名" in h or "ユーザー名" in h or "ターゲット" in h or "screen_name" in h for h in first_row):
                return sheet
        # なければ最後のシート、またはwb.active
        return wb.active

    def append_discovered_target(self, screen_name: str, source_url: str = "", target_path_override: str = None, tweet_url: str = "", display_name: str = ""):
        """収集した新規ターゲットを「アカウント名.xlsx」の「ターゲット履歴」シートに自動追記"""
        clean_name = normalize_screen_name(screen_name)
        if not clean_name:
            return

        target_file = target_path_override or self.target_list_path
        if not os.path.exists(target_file):
            return

        for attempt in range(5):
            try:
                wb = openpyxl.load_workbook(target_file)
                
                # ターゲット履歴シートの取得、存在しなければ作成
                if "ターゲット履歴" in wb.sheetnames:
                    sheet = wb["ターゲット履歴"]
                else:
                    sheet = None
                    for name in wb.sheetnames:
                        first_row = [str(cell.value or "") for cell in wb[name][1]]
                        if any("アカウント名" in h or "ユーザー名" in h or "ターゲット" in h for h in first_row):
                            sheet = wb[name]
                            break
                    if sheet is None:
                        sheet = wb.create_sheet(title="ターゲット履歴")
                        sheet.append(["アカウント名", "ターゲットURL", "個別メッセージ", "ステータス", "処理日時", "詳細備考"])

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
        """「アカウント名.xlsx」のターゲットシートから未処理ターゲット一覧を読込 (limit指定時はその件数のみ直接読込)"""
        target_file = target_path_override or self.target_list_path
        if not os.path.exists(target_file):
            return []
        try:
            wb = self._safe_load_workbook(target_file, read_only=True)
            sheet = self._get_target_sheet(wb)
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
            if not clean_name:
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
