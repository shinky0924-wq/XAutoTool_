"""
Playwright ブラウザ自動操縦コントローラー
- 単一タブ動作（不要な新規タブを防止）
- Cookieログイン (auth_token)
- ベジェ曲線風のマウス移動＆ホバー
- 人間らしいタイピング（速度ゆらぎ＋句読点小休止）
- DM拒否・鍵アカウントの確実な検知＆スキップ
"""

import sys
import os
import re
import time
import random
import math

import functools
print = functools.partial(print, flush=True)

# Windows CP932 / Emoji UnicodeEncodeError 対策
if sys.platform == "win32":
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 他PC配布（Python/Playwright未導入PC）対応: 同梱Chromiumブラウザの自動検知
def _setup_bundled_browsers():
    if "PLAYWRIGHT_BROWSERS_PATH" in os.environ and os.path.exists(os.environ["PLAYWRIGHT_BROWSERS_PATH"]):
        return
    base_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else base_dir
    candidates = [
        os.path.join(app_dir, "_internal", "ms-playwright"),
        os.path.join(app_dir, "browsers"),
        os.path.join(app_dir, "_internal", "browsers"),
        os.path.join(base_dir, "_internal", "ms-playwright"),
        os.path.join(base_dir, "browsers"),
        os.path.join(os.path.dirname(app_dir), "_internal", "ms-playwright"),
        os.path.join(os.path.dirname(app_dir), "browsers"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = c
            break

_setup_bundled_browsers()

from playwright.sync_api import sync_playwright, Page, BrowserContext

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

def extract_display_name_from_text(raw_name: str, target_screen_name: str = "", fallback_to_id: bool = False) -> str:
    """
    アカウントの表示名（アカウント名）を抽出し、{name}さん に適した自然な呼称に整形
    """
    clean_target = target_screen_name.lstrip("@").strip().lower()
    val = str(raw_name or "").strip()
    if not val or val.lstrip("@").strip().lower() == clean_target:
        return target_screen_name.lstrip("@").strip() if fallback_to_id else ""
    
    if val.startswith("@") and not any(sep in val for sep in ["｜", "|", "【", "◆", "★", "■", "／", "/"]):
        val = val.lstrip("@").strip()

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
    - {name}, {名前}, {アカウント名}: 自然な表示名（例: "限界OLちゃん", "山田太郎", "田中"）
    - {full_name}, {表示名}: 完全な表示名
    - {id}, {ユーザー名}: ユーザーID（@なし）
    - {mention}: メンション（@あり）
    - preserve_unresolved: Trueの場合、display_nameが未確定なら{name}等を置換せずに保持
    """
    if not template:
        return ""
    clean_id = screen_name.lstrip("@").strip()
    clean_name = extract_display_name_from_text(display_name, clean_id, fallback_to_id=not preserve_unresolved)
    
    full_disp = str(display_name or "").strip()
    if full_disp.lstrip("@").lower() == clean_id.lower():
        full_disp = "" if preserve_unresolved else clean_id

    res = template
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

    res = re.sub(r'さんさん(?=[^w]|$)', 'さん', res)
    res = re.sub(r'様様(?=[^w]|$)', '様', res)
    return res

class XBrowserController:
    def __init__(self, headless: bool = False, user_data_dir: str = "", base_dir: str = "", block_media: bool = True, preferred_channel: str = ""):
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.block_media = block_media
        self.preferred_channel = preferred_channel
        self.active_channel = None
        self.current_account_id = ""
        self.current_passcode = ""
        self.playwright = None
        self.browser = None
        self.context = None
        self.page: Page = None

    def _cleanup_profile_locks(self):
        """プロファイルフォルダ内の残存ロックファイルをクリーンアップ (Windows lock/EBUSY対策)"""
        if not self.user_data_dir or not os.path.isdir(self.user_data_dir):
            return
        lock_names = ["SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"]
        for name in lock_names:
            p = os.path.join(self.user_data_dir, name)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    def _terminate_zombie_browsers(self):
        """同一プロファイルを掴んだままバックグラウンドで死んでいるchrome/msedgeゾンビプロセスを解放"""
        if sys.platform != "win32" or not self.user_data_dir:
            return
        prof_name = os.path.basename(os.path.normpath(self.user_data_dir))
        if not prof_name or prof_name in ["", ".", ".."]:
            return
        try:
            # 該当プロファイルフォルダ名を含むプロセスのみピンポイントで解放（ユーザー自身の個人ブラウザには一切干渉しない）
            ps_cmd = f'Get-CimInstance Win32_Process -Filter "Name=\'chrome.exe\' OR Name=\'msedge.exe\'" | Where-Object {{ $_.CommandLine -like \'*{prof_name}*\' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}'
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd], capture_output=True, timeout=3)
        except Exception:
            pass

    def _get_launch_candidates(self) -> list:
        """
        起動候補ブラウザを優先度順に決定:
        Windows Defenderなどのセキュリティソフトが AppData 以下の ms-playwright/chrome.exe を
        「spawn UNKNOWN」でブロックすることがあるため、
        PCにインストール済みの正規署名ブラウザ (Google Chrome / Microsoft Edge) を最優先し、
        万一失敗した場合でも自動でフォールバックして確実にウィンドウを開く。
        """
        candidates = []
        if self.preferred_channel:
            pref = self.preferred_channel.strip().lower()
            if pref in ["chrome", "google-chrome", "googlechrome"]:
                candidates.append("chrome")
            elif pref in ["edge", "msedge"]:
                candidates.append("msedge")
            elif pref in ["chromium", "playwright"]:
                candidates.append(None)
            else:
                candidates.append(pref)

        # 1. Google Chrome (PCにインストールされている場合、最も安定・高速・BAN耐性高)
        chrome_paths = [
            r"C:Program FilesGoogleChromeApplicationchrome.exe",
            r"C:Program Files (x86)GoogleChromeApplicationchrome.exe",
            os.path.expandvars(r"%LocalAppData%GoogleChromeApplicationchrome.exe"),
            os.path.expandvars(r"%ProgramFiles%GoogleChromeApplicationchrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%GoogleChromeApplicationchrome.exe"),
        ]
        if any(os.path.isfile(p) for p in chrome_paths):
            if "chrome" not in candidates:
                candidates.append("chrome")

        # 2. Microsoft Edge (Windows 10/11標準搭載。Microsoft署名済みでセキュリティソフトに絶対ブロックされない)
        edge_paths = [
            r"C:Program Files (x86)MicrosoftEdgeApplicationmsedge.exe",
            r"C:Program FilesMicrosoftEdgeApplicationmsedge.exe",
            os.path.expandvars(r"%ProgramFiles(x86)%MicrosoftEdgeApplicationmsedge.exe"),
            os.path.expandvars(r"%ProgramFiles%MicrosoftEdgeApplicationmsedge.exe"),
        ]
        if any(os.path.isfile(p) for p in edge_paths):
            if "msedge" not in candidates:
                candidates.append("msedge")

        # 3. 常に Chrome と Edge を候補に保持 (Playwrightがレジストリ等から検知できるため)
        if "chrome" not in candidates:
            candidates.append("chrome")
        if "msedge" not in candidates:
            candidates.append("msedge")

        # 4. Playwright 同梱 Chromium (最後尾)
        if None not in candidates:
            candidates.append(None)

        return candidates

    def start(self, user_data_dir: str = ""):
        """ブラウザを単一タブモードで起動（アカウント別の永続Cookie/IndexedDB暗号化鍵キャッシュ対応）"""
        if user_data_dir:
            self.user_data_dir = user_data_dir
        if not self.playwright:
            self.playwright = sync_playwright().start()
        
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-default-browser-check",
            "--disable-infobars"
        ]
        
        launch_candidates = self._get_launch_candidates()
        self.context = None
        self.browser = None
        self.page = None
        last_error = None

        if self.user_data_dir:
            os.makedirs(self.user_data_dir, exist_ok=True)
            self._terminate_zombie_browsers()
            self._cleanup_profile_locks()

            for ch in launch_candidates:
                ch_label = "Google Chrome (channel='chrome')" if ch == "chrome" else (
                    "Microsoft Edge (channel='msedge')" if ch == "msedge" else "Playwright同梱 Chromium"
                )
                try:
                    self._cleanup_profile_locks()
                    launch_kwargs = {
                        "user_data_dir": self.user_data_dir,
                        "headless": self.headless,
                        "args": args,
                        "viewport": {"width": 1280, "height": 900},
                        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    }
                    if ch:
                        launch_kwargs["channel"] = ch
                    
                    print(f"🌐 ブラウザ [{ch_label}] を起動試行中...", flush=True)
                    self.context = self.playwright.chromium.launch_persistent_context(**launch_kwargs)
                    self.active_channel = ch
                    print(f"✅ ブラウザ [{ch_label}] の起動に成功しました！（ウィンドウ表示）", flush=True)
                    break
                except Exception as e:
                    last_error = e
                    err_brief = str(e).splitlines()[0] if str(e) else "unknown"
                    print(f"   ⚠️ [{ch_label}] 起動スキップ: {err_brief[:100]}。次の候補ブラウザへ自動切替...", flush=True)
                    try:
                        if self.playwright is None:
                            self.playwright = sync_playwright().start()
                    except Exception:
                        pass
                    continue

            if self.context is None:
                raise RuntimeError(
                    f"すべてのブラウザ候補で起動に失敗しました (最後のエラー: {last_error})。\n"
                    "【原因と対策】\n"
                    "1. Windows Defender等のセキュリティソフトが AppData 実行をブロックした可能性があります。\n"
                    "2. PCにGoogle Chrome または Microsoft Edge があることを確認してください。\n"
                    "3. ツール付属の『【修復】ブラウザ起動エラー解消.bat』を実行してブラウザ環境を修復してください。"
                )

            # storage_state.json が存在する場合はCookieを追加して完全復元
            state_file = os.path.join(self.user_data_dir, "storage_state.json")
            if os.path.exists(state_file) and os.path.getsize(state_file) > 10:
                try:
                    import json
                    with open(state_file, "r", encoding="utf-8") as f:
                        state_data = json.load(f)
                    cookies = state_data.get("cookies", [])
                    if cookies:
                        self.context.add_cookies(cookies)
                except Exception:
                    pass
            self.browser = None
            if len(self.context.pages) > 0:
                self.page = self.context.pages[0]
            else:
                self.page = self.context.new_page()
        else:
            for ch in launch_candidates:
                ch_label = "Google Chrome (channel='chrome')" if ch == "chrome" else (
                    "Microsoft Edge (channel='msedge')" if ch == "msedge" else "Playwright同梱 Chromium"
                )
                try:
                    launch_kwargs = {
                        "headless": self.headless,
                        "args": args,
                    }
                    if ch:
                        launch_kwargs["channel"] = ch
                    self.browser = self.playwright.chromium.launch(**launch_kwargs)
                    self.active_channel = ch
                    break
                except Exception as e:
                    last_error = e
                    continue

            if self.browser is None:
                raise RuntimeError(f"ブラウザの起動に失敗しました: {last_error}")

            self.context = self.browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            self.page = self.context.new_page()

        # 2. メインページ生成完了後にのみ、別タブ・ポップアップ抑止リスナーを登録
        self.context.on("page", self._on_new_page)

        # ★ 提案2: 画像・動画（リール等）・メディア読み込みブロック機能
        # タイムラインやリールの重い画像・動画通信を遮断し、メモリ消費量50〜70%カット＆CPU低負荷化
        if self.block_media:
            def _block_media_route(route):
                try:
                    req = route.request
                    res_type = req.resource_type
                    url_lower = req.url.lower()

                    # 1. リソースタイプによる遮断 (image, media, font)
                    if res_type in ["image", "media", "font", "imageset"]:
                        return route.abort()

                    # 2. 拡張子による動画・画像・フォントの遮断 (API/JSON通信は通過)
                    media_exts = [
                        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".ico",
                        ".mp4", ".m3u8", ".ts", ".webm", ".mov", ".avi",
                        ".woff", ".woff2", ".ttf", ".otf"
                    ]
                    if any(ext in url_lower for ext in media_exts):
                        if not any(k in url_lower for k in ["/i/api/", "/api/", ".json", "graphql"]):
                            return route.abort()

                    # 3. X (Twitter) 動画・画像配信CDNの遮断
                    media_domains = [
                        "video.twimg.com",
                        "pbs.twimg.com/media",
                        "pbs.twimg.com/tweet_video",
                        "pbs.twimg.com/amplify_video",
                        "pbs.twimg.com/ext_tw_video",
                        "ton.twitter.com"
                    ]
                    if any(dom in url_lower for dom in media_domains):
                        return route.abort()

                    return route.continue_()
                except Exception:
                    try:
                        return route.continue_()
                    except Exception:
                        pass

            try:
                self.context.route("**/*", _block_media_route)
            except Exception:
                pass

    def save_storage_state(self):
        """ブラウザのログインセッション・Cookie・キャッシュを永続化ファイルに確実に同期保存"""
        if self.context and self.user_data_dir:
            try:
                state_file = os.path.join(self.user_data_dir, "storage_state.json")
                self.context.storage_state(path=state_file)
            except Exception:
                pass

    def _on_new_page(self, new_page: Page):
        """別タブやポップアップが開こうとしたら閉じる"""
        try:
            # メインページが稼働中で、かつ新しく開いたページがメインページと異なる場合のみ閉じる
            if self.page is not None and new_page != self.page:
                try:
                    new_page.close()
                except Exception:
                    pass
        except Exception:
            pass

    def ensure_page(self) -> Page:
        """有効なメインページを取得（万一閉じられた、またはブラウザ終了中の場合は自動復旧・再起動）"""
        try:
            if self.context is None or self.playwright is None:
                self.start(self.user_data_dir)
            if self.page is None or self.page.is_closed():
                alive_pages = [p for p in self.context.pages if not p.is_closed()]
                if alive_pages:
                    self.page = alive_pages[0]
                else:
                    self.page = self.context.new_page()
        except Exception:
            try:
                self.start(self.user_data_dir)
            except Exception:
                pass
        return self.page

    def is_passcode_prompt_active(self) -> bool:
        """パスコード入力画面・暗号化解除モーダルが現在表示されているかを厳密に判定"""
        page = self.ensure_page()
        try:
            cur_url = page.url.lower()
            # 検索結果画面、タイムラインなどは絶対にパスコード画面ではない
            if "/search" in cur_url or "/explore" in cur_url:
                return False

            # 専用PIN画面 (/i/chat/pin, /pin/recovery)
            if "/chat/pin" in cur_url or "/pin/recovery" in cur_url or "/i/chat/pin" in cur_url:
                return True

            # モーダルダイアログ (div[role="dialog"], div[aria-modal="true"])
            # ※ primaryColumn や main への広域マッチは誤爆（検索バーやツイート本文検知）を引き起こすため完全廃止
            dialogs = page.locator('div[role="dialog"], div[aria-modal="true"]')
            if dialogs.count() > 0:
                for i in range(min(dialogs.count(), 3)):
                    try:
                        d = dialogs.nth(i)
                        if not d.is_visible():
                            continue
                        d_text = d.inner_text()
                        # 「新しいメッセージ」モーダル等は除外
                        if any(ex in d_text for ex in ["新しいメッセージ", "新しいメッセージを送信", "新規メッセージ", "名前またはユーザー名を検索", "グループを作成", "Search people"]):
                            continue
                        pin_keywords = [
                            "パスコードを入力", "暗号化の鍵をバックアップ", "数字4桁のパスコード",
                            "パスコードを忘れた場合", "以前のメッセージを復号", "暗号化キーを回復するためのパスコード",
                            "暗号化されたダイレクトメッセージ",
                            "Enter passcode", "Enter your passcode", "Forgot passcode", "4-digit passcode"
                        ]
                        if any(kw in d_text for kw in pin_keywords):
                            return True
                    except Exception:
                        pass
        except Exception:
            pass
        return False

    def handle_passcode_prompt(self, passcode: str = "", account_id: str = "", context_label: str = "認証/暗号化") -> bool:
        """
        X上の各種パスコード・暗号化DM PIN・2要素認証コード・確認コードの自動検知＆高精度入力
        - パスコードモーダル・PIN画面内部のみにスコープを限定（★検索バー input[data-testid="SearchBox_Search_Input"] を絶対に触らない）
        - 4つの円形セルUIに対して第1円フォーカス＋物理キーボード打鍵＋Reactイベント結合で100%確実に入力
        - 失敗時もURLスタックを防止し、自動で画面を元に戻す
        """
        page = self.ensure_page()
        try:
            if passcode:
                p_str = str(passcode).strip().strip("'").strip('"')
                if p_str.endswith(".0") and p_str[:-2].isdigit():
                    passcode = p_str[:-2]
                else:
                    passcode = p_str

            # 1. 身元確認 (電話番号/メールアドレス/ユーザー名入力要求)
            id_challenge = page.locator('input[data-testid="ocfEnterTextTextInput"], input[name="text"]')
            if id_challenge.count() > 0:
                body_text = ""
                try:
                    body_text = page.locator("body").inner_text()
                except Exception:
                    pass
                if any(w in body_text for w in ["電話番号", "ユーザー名", "メールアドレス", "phone", "username", "email", "異常な"]):
                    print(f"   🔍 [{context_label}] 身元確認画面を検出。アカウントID (@{account_id}) を自動入力します...", flush=True)
                    self.human_type(id_challenge.first, account_id)
                    time.sleep(0.5)
                    page.keyboard.press("Enter")
                    time.sleep(3)

            # 2. パスコード・暗号化PIN要求が表示されているか確認
            if not self.is_passcode_prompt_active():
                return False

            if not passcode:
                # 保存済みパスコード一覧からの探索 (大文字小文字・@有無を柔軟マッチ)
                try:
                    from db_manager import get_saved_passcodes
                    saved_p = get_saved_passcodes(self.base_dir)
                    clean_acc = normalize_screen_name(account_id).lower()
                    for k, v in saved_p.items():
                        if normalize_screen_name(k).lower() == clean_acc or str(k).strip().lstrip("@").lower() == clean_acc:
                            passcode = str(v).strip()
                            self.current_passcode = passcode
                            break
                    if not passcode and len(saved_p) == 1:
                        passcode = str(list(saved_p.values())[0]).strip()
                        self.current_passcode = passcode
                except Exception:
                    pass

            # パスコードが未設定の場合の手動入力待機（※フリーズ防止のためポップアップは出さず、ブラウザ上で10秒監視）
            if not passcode:
                print(f"   ⚠️ [{context_label}] パスコード入力画面を検出しましたが、パスコードが未登録です。", flush=True)
                print(f"   👉 ブラウザ画面上で4桁のパスコードを手動入力してください（10秒間監視します）...", flush=True)
                for wait_i in range(10):
                    time.sleep(1.0)
                    try:
                        captured = page.evaluate('''() => {
                            const modal = document.querySelector('div[role="dialog"], div[aria-modal="true"], main[role="main"]') || document.body;
                            const inps = Array.from(modal.querySelectorAll('input')).filter(el => {
                                if (el.matches('[data-testid*="Search" i], [placeholder*="検索" i], [placeholder*="Search" i]')) return false;
                                const t = (el.type || 'text').toLowerCase();
                                return !['checkbox', 'radio', 'submit', 'button', 'file', 'hidden'].includes(t);
                            });
                            return inps.map(i => i.value).join('').trim();
                        }''')
                        if captured and len(captured) >= 4 and captured.isdigit():
                            passcode = captured[:4]
                            self.current_passcode = passcode
                    except Exception:
                        pass

                    if not self.is_passcode_prompt_active():
                        print(f"   🎉 [{context_label}] 手動入力によるパスコード解除を確認しました！", flush=True)
                        if passcode and account_id:
                            try:
                                from db_manager import save_account_passcode, update_excel_passcode
                                save_account_passcode(self.base_dir, account_id, passcode)
                                update_excel_passcode(self.base_dir, account_id, passcode)
                                print(f"   💾 [{context_label}] 手動入力されたパスコード ({passcode}) を永続キャッシュ保存しました！次回以降は完全自動になります。", flush=True)
                            except Exception:
                                pass
                        self.save_storage_state()
                        return True

                print(f"   ❌ [{context_label}] パスコード解除がタイムアウトしました。スタック防止のため画面を戻します。", flush=True)
                try:
                    page.goto("https://x.com/home", wait_until="domcontentloaded")
                    time.sleep(1.5)
                except Exception:
                    pass
                return False

            clean_pin = str(passcode).strip()[:4]
            masked = "*" * len(clean_pin)
            print(f"   🔐 [{context_label}] パスコード画面を検出！登録パスコード ({masked} / {len(clean_pin)}桁) の自動解除を実行します...", flush=True)

            for attempt in range(1, 4):
                if attempt > 1:
                    print(f"   🔄 [{context_label}] パスコード入力再試行 ({attempt}/3回目)...", flush=True)
                    time.sleep(1.0)

                # 1. ブラウザウィンドウの最前面化
                try:
                    page.bring_to_front()
                except Exception:
                    pass

                # 2. 検索バーにフォーカスが当たっていた場合は即座に解除（検索誤爆を100%防止）
                try:
                    page.evaluate('''() => {
                        const act = document.activeElement;
                        if (act && (act.matches('input[data-testid*="Search" i]') || act.matches('input[placeholder*="検索" i]') || act.matches('input[placeholder*="Search" i]'))) {
                            act.blur();
                        }
                    }''')
                except Exception:
                    pass

                # 3. パスコードモーダル（または全画面PINコンテナ）の限定特定
                # ※ ページ全体の input (特に検索バー input[data-testid="SearchBox_Search_Input"]) は絶対に触らない
                modal_ready = False
                try:
                    modal_target = page.evaluate('''() => {
                        // A. パスコードモーダルダイアログ
                        const dialogs = Array.from(document.querySelectorAll('div[role="dialog"], div[aria-modal="true"]'));
                        let modal = dialogs.find(d => {
                            const txt = d.innerText || '';
                            return txt.includes('パスコード') || txt.includes('暗号化キー') || txt.includes('passcode') || txt.includes('復号');
                        });
                        
                        // B. 全画面 /i/chat/pin 画面
                        if (!modal && window.location.href.includes('/pin')) {
                            modal = document.querySelector('main[role="main"]') || document.body;
                        }

                        if (!modal) {
                            return { found: false };
                        }

                        // モーダル内の円枠 (border-radius: 50%) を特定
                        const allNodes = Array.from(modal.querySelectorAll('div, span, button, input'));
                        const roundNodes = allNodes.filter(el => {
                            const r = el.getBoundingClientRect();
                            if (r.width < 15 || r.width > 80 || r.height < 15 || r.height > 80) return false;
                            const s = window.getComputedStyle(el);
                            const br = s.borderRadius;
                            const isRound = br.includes('50%') || br.includes('9999px') || parseInt(br, 10) >= 15;
                            return isRound && Math.abs(r.width - r.height) < 12;
                        });

                        if (roundNodes.length >= 4) {
                            roundNodes.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                            const r0 = roundNodes[0].getBoundingClientRect();
                            return { 
                                found: true, 
                                method: 'circles', 
                                x: r0.left + r0.width / 2, 
                                y: r0.top + r0.height / 2 
                            };
                        }

                        // モーダル内の「暗号化キーを回復」と「パスコードを忘れた場合」の中間位置
                        const sub = Array.from(modal.querySelectorAll('*')).find(el => el.textContent && el.textContent.includes('暗号化キーを回復'));
                        const forgot = Array.from(modal.querySelectorAll('*')).find(el => el.textContent && el.textContent.includes('パスコードを忘れた場合'));
                        if (sub && forgot) {
                            const sr = sub.getBoundingClientRect();
                            const fr = forgot.getBoundingClientRect();
                            const cy = (sr.bottom + fr.top) / 2;
                            const cx = (sr.left + sr.right) / 2 - 90; // 第1円の位置
                            return { found: true, method: 'midpoint', x: cx, y: cy };
                        }

                        // モーダル内部限定の input (検索バーは除外)
                        const modalInps = Array.from(modal.querySelectorAll('input')).filter(el => {
                            if (el.matches('[data-testid*="Search" i], [placeholder*="検索" i], [placeholder*="Search" i]')) return false;
                            const t = (el.type || 'text').toLowerCase();
                            return !['checkbox', 'radio', 'submit', 'button', 'file', 'hidden'].includes(t);
                        });
                        if (modalInps.length > 0) {
                            const ir = modalInps[0].getBoundingClientRect();
                            return { found: true, method: 'modal_input', x: ir.left + ir.width / 2, y: ir.top + ir.height / 2 };
                        }

                        return { found: false };
                    }''')

                    if modal_target and modal_target.get("found"):
                        modal_ready = True
                        page.mouse.click(modal_target["x"], modal_target["y"])
                        time.sleep(0.2)
                except Exception:
                    pass

                # 4. モーダル内部限定の input 要素があれば直接入力（※検索バーは絶対に除外）
                try:
                    modal_locator = page.locator('div[role="dialog"]:has-text("パスコード"), div[aria-modal="true"]:has-text("パスコード"), main[role="main"]')
                    if modal_locator.count() > 0:
                        m_scope = modal_locator.first
                        m_inps = m_scope.locator('input:not([data-testid*="Search"]):not([placeholder*="検索"]):not([placeholder*="Search"])').all()
                        if len(m_inps) >= 4:
                            for idx, d in enumerate(clean_pin):
                                try:
                                    m_inps[idx].click(force=True)
                                    m_inps[idx].fill(d)
                                    time.sleep(0.08)
                                except Exception:
                                    pass
                        elif len(m_inps) > 0:
                            try:
                                m_inps[0].click(force=True)
                                m_inps[0].fill(clean_pin)
                                time.sleep(0.1)
                            except Exception:
                                pass
                except Exception:
                    pass

                # 5. バックスペースでクリアして物理キー打鍵 (1桁ずつ確実に送信)
                for _ in range(6):
                    page.keyboard.press("Backspace")
                    time.sleep(0.04)

                for digit in clean_pin:
                    page.keyboard.press(digit)
                    time.sleep(0.12)

                # 6. React内部ディスパッチ（※イベント対象はモーダル限定）
                try:
                    page.evaluate('''(pin) => {
                        const dialogs = Array.from(document.querySelectorAll('div[role="dialog"], div[aria-modal="true"]'));
                        let modal = dialogs.find(d => {
                            const txt = d.innerText || '';
                            return txt.includes('パスコード') || txt.includes('暗号化キー') || txt.includes('passcode') || txt.includes('復号');
                        });
                        if (!modal && window.location.href.includes('/pin')) {
                            modal = document.querySelector('main[role="main"]');
                        }
                        if (!modal) return;

                        // モーダル内部の input にプロトタイプセッター適用
                        const modalInps = Array.from(modal.querySelectorAll('input')).filter(el => {
                            if (el.matches('[data-testid*="Search" i], [placeholder*="検索" i], [placeholder*="Search" i]')) return false;
                            const t = (el.type || 'text').toLowerCase();
                            return !['checkbox', 'radio', 'submit', 'button', 'file', 'hidden'].includes(t);
                        });

                        const setNativeValue = (element, val) => {
                            try {
                                const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                                const prototype = Object.getPrototypeOf(element);
                                const protoSetter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
                                if (protoSetter && valueSetter !== protoSetter) {
                                    protoSetter.call(element, val);
                                } else if (valueSetter) {
                                    valueSetter.call(element, val);
                                } else {
                                    element.value = val;
                                }
                                element.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
                                element.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
                            } catch(e) {}
                        };

                        if (modalInps.length >= 4) {
                            for (let i = 0; i < 4; i++) {
                                modalInps[i].focus();
                                setNativeValue(modalInps[i], pin[i] || '');
                            }
                        } else if (modalInps.length > 0) {
                            modalInps[0].focus();
                            setNativeValue(modalInps[0], pin);
                        }

                        // モーダル内部の要素のみにキーイベントをディスパッチ
                        const target = modal.contains(document.activeElement) ? document.activeElement : modal;
                        for (let i = 0; i < pin.length; i++) {
                            const ch = pin[i];
                            const code = 'Digit' + ch;
                            const keyCode = 48 + parseInt(ch, 10);
                            const evOpts = { key: ch, code: code, keyCode: keyCode, which: keyCode, bubbles: true, cancelable: true };
                            target.dispatchEvent(new KeyboardEvent('keydown', evOpts));
                            target.dispatchEvent(new KeyboardEvent('keypress', evOpts));
                            target.dispatchEvent(new InputEvent('beforeinput', { data: ch, inputType: 'insertText', bubbles: true }));
                            target.dispatchEvent(new InputEvent('input', { data: ch, inputType: 'insertText', bubbles: true }));
                            target.dispatchEvent(new KeyboardEvent('keyup', evOpts));
                        }
                    }''', clean_pin)
                except Exception:
                    pass

                # 7. モーダル内部の確認ボタンがあればクリック (※盲目的なEnter打鍵は検索実行を招くため行わない)
                time.sleep(0.4)
                try:
                    confirm_btn = page.locator('div[role="dialog"] button:has-text("次へ"), div[role="dialog"] button:has-text("確認"), div[role="dialog"] button:has-text("ロック解除"), div[role="dialog"] button:has-text("Unlock"), [data-testid="confirmationSheetConfirm"]')
                    if confirm_btn.count() > 0 and confirm_btn.first.is_visible():
                        confirm_btn.first.click()
                except Exception:
                    pass

                # 8. 解除完了の厳格な確認待機（最大6秒ポーリング）
                start_check = time.time()
                unlocked_confirmed = False
                while time.time() - start_check < 6.0:
                    time.sleep(0.6)
                    if not self.is_passcode_prompt_active():
                        time.sleep(1.2)
                        if not self.is_passcode_prompt_active():
                            unlocked_confirmed = True
                            break

                if unlocked_confirmed:
                    print(f"   🎉 [{context_label}] パスコード認証が成功し、暗号化解除を確認しました！", flush=True)
                    if passcode and account_id:
                        try:
                            from db_manager import save_account_passcode, update_excel_passcode
                            save_account_passcode(self.base_dir, account_id, passcode)
                            update_excel_passcode(self.base_dir, account_id, passcode)
                            print(f"   💾 [{context_label}] パスコードを永続キャッシュ保存しました。", flush=True)
                        except Exception:
                            pass
                    self.save_storage_state()
                    return True
                else:
                    print(f"   ⚠️ [{context_label}] パスコード画面がまだ表示されています（試行 {attempt}/3）。", flush=True)

            print(f"   ❌ [{context_label}] パスコードの自動解除に失敗しました。スタック防止のため安全に画面を戻します。", flush=True)
            try:
                page.goto("https://x.com/home", wait_until="domcontentloaded")
                time.sleep(1.5)
            except Exception:
                pass
            return False
        except Exception as ex:
            print(f"   ⚠️ [{context_label}] パスコード処理中にエラー: {ex}", flush=True)
        return False

    def _find_login_input(self, page: Page, is_password: bool = False):
        """
        X (Twitter) のログイン画面・オンボーディング画面の入力欄を高精度に自動検出
        /i/flow/login および /i/jf/onboarding/web?mode=login の両方に完全対応
        """
        if is_password:
            selectors = [
                'input[type="password"]',
                'input[name="password"]',
                'input[autocomplete="current-password"]',
                'input[autocomplete="password"]',
                'label:has-text("パスワード") input',
                'label:has-text("Password" i) input',
                'input[placeholder*="パスワード"]',
                'input[placeholder*="Password" i]',
                'div[role="dialog"] input[type="password"]',
            ]
        else:
            selectors = [
                'label:has-text("メールアドレス") input',
                'label:has-text("ユーザー名") input',
                'label:has-text("username" i) input',
                'label:has-text("email" i) input',
                'label:has-text("phone" i) input',
                'input[autocomplete="username"]',
                'input[name="text"]',
                'input[name="username"]',
                'input[name="identifier"]',
                'input[placeholder*="ユーザー名"]',
                'input[placeholder*="メール"]',
                'input[placeholder*="電話"]',
                'input[placeholder*="username" i]',
                'input[placeholder*="email" i]',
                'input[placeholder*="phone" i]',
                'div[role="dialog"] input[type="text"]',
                'div[role="dialog"] input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])',
                'main input[type="text"]',
                'input[type="text"]',
                'input[type="email"]',
                'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([data-testid*="Search"])'
            ]

        for sel in selectors:
            try:
                loc = page.locator(sel)
                cnt = loc.count()
                for i in range(cnt):
                    item = loc.nth(i)
                    if item.is_visible():
                        return item
            except Exception:
                continue

        # JavaScriptによるDOM走査フォールバック
        try:
            found_sel = page.evaluate('''(isPw) => {
                const inps = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])')).filter(el => {
                    if (el.matches('[data-testid*="Search" i], [placeholder*="検索" i], [placeholder*="Search" i]')) return false;
                    return el.offsetParent !== null;
                });
                for (let i = 0; i < inps.length; i++) {
                    const el = inps[i];
                    const t = (el.type || 'text').toLowerCase();
                    if (isPw && (t === 'password' || el.name === 'password')) {
                        el.setAttribute('data-target-input', 'password');
                        return 'input[data-target-input="password"]';
                    }
                    if (!isPw && t !== 'password') {
                        el.setAttribute('data-target-input', 'username');
                        return 'input[data-target-input="username"]';
                    }
                }
                return null;
            }''', is_password)
            if found_sel:
                return page.locator(found_sel).first
        except Exception:
            pass

        return None

    def _safe_fill_input(self, locator, value: str):
        """
        Reactの制御されたコンポーネント (Controlled Input) に値を確実に反映させ、
        プロトタイプセッターおよび input / change イベントを発火して
        「続ける」「ログイン」ボタンの非活性(disabled)状態を即座に解除する。
        """
        page = self.ensure_page()
        try:
            locator.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass

        # 1. クリック＆フォーカス
        try:
            locator.click(force=True, timeout=3000)
            locator.focus()
            time.sleep(0.15)
        except Exception:
            pass

        # 2. 全選択してクリア
        try:
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            time.sleep(0.05)
        except Exception:
            pass

        # 3. Playwrightネイティブ入力
        try:
            locator.fill(value)
            time.sleep(0.1)
        except Exception:
            pass

        # 4. React内部状態更新 (Prototype value setter + Events)
        try:
            locator.evaluate('''(el, val) => {
                el.focus();
                const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                const prototype = Object.getPrototypeOf(el);
                const protoSetter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
                if (protoSetter && valueSetter !== protoSetter) {
                    protoSetter.call(el, val);
                } else if (valueSetter) {
                    valueSetter.call(el, val);
                } else {
                    el.value = val;
                }
                el.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
                el.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
            }''', value)
        except Exception:
            pass

        # 5. もし値が反映されていなければ物理キーボードで直接タイピング
        try:
            current_val = locator.input_value()
            if not current_val:
                page.keyboard.type(value, delay=35)
                time.sleep(0.1)
        except Exception:
            pass

        # 6. キーボード打鍵イベントも補助発火
        try:
            page.keyboard.press("End")
        except Exception:
            pass
        time.sleep(0.2)

    def _click_next_or_submit(self, page: Page, is_login_submit: bool = False) -> bool:
        """
        「続ける」「次へ」「ログイン」「Log in」ボタンを検索してクリック。
        見つからない場合は Enter キーを押下。
        """
        if is_login_submit:
            selectors = [
                'button[data-testid="LoginForm_Login_Button"]',
                'button:has-text("ログイン")',
                'button:has-text("Log in")',
                'button:has-text("続ける")',
                'button:has-text("Continue")',
                'button:has-text("次へ")',
                'button:has-text("Next")',
                'div[role="button"]:has-text("ログイン")',
                'div[role="button"]:has-text("Log in")',
                'div[role="button"]:has-text("続ける")',
                'div[role="button"]:has-text("Continue")',
                'button[type="submit"]',
            ]
        else:
            selectors = [
                'button:has-text("続ける")',
                'button:has-text("Continue")',
                'button:has-text("次へ")',
                'button:has-text("Next")',
                'div[role="button"]:has-text("続ける")',
                'div[role="button"]:has-text("Continue")',
                'div[role="button"]:has-text("次へ")',
                'div[role="button"]:has-text("Next")',
                'button[type="submit"]',
                'button[data-testid*="next" i]',
                'div[data-testid*="next" i]'
            ]

        for sel in selectors:
            try:
                btn = page.locator(sel)
                cnt = btn.count()
                for i in range(cnt):
                    item = btn.nth(i)
                    if item.is_visible():
                        try:
                            item.click(timeout=3000)
                            return True
                        except Exception:
                            try:
                                item.click(force=True, timeout=3000)
                                return True
                            except Exception:
                                pass
            except Exception:
                continue

        # JSによるクリック実行 (disabled/aria-disabled を強制解除してクリック)
        try:
            clicked = page.evaluate('''(isLogin) => {
                const candidates = isLogin ? ['ログイン', 'log in', '続ける', 'continue'] : ['続ける', 'continue', '次へ', 'next'];
                const btns = Array.from(document.querySelectorAll('button, div[role="button"], input[type="submit"]')).filter(b => b.offsetParent !== null);
                for (const b of btns) {
                    const txt = (b.innerText || b.value || '').trim().toLowerCase();
                    if (candidates.some(c => txt.includes(c))) {
                        b.removeAttribute('disabled');
                        b.setAttribute('aria-disabled', 'false');
                        b.click();
                        return true;
                    }
                }
                return false;
            }''', is_login_submit)
            if clicked:
                return True
        except Exception:
            pass

        # セレクタで見つからない場合は Enter キー
        try:
            page.keyboard.press("Enter")
            return True
        except Exception:
            pass
        return False

    def login(self, account_id: str, auth_token: str = "", password: str = "", passcode: str = "") -> bool:
        """
        プロファイル・セッションID・ID/PWハイブリッドログイン
        1. 永続プロファイル内の既存ログインセッションを確認 (すでにログイン中なら即座に継続)
        2. セッションID (auth_token) がある場合: まずCookieログインを試行
           - 成功時: プロファイルにセッション同期して即完了
           - 失敗時 (無効・期限切れ・強制ログアウト等): 自動的にID・PWログインへフォールバック
        3. セッションID未設定時、またはセッションIDでログインできない場合:
           ID・パスワードによる自動フォームログインを実行し、プロファイルに永続保存
           - /i/flow/login および /i/jf/onboarding/web?mode=login の両UIに対応
           - 「メールアドレスまたはユーザー名」入力欄への確実な自動入力と「続ける」ボタン押下
           - パスワード入力欄への自動入力と「ログイン」ボタン押下
        4. 2FA等の追加確認時は手動入力待ち受け (最大60秒ポーリング検知)
        """
        self.current_account_id = account_id
        if passcode:
            self.current_passcode = str(passcode).strip()
        elif not self.current_passcode:
            try:
                from db_manager import get_saved_passcodes
                saved_p = get_saved_passcodes(self.base_dir)
                clean_acc = normalize_screen_name(account_id).lower()
                for k, v in saved_p.items():
                    if normalize_screen_name(k).lower() == clean_acc or str(k).strip().lstrip("@").lower() == clean_acc:
                        self.current_passcode = str(v).strip()
                        break
            except Exception:
                pass
        passcode = passcode or self.current_passcode

        page = self.ensure_page()

        # -------------------------------------------------------------
        # 1. 永続プロファイル内の既存ログインセッション確認
        # -------------------------------------------------------------
        print(f"🔑 [@{account_id}] ブラウザプロファイルの既存ログイン状態を確認中...")
        try:
            page.goto("https://x.com/home", timeout=25000, wait_until="domcontentloaded")
            for _ in range(8):
                time.sleep(0.5)
                if self._is_logged_in():
                    print(f"✅ [@{account_id}] プロファイルの既存セッションを確認しました！（ログイン維持 / 再認証不要）")
                    self.save_storage_state()
                    return True
                cur_url = page.url.lower()
                if "/login" in cur_url or "/flow/login" in cur_url or "mode=login" in cur_url:
                    break
        except Exception:
            pass

        # -------------------------------------------------------------
        # 2. セッションID (auth_token) がある場合: まずCookie注入ログインを試行
        # -------------------------------------------------------------
        clean_token = auth_token.strip() if auth_token else ""
        if clean_token:
            print(f"🍪 [@{account_id}] セッションID (auth_token) によるログインを試行します...")
            try:
                self.context.add_cookies([
                    {
                        "name": "auth_token",
                        "value": clean_token,
                        "domain": ".x.com",
                        "path": "/",
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "Lax"
                    },
                    {
                        "name": "auth_token",
                        "value": clean_token,
                        "domain": ".twitter.com",
                        "path": "/",
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "Lax"
                    }
                ])
                page.goto("https://x.com/home", timeout=30000, wait_until="domcontentloaded")
                time.sleep(2.0)

                if self.is_passcode_prompt_active():
                    self.handle_passcode_prompt(passcode=passcode, account_id=account_id, context_label="Cookieログイン後認証")
                    time.sleep(2.0)

                if self._is_logged_in():
                    print(f"✅ [@{account_id}] セッションIDでのログインに成功しました！（プロファイルにセッション同期）")
                    self.save_storage_state()
                    return True
                else:
                    print(f"⚠️ [@{account_id}] セッションIDでのログインに失敗しました（失効または強制ログアウト検知）。")
            except Exception as e:
                print(f"⚠️ [@{account_id}] セッションID登録エラー: {e}")

        # -------------------------------------------------------------
        # 3. セッションIDがない場合、またはセッションIDでログインできない場合:
        #    ID・パスワードによる自動フォームログインを実行
        # -------------------------------------------------------------
        clean_pass = password.strip() if password else ""
        raw_acc_str = str(account_id or "").strip()
        if "@" in raw_acc_str and "." in raw_acc_str and not raw_acc_str.startswith("@"):
            target_username = raw_acc_str
        elif raw_acc_str.startswith("@"):
            target_username = raw_acc_str[1:].strip()
        else:
            target_username = raw_acc_str

        if not clean_token:
            print(f"ℹ️ [@{account_id}] セッションID (auth_token) が未設定のため、ID・パスワードで自動ログインします...")
        else:
            print(f"🔄 [@{account_id}] セッションIDでログインできないため、ID・パスワードによる自動ログインへ切り替えます...")

        # パスワードが渡されていない場合でも、保存済みキャッシュ等から探索
        if not clean_pass:
            try:
                from db_manager import get_saved_passcodes
                saved_p = get_saved_passcodes(self.base_dir)
                clean_acc = normalize_screen_name(account_id).lower()
                for k, v in saved_p.items():
                    if f"{clean_acc}_pw" == str(k).lower():
                        clean_pass = str(v).strip()
                        break
            except Exception:
                pass

        print(f"🔑 [@{account_id}] ID・パスワードによる自動フォームログインを開始します...")
        try:
            # ログインページへ移動 (既にログイン/オンボーディング画面にいる場合はそのまま利用)
            cur_url = page.url.lower()
            if not ("/login" in cur_url or "mode=login" in cur_url or "/i/flow/" in cur_url):
                page.goto("https://x.com/i/flow/login", timeout=35000, wait_until="domcontentloaded")
            time.sleep(2.0)

            # ユーザー名 / メールアドレス入力欄を検出 (最大15秒ポーリング)
            user_input = None
            for _ in range(30):
                user_input = self._find_login_input(page, is_password=False)
                if user_input:
                    break
                time.sleep(0.5)

            if user_input:
                print(f"   📝 ユーザー名/アカウントID ({target_username}) を入力中...")
                self._safe_fill_input(user_input, target_username)
                time.sleep(0.6)

                print("   👉 「続ける / 次へ」ボタンをクリックします...")
                self._click_next_or_submit(page, is_login_submit=False)
                time.sleep(2.5)
            else:
                print("   ℹ️ ユーザー名入力欄は既に通過しているか、直接パスワード入力画面です。")

            # 追加の身元確認要求 (電話番号/メールアドレス/アカウント名) が出た場合の自動入力
            time.sleep(1.0)
            pass_check = self._find_login_input(page, is_password=True)
            if not pass_check:
                challenge_input = self._find_login_input(page, is_password=False)
                if challenge_input:
                    print(f"   🔍 追加の身元確認画面を検出。アカウントID (@{account_id}) を自動入力します...")
                    self._safe_fill_input(challenge_input, target_username)
                    time.sleep(0.5)
                    self._click_next_or_submit(page, is_login_submit=False)
                    time.sleep(2.5)

            # パスワード入力欄を検出 (最大15秒ポーリング)
            pass_input = None
            for _ in range(30):
                pass_input = self._find_login_input(page, is_password=True)
                if pass_input:
                    break
                time.sleep(0.5)

            if pass_input:
                if clean_pass:
                    print(f"   🔑 パスワードを自動入力中...")
                    self._safe_fill_input(pass_input, clean_pass)
                    time.sleep(0.6)

                    print("   🚀 「ログイン」ボタンをクリックします...")
                    self._click_next_or_submit(page, is_login_submit=True)
                    time.sleep(3.0)
                else:
                    print(f"   ⚠️ アカウント情報.xlsx にパスワードが未設定です。ブラウザ画面でパスワードを手動入力してください...")
            else:
                print("   ℹ️ パスワード入力欄は検出されませんでした（既にログイン完了または追加認証待ち）。")

            # 2FA / パスコード / 認証コードチェック
            if self.is_passcode_prompt_active():
                self.handle_passcode_prompt(passcode=passcode, account_id=account_id, context_label="ログイン認証コード")
                time.sleep(2.5)

            # ログイン確認ポーリング (最大15秒)
            for _ in range(15):
                if self._is_logged_in():
                    print(f"🎉 [@{account_id}] ID・パスワードでのログインが成功しました！セッション情報をプロファイルに永続保存します。")
                    self.save_storage_state()
                    return True
                time.sleep(1.0)

        except Exception as e:
            print(f"   ⚠️ ID・パスワード フォームログイン試行エラー: {e}")

        # -------------------------------------------------------------
        # 4. 手動ログイン待ち受け救済 (ブラウザ画面上で手動ログインを待機)
        # -------------------------------------------------------------
        print(f"⏳ [@{account_id}] 画面上でログイン認証の完了を確認中（最大60秒待機）...")
        for wait_idx in range(30):
            time.sleep(2.0)
            if self._is_logged_in():
                print(f"🎉 [@{account_id}] ログインが確認できました！セッション情報を永続プロファイルに保存します。")
                self.save_storage_state()
                return True
            if wait_idx % 5 == 0 and wait_idx > 0:
                print(f"   ⏳ ログイン完了を待機中... ({wait_idx * 2}/60秒)")

        # 5. 失敗時のガイダンス
        print()
        print(f"❌ [@{account_id}] ログイン状態が確認できませんでした。")
        print("💡 【おすすめの解決方法】")
        print("   1. 『アカウント情報.xlsx』の「アカウントPW」列に正しいパスワードが記入されているか確認してください。")
        print(f"   2. または付属の『profile_launcher.bat』を実行し、プロファイル名に「{account_id}」を入力してブラウザ上で1度手動ログインを行ってください。")
        print("      （一度ログインすればブラウザプロファイルに永続保存され、次回以降パスワード入力も不要になります）")
        print()
        return False

    def _is_logged_in(self) -> bool:
        """ログイン状態の厳密判定 (偽陽性・偽陰性を完全防止)"""
        try:
            page = self.ensure_page()
            url = page.url.lower()

            # ログイン画面またはフロー中は未ログイン
            if "/login" in url or "/i/flow/" in url or "/logout" in url or "mode=login" in url or "/onboarding" in url:
                return False

            # 明確なログインボタンが表示されている場合は未ログイン
            try:
                if page.locator('a[data-testid="loginButton"], a[href="/login"]').count() > 0:
                    return False
            except Exception:
                pass

            # ログイン済み要素（サイドバー主要メニュー、DMリンク、アカウント切替ボタン、ポスト作成ボタン）
            try:
                if page.locator('a[data-testid="AppTabBar_DirectMessage_Link"], div[data-testid="SideNav_AccountSwitcher_Button"], button[data-testid="SideNav_NewTweet_Button"]').count() > 0:
                    return True
                if page.locator('nav[aria-label="主要メニュー"], nav[aria-label="Primary"]').count() > 0:
                    return True
            except Exception:
                pass

            # ホームURLかつリダイレクトされていない場合
            if "x.com/home" in url or "twitter.com/home" in url:
                return True
        except:
            pass
        return False

    def wait_for_profile_ready(self, target_screen_name: str, timeout: float = 20.0) -> bool:
        """
        プロフィールのReactハイドレーション・ヘッダーボタン（フォロー/DM等）の描画完了を確実に待機 (表示ラグ完全吸収)
        """
        page = self.ensure_page()
        clean_target = normalize_screen_name(target_screen_name)
        if not clean_target:
            return False
        profile_url = f"https://x.com/{clean_target}"
        
        current_url = page.url.lower().split("?")[0].rstrip("/")
        is_already_on_page = current_url == f"https://x.com/{clean_target.lower()}" or current_url.endswith(f"/{clean_target.lower()}")
        if not is_already_on_page:
            print(f"🌐 ユーザーページにアクセス: {profile_url}")
            try:
                page.goto(profile_url, timeout=35000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"⚠️ ページ遷移警告: {e}")
        
        print(f"   ⏳ プロフィール画面の描画完了を待機中 (@{clean_target} / 表示ラグ吸収)...")
        start_t = time.time()
        
        # 1. まず主要コンテナまたはUserNameが表示されるまで少し待機
        try:
            page.wait_for_selector('div[data-testid="primaryColumn"], div[data-testid="UserName"], div[role="main"]', timeout=8000)
        except Exception:
            pass

        while time.time() - start_t < timeout:
            # 0. エラー画面（問題が発生しました）の自動検知とページ内再試行
            if self.check_error_screen(page):
                print(f"   ⚠️ プロフィール画面（@{clean_target}）でエラー画面を検知。ページ内再試行を実行します...")
                self.recover_if_stuck(profile_url, "プロフィール画面読み込みエラー")
                page = self.ensure_page()
                start_t = time.time()
                time.sleep(2.0)
                continue

            # A. 存在しない / 凍結 / 制限アカウントの即時チェック
            try:
                empty_state = page.locator('[data-testid="emptyState"], [data-testid="error-detail"]')
                if empty_state.count() > 0 and empty_state.first.is_visible():
                    err_txt = empty_state.first.inner_text()
                    if any(kw in err_txt for kw in ["存在しません", "凍結", "制限", "ブロック"]):
                        print(f"   ⚠️ アカウント利用不可（{err_txt[:30]}）")
                        return False
            except Exception:
                pass

            # B. アクションボタン領域（フォロー/DM/プロフィール編集など）の出現チェック
            # ※ UserNameだけでなく、ボタン類（placementTracking等）がReactでマウントされるまで待つことで「フォローボタンなし」の誤判定を完全防止
            action_locators = [
                'div[data-testid="placementTracking"] button',
                'button[data-testid$="-follow"]',
                'button[data-testid$="-unfollow"]',
                'button[data-testid="sendDMFromProfile"]',
                'button:has-text("フォロー中")',
                'button:has-text("Following")',
                'button:has-text("プロフィールを編集")',
                'button:has-text("Edit profile")',
                'div[data-testid="placementTracking"]',
                'div[data-testid="UserProfileHeader_Items"]'
            ]
            action_ready = False
            for a_sel in action_locators:
                loc = page.locator(a_sel)
                if loc.count() > 0:
                    try:
                        if loc.first.is_visible():
                            action_ready = True
                            break
                    except Exception:
                        pass
            
            if action_ready:
                print(f"   ✨ プロフィール画面およびアクションボタンの描画完了を確認しました！(@{clean_target})")
                time.sleep(random.uniform(1.2, 1.8))  # Reactのイベントリスナー結合と描画安定のための待機
                return True

            # C. もし5秒以上経過してUserNameが表示されていれば、ページ下部のタイムライン等も確認
            if time.time() - start_t > 5.0:
                user_name_loc = page.locator('div[data-testid="UserName"]')
                if user_name_loc.count() > 0 and user_name_loc.first.is_visible():
                    # 既にフォローボタン等の探索準備ができているか
                    has_buttons = page.locator('div[role="main"] button').count() > 2
                    if has_buttons:
                        print(f"   ✨ プロフィール主要要素の描画完了を確認しました (@{clean_target})")
                        time.sleep(1.0)
                        return True

            time.sleep(0.4)
            
        print("   ⚠️ プロフィールの描画待機がタイムアウトしましたが、現状の画面で処理を続行します。")
        return False

    def get_profile_display_name(self, target_screen_name: str = "") -> str:
        """
        現在のプロフィール画面またはツイート画面からユーザーのアカウント名（表示名）を取得
        例: @genkaidesu03 の画面から「限界OLちゃん」を取得
        """
        page = self.ensure_page()
        clean_target = normalize_screen_name(target_screen_name).lower()
        
        # 1. document.title からの抽出 (Xのタイトルは「表示名 (@screen_name) / X」形式で最も確実・高速)
        try:
            cur_title = page.title()
            if cur_title:
                m = re.search(r'^(.*?)s*[(（]@?' + re.escape(clean_target) + r'[)）]', cur_title, re.IGNORECASE)
                if m:
                    cand = m.group(1).strip()
                    if cand and cand.lstrip("@").lower() != clean_target and not cand.startswith("@"):
                        return cand
                m2 = re.search(r'^(.*?)s*(?:/|on)s*(?:X|Twitter)$', cur_title, re.IGNORECASE)
                if m2:
                    t_cand = m2.group(1).strip()
                    m3 = re.search(r'^(.*?)s*[(（]@', t_cand)
                    if m3:
                        sub_cand = m3.group(1).strip()
                        if sub_cand and sub_cand.lstrip("@").lower() != clean_target:
                            return sub_cand
                    elif t_cand and t_cand.lstrip("@").lower() != clean_target and not t_cand.startswith("@"):
                        return t_cand
        except Exception:
            pass

        # 2. og:title メタタグからの抽出
        try:
            og_title = page.locator('meta[property="og:title"]').get_attribute("content")
            if og_title:
                m = re.search(r'^(.*?)s*[(（]@?' + re.escape(clean_target) + r'[)）]', og_title, re.IGNORECASE)
                if m:
                    cand = m.group(1).strip()
                    if cand and cand.lstrip("@").lower() != clean_target:
                        return cand
        except Exception:
            pass

        # 3. DOM: div[data-testid="UserName"]
        try:
            user_box = page.locator('div[data-testid="UserName"]')
            if user_box.count() > 0:
                raw_text = user_box.first.inner_text().strip()
                lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
                for line in lines:
                    line_clean = line.lstrip("@").strip().lower()
                    if line_clean == clean_target:
                        continue
                    if line.startswith("@") or line.startswith("·") or line.startswith("・"):
                        continue
                    if line in ["認証済みアカウント", "認証バッジ", "公式", "Verified account", "Verified", "Follows you", "フォローされています"]:
                        continue
                    if len(line) > 0:
                        return line
        except Exception:
            pass

        # 4. DOM: div[data-testid="User-Name"]
        try:
            user_box2 = page.locator('div[data-testid="User-Name"]')
            if user_box2.count() > 0:
                raw_text = user_box2.first.inner_text().strip()
                lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
                for line in lines:
                    line_clean = line.lstrip("@").strip().lower()
                    if line_clean == clean_target or line.startswith("@") or line.startswith("·") or line.startswith("・"):
                        continue
                    if line in ["認証済みアカウント", "認証バッジ", "公式", "Verified account", "Verified"]:
                        continue
                    if len(line) > 0:
                        return line
        except Exception:
            pass

        # 5. primaryColumn h2 見出し
        try:
            h2_box = page.locator('div[data-testid="primaryColumn"] h2[role="heading"]')
            if h2_box.count() > 0:
                txt = h2_box.first.inner_text().strip()
                if txt and txt.lstrip("@").lower() != clean_target and not txt.startswith("@"):
                    return txt
        except Exception:
            pass

        return ""

    def send_direct_message(self, target_screen_name: str, message_text: str, passcode: str = "") -> tuple[str, str]:
        """
        指定ユーザーへのDM送信 (暗号化DMパスコード自動解除・全文章完全入力対応)
        戻り値: (STATUS, DETAIL)
        STATUS: 'SUCCESS' | 'DM_REJECTED' | 'LOCKED_ACCOUNT' | 'FAILED'
        """
        page = self.ensure_page()
        clean_target = normalize_screen_name(target_screen_name)
        
        # 0. プロフィールの描画完了を待機 (ラグ対策: ボタンが完全に表示されるまで待機)
        self.wait_for_profile_ready(clean_target, timeout=20.0)

        # テンプレート変数 ({name}, {名前}, {アカウント名}, {full_name}, {表示名}, {id}) の自動解決 (IDではなく実際のアカウント名に置換)
        if any(tag in message_text for tag in ["{name}", "{名前}", "{アカウント名}", "{full_name}", "{表示名}", "{id}", "{mention}"]):
            profile_display_name = self.get_profile_display_name(clean_target)
            if profile_display_name:
                print(f"   👤 [アカウント名解決] プロフィール画面より表示名「{profile_display_name}」を検出・適用しました (ID: @{clean_target})")
            message_text = format_template_message(message_text, profile_display_name, clean_target, preserve_unresolved=False)

        # 1. 鍵アカウント（非公開アカウント）のチェック
        locked_indicator = page.locator('svg[data-testid="icon-lock"]')
        is_locked = locked_indicator.count() > 0

        # DMドロワーが画面下部に展開されている場合は折りたたむ（予期せぬポップアップ画面防止）
        try:
            drawer = page.locator('div[data-testid="DMDrawer"]')
            if drawer.count() > 0:
                collapse_btn = drawer.locator('[data-testid="dmDrawerCloseButton"], [aria-label*="折りたたむ"], [aria-label*="閉じる"], [aria-label*="最小化"], [aria-label*="Collapse"]').first
                if collapse_btn.count() > 0 and collapse_btn.is_visible():
                    collapse_btn.click()
                    time.sleep(0.3)
        except Exception:
            pass

        # 2. DMボタンの検出（React描画ラグを考慮し最大14秒間ポーリング待機）
        print("   🔍 プロフィールヘッダーからDMボタンを探索中（React描画待機）...")
        dm_selectors = [
            'button[data-testid="sendDMFromProfile"]',
            '[data-testid="sendDMFromProfile"]',
            'button[aria-label*="メッセージを送信"]',
            'button[aria-label*="ダイレクトメッセージ"]',
            'button[aria-label*="メッセージ"]',
            'button[aria-label*="Message"]',
            'button[aria-label*="Direct message"]',
            'a[href*="/messages/"][role="button"]',
            'a[href*="/messages/"]'
        ]

        dm_button = None
        start_find = time.time()
        while time.time() - start_find < 14.0:
            for sel in dm_selectors:
                loc = page.locator(sel)
                if loc.count() > 0:
                    for idx in range(loc.count()):
                        candidate = loc.nth(idx)
                        try:
                            if candidate.is_visible():
                                dm_button = candidate
                                break
                        except Exception:
                            pass
                if dm_button:
                    break
            if dm_button:
                break

            # 3点リーダーメニュー（その他）の中に格納されている場合の検出 (幅の狭い画面対応)
            if time.time() - start_find > 4.0:
                user_actions = page.locator('button[data-testid="userActions"], button[aria-label*="その他のアクション"], button[aria-label*="More"]')
                if user_actions.count() > 0 and user_actions.first.is_visible():
                    try:
                        user_actions.first.click()
                        time.sleep(0.5)
                        menu_dm = page.locator('[role="menuitem"]:has-text("メッセージ"), [role="menuitem"]:has-text("Direct Message")')
                        if menu_dm.count() > 0 and menu_dm.first.is_visible():
                            dm_button = menu_dm.first
                            print("   ✨ 3点メニュー内にDMボタンを検出しました。")
                            break
                        else:
                            page.keyboard.press("Escape")
                            time.sleep(0.3)
                    except Exception:
                        pass

            time.sleep(0.4)

        if not dm_button:
            if is_locked:
                print("   🚫 鍵アカウント（非公開）のためDMボタンが存在しません。")
                return "DM_REJECTED", "鍵アカウント（非公開アカウント）"
            # フォローボタンが存在するか確認（プロフィール自体はロードされているか）
            has_follow_btn = page.locator('button[data-testid$="-follow"], button[data-testid$="-unfollow"], button[role="button"]:has-text("フォロー")').count() > 0
            if has_follow_btn:
                print("   🚫 DMボタンが存在しません（相互フォロー外からのDM拒否設定 または 認証限定）")
                return "DM_REJECTED", "DMボタン非表示 (受信拒否または鍵アカウント)"
            else:
                print("   🚫 ユーザープロフィールまたはDMボタンの取得に失敗しました。")
                return "DM_REJECTED", "プロフィール未取得またはDM非対応"

        # 3. 人間らしいベジェ曲線でDMボタンへマウス移動＆クリック
        try:
            dm_button.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        time.sleep(0.3)

        box = dm_button.bounding_box()
        if box:
            target_x = box["x"] + box["width"] / 2
            target_y = box["y"] + box["height"] / 2
            self.bezier_mouse_move(target_x, target_y)
            time.sleep(random.uniform(0.2, 0.4))
            page.mouse.click(target_x, target_y)
        else:
            dm_button.click(force=True)

        print("   🖱️ DMボタンをクリックしました。")
        print("   ⏳ 【ラグ待機中】DMチャット画面・ドロワー・ダイアログの表示完了まで待機しています（最大30秒）...")

        def check_and_dismiss_rejection_dialog() -> tuple[bool, str]:
            """送信不可画面・認証限定・DM拒否ダイアログ・受信トレイ閉鎖（Xナンバー）の高速検知＆自動閉鎖"""
            try:
                # 1. 受信トレイ閉鎖 (Xナンバー要求) 特化検知 (最優先)
                closed_inbox_keywords = [
                    "受信トレイは閉じられています",
                    "受信トレイが閉じられています",
                    "相手のXナンバー",
                    "Xナンバーを使用する",
                    "Xナンバーが分かっている場合は",
                    "相手のXナンバーが分かっている場合は、メッセージを送信できます"
                ]
                has_closed_inbox = False
                try:
                    body_text = page.locator("body").inner_text()
                    if any(k in body_text for k in closed_inbox_keywords):
                        has_closed_inbox = True
                except Exception:
                    pass

                if has_closed_inbox:
                    print("   ⚠️ 受信トレイ閉鎖ダイアログ（相手のXナンバー要求）を検知しました。", flush=True)
                    try:
                        dismiss_loc = page.locator('button:has-text("今はしない"), [role="button"]:has-text("今はしない"), span:has-text("今はしない"), [data-testid="confirmationSheetCancel"], text="今はしない"')
                        if dismiss_loc.count() > 0:
                            dismiss_loc.first.click(force=True)
                            print("   👉 モーダルの「今はしない」をクリックしてダイアログを閉じました。", flush=True)
                            time.sleep(0.5)
                        else:
                            page.keyboard.press("Escape")
                    except Exception:
                        page.keyboard.press("Escape")
                    return True, "受信トレイ閉鎖 (相手のXナンバー要求)"

                # 2. その他の各種送信不可・拒否ダイアログ
                rej_dialogs = page.locator('div[role="dialog"], div[aria-modal="true"], [data-testid="sheetDialog"], [data-testid="confirmationSheetDialog"], [data-testid="toast"]')
                if rej_dialogs.count() > 0:
                    for di in range(min(rej_dialogs.count(), 3)):
                        d = rej_dialogs.nth(di)
                        try:
                            if d.is_visible():
                                d_text = d.inner_text()
                                for kw in [
                                    "認証済みユーザーのみがこのアカウントにダイレクトメッセージを送信できます",
                                    "認証済みユーザーのみがこのアカウントにメッセージリクエストを送信できます",
                                    "このアカウントにメッセージリクエストを送信できるのは、認証済みユーザーのみです",
                                    "メッセージリクエストを送信するには、認証を受けるか、Premiumに登録してください",
                                    "メッセージリクエストを送信するにはPremiumに登録してください",
                                    "認証済みユーザーのみ",
                                    "Only verified users",
                                    "Premiumに登録",
                                    "プレミアムに登録",
                                    "Subscribe to Premium",
                                    "X Premium",
                                    "メッセージを送信できません",
                                    "ダイレクトメッセージを送信できません",
                                    "このユーザーにはダイレクトメッセージを送信できません",
                                    "このアカウントにはダイレクトメッセージを送信できません",
                                    "このアカウントにはDMを送信できません",
                                    "このユーザーにメッセージを送信できません",
                                    "You cannot message this user",
                                    "You can't message this account",
                                    "Direct Messages are closed",
                                    "このアカウントはメッセージを受け取っていません",
                                    "メッセージの受信を制限",
                                    "リクエストを送信できません",
                                    "アカウントが制限されています",
                                    "ブロックされています",
                                    "メッセージの上限に達しました",
                                    "送信上限に達しました",
                                    "Limit reached"
                                ]:
                                    if kw in d_text:
                                        print(f"   🚫 送信不可ダイアログ（{kw}）を検知しました。", flush=True)
                                        try:
                                            d_close = d.locator('button:has-text("今はしない"), [role="button"]:has-text("今はしない"), span:has-text("今はしない"), button:has-text("閉じる"), button:has-text("キャンセル"), button:has-text("OK"), button:has-text("Not now"), [aria-label="閉じる"], [aria-label="Close"], [data-testid="app-bar-close"]').first
                                            if d_close.count() > 0 and d_close.is_visible():
                                                d_close.click(force=True)
                                            else:
                                                page.keyboard.press("Escape")
                                            time.sleep(0.5)
                                        except Exception:
                                            page.keyboard.press("Escape")
                                        return True, f"送信不可ダイアログ検知 ({kw})"
                        except Exception:
                            pass
            except Exception:
                pass
            return False, ""

        # 4. DM画面および各種ダイアログの描画待機 (最大30秒、ラグを完全に吸収)
        composer_selectors = [
            'div[data-testid="dmComposerTextInput"]',
            'div[role="textbox"][data-testid="dmComposerTextInput"]',
            'div[data-testid="DMDrawer"] div[role="textbox"]',
            'div[role="dialog"] div[role="textbox"]',
            'div[data-testid="messageEntry"] div[role="textbox"]',
            'div[aria-label*="新しいメッセージを入力"]',
            'div[aria-label*="メッセージを入力"]',
            'div[aria-label*="Start a new message"]',
            'div[aria-label*="Direct message text"]',
            'aside[aria-label*="メッセージ"] div[role="textbox"]',
            'div[role="textbox"][contenteditable="true"]'
        ]

        composer = None
        start_wait = time.time()
        timeout = 30.0
        passcode_handled = False
        has_retried_click = False

        while time.time() - start_wait < timeout:
            # 0. DM拒否 / 送信不可ダイアログの高速検知＆スキップ
            is_rej, rej_msg = check_and_dismiss_rejection_dialog()
            if is_rej:
                is_closed_inbox = "受信トレイ閉鎖" in rej_msg or "Xナンバー" in rej_msg
                status_code = "INBOX_CLOSED" if is_closed_inbox else "DM_REJECTED"
                print(f"   ⏩ [スキップ実行] {rej_msg} のため、送信カウントに含めず次のターゲットへ進みます。", flush=True)
                return status_code, rej_msg

            # 1. DMテキスト入力欄 (composer) の存在チェック (最優先)
            for c_sel in composer_selectors:
                c_loc = page.locator(c_sel)
                if c_loc.count() > 0:
                    for ci in range(c_loc.count()):
                        c_cand = c_loc.nth(ci)
                        try:
                            if c_cand.is_visible():
                                composer = c_cand
                                break
                        except Exception:
                            pass
                    if composer:
                        break

            if composer:
                print("   ✨ 【表示完了】DMチャット画面（入力欄）の展開を確認しました！（ラグ待機完了）", flush=True)
                time.sleep(1.2)  # React DraftJSの初期化完了を待機
                break

            # 2. 「新しいメッセージを送信」画面の「次へ」ボタン自動進行
            try:
                next_btns = page.locator('[data-testid="nextButton"], [role="button"]:has-text("次へ"), button:has-text("次へ"), [role="button"]:has-text("Next"), button:has-text("Next")')
                if next_btns.count() > 0:
                    for ni in range(next_btns.count()):
                        nb = next_btns.nth(ni)
                        if nb.is_visible() and nb.is_enabled() and nb.get_attribute("aria-disabled") != "true":
                            print("   ➡️ 「新しいメッセージ」画面を検知。「次へ」をクリックして会話へ進みます...", flush=True)
                            nb.click()
                            time.sleep(1.5)
                            break
            except Exception:
                pass

            # 3. 会話開始 / チャット開始 / 同意ダイアログの自動同意
            try:
                start_btns = page.locator('[data-testid="startConversationButton"], button:has-text("会話を開始"), [role="button"]:has-text("会話を開始"), button:has-text("チャットを開始"), [role="button"]:has-text("チャットを開始"), button:has-text("メッセージを作成")')
                if start_btns.count() > 0 and start_btns.first.is_visible():
                    print("   💬 「会話を開始」をクリックします...", flush=True)
                    start_btns.first.click()
                    time.sleep(1.5)

                agree_btn = page.locator('button:has-text("同意して続ける"), [role="button"]:has-text("同意して続ける"), button:has-text("リクエストを送信"), [role="button"]:has-text("リクエストを送信"), button:has-text("OK"), button:has-text("確認"), button:has-text("続ける")')
                if agree_btn.count() > 0 and agree_btn.first.is_visible():
                    agree_btn.first.click()
                    time.sleep(1.2)
            except Exception:
                pass

            # 4. 暗号化DMパスコード / PIN入力要求の自動検知＆入力
            if self.is_passcode_prompt_active():
                effective_passcode = passcode or self.current_passcode
                effective_account = self.current_account_id or clean_target
                unlocked = self.handle_passcode_prompt(passcode=effective_passcode, account_id=effective_account, context_label="暗号化DMロック解除")
                if unlocked:
                    passcode_handled = True
                    print("   ⏳ パスコード解除完了を確認。DM入力欄の描画を待機しています...", flush=True)
                    time.sleep(2.0)
                    if "/chat/pin" in page.url or "/pin/recovery" in page.url:
                        try:
                            import urllib.parse
                            qs = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
                            if "from" in qs and qs["from"]:
                                redirect_path = qs["from"][0]
                                full_dest = f"https://x.com{redirect_path}" if redirect_path.startswith("/") else redirect_path
                                print(f"   🚀 パスコード解除後、対象のDM会話画面へ遷移します: {full_dest}", flush=True)
                                page.goto(full_dest, wait_until="domcontentloaded")
                                time.sleep(2.5)
                        except Exception:
                            pass
                else:
                    print("   🚫 暗号化パスコードが未解除のため、このターゲットのDM送信を安全にスキップします。", flush=True)
                    try:
                        page.goto(f"https://x.com/{clean_target}", wait_until="domcontentloaded")
                        time.sleep(1.5)
                    except Exception:
                        pass
                    return "DM_REJECTED", "パスコード未解除 (暗号化DMロック)"

            # 5. もし8秒経っても全く反応がない場合のみ、1回だけ慎重にDMボタンを再クリック
            elapsed = time.time() - start_wait
            if elapsed > 8.0 and not has_retried_click and not composer:
                has_retried_click = True
                has_any_dm_ui = page.locator('div[data-testid="DMDrawer"], div[role="dialog"], [data-testid="messageEntry"]').count() > 0
                if not has_any_dm_ui and not ("/messages" in page.url):
                    print("   🔄 [通信ラグ対応] DM画面がまだ開かないため、DMボタンをもう一度クリックします...", flush=True)
                    try:
                        dm_button.click(force=True)
                    except Exception:
                        pass
                    time.sleep(1.5)

            time.sleep(0.5)

        if not composer or not composer.is_visible():
            # 最後の再確認
            for c_sel in composer_selectors:
                comp_loc = page.locator(c_sel)
                if comp_loc.count() > 0 and comp_loc.first.is_visible():
                    composer = comp_loc.first
                    break

            if not composer or not composer.is_visible():
                alt_comp = page.locator('div[role="textbox"], textarea')
                if alt_comp.count() > 0 and alt_comp.first.is_visible():
                    composer = alt_comp.first
                else:
                    print("   🚫 DMテキスト入力欄が表示されません (DM拒否設定 または 読み込みタイムアウト)。")
                    try:
                        close_btn = page.locator('button[aria-label="閉じる"], button[aria-label="Close"], [data-testid="app-bar-close"]')
                        if close_btn.count() > 0 and close_btn.first.is_visible():
                            close_btn.first.click()
                        else:
                            page.keyboard.press("Escape")
                    except Exception:
                        pass
                    return "DM_REJECTED", "入力エリア非活性 (DM拒否またはタイムアウト)"

        # 8. 入力エリアをクリック＆安全・確実な全文章入力 (insert_text)
        c_box = composer.first.bounding_box()
        if c_box:
            self.bezier_mouse_move(c_box["x"] + 20, c_box["y"] + c_box["height"] / 2)
            page.mouse.click(c_box["x"] + 20, c_box["y"] + c_box["height"] / 2)
            time.sleep(0.5)

        # 送信直前にも念のためテンプレート変数が残っていれば最終展開
        if any(tag in message_text for tag in ["{name}", "{名前}", "{アカウント名}", "{full_name}", "{表示名}"]):
            p_name = self.get_profile_display_name(clean_target)
            message_text = format_template_message(message_text, p_name, clean_target, preserve_unresolved=False)

        print("   ⌨️ 登録文章の安全入力（複数行・全角文字・絵文字100%完全入力）を実行中...")
        self.input_message_safely(composer.first, message_text)
        time.sleep(random.uniform(1.0, 1.8))

        # 9. 送信ボタン (data-testid="dmComposerSendButton") の検出＆クリック
        send_btn = page.locator('[data-testid="dmComposerSendButton"], button[aria-label*="送信"], [role="button"][aria-label*="送信"], [aria-label="Send message"], [aria-label="Send"], button:has-text("送信")')
        sent = False
        if send_btn.count() > 0:
            for s_idx in range(send_btn.count()):
                sb = send_btn.nth(s_idx)
                try:
                    if sb.is_visible() and sb.is_enabled() and sb.get_attribute("aria-disabled") != "true":
                        s_box = sb.bounding_box()
                        if s_box:
                            self.bezier_mouse_move(s_box["x"] + s_box["width"] / 2, s_box["y"] + s_box["height"] / 2)
                            time.sleep(random.uniform(0.3, 0.6))
                            page.mouse.click(s_box["x"] + s_box["width"] / 2, s_box["y"] + s_box["height"] / 2)
                        else:
                            sb.click()
                        time.sleep(random.uniform(2.0, 3.5))
                        sent = True
                        break
                except Exception:
                    pass
        if not sent:
            print("   送信ボタンが見当たらない、または非活性のためEnterキーでの送信を試みます...")
            page.keyboard.press("Enter")
            time.sleep(2)

        # 10. 送信完了後、「新しいメッセージを送信」画面やDMドロワーのポップアップを確実に閉じる/折りたたむ（スクリーンショット125145画面の消去）
        try:
            close_targets = page.locator('div[role="dialog"] [data-testid="app-bar-close"], div[role="dialog"] button[aria-label="閉じる"], div[role="dialog"] button[aria-label="Close"], [data-testid="DMDrawer"] [data-testid="dmDrawerCloseButton"], [data-testid="DMDrawer"] [aria-label*="折りたたむ"], [data-testid="DMDrawer"] [aria-label*="閉じる"]')
            if close_targets.count() > 0 and close_targets.first.is_visible():
                close_targets.first.click()
                time.sleep(0.5)
        except Exception:
            pass

        return "SUCCESS", "送信完了"

    def _find_tweet_on_status_page(self, tweet_url: str):
        """ステータス詳細URLへアクセスし、対象ツイートのarticle要素を取得"""
        page = self.ensure_page()
        try:
            current_url = page.url.lower().split("?")[0].rstrip("/")
            clean_dest = tweet_url.lower().split("?")[0].rstrip("/")
            if current_url != clean_dest:
                page.goto(tweet_url, timeout=35000, wait_until="domcontentloaded")
                time.sleep(random.uniform(2.0, 3.0))

            # ツイート要素の描画待機 (最大12秒)
            tweet_articles = page.locator('article[data-testid="tweet"]')
            start_w = time.time()
            while time.time() - start_w < 12.0:
                if tweet_articles.count() > 0:
                    try:
                        if tweet_articles.first.is_visible():
                            break
                    except Exception:
                        pass
                time.sleep(0.4)

            # 12秒待っても見つからず、かつエラー画面が出ている場合のみページ内再試行
            if tweet_articles.count() == 0 and self.check_error_screen(page):
                self.recover_if_stuck(tweet_url, "ツイート詳細画面エラー")
                page = self.ensure_page()
                tweet_articles = page.locator('article[data-testid="tweet"]')

            if tweet_articles.count() == 0:
                return None

            status_id = tweet_url.split("/status/")[-1].split("?")[0].split("/")[0]
            if status_id:
                for idx in range(min(tweet_articles.count(), 5)):
                    cand = tweet_articles.nth(idx)
                    try:
                        if cand.locator(f'a[href*="/status/{status_id}"]').count() > 0:
                            return cand
                    except Exception:
                        pass

            return tweet_articles.first
        except Exception as e:
            print(f"   ⚠️ ステータス画面取得エラー: {e}")
            return None

    def get_tweet_text_from_url(self, tweet_url: str) -> str:
        """指定したツイートURLにアクセスして投稿本文テキストを取得"""
        if not tweet_url or "/status/" not in tweet_url:
            return ""
        try:
            target_tweet = self._find_tweet_on_status_page(tweet_url)
            if target_tweet:
                text_el = target_tweet.locator('div[data-testid="tweetText"]')
                if text_el.count() > 0:
                    return text_el.first.inner_text().strip()
            page = self.ensure_page()
            all_texts = page.locator('div[data-testid="tweetText"]')
            if all_texts.count() > 0:
                return all_texts.first.inner_text().strip()
        except Exception as e:
            print(f"   ⚠️ ツイートテキスト取得例外: {e}")
        return ""

    def send_reply(self, target_screen_name: str, reply_text: str, tweet_url: str = "") -> tuple[str, str]:
        """
        指定ポストまたは指定ユーザーの最新ポストへリプライ送信
        - ★ ユーザー要件対応: tweet_url が指定されている場合、取得したツイートへ直接アクセスしてリプライ
        - tweet_url が未指定または失敗時: プロフィール画面の最新ポストにリプライ (フォールバック)
        戻り値: (STATUS, DETAIL)
        STATUS: 'SUCCESS' | 'NO_POSTS' | 'LOCKED_ACCOUNT' | 'FAILED'
        """
        page = self.ensure_page()
        clean_target = normalize_screen_name(target_screen_name)
        if not clean_target and not tweet_url:
            return "FAILED", "ターゲット情報不正"

        # テンプレート変数の自動解決
        if any(tag in reply_text for tag in ["{name}", "{名前}", "{アカウント名}", "{full_name}", "{表示名}", "{id}", "{mention}"]):
            disp = self.get_profile_display_name(clean_target) if clean_target else ""
            reply_text = format_template_message(reply_text, disp, clean_target)

        # -------------------------------------------------------------
        # A. 取得したツイートURLが指定されている場合: そのツイートへ直接リプライ
        # -------------------------------------------------------------
        if tweet_url and "/status/" in tweet_url:
            print(f"💬 取得ツイートへのリプライ処理: {tweet_url}")
            target_tweet = self._find_tweet_on_status_page(tweet_url)
            if target_tweet:
                reply_btn = target_tweet.locator('button[data-testid="reply"]')
                inline_reply = page.locator('div[data-testid="tweetTextarea_0"], div[role="textbox"][data-testid="tweetTextarea_0"]')

                if reply_btn.count() > 0 and reply_btn.first.is_visible():
                    r_box = reply_btn.first.bounding_box()
                    if r_box:
                        self.bezier_mouse_move(r_box["x"] + r_box["width"] / 2, r_box["y"] + r_box["height"] / 2)
                        time.sleep(0.4)
                        page.mouse.click(r_box["x"] + r_box["width"] / 2, r_box["y"] + r_box["height"] / 2)
                    else:
                        reply_btn.first.click()
                    time.sleep(random.uniform(1.5, 2.5))
                elif inline_reply.count() > 0 and inline_reply.first.is_visible():
                    inline_reply.first.click()
                    time.sleep(0.5)

                composer = page.locator('div[data-testid="tweetTextarea_0"], div[role="textbox"][data-testid="tweetTextarea_0"]')
                try:
                    composer.first.wait_for(timeout=6000)
                except Exception:
                    pass

                if composer.count() > 0 and composer.first.is_visible():
                    c_box = composer.first.bounding_box()
                    if c_box:
                        self.bezier_mouse_move(c_box["x"] + 20, c_box["y"] + c_box["height"] / 2)
                        page.mouse.click(c_box["x"] + 20, c_box["y"] + c_box["height"] / 2)
                        time.sleep(0.4)

                    print("   ⌨️ 人間らしいタイピングで取得ツイートへリプライ文面を入力中...")
                    self.human_type(composer.first, reply_text)
                    time.sleep(random.uniform(1.0, 2.0))

                    send_btn = page.locator('button[data-testid="tweetButton"], button[data-testid="tweetButtonInline"]')
                    if send_btn.count() > 0 and send_btn.first.is_enabled():
                        s_box = send_btn.first.bounding_box()
                        if s_box:
                            self.bezier_mouse_move(s_box["x"] + s_box["width"] / 2, s_box["y"] + s_box["height"] / 2)
                            time.sleep(random.uniform(0.4, 0.8))
                            page.mouse.click(s_box["x"] + s_box["width"] / 2, s_box["y"] + s_box["height"] / 2)
                        else:
                            send_btn.first.click()
                        time.sleep(random.uniform(2.5, 4.0))
                        return "SUCCESS", "取得ツイートにリプライ完了"
                    else:
                        page.keyboard.press("Control+Enter")
                        time.sleep(2.5)
                        return "SUCCESS", "取得ツイートにリプライ完了"
                else:
                    print("   ⚠️ 取得ツイートのリプライ入力欄起動に失敗しました。")
            else:
                print("   ⚠️ 取得ツイートが見つかりませんでした。")
            print("   👉 プロフィール画面からの最新ツイートリプライへフォールバックします...")

        # -------------------------------------------------------------
        # B. tweet_url 未指定、または取得ツイートへのアクセス失敗時: プロフィール最新ポストへ
        # -------------------------------------------------------------
        profile_url = f"https://x.com/{clean_target}"
        print(f"💬 プロフィール最新ポストへのリプライ処理 (フォールバック): {profile_url}")

        # 0. プロフィールの描画完了を待機 (ラグ対策)
        self.wait_for_profile_ready(clean_target, timeout=20.0)

        # 鍵垢チェック
        if page.locator('svg[data-testid="icon-lock"]').count() > 0 and page.locator('button[data-testid$="-follow"]').count() > 0:
            print("   🔒 非公開（鍵アカウント）かつ未フォローのためリプライできません。")
            return "LOCKED_ACCOUNT", "鍵アカウント"

        # タイムライン上の最新ツイート (article) の出現を待機 (最大12秒)
        print("   ⏳ タイムラインから最新ポストの描画を待機中...")
        tweet_articles = page.locator('article[data-testid="tweet"]')
        start_wait_rep = time.time()
        while time.time() - start_wait_rep < 12.0:
            if tweet_articles.count() > 0:
                try:
                    if tweet_articles.first.is_visible():
                        break
                except Exception:
                    pass
            elapsed = time.time() - start_wait_rep
            if elapsed > 3.0 and elapsed < 3.8:
                try:
                    page.mouse.wheel(0, 350)
                except Exception:
                    pass
            time.sleep(0.4)

        if tweet_articles.count() == 0:
            print("   ⚠️ ポストが見つかりません。")
            return "NO_POSTS", "ポストなし"

        first_tweet = tweet_articles.first
        reply_btn = first_tweet.locator('button[data-testid="reply"]')
        if reply_btn.count() == 0:
            print("   ⚠️ リプライボタンが見つかりません（返信制限の可能性）。")
            return "FAILED", "リプライ不可設定"

        r_box = reply_btn.first.bounding_box()
        if r_box:
            self.bezier_mouse_move(r_box["x"] + r_box["width"] / 2, r_box["y"] + r_box["height"] / 2)
            time.sleep(0.4)
            page.mouse.click(r_box["x"] + r_box["width"] / 2, r_box["y"] + r_box["height"] / 2)
        else:
            reply_btn.first.click()

        time.sleep(random.uniform(1.5, 2.5))

        # リプライ入力ダイアログ
        composer = page.locator('div[data-testid="tweetTextarea_0"], div[role="textbox"][data-testid="tweetTextarea_0"]')
        try:
            composer.first.wait_for(timeout=6000)
        except Exception:
            print("   ⚠️ リプライ入力欄の起動に失敗しました。")
            return "FAILED", "入力欄起動失敗"

        c_box = composer.first.bounding_box()
        if c_box:
            self.bezier_mouse_move(c_box["x"] + 20, c_box["y"] + c_box["height"] / 2)
            page.mouse.click(c_box["x"] + 20, c_box["y"] + c_box["height"] / 2)
            time.sleep(0.4)

        print("   ⌨️ 人間らしいタイピングでリプライ文面を入力中...")
        self.human_type(composer.first, reply_text)
        time.sleep(random.uniform(1.0, 2.0))

        # 送信ボタン (tweetButton または tweetButtonInline)
        send_btn = page.locator('button[data-testid="tweetButton"], button[data-testid="tweetButtonInline"]')
        if send_btn.count() > 0 and send_btn.first.is_enabled():
            s_box = send_btn.first.bounding_box()
            if s_box:
                self.bezier_mouse_move(s_box["x"] + s_box["width"] / 2, s_box["y"] + s_box["height"] / 2)
                time.sleep(random.uniform(0.4, 0.8))
                page.mouse.click(s_box["x"] + s_box["width"] / 2, s_box["y"] + s_box["height"] / 2)
            else:
                send_btn.first.click()
            time.sleep(random.uniform(2.5, 4.0))
            return "SUCCESS", "リプライ完了"
        else:
            page.keyboard.press("Control+Enter")
            time.sleep(2.5)
            return "SUCCESS", "リプライ完了"

    def like_recent_post(self, target_screen_name: str, tweet_url: str = "") -> tuple[str, str]:
        """
        指定ポストまたは指定ユーザーの最新ポストにいいねを実行
        - ★ ユーザー要件対応: tweet_url が指定されている場合、取得したツイートへ直接アクセスしていいね
        - tweet_url が未指定または失敗時: プロフィール画面の最新ポストにいいね (フォールバック)
        """
        page = self.ensure_page()
        clean_target = normalize_screen_name(target_screen_name)
        if not clean_target and not tweet_url:
            return "FAILED", "ユーザー名不正"

        # -------------------------------------------------------------
        # A. 取得したツイートURLが指定されている場合: そのツイートへ直接アクセスしていいね
        # -------------------------------------------------------------
        if tweet_url and "/status/" in tweet_url:
            print(f"❤️ 取得ツイートへのいいね処理: {tweet_url}")
            target_tweet = self._find_tweet_on_status_page(tweet_url)
            if target_tweet:
                try:
                    target_tweet.scroll_into_view_if_needed(timeout=3000)
                    time.sleep(0.3)
                except Exception:
                    pass

                # 既にいいね済みかチェック
                unlike_btn = target_tweet.locator('button[data-testid="unlike"], div[data-testid="unlike"]')
                if unlike_btn.count() > 0:
                    print("   ✨ 取得ツイートは既にいいね済みです。")
                    return "ALREADY_LIKED", "取得ツイートは既にいいね済"

                # いいねボタン探索
                like_btn = target_tweet.locator('button[data-testid="like"], div[data-testid="like"], button[aria-label*="いいね"], button[aria-label*="Like"]')
                if like_btn.count() > 0:
                    target_btn = like_btn.first
                    try:
                        target_btn.scroll_into_view_if_needed(timeout=3000)
                        time.sleep(0.3)
                    except Exception:
                        pass

                    l_box = target_btn.bounding_box()
                    if l_box:
                        self.bezier_mouse_move(l_box["x"] + l_box["width"] / 2, l_box["y"] + l_box["height"] / 2)
                        time.sleep(0.3)
                        page.mouse.click(l_box["x"] + l_box["width"] / 2, l_box["y"] + l_box["height"] / 2)
                    else:
                        target_btn.click(force=True)

                    print("   ❤️ 取得ツイートにいいねをクリックしました。反映待機中...")
                    time.sleep(random.uniform(2.0, 3.2))
                    return "SUCCESS", "取得ツイートにいいね完了"
                else:
                    print("   ⚠️ 取得ツイートにいいねボタンが見つかりません。")
            else:
                print("   ⚠️ 取得ツイートの読み込みに失敗しました（削除または非公開の可能性）。")
            print("   👉 プロフィール画面からの最新ツイートいいねへフォールバックします...")

        # -------------------------------------------------------------
        # B. tweet_url 未指定、または取得ツイートへのアクセス失敗時: プロフィール最新ポストへ
        # -------------------------------------------------------------
        profile_url = f"https://x.com/{clean_target}"
        print(f"❤️ 最新ポストへのいいね処理 (フォールバック): {profile_url}")

        # 0. プロフィールの描画完了を待機 (ラグ対策: ボタンが完全に表示されるまで待機)
        self.wait_for_profile_ready(clean_target, timeout=20.0)

        # 1. 鍵アカウント（非公開アカウント）かつ未フォローのチェック
        is_locked = page.locator('svg[data-testid="icon-lock"]').count() > 0
        has_follow_btn = page.locator('button[data-testid$="-follow"]').count() > 0
        if is_locked and has_follow_btn:
            print("   🔒 非公開（鍵アカウント）かつ未フォローのため、ポストを閲覧できません。")
            return "LOCKED_ACCOUNT", "鍵アカウント（未フォローのためポスト非公開）"

        # 2. タイムライン上のポスト (article[data-testid="tweet"]) の出現待機 (最大12秒)
        print("   ⏳ タイムラインから最新ポストの描画を待機中...")
        tweet_articles = page.locator('article[data-testid="tweet"]')
        start_wait_tweets = time.time()
        while time.time() - start_wait_tweets < 12.0:
            if tweet_articles.count() > 0:
                try:
                    if tweet_articles.first.is_visible():
                        break
                except Exception:
                    pass
            # 3秒経過時点でポストがまだ見えなければ少しスクロールして読み込み促進
            elapsed = time.time() - start_wait_tweets
            if elapsed > 3.0 and elapsed < 3.8:
                try:
                    page.mouse.wheel(0, 400)
                except Exception:
                    pass
            elif elapsed > 6.0 and elapsed < 6.8:
                try:
                    page.mouse.wheel(0, 500)
                except Exception:
                    pass

            # 空白タイムライン・ポスト未投稿チェック
            empty_state = page.locator('div[data-testid="emptyState"]')
            if empty_state.count() > 0 and empty_state.first.is_visible():
                e_text = empty_state.first.inner_text()
                if "ポストがありません" in e_text or "まだポストしていません" in e_text or "No posts" in e_text:
                    return "NO_POSTS", "ポストなし (未投稿)"

            time.sleep(0.4)

        if tweet_articles.count() == 0:
            if is_locked:
                return "LOCKED_ACCOUNT", "鍵アカウント（ポスト非公開）"
            return "NO_POSTS", "ポストなし (未投稿または読み込み制限)"

        # 3. 最新3件のツイートを走査し、未いいねのポストにいいねを実行
        total_tweets = tweet_articles.count()
        inspected = min(total_tweets, 3)
        all_already_liked = True

        for i in range(inspected):
            tweet = tweet_articles.nth(i)
            try:
                tweet.scroll_into_view_if_needed(timeout=4000)
                time.sleep(0.4)
            except Exception:
                pass

            # 既にいいね済みかチェック
            unlike_btn = tweet.locator('button[data-testid="unlike"], div[data-testid="unlike"]')
            if unlike_btn.count() > 0:
                continue

            all_already_liked = False

            # いいねボタン探索
            like_btn = tweet.locator('button[data-testid="like"], div[data-testid="like"], button[aria-label*="いいね"], button[aria-label*="Like"]')
            if like_btn.count() > 0:
                target_btn = like_btn.first
                try:
                    target_btn.scroll_into_view_if_needed(timeout=4000)
                    time.sleep(0.3)
                except Exception:
                    pass

                l_box = target_btn.bounding_box()
                if l_box:
                    self.bezier_mouse_move(l_box["x"] + l_box["width"] / 2, l_box["y"] + l_box["height"] / 2)
                    time.sleep(0.3)
                    page.mouse.click(l_box["x"] + l_box["width"] / 2, l_box["y"] + l_box["height"] / 2)
                else:
                    target_btn.click(force=True)

                print("   ❤️ いいねをクリックしました。反映待機中...")
                time.sleep(random.uniform(2.0, 3.2))
                return "SUCCESS", "いいね完了"

        if all_already_liked:
            return "ALREADY_LIKED", "最新ポストは全ていいね済"

        return "FAILED", "いいねボタンなし (返信・いいね制限設定)"

    def follow_user(self, target_screen_name: str) -> tuple[str, str]:
        """指定ユーザーをフォロー (プロフィール描画ラグ完全待機・リトライ付き)"""
        page = self.ensure_page()
        clean_target = normalize_screen_name(target_screen_name)
        if not clean_target:
            return "FAILED", "ユーザー名不正"
        profile_url = f"https://x.com/{clean_target}"
        print(f"👤 フォロー処理: {profile_url}")

        # 0. プロフィールの描画完了を待機 (ラグ対策: ボタンが完全に表示されるまで待機)
        self.wait_for_profile_ready(clean_target, timeout=20.0)

        # 1. 存在しないアカウント / 凍結チェック
        try:
            body_text = page.locator('body').inner_text()
            if "このアカウントは存在しません" in body_text or "アカウントは凍結されています" in body_text:
                return "FAILED", "アカウントが存在しないか凍結"
        except Exception:
            pass

        # 2. 自身のアカウントチェック
        if page.locator('button:has-text("プロフィールを編集"), a[href="/settings/profile"], button:has-text("Edit profile")').count() > 0:
            return "FAILED", "自身のアカウント"

        # 3. フォローボタンまたはフォロー中状態の探索（最大12秒間ポーリング待機）
        unfollow_selectors = [
            'button[data-testid$="-unfollow"]',
            'div[data-testid="placementTracking"] button:has-text("フォロー中")',
            'div[data-testid="placementTracking"] button:has-text("Following")',
            'button[aria-label*="Following"]',
            'button[aria-label*="フォロー中"]',
            'button:has-text("フォロー中")',
            'button:has-text("Following")'
        ]

        target_follow_btn = None
        start_find = time.time()
        print("   🔍 フォローボタンの出現を待機・探索中...")

        while time.time() - start_find < 12.0:
            # 既にフォロー中かチェック
            for sel in unfollow_selectors:
                u_loc = page.locator(sel)
                if u_loc.count() > 0:
                    try:
                        if u_loc.first.is_visible():
                            return "ALREADY_FOLLOWING", "既にフォロー中"
                    except Exception:
                        pass

            # フォローリクエスト送信済み（非公開アカウント）チェック
            req_loc = page.locator('button:has-text("リクエスト送信済み"), button:has-text("フォロー許可待ち"), button:has-text("Requested")')
            if req_loc.count() > 0 and req_loc.first.is_visible():
                return "ALREADY_FOLLOWING", "既にフォローリクエスト送信済み"

            # フォローボタン探索 (data-testid$="-follow")
            follow_candidates = page.locator('button[data-testid$="-follow"]')
            if follow_candidates.count() > 0:
                for idx in range(follow_candidates.count()):
                    cand = follow_candidates.nth(idx)
                    try:
                        if cand.is_visible():
                            target_follow_btn = cand
                            break
                    except Exception:
                        pass
            if target_follow_btn:
                break

            # placementTracking やヘッダーボタン内の探索
            track_buttons = page.locator('div[data-testid="placementTracking"] button, div[data-testid="UserProfileHeader_Items"] button, div[data-testid="UserName"] ~ div button').all()
            for b in track_buttons:
                try:
                    if not b.is_visible():
                        continue
                    txt = b.inner_text().strip()
                    if ("フォロー" in txt or "Follow" in txt) and "中" not in txt and "フォロワー" not in txt and "リクエスト" not in txt:
                        target_follow_btn = b
                        break
                    elif "フォロー中" in txt or "Following" in txt:
                        return "ALREADY_FOLLOWING", "既にフォロー中"
                    elif "リクエスト送信済み" in txt or "Requested" in txt:
                        return "ALREADY_FOLLOWING", "既にフォローリクエスト送信済み"
                except Exception:
                    pass
            if target_follow_btn:
                break

            # ページ全体のボタンから探索
            for b in page.locator('div[role="main"] button, header button').all():
                try:
                    if not b.is_visible():
                        continue
                    txt = b.inner_text().strip()
                    if txt in ["フォロー", "フォローする", "Follow"] and "中" not in txt:
                        target_follow_btn = b
                        break
                    elif "フォロー中" in txt or "Following" in txt:
                        return "ALREADY_FOLLOWING", "既にフォロー中"
                except Exception:
                    pass
            if target_follow_btn:
                break

            time.sleep(0.4)

        if not target_follow_btn:
            # 最終チェック: もし既にフォロー中だったか
            for sel in unfollow_selectors:
                if page.locator(sel).count() > 0:
                    return "ALREADY_FOLLOWING", "既にフォロー中"
            return "FAILED", "フォローボタンなし"

        try:
            target_follow_btn.scroll_into_view_if_needed(timeout=5000)
            time.sleep(0.4)
        except Exception:
            pass

        b_box = target_follow_btn.bounding_box()
        if b_box:
            self.bezier_mouse_move(b_box["x"] + b_box["width"] / 2, b_box["y"] + b_box["height"] / 2)
            time.sleep(0.4)
            page.mouse.click(b_box["x"] + b_box["width"] / 2, b_box["y"] + b_box["height"] / 2)
        else:
            target_follow_btn.click(force=True)

        print("   🖱️ フォローボタンをクリックしました。反映を待機中...")
        time.sleep(random.uniform(2.0, 3.5))

        # 確認ダイアログ（非公開アカウントのフォローリクエスト等）が出た場合の処理
        confirm_btn = page.locator('button[data-testid="confirmationSheetConfirm"]')
        if confirm_btn.count() > 0:
            try:
                confirm_btn.click(force=True)
                time.sleep(1.0)
            except Exception:
                pass

        return "SUCCESS", "フォロー完了"

    def quote_tweet(self, target_screen_name: str, quote_text: str, tweet_url: str = "") -> tuple[str, str]:
        """
        指定ポストまたは指定ユーザーの最新ポストを引用リツイート (プロフィール描画ラグ完全待機対応)
        - ★ ユーザー要件対応: tweet_url が指定されている場合、取得したツイートへ直接アクセスして引用RT
        - tweet_url が未指定または失敗時: プロフィール画面の最新ポストを引用RT (フォールバック)
        """
        page = self.ensure_page()
        clean_target = normalize_screen_name(target_screen_name)
        if not clean_target and not tweet_url:
            return "FAILED", "ユーザー名不正"

        # テンプレート変数の自動解決
        if any(tag in quote_text for tag in ["{name}", "{名前}", "{アカウント名}", "{full_name}", "{表示名}", "{id}", "{mention}"]):
            disp = self.get_profile_display_name(clean_target) if clean_target else ""
            quote_text = format_template_message(quote_text, disp, clean_target)

        # -------------------------------------------------------------
        # A. 取得したツイートURLが指定されている場合: そのツイートへ直接アクセスして引用RT
        # -------------------------------------------------------------
        if tweet_url and "/status/" in tweet_url:
            print(f"🔁 取得ツイートへの引用リツイート処理: {tweet_url}")
            target_tweet = self._find_tweet_on_status_page(tweet_url)
            if target_tweet:
                retweet_btn = target_tweet.locator('button[data-testid="retweet"]')
                if retweet_btn.count() > 0:
                    r_box = retweet_btn.first.bounding_box()
                    if r_box:
                        self.bezier_mouse_move(r_box["x"] + r_box["width"] / 2, r_box["y"] + r_box["height"] / 2)
                        time.sleep(0.4)
                        page.mouse.click(r_box["x"] + r_box["width"] / 2, r_box["y"] + r_box["height"] / 2)
                    else:
                        retweet_btn.first.click()

                    time.sleep(1.2)
                    quote_menu = page.locator('a[href*="/retweet"], div[role="menuitem"]').filter(has_text="引用")
                    if quote_menu.count() == 0:
                        quote_menu = page.locator('div[role="menuitem"]').filter(has_text="Quote")

                    if quote_menu.count() > 0:
                        quote_menu.first.click()
                        time.sleep(1.5)
                        composer = page.locator('div[data-testid="tweetTextarea_0"]')
                        if composer.count() > 0:
                            self.human_type(composer.first, quote_text)
                            time.sleep(1.0)
                            send_btn = page.locator('button[data-testid="tweetButton"]')
                            if send_btn.count() > 0:
                                send_btn.first.click()
                                time.sleep(3.0)
                                return "SUCCESS", "取得ツイートに引用RT完了"
                else:
                    print("   ⚠️ 取得ツイートにリツイートボタンが見つかりません。")
            else:
                print("   ⚠️ 取得ツイートが見つかりませんでした。")
            print("   👉 プロフィール画面からの最新ツイート引用RTへフォールバックします...")

        # -------------------------------------------------------------
        # B. tweet_url 未指定、またはアクセス失敗時: プロフィール最新ポストへ
        # -------------------------------------------------------------
        profile_url = f"https://x.com/{clean_target}"
        print(f"🔁 最新ポストへの引用リツイート処理 (フォールバック): {profile_url}")

        # 0. プロフィールの描画完了を待機 (ラグ対策)
        self.wait_for_profile_ready(clean_target, timeout=20.0)

        try:
            current_url = page.url.lower().rstrip("/")
            expected_prefix = f"x.com/{clean_target.lower()}"
            if expected_prefix not in current_url:
                page.goto(profile_url, timeout=35000, wait_until="domcontentloaded")
                time.sleep(random.uniform(2.5, 4.0))
        except Exception as e:
            return "FAILED", f"アクセス失敗: {e}"

        tweet_articles = page.locator('article[data-testid="tweet"]')
        try:
            tweet_articles.first.wait_for(timeout=7000)
        except Exception:
            return "NO_POSTS", "ポストなし"

        first_tweet = tweet_articles.first
        retweet_btn = first_tweet.locator('button[data-testid="retweet"]')
        if retweet_btn.count() == 0:
            return "FAILED", "RTボタンなし"

        r_box = retweet_btn.first.bounding_box()
        if r_box:
            self.bezier_mouse_move(r_box["x"] + r_box["width"] / 2, r_box["y"] + r_box["height"] / 2)
            time.sleep(0.4)
            page.mouse.click(r_box["x"] + r_box["width"] / 2, r_box["y"] + r_box["height"] / 2)
        else:
            retweet_btn.first.click()

        time.sleep(1.2)
        quote_menu = page.locator('a[href*="/retweet"], div[role="menuitem"]').filter(has_text="引用")
        if quote_menu.count() == 0:
            quote_menu = page.locator('div[role="menuitem"]').filter(has_text="Quote")

        if quote_menu.count() > 0:
            quote_menu.first.click()
            time.sleep(1.5)
            composer = page.locator('div[data-testid="tweetTextarea_0"]')
            if composer.count() > 0:
                self.human_type(composer.first, quote_text)
                time.sleep(1.0)
                send_btn = page.locator('button[data-testid="tweetButton"]')
                if send_btn.count() > 0:
                    send_btn.first.click()
                    time.sleep(3.0)
                    return "SUCCESS", "引用RT完了"
        return "FAILED", "引用RT失敗"

    def bezier_mouse_move(self, end_x: float, end_y: float, steps: int = 25):
        """
        3次ベジェ曲線を用いた人間らしいマウス移動
        直線ではなく自然な曲率と微細なジッターを伴う
        """
        page = self.ensure_page()
        start_x = random.uniform(300, 600)
        start_y = random.uniform(300, 500)

        ctrl1_x = start_x + (end_x - start_x) * random.uniform(0.2, 0.4) + random.uniform(-40, 40)
        ctrl1_y = start_y + (end_y - start_y) * random.uniform(0.1, 0.3) + random.uniform(-40, 40)

        ctrl2_x = start_x + (end_x - start_x) * random.uniform(0.6, 0.8) + random.uniform(-30, 30)
        ctrl2_y = start_y + (end_y - start_y) * random.uniform(0.7, 0.9) + random.uniform(-30, 30)

        for i in range(steps + 1):
            t = i / steps
            x = (1 - t)**3 * start_x + 3 * (1 - t)**2 * t * ctrl1_x + 3 * (1 - t) * t**2 * ctrl2_x + t**3 * end_x
            y = (1 - t)**3 * start_y + 3 * (1 - t)**2 * t * ctrl1_y + 3 * (1 - t) * t**2 * ctrl2_y + t**3 * end_y
            
            x += random.uniform(-1.5, 1.5)
            y += random.uniform(-1.5, 1.5)

            try:
                page.mouse.move(x, y)
            except Exception:
                pass
            time.sleep(random.uniform(0.008, 0.025))

    def input_message_safely(self, composer_locator, text: str):
        """
        Twitter(X)のcontenteditable入力欄 (Draft.js / Lexical) への安全・確実なテキスト入力
        - 全文（複数行・改行・絵文字・記号含む）を確実に100%欠落なく入力
        - 途中送信（誤Enter）を完全防止（改行はShift+Enterで安全に挿入）
        - 全角文字・絵文字・長文も完全に反映（「一部しか送信されない」不具合を完全解消）
        """
        page = self.ensure_page()
        try:
            composer_locator.click()
            time.sleep(0.4)
        except Exception:
            pass

        lines = [l for l in str(text or "").splitlines()]
        clean_text = "\n".join(lines).strip()
        if not clean_text:
            return

        # 1. まず全行（複数行・改行・絵文字含む）をクリップボード貼付イベントで一括挿入
        pasted = False
        try:
            pasted = page.evaluate('''(data) => {
                const el = document.activeElement;
                if (!el) return false;
                const dt = new DataTransfer();
                dt.setData('text/plain', data.text);
                const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
                el.dispatchEvent(ev);
                return true;
            }''', {"text": clean_text})
            time.sleep(0.5)
            cur_val = page.evaluate("() => (document.activeElement ? document.activeElement.innerText || document.activeElement.textContent : '')")
            if len(str(cur_val).strip()) >= len(clean_text) * 0.7:
                pasted = True
            else:
                pasted = False
        except Exception:
            pasted = False

        # 2. クリップボード貼付が無効または不完全な場合：行単位の安全入力 (Shift+Enter 改行)
        if not pasted:
            try:
                page.keyboard.press("Control+A")
                time.sleep(0.05)
                page.keyboard.press("Backspace")
                time.sleep(0.1)
            except Exception:
                pass

            for i, line in enumerate(lines):
                if line:
                    try:
                        page.keyboard.insert_text(line)
                    except Exception:
                        page.keyboard.type(line)
                    time.sleep(random.uniform(0.05, 0.12))
                if i < len(lines) - 1:
                    # Twitter DM では Enter は送信になってしまうため、必ず Shift+Enter で改行する
                    page.keyboard.press("Shift+Enter")
                    time.sleep(random.uniform(0.08, 0.18))

        time.sleep(random.uniform(0.6, 1.2))

    def human_type(self, locator, text: str):
        """
        人間らしいタイピング:
        - ターゲット要素のクリック＆フォーカス
        - 1文字ごとの打鍵間隔ゆらぎ (40ms〜120ms)
        - 句読点（、。！？）や改行での小休止
        - React controlled input イベントの発火
        """
        page = self.ensure_page()
        if locator is not None:
            try:
                locator.click(force=True, timeout=3000)
                locator.focus()
                time.sleep(0.1)
            except Exception:
                pass
        for char in text:
            if char == "\n":
                page.keyboard.press("Shift+Enter")
            else:
                try:
                    page.keyboard.type(char)
                except Exception:
                    pass
            if char in ["、", ",", "，"]:
                time.sleep(random.uniform(0.2, 0.4))
            elif char in ["。", ".", "!", "?", "！", "？", "\n"]:
                time.sleep(random.uniform(0.35, 0.7))
            else:
                time.sleep(random.uniform(0.04, 0.11))

    def collect_targets_from_search(self, keyword: str, search_type: str = "最新", scroll_limit: int = 5, max_targets: int = 0) -> list[dict]:
        """
        1. 検索キーワードからターゲットをスクロール収集
        - 最新: https://x.com/search?q={keyword}&f=live
        - おすすめ: https://x.com/search?q={keyword}

        ★ 重要仕様:
        ・検索結果タイムライン (primaryColumn) 内に実際に出てきたツイートの投稿者および【そのツイート自身のURL (tweet_url)】を上から順番に抽出
        ・右サイドバーのおすすめユーザーや @elonmusk 等の無関係な推薦アカウントは一切除外
        ・プロモーション（広告）ツイートは除外
        ・max_targets (Excelの「ターゲット件数」) が指定されている場合、上限に達した時点でスクロールを即時完了
        """
        import urllib.parse
        page = self.ensure_page()
        clean_kw = str(keyword or "").strip()
        encoded_kw = urllib.parse.quote(clean_kw)
        if search_type in ["最新", "live", "Live"]:
            url = f"https://x.com/search?q={encoded_kw}&f=live"
        else:
            url = f"https://x.com/search?q={encoded_kw}"

        print(f"🔍 検索ページへ移動中 ({search_type}): {url}")
        try:
            page.goto(url, timeout=40000, wait_until="domcontentloaded")
        except Exception as ge:
            print(f"   ⚠️ 検索ページアクセス通知: {ge}")
        time.sleep(3)

        # 検索結果の投稿描画を待機（くるくる表示中は最大15秒じっくり待機）
        print("   ⏳ 検索結果（ツイート）の描画完了を待機中...")
        tweets_found = False
        start_wait = time.time()
        while time.time() - start_wait < 15.0:
            try:
                cnt = page.locator('article[data-testid="tweet"], div[data-testid="cellInnerDiv"]').count()
                if cnt > 0:
                    tweets_found = True
                    break
            except Exception:
                pass
            time.sleep(1.0)

        # 15秒待っても描画されず、真のエラー画面が出ている場合のみページ内再読み込み
        if not tweets_found and self.check_error_screen(page):
            self.recover_if_stuck(url, "検索結果の読み込みエラー")
            page = self.ensure_page()
            try:
                page.wait_for_selector('article[data-testid="tweet"], div[data-testid="cellInnerDiv"]', timeout=10000)
            except Exception:
                pass

        try:
            page.mouse.wheel(0, 300)
            time.sleep(1.0)
        except Exception:
            pass

        discovered = []
        seen = set()
        system_names = {
            "home", "explore", "notifications", "messages", "search", "settings", 
            "i", "privacy", "tos", "help", "support", "x", "twitter", "safety", "verified"
        }
        kw_lower = clean_kw.lower()
        if "elon" not in kw_lower and "musk" not in kw_lower:
            system_names.add("elonmusk")

        has_tried_top_fallback = False

        for scroll_idx in range(scroll_limit):
            # メインタイムライン (primaryColumn) 内のツイートのみを取得（サイドバーは完全除外）
            tweet_articles = page.locator('div[data-testid="primaryColumn"] article[data-testid="tweet"]').all()
            if not tweet_articles:
                tweet_articles = page.locator('main[role="main"] article[data-testid="tweet"]').all()
            if not tweet_articles:
                tweet_articles = page.locator('article[data-testid="tweet"]').all()

            for tweet in tweet_articles:
                try:
                    # 広告・プロモーションの除外
                    txt = tweet.inner_text()
                    if any(ad in txt for ad in ["プロモーション", "Promoted", "広告", "Ad"]):
                        continue

                    # ツイート投稿者のリンク抽出（複数戦略: User-Name > Tweet-User-Avatar > 内部プロフィールリンク）
                    links = []
                    user_box = tweet.locator('div[data-testid="User-Name"]')
                    if user_box.count() > 0:
                        links = user_box.locator('a[role="link"]').all()
                    if not links:
                        avatar_box = tweet.locator('div[data-testid="Tweet-User-Avatar"]')
                        if avatar_box.count() > 0:
                            links = avatar_box.locator('a[role="link"]').all()
                    if not links:
                        links = tweet.locator('a[role="link"][href^="/"]').all()

                    clean_u = ""
                    for link in links:
                        href = link.get_attribute("href")
                        if href and href.startswith("/") and not href.startswith("/i/") and "/status/" not in href and "/search" not in href and "/hashtag/" not in href:
                            username = href.strip("/").split("/")[0].split("?")[0]
                            cand_u = normalize_screen_name(username)
                            if cand_u and cand_u.lower() not in seen and cand_u.lower() not in system_names:
                                clean_u = cand_u
                                break
                    if not clean_u:
                        continue

                    # ★ ユーザー要件対応: 取得したツイート自身のパーマリンクURL抽出
                    tweet_url = ""
                    try:
                        time_els = tweet.locator('time')
                        if time_els.count() > 0:
                            parent_a = time_els.first.locator('xpath=ancestor::a[contains(@href, "/status/")]')
                            if parent_a.count() > 0:
                                th = parent_a.first.get_attribute("href")
                                if th and "/status/" in th:
                                    clean_th = th.split("?")[0].split("#")[0]
                                    tweet_url = f"https://x.com{clean_th}" if clean_th.startswith("/") else clean_th
                        if not tweet_url and user_box.count() > 0:
                            for sl in user_box.locator('a[href*="/status/"]').all():
                                sh = sl.get_attribute("href")
                                if sh and "/status/" in sh:
                                    clean_sh = sh.split("?")[0].split("#")[0]
                                    tweet_url = f"https://x.com{clean_sh}" if clean_sh.startswith("/") else clean_sh
                                    break
                        if not tweet_url:
                            for sl in tweet.locator('a[href*="/status/"]').all():
                                sh = sl.get_attribute("href")
                                if sh and "/status/" in sh and not any(ex in sh for ex in ["/analytics", "/photo/", "/video/"]):
                                    clean_sh = sh.split("?")[0].split("#")[0]
                                    tweet_url = f"https://x.com{clean_sh}" if clean_sh.startswith("/") else clean_sh
                                    break
                    except Exception:
                        pass

                    disp_name = ""
                    if user_box.count() > 0:
                        try:
                            ub_text = user_box.first.inner_text().strip()
                            ub_lines = [l.strip() for l in ub_text.splitlines() if l.strip()]
                            for l in ub_lines:
                                if l.startswith("@") or l.startswith("·") or l.startswith("・"):
                                    continue
                                if l in ["認証済みアカウント", "認証バッジ", "公式", "Verified account", "Verified"]:
                                    continue
                                disp_name = l
                                break
                        except Exception:
                            pass

                    seen.add(clean_u.lower())
                    discovered.append({
                        "screen_name": clean_u,
                        "tweet_url": tweet_url,
                        "display_name": disp_name
                    })
                    if max_targets > 0 and len(discovered) >= max_targets:
                        break
                except Exception:
                    continue

            print(f"   スクロール [{scroll_idx + 1}/{scroll_limit}]: 検索結果タイムラインから現在 {len(discovered)} 件抽出 (上から順)")
            if max_targets > 0 and len(discovered) >= max_targets:
                print(f"   🎯 Excel「ターゲット件数」上限 ({max_targets} 件) に達したため、収集を完了します。")
                break

            # もし「最新」タブで2回スクロールしても0件の場合、Top (おすすめ) 検索へ自動切り替え
            if not has_tried_top_fallback and scroll_idx >= 1 and len(discovered) == 0 and search_type in ["最新", "live", "Live"]:
                has_tried_top_fallback = True
                top_url = f"https://x.com/search?q={encoded_kw}"
                print(f"   ℹ️ 最新タブで0件のため、Top (おすすめ) 検索タブ ({top_url}) へ切り替えて探索します...")
                try:
                    page.goto(top_url, timeout=30000, wait_until="domcontentloaded")
                    time.sleep(2.5)
                except Exception:
                    pass
                continue

            try:
                page.mouse.wheel(0, random.randint(700, 1200))
            except Exception:
                pass
            time.sleep(random.uniform(1.8, 3.2))

        return discovered[:max_targets] if max_targets > 0 else discovered

    def collect_targets_from_replies(self, replies_url: str, scroll_limit: int = 5, max_targets: int = 0) -> list[dict]:
        """
        2. リプライURLからターゲットをスクロール収集 (例: https://x.com/Panda_ko7/with_replies)
        メインタイムライン上のリプライ投稿者および【そのリプライツイートのURL】を上から順に抽出
        """
        page = self.ensure_page()
        print(f"💬 リプライURLへアクセス中: {replies_url}")
        try:
            page.goto(replies_url, timeout=35000, wait_until="domcontentloaded")
        except Exception as ge:
            print(f"   ⚠️ リプライページアクセス通知: {ge}")
        # リプライツイートの描画完了を待機（最大12秒）
        print("   ⏳ リプライ一覧の描画完了を待機中...")
        tweets_found = False
        start_wait = time.time()
        while time.time() - start_wait < 12.0:
            try:
                if page.locator('article[data-testid="tweet"]').count() > 0:
                    tweets_found = True
                    break
            except Exception:
                pass
            time.sleep(1.0)

        if not tweets_found and self.check_error_screen(page):
            self.recover_if_stuck(replies_url, "リプライ一覧の読み込みエラー")
            page = self.ensure_page()

        discovered = []
        seen = set()
        system_names = {"home", "explore", "notifications", "messages", "search", "settings", "i", "privacy", "tos", "elonmusk"}

        for scroll_idx in range(scroll_limit):
            tweet_articles = page.locator('div[data-testid="primaryColumn"] article[data-testid="tweet"]').all()
            if not tweet_articles:
                tweet_articles = page.locator('article[data-testid="tweet"]').all()

            for tweet in tweet_articles:
                try:
                    user_box = tweet.locator('div[data-testid="User-Name"]')
                    if user_box.count() == 0:
                        continue
                    clean_u = ""
                    for link in user_box.locator('a[role="link"]').all():
                        href = link.get_attribute("href")
                        if href and href.startswith("/") and not href.startswith("/i/") and "/status/" not in href:
                            username = href.strip("/").split("/")[0].split("?")[0]
                            cand_u = normalize_screen_name(username)
                            if cand_u and cand_u.lower() not in seen and cand_u.lower() not in system_names:
                                clean_u = cand_u
                                break
                    if not clean_u:
                        continue

                    # ★ ユーザー要件対応: リプライツイート自身のURLを抽出
                    tweet_url = ""
                    try:
                        time_els = tweet.locator('time')
                        if time_els.count() > 0:
                            parent_a = time_els.first.locator('xpath=ancestor::a[contains(@href, "/status/")]')
                            if parent_a.count() > 0:
                                th = parent_a.first.get_attribute("href")
                                if th and "/status/" in th:
                                    clean_th = th.split("?")[0].split("#")[0]
                                    tweet_url = f"https://x.com{clean_th}" if clean_th.startswith("/") else clean_th
                        if not tweet_url:
                            for sl in user_box.locator('a[href*="/status/"]').all():
                                sh = sl.get_attribute("href")
                                if sh and "/status/" in sh:
                                    clean_sh = sh.split("?")[0].split("#")[0]
                                    tweet_url = f"https://x.com{clean_sh}" if clean_sh.startswith("/") else clean_sh
                                    break
                    except Exception:
                        pass

                    disp_name = ""
                    if user_box.count() > 0:
                        try:
                            ub_text = user_box.first.inner_text().strip()
                            ub_lines = [l.strip() for l in ub_text.splitlines() if l.strip()]
                            for l in ub_lines:
                                if l.startswith("@") or l.startswith("·") or l.startswith("・"):
                                    continue
                                if l in ["認証済みアカウント", "認証バッジ", "公式", "Verified account", "Verified"]:
                                    continue
                                disp_name = l
                                break
                        except Exception:
                            pass

                    seen.add(clean_u.lower())
                    discovered.append({
                        "screen_name": clean_u,
                        "tweet_url": tweet_url,
                        "display_name": disp_name
                    })
                    if max_targets > 0 and len(discovered) >= max_targets:
                        break
                except Exception:
                    continue

            print(f"   リプライ収集 [{scroll_idx + 1}/{scroll_limit}]: 現在 {len(discovered)} 件抽出 (上から順)")
            if max_targets > 0 and len(discovered) >= max_targets:
                print(f"   🎯 Excel「ターゲット件数」上限 ({max_targets} 件) に達したため、収集を完了します。")
                break

            try:
                page.mouse.wheel(0, random.randint(600, 1100))
            except Exception:
                pass
            time.sleep(random.uniform(1.8, 3.0))

        return discovered[:max_targets] if max_targets > 0 else discovered

    def collect_targets_from_follow(self, follow_url: str, scroll_limit: int = 5, max_targets: int = 0) -> list[dict]:
        """
        3. フォロー・フォロワーURLからターゲットをスクロール収集 (例: https://x.com/dekasegood03/following)
        メインカラム上のユーザーセルのみを上から順に抽出
        """
        page = self.ensure_page()
        print(f"👥 フォロー一覧URLへアクセス中: {follow_url}")
        try:
            page.goto(follow_url, timeout=35000, wait_until="domcontentloaded")
        except Exception as ge:
            print(f"   ⚠️ フォロー一覧ページアクセス通知: {ge}")
        # フォロー・フォロワー一覧の描画完了を待機（最大12秒）
        print("   ⏳ フォロー・フォロワー一覧の描画完了を待機中...")
        users_found = False
        start_wait = time.time()
        while time.time() - start_wait < 12.0:
            try:
                if page.locator('button[data-testid="UserCell"], [data-testid="UserCell"]').count() > 0:
                    users_found = True
                    break
            except Exception:
                pass
            time.sleep(1.0)

        if not users_found and self.check_error_screen(page):
            self.recover_if_stuck(follow_url, "フォロー一覧の読み込みエラー")
            page = self.ensure_page()

        discovered = []
        seen = set()
        system_names = {"home", "explore", "notifications", "messages", "search", "settings", "i", "privacy", "tos", "elonmusk"}

        for scroll_idx in range(scroll_limit):
            user_cells = page.locator('div[data-testid="primaryColumn"] button[data-testid="UserCell"] a, div[data-testid="primaryColumn"] div[data-testid="cellInnerDiv"] a[role="link"]').all()
            if not user_cells:
                user_cells = page.locator('button[data-testid="UserCell"] a, div[data-testid="cellInnerDiv"] a[role="link"]').all()

            for link in user_cells:
                try:
                    href = link.get_attribute("href")
                    if href and href.startswith("/") and not href.startswith("/i/") and "/status/" not in href:
                        username = href.strip("/").split("/")[0].split("?")[0]
                        clean_u = normalize_screen_name(username)
                        if clean_u and clean_u.lower() not in seen and clean_u.lower() not in system_names:
                            disp_name = ""
                            try:
                                cell_parent = link.locator('xpath=ancestor::*[@data-testid="UserCell"]')
                                if cell_parent.count() > 0:
                                    ctxt = cell_parent.first.inner_text().strip()
                                    for cl in ctxt.splitlines():
                                        cl = cl.strip()
                                        if cl and not cl.startswith("@") and cl not in ["フォロー", "フォロー中", "Following", "Follow"]:
                                            disp_name = cl
                                            break
                            except Exception:
                                pass
                            seen.add(clean_u.lower())
                            discovered.append({
                                "screen_name": clean_u,
                                "tweet_url": "",
                                "display_name": disp_name
                            })
                            if max_targets > 0 and len(discovered) >= max_targets:
                                break
                    if max_targets > 0 and len(discovered) >= max_targets:
                        break
                except Exception:
                    continue

            print(f"   フォロー一覧収集 [{scroll_idx + 1}/{scroll_limit}]: 現在 {len(discovered)} 件抽出 (上から順)")
            if max_targets > 0 and len(discovered) >= max_targets:
                print(f"   🎯 Excel「ターゲット件数」上限 ({max_targets} 件) に達したため、収集を完了します。")
                break

            try:
                page.mouse.wheel(0, random.randint(700, 1300))
            except Exception:
                pass
            time.sleep(random.uniform(1.8, 3.2))

        return discovered[:max_targets] if max_targets > 0 else discovered

    def close(self):
        """ブラウザの安全な終了（Cookie・IndexedDB暗号化鍵・ストレージの完全ディスクフラッシュ）"""
        try:
            self.save_storage_state()
            if self.page and not self.page.is_closed():
                try:
                    self.page.wait_for_timeout(500)
                except Exception:
                    pass
            if self.context:
                try:
                    self.context.close()
                except Exception:
                    pass
            if self.browser:
                try:
                    self.browser.close()
                except Exception:
                    pass
            if self.playwright:
                try:
                    self.playwright.stop()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self.page = None
            self.context = None
            self.browser = None
            self.playwright = None
            time.sleep(0.5)

    def restart_browser(self):
        """ブラウザウィンドウを一度安全に閉じて、再起動（Cookie/ログインセッション完全維持）"""
        print("🚪 ブラウザウィンドウを一度終了し、再起動します...")
        try:
            self.close()
        except Exception as e:
            print(f"   (ブラウザ終了通知: {e})")
        time.sleep(1.5)
        self.start(self.user_data_dir)
        print("   ✅ ブラウザウィンドウを再起動しました（ログイン状態維持）")
        return self.ensure_page()

    def check_error_screen(self, page: Page = None) -> bool:
        """Xの『問題が発生しました』エラー画面を厳格に判定（通常ローディングのくるくるやツイート本文との誤検知を完全防止）"""
        try:
            p = page or self.ensure_page()
            if p is None or p.is_closed():
                return False

            # ツイートやプロフィール等のメインコンテンツが既に描画されている場合はエラー画面ではない
            try:
                if p.locator('article[data-testid="tweet"], div[data-testid="cellInnerDiv"], div[data-testid="UserName"]').count() > 0:
                    return False
            except Exception:
                pass

            # Xの真のエラー画面（empty_state または error-detail）
            empty_btn = p.locator('button[data-testid="empty_state_button_text"]')
            if empty_btn.count() > 0 and empty_btn.first.is_visible():
                return True

            err_detail = p.locator('div[data-testid="error-detail"]')
            if err_detail.count() > 0 and err_detail.first.is_visible():
                return True

            retry_btn = p.locator('button:has-text("やりなおす"), button:has-text("再読み込み")')
            if retry_btn.count() > 0 and retry_btn.first.is_visible():
                return True
        except Exception:
            pass
        return False

    def recover_if_stuck(self, current_url: str = "", reason: str = "") -> bool:
        """
        エラー画面や読み込み遅延を検知した場合に、ブラウザウィンドウを閉じずに
        ページ内での「やりなおす」クリックまたはページ再読み込み (page.reload) で安全に復帰する
        """
        print(f"🔄 [リカバリ] Xの読み込み遅延/エラー画面を検知 (理由: {reason or '読み込み遅延'})。ページ内再試行を実行します...")
        page = self.ensure_page()
        if page is None or page.is_closed():
            return False

        # 1. まず「やりなおす」ボタンがあればクリック
        try:
            retry_btn = page.locator('button[data-testid="empty_state_button_text"], button:has-text("やりなおす"), button:has-text("再読み込み"), button:has-text("再試行"), button:has-text("Retry")')
            if retry_btn.count() > 0 and retry_btn.first.is_visible():
                print("   👉 「やりなおす」ボタンをクリックして再描画を試行します...")
                retry_btn.first.click()
                time.sleep(3.0)
                if not self.check_error_screen(page):
                    print("   ✨ 「やりなおす」クリックでページが正常回復しました！")
                    return True
        except Exception:
            pass

        # 2. 「やりなおす」ボタンがない、または回復しない場合はブラウザウィンドウを維持したままページ内再読み込み (F5)
        print("   🔄 ウィンドウは維持したまま、ページ内再読み込み (リロード) を実行します...")
        try:
            if current_url and "x.com" in current_url:
                page.goto(current_url, timeout=30000, wait_until="domcontentloaded")
            else:
                page.reload(timeout=30000, wait_until="domcontentloaded")
            time.sleep(3.0)
            print("   ✅ ページの再読み込みを完了しました")
            return True
        except Exception as e:
            print(f"   ⚠️ ページ再読み込み通知: {e}")
            return False

