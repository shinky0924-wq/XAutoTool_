# -*- coding: utf-8 -*-
"""
AI文章生成エンジン統合モジュール (ai_generator.py)
対応エンジン:
  1. gemini         : Google Gemini API (gemini-1.5-flash / gemini-2.0-flash - 推奨・高速・高精度)
  2. groq           : Groq Cloud (Llama 3.3 70B 超高速推論)
  3. openrouter     : OpenRouter (各種LLM集約プラットフォーム)
  4. ollama         : Ollama (完全ローカルLLM / オフライン)
  5. chrome_builtin : Chrome 内蔵 AI (Gemini Nano / window.ai / Prompt API - APIキー不要)
  6. grok           : xAI Grok (X公式連携)
  7. lmstudio       : LM Studio (ローカル推論サーバー)

機能:
  ・ターゲットポストURLおよびツイート本文から、最適な共感リプライ・引用RT文を自動生成
  ・AI専用ウィンドウ (Profile: profiles/ai_worker) 連携モード対応
    (アカウントAでURL抽出 ➔ AI専用ウィンドウで生成・クリップボード連携 ➔ アカウントAで実行)
  ・純粋標準ライブラリ (urllib.request / json) で動作するため外部pip不要
"""

import os
import sys
import json
import time
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
{post_text.strip() if post_text.strip() else '（ツイート本文取得中）'}
\"\"\"

【指示】
{instruction}

※出力は送信するメッセージ本文のみを1〜2行で返してください。挨拶や前置き（例: 「了解しました」「返信案:」）などの余計な注釈は一切含めないでください。"""
        return prompt

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
            raise ValueError("Gemini APIキーが設定されていません。Excelの「AI APIキー」または環境変数 GEMINI_API_KEY に入力してください。")

        # gemini-1.5-flash または gemini-2.0-flash
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 200,
                "topP": 0.95
            }
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError("Geminiから有効な回答が得られませんでした")
            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            if not parts:
                raise ValueError("Geminiの回答パーツが空です")
            return parts[0].get("text", "")

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
        Chrome 128+ 内蔵の Gemini Nano Prompt API (window.ai.languageModel) をPlaywrightコンテキストから直接実行
        ※ APIキー完全不要・ローカル完結・高速
        """
        js_code = """
        async (promptText) => {
            // Chrome 内蔵 AI (Gemini Nano) の Prompt API を確認
            const ai = window.ai || (window.chrome && window.chrome.ai);
            if (ai && ai.languageModel) {
                const capabilities = await ai.languageModel.capabilities();
                if (capabilities.available === 'no') {
                    throw new Error("Chrome 内蔵 AI はご利用の端末で現在サポートされていません");
                }
                const session = await ai.languageModel.create();
                const result = await session.prompt(promptText);
                session.destroy();
                return result;
            }
            if (window.LanguageModel) {
                const session = await window.LanguageModel.create();
                const result = await session.prompt(promptText);
                return result;
            }
            throw new Error("Chrome 内蔵 AI (window.ai) が未有効化です。Chromeで chrome://flags/#prompt-api-for-gemini-nano を有効化してください");
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
        clean = text.strip()
        # 前後の引用符を除去
        if (clean.startswith('"') and clean.endswith('"')) or (clean.startswith('「') and clean.endswith('」')):
            clean = clean[1:-1].strip()
        # 前置きの除去 (例: "返信案: 〜", "了解しました。〜")
        prefixes = ["返信案:", "リプライ案:", "回答:", "返信:", "AI:", "了解しました。", "了解しました！"]
        for p in prefixes:
            if clean.startswith(p):
                clean = clean[len(p):].strip()
        return clean


class AIDedicatedWindowManager:
    """
    AI専用ウィンドウ (Profile: profiles/ai_worker) 管理クラス
    アカウントAのウィンドウと分離し、AI専用のブラウザウィンドウでURLの読み込み、
    文章生成、クリップボード連携を統括します。
    """
    _ai_context = None
    _ai_page = None

    @classmethod
    def get_or_create_ai_page(cls, playwright, headless: bool = False, base_dir: str = "."):
        """AI専用プロファイル (profiles/ai_worker) のブラウザコンテキストとページを取得"""
        if cls._ai_page and not cls._ai_page.is_closed():
            return cls._ai_page

        ai_profile_dir = os.path.join(base_dir, "profiles", "ai_worker")
        os.makedirs(ai_profile_dir, exist_ok=True)

        # Chrome/Chromium 起動引数 (内蔵AI Prompt APIフラグ付き)
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--enable-features=PromptAPIForGeminiNano,OptimizationGuideModelDownloading",
            "--no-first-run",
            "--no-default-browser-check"
        ]

        cls._ai_context = playwright.chromium.launch_persistent_context(
            user_data_dir=ai_profile_dir,
            headless=headless,
            args=launch_args,
            viewport={"width": 1000, "height": 800}
        )
        if cls._ai_context.pages:
            cls._ai_page = cls._ai_context.pages[0]
        else:
            cls._ai_page = cls._ai_context.new_page()

        return cls._ai_page

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
        base_dir: str = "."
    ) -> Tuple[bool, str]:
        """
        AI専用ウィンドウで文章を生成し、クリップボードおよび戻り値として返却
        """
        try:
            ai_page = cls.get_or_create_ai_page(playwright, headless=headless, base_dir=base_dir)

            # AIエンジンでの生成を実行 (Chrome内蔵AIの場合はこのAI専用ページのJSコンテキストを利用)
            ok, result_text = AIGenerator.generate_comment(
                engine=engine,
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
                # AI専用ウィンドウのクリップボードへ書き込み (Windows/OS共通連携)
                try:
                    ai_page.evaluate("(text) => { navigator.clipboard ? navigator.clipboard.writeText(text) : null; }", result_text)
                except Exception:
                    pass
                return True, result_text
            else:
                return False, result_text

        except Exception as e:
            return False, f"AI専用ウィンドウエラー: {str(e)}"
