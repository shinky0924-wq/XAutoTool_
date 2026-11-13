# -*- coding: utf-8 -*-
"""
AI文章生成エンジン統合モジュール (ai_generator.py)
対応エンジン:
  1. gemini         : Google Gemini API または AI専用ウィンドウ(Gemini Web / Chrome内蔵AI)
  2. groq           : Groq Cloud (Llama 3.3 70B 超高速推論)
  3. openrouter     : OpenRouter (各種LLM集約プラットフォーム)
  4. ollama         : Ollama (完全ローカルLLM / オフライン)
  5. chrome_builtin : Chrome 内蔵 AI (Gemini Nano / window.ai / Prompt API - APIキー不要)
  6. grok           : xAI Grok (API または AI専用ウィンドウ Grok Web)
  7. lmstudio       : LM Studio (ローカル推論サーバー)

機能:
  ・ターゲットポストURLおよびツイート本文から、最適な共感リプライ・引用RT文を自動生成
  ・AI専用ウィンドウ (Profile: browser_profiles/ai_worker) 連携モード対応
    (アカウントAでURL/本文抽出 ➔ AI専用ウィンドウで生成・クリップボード連携 ➔ アカウントAで実行)
  ・純粋標準ライブラリ (urllib.request / json) で動作するため外部pip不要
"""

import os
import sys
import json
import time
import threading
import urllib.request
import urllib.error
from typing import Optional, Tuple


class AIGenerator:
    """各社AIエンジンおよびローカルLLM・ブラウザ内蔵AIを統括する文章生成クラス"""

    @staticmethod
    def build_prompt(
        post_url: str = "",
        post_text: str = "",
        action_type: str = "reply",
        target_name: str = "",
        custom_instruction: str = ""
    ) -> str:
        action_label = "リプライ（返信）" if "reply" in action_type.lower() else "引用リツイート（感想・紹介）"

        instruction = custom_instruction.strip() if custom_instruction else (
            "投稿者のポスト内容に深く共感し、親しみやすく丁寧な日本語の1〜2文で自然な会話口調を作成してください。"
            "ハッシュタグ（#）、絵文字の過剰使用、不自然な敬語、宣伝的な表現は避けてください。"
        )

        prompt = f"""あなたはX(Twitter)の親しみやすく礼儀正しいユーザーです。
以下の投稿に対して、魅力的な{action_label}のメッセージを作成してください。

【対象の投稿情報】
・投稿者: @{target_name if target_name else 'ユーザー'}
・投稿URL: {post_url if post_url else 'なし'}
・投稿本文:
\"\"\"
{post_text.strip() if post_text and post_text.strip() else '（投稿本文取得不可・投稿者名や文脈に合わせて自然な応援/共感メッセージを作成）'}
\"\"\"

【指示】
{instruction}

※出力は送信するメッセージ本文のみを1〜2行（140文字以内）で返してください。挨拶や前置き（例: 「了解しました」「返信案:」）や引用符などの余計な注釈は一切含めないでください。"""
        return prompt

    @staticmethod
    def has_api_key(engine: str, api_key: str = "") -> bool:
        eng = (engine or "").lower().strip()
        key = (api_key or "").strip()
        if eng in ["ollama", "local_ollama", "lmstudio", "lm_studio", "local"]:
            return True
        if key:
            return True
        if eng in ["gemini", "gemini_api", "google"]:
            return bool(os.environ.get("GEMINI_API_KEY", "").strip())
        if eng in ["groq", "groq_cloud"]:
            return bool(os.environ.get("GROQ_API_KEY", "").strip())
        if eng in ["openrouter", "open_router"]:
            return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
        if eng in ["grok", "xai"]:
            return bool(os.environ.get("XAI_API_KEY", "").strip())
        return False

    @classmethod
    def generate_comment(
        cls,
        engine: str = "gemini",
        post_url: str = "",
        post_text: str = "",
        action_type: str = "reply",
        target_name: str = "",
        api_key: str = "",
        custom_prompt: str = "",
        page = None,
        use_dedicated_window: bool = False
    ) -> Tuple[bool, str]:
        """
        指定されたAIエンジンを使用して返信・引用文章を生成
        戻り値: (成功フラグ: bool, 生成テキストまたはエラーメッセージ: str)
        """
        eng = (engine or "gemini").lower().strip()
        if eng in ["none", "off", "無効", ""]:
            return False, "AIエンジンが未指定または無効です"

        prompt = cls.build_prompt(post_url, post_text, action_type, target_name, custom_prompt)

        try:
            # 1. Google Gemini API (推奨)
            if eng in ["gemini", "gemini_api", "google"]:
                key = (api_key or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
                if not key and page is not None:
                    # APIキー空欄時はChrome内蔵AIを自動試行
                    try:
                        text = cls._call_chrome_builtin(page, prompt)
                        clean = cls._sanitize_output(text)
                        if clean:
                            return True, clean
                    except Exception:
                        pass
                text = cls._call_gemini(prompt, api_key)
                return True, cls._sanitize_output(text)

            # 2. Groq Cloud (超高速推論)
            elif eng in ["groq", "groq_cloud"]:
                text = cls._call_groq(prompt, api_key)
                return True, cls._sanitize_output(text)

            # 3. OpenRouter (各種LLM集約)
            elif eng in ["openrouter", "open_router"]:
                text = cls._call_openrouter(prompt, api_key)
                return True, cls._sanitize_output(text)

            # 4. Ollama (ローカルLLM)
            elif eng in ["ollama", "local_ollama"]:
                endpoint = api_key if api_key.startswith("http") else "http://localhost:11434"
                text = cls._call_ollama(prompt, endpoint)
                return True, cls._sanitize_output(text)

            # 5. Chrome 内蔵 AI (Gemini Nano / window.ai)
            elif eng in ["chrome_builtin", "chrome_ai", "chrome", "builtin", "gemini_nano"]:
                if page is not None:
                    text = cls._call_chrome_builtin(page, prompt)
                    return True, cls._sanitize_output(text)
                else:
                    return False, "Chrome内蔵AIの実行にはブラウザページが必要です"

            # 6. xAI Grok
            elif eng in ["grok", "xai"]:
                text = cls._call_grok(prompt, api_key)
                return True, cls._sanitize_output(text)

            # 7. LM Studio (ローカル)
            elif eng in ["lmstudio", "lm_studio", "local"]:
                endpoint = api_key if api_key.startswith("http") else "http://localhost:1234"
                text = cls._call_lmstudio(prompt, endpoint)
                return True, cls._sanitize_output(text)

            else:
                return False, f"未対応のAIエンジンです: {engine}"

        except Exception as e:
            err_msg = str(e)
            return False, f"AI生成エラー ({eng}): {err_msg}"

    # -------------------------------------------------------------
    # 各エンジン別 実装
    # -------------------------------------------------------------

    @staticmethod
    def _call_gemini(prompt: str, api_key: str = "") -> str:
        key = api_key.strip() or os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise ValueError("Gemini APIキーが設定されていません。Excelの「AI APIキー」に入力するか「AI専用ウィンドウ連携=1」を使用してください。")

        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 220,
                "topP": 0.95
            }
        }

        last_err = None
        for model_name in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=18) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    candidates = data.get("candidates", [])
                    if not candidates:
                        raise ValueError("Geminiから有効な回答が得られませんでした")
                    content = candidates[0].get("content", {})
                    parts = content.get("parts", [])
                    if not parts:
                        raise ValueError("Geminiの回答パーツが空です")
                    return parts[0].get("text", "")
            except urllib.error.HTTPError as he:
                last_err = he
                if he.code == 404:
                    continue
                raise
            except Exception as e:
                last_err = e
                raise
        raise last_err or ValueError("Gemini API呼び出しに失敗しました")

    @staticmethod
    def _call_groq(prompt: str, api_key: str = "") -> str:
        key = api_key.strip() or os.environ.get("GROQ_API_KEY", "").strip()
        if not key:
            raise ValueError("Groq APIキーが設定されていません。Excelの「AI APIキー」または環境変数 GROQ_API_KEY に入力してください。")

        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": "あなたはX(Twitter)の親切なユーザーです。自然で好意的な日本語の返信本文のみを出力します。"},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 160,
            "temperature": 0.7
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}"
            }
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]

    @staticmethod
    def _call_openrouter(prompt: str, api_key: str = "") -> str:
        key = api_key.strip() or os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise ValueError("OpenRouter APIキーが設定されていません。Excelの「AI APIキー」または環境変数 OPENROUTER_API_KEY に入力してください。")

        url = "https://openrouter.ai/api/v1/chat/completions"
        payload = {
            "model": "google/gemini-2.0-flash-exp:free",
            "messages": [
                {"role": "system", "content": "あなたはX(Twitter)の自然なユーザーです。返信本文のみを出力します。"},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 160,
            "temperature": 0.7
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "HTTP-Referer": "https://github.com/shinky0924-wq/XAutoTool_",
                "X-Title": "XAutoTool"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]

    @staticmethod
    def _call_ollama(prompt: str, endpoint: str = "http://localhost:11434") -> str:
        base = endpoint.rstrip("/")
        url = f"{base}/api/generate"
        payload = {
            "model": "llama3",
            "prompt": prompt,
            "stream": False
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "")

    @staticmethod
    def _call_chrome_builtin(page, prompt: str) -> str:
        """
        Chrome 128+ 内蔵の Gemini Nano Prompt API (window.ai.languageModel / LanguageModel) を直接実行
        """
        js_code = """
        async (promptText) => {
            const ai = window.ai || (window.chrome && window.chrome.ai);
            if (ai && ai.languageModel) {
                const capabilities = await ai.languageModel.capabilities();
                if (capabilities.available === 'no') {
                    throw new Error("Chrome 内蔵 AI はご利用の端末で現在サポートされていません");
                }
                const session = await ai.languageModel.create();
                const result = await session.prompt(promptText);
                if (session.destroy) session.destroy();
                return result;
            }
            if (window.LanguageModel) {
                const avail = window.LanguageModel.availability ? await window.LanguageModel.availability() : 'available';
                if (avail === 'unavailable' || avail === 'no') {
                    throw new Error("Chrome LanguageModel が利用不可です");
                }
                const session = await window.LanguageModel.create();
                const result = await session.prompt(promptText);
                if (session.destroy) session.destroy();
                return result;
            }
            throw new Error("Chrome 内蔵 AI (window.ai) が未有効化です");
        }
        """
        res = page.evaluate(js_code, prompt)
        return str(res)

    @staticmethod
    def _call_grok(prompt: str, api_key: str = "") -> str:
        key = api_key.strip() or os.environ.get("XAI_API_KEY", "").strip()
        if not key:
            raise ValueError("xAI Grok APIキーが設定されていません。Excelの「AI APIキー」に入力してください。")

        url = "https://api.x.ai/v1/chat/completions"
        payload = {
            "model": "grok-beta",
            "messages": [
                {"role": "system", "content": "あなたは親しみやすいXユーザーです。返信本文のみを出力します。"},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 150,
            "temperature": 0.7
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]

    @staticmethod
    def _call_lmstudio(prompt: str, endpoint: str = "http://localhost:1234") -> str:
        base = endpoint.rstrip("/")
        url = f"{base}/v1/chat/completions"
        payload = {
            "messages": [
                {"role": "system", "content": "返信本文のみを出力してください。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 150
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]

    @staticmethod
    def _sanitize_output(text: str) -> str:
        """生成結果から引用符や余分な見出しをクリーニング"""
        clean = (text or "").strip()
        # 前後の引用符を除去
        if (clean.startswith('"') and clean.endswith('"')) or (clean.startswith('「') and clean.endswith('」')):
            clean = clean[1:-1].strip()
        # 前置きの除去 (例: "返信案: 〜", "了解しました。〜")
        prefixes = ["返信案:", "リプライ案:", "回答:", "返信:", "AI:", "了解しました。", "了解しました！", "Gemini の回答", "Gemini:"]
        for p in prefixes:
            if clean.startswith(p):
                clean = clean[len(p):].strip()
        if (clean.startswith('"') and clean.endswith('"')) or (clean.startswith('「') and clean.endswith('」')):
            clean = clean[1:-1].strip()
        # Xポスト文字数上限対策（全角ベースで長すぎる場合は自然に切る）
        if len(clean) > 140:
            clean = clean[:139].rstrip() + "…"
        return clean


class AIDedicatedWindowManager:
    """
    AI専用ウィンドウ (Profile: browser_profiles/ai_worker) 管理クラス
    アカウントAのウィンドウと分離し、AI専用のブラウザウィンドウで
    Gemini Web / Grok Web / API連携・クリップボード連携を統括します。
    """
    _thread_local = threading.local()

    @classmethod
    def _get_state(cls):
        if not hasattr(cls._thread_local, "ai_context"):
            cls._thread_local.ai_context = None
            cls._thread_local.ai_page = None
        return cls._thread_local

    @classmethod
    def _cleanup_locks(cls, profile_dir: str):
        for lock_name in ["SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"]:
            p = os.path.join(profile_dir, lock_name)
            try:
                if os.path.exists(p) or os.path.islink(p):
                    os.remove(p)
            except Exception:
                pass

    @classmethod
    def get_or_create_ai_page(
        cls,
        playwright,
        headless: bool = False,
        base_dir: str = ".",
        initial_engine: str = "gemini",
        has_api_key: bool = False,
        slot_id: int = 1
    ):
        """AI専用プロファイル (browser_profiles/ai_worker) のブラウザコンテキストとページを取得・起動"""
        st = cls._get_state()
        if st.ai_page is not None:
            try:
                if not st.ai_page.is_closed():
                    return st.ai_page
            except Exception:
                pass
            st.ai_page = None
            st.ai_context = None

        prof_name = "ai_worker" if slot_id <= 1 else f"ai_worker_{slot_id}"
        ai_profile_dir = os.path.join(base_dir, "browser_profiles", prof_name)
        legacy_dir = os.path.join(base_dir, "profiles", prof_name)
        if not os.path.exists(ai_profile_dir) and os.path.exists(legacy_dir):
            ai_profile_dir = legacy_dir
        os.makedirs(ai_profile_dir, exist_ok=True)
        cls._cleanup_locks(ai_profile_dir)

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--enable-features=PromptAPIForGeminiNano,OptimizationGuideModelDownloading",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--window-position=80,60",
        ]

        candidates = [
            ("Google Chrome (channel='chrome')", {"channel": "chrome"}),
            ("Microsoft Edge (channel='msedge')", {"channel": "msedge"}),
            ("Playwright Chromium (同梱)", {}),
        ]

        last_err = None
        for label, extra in candidates:
            try:
                print(f"🪟 [AI専用ウィンドウ] {label} で起動試行中 (プロファイル: {prof_name})...")
                st.ai_context = playwright.chromium.launch_persistent_context(
                    user_data_dir=ai_profile_dir,
                    headless=headless,
                    args=launch_args,
                    ignore_default_args=["--enable-automation"],
                    viewport={"width": 980, "height": 760},
                    **extra
                )
                print(f"   ✅ AI専用ウィンドウの起動に成功しました！ ({label})")
                break
            except Exception as le:
                last_err = le
                print(f"   ⚠️ AI専用ウィンドウ起動スキップ ({label}): {le}")
                continue

        if st.ai_context is None:
            raise RuntimeError(f"AI専用ウィンドウの起動に失敗しました: {last_err}")

        if st.ai_context.pages:
            st.ai_page = st.ai_context.pages[0]
        else:
            st.ai_page = st.ai_context.new_page()

        try:
            st.ai_page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        except Exception:
            pass

        # APIキー未設定時は対応Webサービス画面を即座に開いて待機状態にする
        eng = (initial_engine or "gemini").lower().strip()
        try:
            cur_url = st.ai_page.url or ""
            if not has_api_key and (not cur_url or cur_url == "about:blank"):
                if eng in ["gemini", "gemini_api", "google", "chrome_builtin", "chrome_ai"]:
                    print("🌐 [AI専用ウィンドウ] Google Gemini (https://gemini.google.com/app) を開いています...")
                    st.ai_page.goto("https://gemini.google.com/app", timeout=35000, wait_until="domcontentloaded")
                elif eng in ["grok", "xai"]:
                    print("🌐 [AI専用ウィンドウ] xAI Grok (https://x.com/i/grok) を開いています...")
                    st.ai_page.goto("https://x.com/i/grok", timeout=35000, wait_until="domcontentloaded")
        except Exception as nav_e:
            print(f"   ⚠️ AI専用ウィンドウ初期ページ遷移通知: {nav_e}")

        return st.ai_page

    @classmethod
    def close_ai_window(cls):
        """AI専用ウィンドウを安全に閉じる（プロファイル・ログイン状態保存）"""
        st = cls._get_state()
        try:
            if st.ai_context is not None:
                st.ai_context.close()
        except Exception:
            pass
        finally:
            st.ai_page = None
            st.ai_context = None

    @classmethod
    def _find_visible_input(cls, page, selectors: list[str]):
        for sel in selectors:
            try:
                loc = page.locator(sel)
                cnt = loc.count()
                for i in range(min(cnt, 5)):
                    el = loc.nth(i)
                    if el.is_visible():
                        return el
            except Exception:
                continue
        return None

    @classmethod
    def _generate_via_gemini_web(cls, ai_page, prompt: str) -> Tuple[bool, str]:
        """AI専用ウィンドウ上の Google Gemini Web UI を使って文章を生成"""
        try:
            cur_url = ai_page.url or ""
            if "gemini.google.com" not in cur_url:
                print("🌐 [AI専用ウィンドウ] Gemini画面 (https://gemini.google.com/app) へ移動します...")
                ai_page.goto("https://gemini.google.com/app", timeout=35000, wait_until="domcontentloaded")
                time.sleep(2.5)
            else:
                # 連続実行時は会話履歴が汚れないように「新しいチャット」があれば押す（なければそのまま入力）
                try:
                    new_chat = ai_page.locator('a[href="/app"], button[aria-label*="新しいチャット"], button[aria-label*="New chat"], span:has-text("新しいチャット")')
                    if new_chat.count() > 0 and new_chat.first.is_visible():
                        new_chat.first.click(timeout=2500)
                        time.sleep(1.2)
                except Exception:
                    pass

            input_selectors = [
                'div.ql-editor[contenteditable="true"]',
                'rich-textarea div[contenteditable="true"]',
                'div[role="textbox"][contenteditable="true"]',
                'textarea[placeholder*="Gemini"]',
                'textarea'
            ]

            input_el = None
            login_notified = False
            wait_start = time.time()
            # 入力欄が出ない場合（未ログイン時など）は最大180秒ユーザーの手動Googleログインを待機
            while time.time() - wait_start < 180.0:
                input_el = cls._find_visible_input(ai_page, input_selectors)
                if input_el is not None:
                    break

                # 利用規約/同意ポップアップがあれば確認ログを出さずそのまま待機（ログイン画面なら案内）
                if not login_notified:
                    cur = ai_page.url or ""
                    try:
                        body_txt = ai_page.inner_text("body")[:500]
                    except Exception:
                        body_txt = ""
                    if "accounts.google.com" in cur or "ログイン" in body_txt or "Sign in" in body_txt:
                        print("🔑 【AI専用ウィンドウ】Geminiを使用するためGoogleアカウントにログインしてください。")
                        print("   👉 開いたAI専用ウィンドウでログインを完了すると、プロファイルに保存され自動で文章生成を再開します（最大180秒待機）...")
                        login_notified = True
                time.sleep(1.5)

            if input_el is None:
                return False, "Geminiの入力欄が検出できませんでした（Googleログイン未完了またはUI変更）"

            # 送信前のモデル回答数をセレクタごとに記録
            resp_selectors = [
                'model-response message-content',
                'model-response .model-response-text',
                'model-response',
                'message-content',
                '.response-content'
            ]
            prev_counts = {}
            for rsel in resp_selectors:
                try:
                    prev_counts[rsel] = ai_page.locator(rsel).count()
                except Exception:
                    prev_counts[rsel] = 0

            # プロンプトを1行/改行安全化（Enter送信誤爆を防ぐため改行をスペースに変換）
            single_line_prompt = " ".join([ln.strip() for ln in prompt.splitlines() if ln.strip()])
            input_el.click(timeout=4000)
            time.sleep(0.3)
            try:
                ai_page.keyboard.press("Control+A")
                ai_page.keyboard.press("Backspace")
                time.sleep(0.1)
            except Exception:
                pass

            try:
                ai_page.keyboard.insert_text(single_line_prompt)
            except Exception:
                input_el.fill(single_line_prompt)
            time.sleep(0.6)

            # 送信ボタンクリックまたはEnter
            sent = False
            send_selectors = [
                'button[aria-label*="プロンプトを送信"]',
                'button[aria-label*="送信"]',
                'button[aria-label*="Send"]',
                'button.send-button',
                'button[data-testid="send-button"]'
            ]
            for ssel in send_selectors:
                try:
                    sbtn = ai_page.locator(ssel)
                    if sbtn.count() > 0 and sbtn.first.is_visible() and sbtn.first.is_enabled():
                        sbtn.first.click(timeout=3000)
                        sent = True
                        break
                except Exception:
                    continue
            if not sent:
                ai_page.keyboard.press("Enter")

            # 生成完了待機 (最大45秒)
            print("   🤖 [AI専用ウィンドウ] Geminiでリプライ/引用文を生成中...")
            gen_start = time.time()
            last_txt = ""
            stable_ticks = 0
            while time.time() - gen_start < 45.0:
                time.sleep(0.8)
                cur_txt = ""
                for rsel in resp_selectors:
                    try:
                        loc = ai_page.locator(rsel)
                        cnt = loc.count()
                        if cnt > prev_counts.get(rsel, 0):
                            candidate = loc.last.inner_text().strip()
                            if candidate:
                                cur_txt = candidate
                                break
                    except Exception:
                        continue

                if cur_txt:
                    # ストリーミング中（「停止」ボタンが見える）かチェック
                    is_streaming = False
                    try:
                        stop_btn = ai_page.locator('button[aria-label*="回答を停止"], button[aria-label*="停止"], button[aria-label*="Stop"]')
                        if stop_btn.count() > 0 and stop_btn.first.is_visible():
                            is_streaming = True
                    except Exception:
                        pass

                    if cur_txt == last_txt and not is_streaming:
                        stable_ticks += 1
                        if stable_ticks >= 2:
                            clean_out = AIGenerator._sanitize_output(cur_txt)
                            if clean_out:
                                return True, clean_out
                    else:
                        stable_ticks = 0
                        last_txt = cur_txt

            if last_txt:
                clean_out = AIGenerator._sanitize_output(last_txt)
                if clean_out:
                    return True, clean_out
            return False, "Gemini Webからの回答取得がタイムアウトしました"
        except Exception as e:
            return False, f"Gemini Web連携エラー: {e}"

    @classmethod
    def _generate_via_grok_web(cls, ai_page, prompt: str) -> Tuple[bool, str]:
        """AI専用ウィンドウ上の Grok (x.com/i/grok) を使って文章を生成"""
        try:
            if "x.com/i/grok" not in (ai_page.url or ""):
                ai_page.goto("https://x.com/i/grok", timeout=35000, wait_until="domcontentloaded")
                time.sleep(2.5)
            input_el = cls._find_visible_input(ai_page, ['textarea', 'div[contenteditable="true"]', 'div[role="textbox"]'])
            if input_el is None:
                return False, "Grok画面の入力欄が見つかりません（X未ログイン等）"
            single_line_prompt = " ".join([ln.strip() for ln in prompt.splitlines() if ln.strip()])
            input_el.click()
            time.sleep(0.2)
            ai_page.keyboard.insert_text(single_line_prompt)
            time.sleep(0.5)
            ai_page.keyboard.press("Enter")
            time.sleep(6.0)
            # 応答ブロック探索
            for sel in ['[data-testid="grokMessage"]', 'div[dir="auto"]', 'article']:
                try:
                    loc = ai_page.locator(sel)
                    if loc.count() > 0:
                        txt = loc.last.inner_text().strip()
                        if txt and txt != single_line_prompt:
                            clean_out = AIGenerator._sanitize_output(txt)
                            if clean_out:
                                return True, clean_out
                except Exception:
                    continue
            return False, "Grok Webから回答を取得できませんでした"
        except Exception as e:
            return False, f"Grok Web連携エラー: {e}"

    @classmethod
    def generate_and_copy_via_ai_window(
        cls,
        playwright,
        post_url: str,
        post_text: str,
        engine: str = "gemini",
        action_type: str = "reply",
        target_name: str = "",
        api_key: str = "",
        custom_prompt: str = "",
        headless: bool = False,
        base_dir: str = ".",
        slot_id: int = 1
    ) -> Tuple[bool, str]:
        """
        AI専用ウィンドウで文章を生成し、クリップボードおよび戻り値として返却
        """
        eng = (engine or "gemini").lower().strip()
        if eng in ["none", "off", "無効", ""]:
            return False, "AIエンジンが未指定または無効です"

        has_key = AIGenerator.has_api_key(eng, api_key)
        try:
            ai_page = cls.get_or_create_ai_page(
                playwright,
                headless=headless,
                base_dir=base_dir,
                initial_engine=eng,
                has_api_key=has_key,
                slot_id=slot_id
            )

            ok = False
            result_text = ""

            # 1. APIキー設定済み または ローカルLLM の場合: APIで高速生成し、AI専用ウィンドウにも表示・クリップボード連携
            if has_key and eng not in ["chrome_builtin", "chrome_ai", "chrome", "builtin", "gemini_nano"]:
                ok, result_text = AIGenerator.generate_comment(
                    engine=eng,
                    post_url=post_url,
                    post_text=post_text,
                    action_type=action_type,
                    target_name=target_name,
                    api_key=api_key,
                    custom_prompt=custom_prompt,
                    page=ai_page,
                    use_dedicated_window=True
                )
                if ok and result_text:
                    try:
                        preview_html = f"""<html><body style='font-family:Meiryo,sans-serif;padding:24px;background:#f8fafc;'>
                        <h2 style='color:#312e81;'>AI専用ウィンドウ 生成結果 ({eng})</h2>
                        <p><b>対象:</b> @{target_name} / {post_url}</p>
                        <div style='background:#fff;border:1px solid #cbd5e1;padding:12px;border-radius:8px;margin-bottom:12px;'>
                        <b>元ポスト:</b><br>{(post_text or '').replace('<','&lt;')}</div>
                        <div style='background:#eef2ff;border:2px solid #6366f1;padding:14px;border-radius:8px;'>
                        <b>生成文章:</b><br>{result_text.replace('<','&lt;')}</div></body></html>"""
                        ai_page.set_content(preview_html)
                    except Exception:
                        pass

            # 2. Chrome内蔵AI指定時
            elif eng in ["chrome_builtin", "chrome_ai", "chrome", "builtin", "gemini_nano"]:
                ok, result_text = AIGenerator.generate_comment(
                    engine=eng,
                    post_url=post_url,
                    post_text=post_text,
                    action_type=action_type,
                    target_name=target_name,
                    api_key=api_key,
                    custom_prompt=custom_prompt,
                    page=ai_page,
                    use_dedicated_window=True
                )
                if not ok:
                    prompt = AIGenerator.build_prompt(post_url, post_text, action_type, target_name, custom_prompt)
                    ok, result_text = cls._generate_via_gemini_web(ai_page, prompt)

            # 3. APIキー未設定の Gemini / Grok の場合: AI専用ウィンドウ上のWeb UIで生成！
            else:
                prompt = AIGenerator.build_prompt(post_url, post_text, action_type, target_name, custom_prompt)
                if eng in ["grok", "xai"]:
                    ok, result_text = cls._generate_via_grok_web(ai_page, prompt)
                else:
                    ok, result_text = cls._generate_via_gemini_web(ai_page, prompt)
                    if not ok:
                        # Gemini Webが失敗した場合、Chrome内蔵AIも最後の手段として試行
                        try:
                            txt = AIGenerator._call_chrome_builtin(ai_page, prompt)
                            clean = AIGenerator._sanitize_output(txt)
                            if clean:
                                ok, result_text = True, clean
                        except Exception:
                            pass

            if ok and result_text:
                try:
                    ai_page.evaluate("(text) => { try { navigator.clipboard && navigator.clipboard.writeText(text); } catch(e){} }", result_text)
                except Exception:
                    pass
                return True, result_text
            else:
                return False, result_text

        except Exception as e:
            return False, f"AI専用ウィンドウエラー: {str(e)}"
