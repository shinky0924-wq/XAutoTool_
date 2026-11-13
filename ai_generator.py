# -*- coding: utf-8 -*-
"""
AI文章生成エンジン統合モジュール (ai_generator.py)
対応エンジン (Web連携 / API連携 / ローカルLLM):
  1. gemini         : Google Gemini (https://gemini.google.com/app または Gemini API)
  2. chatgpt        : ChatGPT Web (https://chatgpt.com/)
  3. claude         : Claude Web (https://claude.ai/)
  4. grok           : xAI Grok (https://x.com/i/grok または xAI API)
  5. deepseek       : DeepSeek (https://chat.deepseek.com/ または DeepSeek API)
  6. perplexity     : Perplexity (https://www.perplexity.ai/)
  7. copilot        : Microsoft Copilot (https://copilot.microsoft.com/)
  8. groq           : Groq Cloud API (Llama 3.3 70B 超高速推論)
  9. openrouter     : OpenRouter API (無料モデル自動切替含む各種LLM)
 10. ollama         : Ollama (完全ローカルLLM / オフライン)
 11. lmstudio       : LM Studio (ローカル推論サーバー)
 12. chrome_builtin : Chrome 内蔵 AI (Gemini Nano / window.ai / Prompt API)
  * カスタムURL     : 「AI APIキー / 接続URL」に任意の https://... URL を入力した場合も自動起動・連携

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
from urllib.parse import urlparse
from typing import Optional, Tuple


class AIGenerator:
    """各社AIエンジンおよびローカルLLM・ブラウザ内蔵AI・WebチャットURLを統括する文章生成クラス"""

    WEB_ENGINE_URLS = {
        "gemini": "https://gemini.google.com/app",
        "gemini_api": "https://gemini.google.com/app",
        "google": "https://gemini.google.com/app",
        "chatgpt": "https://chatgpt.com/",
        "openai": "https://chatgpt.com/",
        "gpt": "https://chatgpt.com/",
        "claude": "https://claude.ai/",
        "anthropic": "https://claude.ai/",
        "grok": "https://x.com/i/grok",
        "xai": "https://x.com/i/grok",
        "deepseek": "https://chat.deepseek.com/",
        "duckai": "https://duck.ai/",
        "duck": "https://duck.ai/",
        "duckduckgo": "https://duck.ai/",
        "qwen": "https://chat.qwen.ai/",
        "mistral": "https://chat.mistral.ai/",
        "lechat": "https://chat.mistral.ai/",
        "perplexity": "https://www.perplexity.ai/",
        "copilot": "https://copilot.microsoft.com/",
        "bing": "https://copilot.microsoft.com/",
        "chrome_builtin": "https://gemini.google.com/app",
        "chrome_ai": "https://gemini.google.com/app",
    }

    WEB_ONLY_ENGINES = {
        "chatgpt", "openai", "gpt",
        "claude", "anthropic",
        "duckai", "duck", "duckduckgo",
        "qwen",
        "mistral", "lechat",
        "perplexity",
        "copilot", "bing",
    }

    @staticmethod
    def is_local_url(val: str) -> bool:
        s = (val or "").strip().lower()
        if not s:
            return False
        if s.startswith(("http://localhost", "https://localhost", "http://127.0.0.1", "https://127.0.0.1", "http://0.0.0.0", "https://0.0.0.0")):
            return True
        if ":11434" in s or ":1234" in s or "/api/generate" in s or "/v1/chat/completions" in s:
            return True
        return False

    @classmethod
    def is_web_url(cls, val: str) -> bool:
        s = (val or "").strip().lower()
        if not s:
            return False
        return s.startswith(("http://", "https://")) and not cls.is_local_url(s)

    @classmethod
    def detect_engine_from_url(cls, url: str) -> str:
        s = (url or "").strip().lower()
        if not s:
            return ""
        if "gemini.google.com" in s or "aistudio.google" in s:
            return "gemini"
        if "chatgpt.com" in s or "chat.openai.com" in s or "openai.com" in s:
            return "chatgpt"
        if "claude.ai" in s or "anthropic.com" in s:
            return "claude"
        if "x.com/i/grok" in s or "grok.com" in s or "x.ai" in s:
            return "grok"
        if "chat.deepseek.com" in s or "deepseek.com" in s:
            return "deepseek"
        if "perplexity.ai" in s:
            return "perplexity"
        if "copilot.microsoft.com" in s or "bing.com/chat" in s:
            return "copilot"
        if "duck.ai" in s or "duckduckgo.com" in s:
            return "duckai"
        if "chat.qwen.ai" in s or "qwen.ai" in s:
            return "qwen"
        if "chat.mistral.ai" in s or "mistral.ai" in s:
            return "mistral"
        if ":11434" in s:
            return "ollama"
        if ":1234" in s:
            return "lmstudio"
        return ""

    @classmethod
    def normalize_engine(cls, engine: str) -> str:
        eng = (engine or "gemini").lower().strip()
        aliases = {
            "gemini_api": "gemini",
            "google": "gemini",
            "openai": "chatgpt",
            "gpt": "chatgpt",
            "anthropic": "claude",
            "xai": "grok",
            "bing": "copilot",
            "duck": "duckai",
            "duckduckgo": "duckai",
            "lechat": "mistral",
            "groq_cloud": "groq",
            "open_router": "openrouter",
            "local_ollama": "ollama",
            "lm_studio": "lmstudio",
            "local": "lmstudio",
            "chrome_ai": "chrome_builtin",
            "chrome": "chrome_builtin",
            "builtin": "chrome_builtin",
            "gemini_nano": "chrome_builtin",
        }
        return aliases.get(eng, eng)

    @classmethod
    def resolve_engine_and_url(cls, engine: str = "gemini", api_key_or_url: str = "") -> Tuple[str, str]:
        """エンジン指定と接続URL/APIキーから (実効エンジン名, Web起動URL) を解決"""
        raw_eng = (engine or "gemini").lower().strip()
        val = (api_key_or_url or "").strip()

        if cls.is_web_url(val):
            detected = cls.detect_engine_from_url(val)
            eff_eng = detected or cls.normalize_engine(raw_eng)
            return eff_eng, val

        if cls.is_local_url(val):
            detected = cls.detect_engine_from_url(val)
            eff_eng = detected or (cls.normalize_engine(raw_eng) if cls.normalize_engine(raw_eng) in ("ollama", "lmstudio") else "ollama")
            return eff_eng, ""

        eff_eng = cls.normalize_engine(raw_eng)
        default_url = cls.WEB_ENGINE_URLS.get(eff_eng, "")
        return eff_eng, default_url

    @classmethod
    def should_launch_ai_window(cls, engine: str = "gemini", api_key_or_url: str = "", dedicated_flag: bool = True) -> bool:
        """エンジンや接続URLがWeb画面起動型であるか、または専用ウィンドウ設定がONの場合にTrue"""
        eff_eng, target_url = cls.resolve_engine_and_url(engine, api_key_or_url)
        if eff_eng in ("none", "off", "無効", ""):
            return False
        if dedicated_flag:
            return True
        if cls.is_web_url(api_key_or_url):
            return True
        if eff_eng in cls.WEB_ONLY_ENGINES:
            return True
        if target_url and not cls.has_api_key(eff_eng, api_key_or_url):
            return True
        return False

    @staticmethod
    def build_prompt(
        post_url: str = "",
        post_text: str = "",
        action_type: str = "reply",
        target_name: str = "",
        custom_instruction: str = "",
        parent_post_text: str = ""
    ) -> str:
        action_label = "リプライ（返信）" if "reply" in action_type.lower() else "引用リツイート（感想・紹介）"

        instruction = custom_instruction.strip() if custom_instruction else (
            "投稿者のポスト内容に深く共感し、親しみやすく丁寧な日本語の1〜2文で自然な会話口調を作成してください。"
            "ハッシュタグ（#）、絵文字の過剰使用、不自然な敬語、宣伝的な表現は避けてください。"
        )

        has_parent = bool(parent_post_text and parent_post_text.strip())
        if has_parent:
            context_block = f"""【対象の投稿情報（やり取り・返信への反応）】
・投稿者: @{target_name if target_name else 'ユーザー'}
・投稿URL: {post_url if post_url else 'なし'}

【元ポスト（会話の前提）】
\"\"\"
{parent_post_text.strip()}
\"\"\"

【対象者の返信内容】
\"\"\"
{post_text.strip() if post_text and post_text.strip() else '（返信本文取得不可・元ポストや対象者に合わせて自然な共感メッセージを作成）'}
\"\"\"
"""
            context_note = f"\n※【元ポスト（会話の前提）】と【対象者の返信内容】の両方の文脈を踏まえ、対象者（@{target_name if target_name else 'ユーザー'}）の返信内容に沿った自然な{action_label}を作成してください。"
        else:
            context_block = f"""【対象の投稿情報】
・投稿者: @{target_name if target_name else 'ユーザー'}
・投稿URL: {post_url if post_url else 'なし'}
・投稿本文:
\"\"\"
{post_text.strip() if post_text and post_text.strip() else '（投稿本文取得不可・投稿者名や文脈に合わせて自然な応援/共感メッセージを作成）'}
\"\"\"
"""
            context_note = ""

        prompt = f"""あなたはX(Twitter)の親しみやすく礼儀正しいユーザーです。
以下の投稿に対して、魅力的な{action_label}のメッセージを作成してください。

{context_block}
【指示】
{instruction}{context_note}

※出力は送信するメッセージ本文のみを1〜2行（140文字以内）で返してください。挨拶や前置き（例: 「了解しました」「返信案:」）や引用符などの余計な注釈は一切含めないでください。"""
        return prompt

    @classmethod
    def has_api_key(cls, engine: str, api_key: str = "") -> bool:
        eff_eng = cls.normalize_engine(engine)
        key = (api_key or "").strip()
        if eff_eng in ["ollama", "lmstudio"] or cls.is_local_url(key):
            return True
        # http(s):// で始まるWebチャットURLはAPIキーではなくWebウィンドウ接続先として扱う
        if cls.is_web_url(key):
            return False
        if eff_eng in cls.WEB_ONLY_ENGINES or eff_eng in ["chrome_builtin", "none", "off", "無効", ""]:
            return False
        if key:
            return True
        if eff_eng == "gemini":
            return bool(os.environ.get("GEMINI_API_KEY", "").strip())
        if eff_eng == "groq":
            return bool(os.environ.get("GROQ_API_KEY", "").strip())
        if eff_eng == "openrouter":
            return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
        if eff_eng == "grok":
            return bool(os.environ.get("XAI_API_KEY", "").strip())
        if eff_eng == "deepseek":
            return bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())
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
        use_dedicated_window: bool = False,
        parent_post_text: str = ""
    ) -> Tuple[bool, str]:
        """
        指定されたAIエンジンを使用して返信・引用文章を生成
        戻り値: (成功フラグ: bool, 生成テキストまたはエラーメッセージ: str)
        """
        eff_eng, _ = cls.resolve_engine_and_url(engine, api_key)
        if eff_eng in ["none", "off", "無効", ""]:
            return False, "AIエンジンが未指定または無効です"

        prompt = cls.build_prompt(post_url, post_text, action_type, target_name, custom_prompt, parent_post_text=parent_post_text)

        try:
            # 1. Google Gemini API (推奨)
            if eff_eng == "gemini":
                key = ("" if cls.is_web_url(api_key) else (api_key or "").strip()) or os.environ.get("GEMINI_API_KEY", "").strip()
                if not key and page is not None:
                    try:
                        text = cls._call_chrome_builtin(page, prompt)
                        clean = cls._sanitize_output(text)
                        if clean:
                            return True, clean
                    except Exception:
                        pass
                text = cls._call_gemini(prompt, key)
                return True, cls._sanitize_output(text)

            # 2. Groq Cloud (超高速推論)
            elif eff_eng == "groq":
                text = cls._call_groq(prompt, api_key)
                return True, cls._sanitize_output(text)

            # 3. OpenRouter (各種LLM集約)
            elif eff_eng == "openrouter":
                text = cls._call_openrouter(prompt, api_key)
                return True, cls._sanitize_output(text)

            # 4. Ollama (ローカルLLM)
            elif eff_eng == "ollama":
                endpoint = api_key if cls.is_local_url(api_key) else "http://localhost:11434"
                text = cls._call_ollama(prompt, endpoint)
                return True, cls._sanitize_output(text)

            # 5. Chrome 内蔵 AI (Gemini Nano / window.ai)
            elif eff_eng == "chrome_builtin":
                if page is not None:
                    text = cls._call_chrome_builtin(page, prompt)
                    return True, cls._sanitize_output(text)
                else:
                    return False, "Chrome内蔵AIの実行にはブラウザページが必要です"

            # 6. xAI Grok API
            elif eff_eng == "grok":
                key = "" if cls.is_web_url(api_key) else api_key
                text = cls._call_grok(prompt, key)
                return True, cls._sanitize_output(text)

            # 7. DeepSeek API
            elif eff_eng == "deepseek":
                key = "" if cls.is_web_url(api_key) else api_key
                text = cls._call_deepseek(prompt, key)
                return True, cls._sanitize_output(text)

            # 8. LM Studio (ローカル)
            elif eff_eng == "lmstudio":
                endpoint = api_key if cls.is_local_url(api_key) else "http://localhost:1234"
                text = cls._call_lmstudio(prompt, endpoint)
                return True, cls._sanitize_output(text)

            # 9. Webチャット専用エンジン (ChatGPT / Claude / Perplexity / Copilot)
            elif eff_eng in cls.WEB_ONLY_ENGINES or cls.is_web_url(api_key):
                return False, f"Webブラウザ連携エンジン ({eff_eng}) は「AI専用ウィンドウ連携=1」で自動実行されます"

            else:
                return False, f"未対応のAIエンジンです: {engine}"

        except Exception as e:
            err_msg = str(e)
            return False, f"AI生成エラー ({eff_eng}): {err_msg}"

    # -------------------------------------------------------------
    # 各エンジン別 API 実装
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
            raise ValueError("Groq APIキーが設定されていません。Excelの「AI APIキー / 接続URL」または環境変数 GROQ_API_KEY に入力してください。")

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
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]

    @staticmethod
    def _call_openrouter(prompt: str, api_key: str = "") -> str:
        key = api_key.strip() or os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise ValueError("OpenRouter APIキーが設定されていません。Excelの「AI APIキー / 接続URL」または環境変数 OPENROUTER_API_KEY に入力してください。")

        url = "https://openrouter.ai/api/v1/chat/completions"
        free_models = [
            "meta-llama/llama-3.3-70b-instruct:free",
            "google/gemini-2.0-flash-exp:free",
            "deepseek/deepseek-chat-v3-0324:free",
            "qwen/qwen-2.5-72b-instruct:free",
        ]
        last_err = None
        for model_name in free_models:
            payload = {
                "model": model_name,
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
            try:
                with urllib.request.urlopen(req, timeout=18) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as he:
                last_err = he
                if he.code in (400, 404, 429, 503):
                    continue
                raise
            except Exception as e:
                last_err = e
                continue
        raise last_err or ValueError("OpenRouter API呼び出しに失敗しました")

    @staticmethod
    def _call_deepseek(prompt: str, api_key: str = "") -> str:
        key = api_key.strip() or os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise ValueError("DeepSeek APIキーが設定されていません。https://chat.deepseek.com/ のWeb連携またはAPIキーを入力してください。")

        url = "https://api.deepseek.com/chat/completions"
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "あなたはX(Twitter)の親しみやすいユーザーです。返信本文のみを出力します。"},
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
        with urllib.request.urlopen(req, timeout=18) as resp:
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
            raise ValueError("xAI Grok APIキーが設定されていません。Excelの「AI APIキー / 接続URL」に入力してください。")

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
        if (clean.startswith('"') and clean.endswith('"')) or (clean.startswith('「') and clean.endswith('」')):
            clean = clean[1:-1].strip()
        prefixes = [
            "返信案:", "リプライ案:", "引用案:", "回答:", "返信:", "AI:",
            "了解しました。", "了解しました！", "承知しました。", "承知しました！",
            "Gemini の回答", "Gemini:", "ChatGPT:", "Claude:"
        ]
        for p in prefixes:
            if clean.startswith(p):
                clean = clean[len(p):].strip()
        if (clean.startswith('"') and clean.endswith('"')) or (clean.startswith('「') and clean.endswith('」')):
            clean = clean[1:-1].strip()
        if len(clean) > 140:
            clean = clean[:139].rstrip() + "…"
        return clean


class AIDedicatedWindowManager:
    """
    AI専用ウィンドウ (Profile: browser_profiles/ai_worker) 管理クラス
    アカウントAのウィンドウと分離し、選択したAIエンジンまたは接続URLの画面を即座に起動し、
    Gemini / ChatGPT / Claude / Grok / DeepSeek / Perplexity / Copilot / 任意URL / API連携を統括します。
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
    def _render_standby_card(cls, ai_page, eng: str, connection_url: str = ""):
        try:
            mode_info = f"接続先: {connection_url}" if connection_url else "高速API / ローカルLLM 接続待機中"
            html = f"""<html><body style='font-family:Meiryo,sans-serif;padding:28px;background:#0f172a;color:#f8fafc;'>
            <div style='max-width:640px;margin:20px auto;background:#1e293b;border:2px solid #6366f1;border-radius:14px;padding:24px;'>
              <h2 style='margin:0 0 8px 0;color:#a5b4fc;'>🪟 AI専用ウィンドウ 準備完了 ({eng})</h2>
              <p style='margin:0 0 12px 0;color:#cbd5e1;font-size:14px;'>{mode_info.replace('<','&lt;')}</p>
              <p style='margin:0;color:#94a3b8;font-size:13px;'>ターゲットポスト取得後に自動生成を実行し、このウィンドウとクリップボードへ結果を反映します。</p>
            </div></body></html>"""
            ai_page.set_content(html)
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
        slot_id: int = 1,
        connection_url: str = ""
    ):
        """AI専用プロファイル (browser_profiles/ai_worker) を起動し、指定エンジン/接続URLの画面を即座に開く"""
        st = cls._get_state()
        eff_eng, target_url = AIGenerator.resolve_engine_and_url(initial_engine, connection_url)
        use_web_ui = bool(target_url) and (
            AIGenerator.is_web_url(connection_url)
            or not has_api_key
            or eff_eng in AIGenerator.WEB_ONLY_ENGINES
        )

        if st.ai_page is not None:
            try:
                if not st.ai_page.is_closed():
                    if use_web_ui and target_url:
                        cur_url = st.ai_page.url or ""
                        cur_host = urlparse(cur_url).netloc.lower()
                        tgt_host = urlparse(target_url).netloc.lower()
                        if not cur_url or cur_url.startswith("about:blank") or cur_url.startswith("data:") or (tgt_host and tgt_host not in cur_host):
                            print(f"🌐 [AI専用ウィンドウ] {eff_eng} ({target_url}) を開いています...")
                            st.ai_page.goto(target_url, timeout=35000, wait_until="domcontentloaded")
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
                print(f"🪟 [AI専用ウィンドウ] {label} で起動試行中 (プロファイル: {prof_name} / エンジン: {eff_eng})...")
                st.ai_context = playwright.chromium.launch_persistent_context(
                    user_data_dir=ai_profile_dir,
                    headless=headless,
                    args=launch_args,
                    ignore_default_args=["--enable-automation"],
                    viewport={"width": 1020, "height": 780},
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

        # 選択されたAIエンジン・接続URLを即座に開く
        try:
            cur_url = st.ai_page.url or ""
            if use_web_ui and target_url:
                cur_host = urlparse(cur_url).netloc.lower()
                tgt_host = urlparse(target_url).netloc.lower()
                if not cur_url or cur_url == "about:blank" or (tgt_host and tgt_host not in cur_host):
                    print(f"🌐 [AI専用ウィンドウ] {eff_eng} ({target_url}) を開いています...")
                    st.ai_page.goto(target_url, timeout=35000, wait_until="domcontentloaded")
            elif not cur_url or cur_url == "about:blank":
                cls._render_standby_card(st.ai_page, eff_eng, connection_url if AIGenerator.is_local_url(connection_url) else "")
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
                for i in range(min(cnt, 6)):
                    el = loc.nth(i)
                    if el.is_visible():
                        return el
            except Exception:
                continue
        return None

    @classmethod
    def _wait_for_input_or_login(
        cls,
        ai_page,
        input_selectors: list[str],
        service_label: str,
        target_url: str,
        login_markers: list[str],
        new_chat_selectors: list[str] = None
    ):
        cur_url = ai_page.url or ""
        tgt_host = urlparse(target_url).netloc.lower()
        cur_host = urlparse(cur_url).netloc.lower()
        if not cur_url or cur_url == "about:blank" or (tgt_host and tgt_host not in cur_host):
            print(f"🌐 [AI専用ウィンドウ] {service_label} ({target_url}) へ移動します...")
            ai_page.goto(target_url, timeout=35000, wait_until="domcontentloaded")
            time.sleep(2.5)
        elif new_chat_selectors:
            for nsel in new_chat_selectors:
                try:
                    nbtn = ai_page.locator(nsel)
                    if nbtn.count() > 0 and nbtn.first.is_visible():
                        nbtn.first.click(timeout=2500)
                        time.sleep(1.2)
                        break
                except Exception:
                    continue

        input_el = None
        login_notified = False
        wait_start = time.time()
        while time.time() - wait_start < 180.0:
            input_el = cls._find_visible_input(ai_page, input_selectors)
            if input_el is not None:
                return input_el
            if not login_notified:
                cur = (ai_page.url or "").lower()
                try:
                    body_txt = ai_page.inner_text("body")[:600]
                except Exception:
                    body_txt = ""
                if any(m in cur for m in login_markers) or any(w in body_txt for w in ["ログイン", "Sign in", "Log in", "アカウントを作成", "Continue with"]):
                    print(f"🔑 【AI専用ウィンドウ】{service_label} を使用するため開いたブラウザでログインしてください。")
                    print("   👉 ログイン完了後、プロファイルに保存され自動で文章生成を再開します（最大180秒待機）...")
                    login_notified = True
            time.sleep(1.5)
        return None

    @classmethod
    def _submit_and_stream_response(
        cls,
        ai_page,
        input_el,
        prompt: str,
        resp_selectors: list[str],
        send_selectors: list[str],
        stop_selectors: list[str],
        service_label: str,
        timeout_sec: float = 55.0
    ) -> Tuple[bool, str]:
        prev_counts = {}
        prev_last_texts = {}
        for rsel in resp_selectors:
            try:
                loc = ai_page.locator(rsel)
                cnt = loc.count()
                prev_counts[rsel] = cnt
                prev_last_texts[rsel] = loc.last.inner_text().strip() if cnt > 0 else ""
            except Exception:
                prev_counts[rsel] = 0
                prev_last_texts[rsel] = ""

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
            try:
                input_el.fill(single_line_prompt)
            except Exception:
                pass
        time.sleep(0.6)

        sent = False
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

        print(f"   🤖 [AI専用ウィンドウ] {service_label} でリプライ/引用文を生成中...")
        gen_start = time.time()
        last_txt = ""
        stable_ticks = 0
        while time.time() - gen_start < timeout_sec:
            time.sleep(0.8)
            cur_txt = ""
            for rsel in resp_selectors:
                try:
                    loc = ai_page.locator(rsel)
                    cnt = loc.count()
                    if cnt > prev_counts.get(rsel, 0):
                        candidate = loc.last.inner_text().strip()
                        if candidate and candidate != single_line_prompt:
                            cur_txt = candidate
                            break
                    elif cnt > 0:
                        candidate = loc.last.inner_text().strip()
                        if candidate and candidate != prev_last_texts.get(rsel, "") and candidate != single_line_prompt:
                            cur_txt = candidate
                            break
                except Exception:
                    continue

            if cur_txt:
                is_streaming = False
                for stsel in stop_selectors:
                    try:
                        stop_btn = ai_page.locator(stsel)
                        if stop_btn.count() > 0 and stop_btn.first.is_visible():
                            is_streaming = True
                            break
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
        return False, f"{service_label} からの回答取得がタイムアウトしました"

    @classmethod
    def _generate_via_gemini_web(cls, ai_page, prompt: str, custom_url: str = "") -> Tuple[bool, str]:
        """Google Gemini Web UI (https://gemini.google.com/app) 連携"""
        try:
            target = custom_url or "https://gemini.google.com/app"
            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    'div.ql-editor[contenteditable="true"]',
                    'rich-textarea div[contenteditable="true"]',
                    'div[role="textbox"][contenteditable="true"]',
                    'textarea[placeholder*="Gemini"]',
                    'textarea',
                ],
                service_label="Google Gemini",
                target_url=target,
                login_markers=["accounts.google.com", "signin"],
                new_chat_selectors=['a[href="/app"]', 'button[aria-label*="新しいチャット"]', 'button[aria-label*="New chat"]']
            )
            if input_el is None:
                return False, "Geminiの入力欄が検出できませんでした（Googleログイン未完了またはUI変更）"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    'model-response message-content',
                    'model-response .model-response-text',
                    'model-response',
                    'message-content',
                    '.response-content',
                ],
                send_selectors=[
                    'button[aria-label*="プロンプトを送信"]',
                    'button[aria-label*="送信"]',
                    'button[aria-label*="Send"]',
                    'button.send-button',
                    'button[data-testid="send-button"]',
                ],
                stop_selectors=[
                    'button[aria-label*="回答を停止"]',
                    'button[aria-label*="停止"]',
                    'button[aria-label*="Stop"]',
                ],
                service_label="Gemini Web"
            )
        except Exception as e:
            return False, f"Gemini Web連携エラー: {e}"

    @classmethod
    def _generate_via_chatgpt_web(cls, ai_page, prompt: str, custom_url: str = "") -> Tuple[bool, str]:
        """ChatGPT Web UI (https://chatgpt.com/) 連携"""
        try:
            target = custom_url or "https://chatgpt.com/"
            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    '#prompt-textarea',
                    'div[contenteditable="true"][id="prompt-textarea"]',
                    'div[contenteditable="true"]',
                    'textarea[placeholder*="質問"]',
                    'textarea[placeholder*="Message"]',
                    'textarea',
                ],
                service_label="ChatGPT",
                target_url=target,
                login_markers=["auth.openai.com", "login"],
                new_chat_selectors=['a[href="/"]', 'button[aria-label*="新しいチャット"]', 'button[aria-label*="New chat"]']
            )
            if input_el is None:
                return False, "ChatGPTの入力欄が検出できませんでした（ログイン未完了等）"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    '[data-message-author-role="assistant"] .markdown',
                    '[data-message-author-role="assistant"]',
                    'article[data-testid*="conversation-turn"] .markdown',
                ],
                send_selectors=[
                    'button[data-testid="send-button"]',
                    'button[aria-label*="プロンプトを送信"]',
                    'button[aria-label*="送信"]',
                    'button[aria-label*="Send"]',
                ],
                stop_selectors=[
                    'button[data-testid="stop-button"]',
                    'button[aria-label*="ストリーミングを停止"]',
                    'button[aria-label*="Stop"]',
                ],
                service_label="ChatGPT Web"
            )
        except Exception as e:
            return False, f"ChatGPT Web連携エラー: {e}"

    @classmethod
    def _generate_via_claude_web(cls, ai_page, prompt: str, custom_url: str = "") -> Tuple[bool, str]:
        """Claude Web UI (https://claude.ai/) 連携"""
        try:
            target = custom_url or "https://claude.ai/"
            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    'div.ProseMirror[contenteditable="true"]',
                    'div[contenteditable="true"]',
                    'textarea',
                ],
                service_label="Claude",
                target_url=target,
                login_markers=["claude.ai/login"],
                new_chat_selectors=['a[href="/new"]', 'button[aria-label*="New chat"]', 'button[aria-label*="新しいチャット"]']
            )
            if input_el is None:
                return False, "Claudeの入力欄が検出できませんでした（ログイン未完了等）"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    '.font-claude-response',
                    '.font-claude-message',
                    '[data-testid*="assistant"] .prose',
                    'div[data-is-streaming]',
                ],
                send_selectors=[
                    'button[aria-label*="メッセージを送信"]',
                    'button[aria-label*="Send message"]',
                    'button[aria-label*="Send"]',
                ],
                stop_selectors=[
                    'button[aria-label*="Stop response"]',
                    'button[aria-label*="停止"]',
                ],
                service_label="Claude Web"
            )
        except Exception as e:
            return False, f"Claude Web連携エラー: {e}"

    @classmethod
    def _generate_via_grok_web(cls, ai_page, prompt: str, custom_url: str = "") -> Tuple[bool, str]:
        """xAI Grok Web UI (https://x.com/i/grok) 連携"""
        try:
            target = custom_url or "https://x.com/i/grok"
            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    'textarea[placeholder*="質問"]',
                    'textarea[placeholder*="Ask"]',
                    'textarea',
                    'div[contenteditable="true"]',
                    'div[role="textbox"]',
                ],
                service_label="xAI Grok",
                target_url=target,
                login_markers=["/i/flow/login", "/login"]
            )
            if input_el is None:
                return False, "Grok画面の入力欄が見つかりません（X未ログイン等）"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    '[data-testid="grokMessage"]',
                    'main div[dir="auto"][lang]',
                    'div[dir="auto"]',
                ],
                send_selectors=[
                    'button[aria-label*="送信"]',
                    'button[aria-label*="Send"]',
                    'button[data-testid*="send"]',
                ],
                stop_selectors=[
                    'button[aria-label*="停止"]',
                    'button[aria-label*="Stop"]',
                ],
                service_label="Grok Web"
            )
        except Exception as e:
            return False, f"Grok Web連携エラー: {e}"

    @classmethod
    def _generate_via_deepseek_web(cls, ai_page, prompt: str, custom_url: str = "") -> Tuple[bool, str]:
        """DeepSeek Web UI (https://chat.deepseek.com/) 連携"""
        try:
            target = custom_url or "https://chat.deepseek.com/"
            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    'textarea#chat-input',
                    'textarea',
                    'div[contenteditable="true"]',
                ],
                service_label="DeepSeek",
                target_url=target,
                login_markers=["sign_in", "login"]
            )
            if input_el is None:
                return False, "DeepSeekの入力欄が検出できませんでした（ログイン未完了等）"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    '.ds-markdown',
                    '.markdown-body',
                    'div[class*="message"]',
                ],
                send_selectors=[
                    'div[role="button"][aria-disabled="false"]',
                    'button[aria-label*="Send"]',
                    'button[type="submit"]',
                ],
                stop_selectors=[
                    'button[aria-label*="Stop"]',
                    'div[aria-label*="Stop"]',
                ],
                service_label="DeepSeek Web"
            )
        except Exception as e:
            return False, f"DeepSeek Web連携エラー: {e}"

    @classmethod
    def _generate_via_perplexity_web(cls, ai_page, prompt: str, custom_url: str = "") -> Tuple[bool, str]:
        """Perplexity Web UI (https://www.perplexity.ai/) 連携"""
        try:
            target = custom_url or "https://www.perplexity.ai/"
            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    'textarea[placeholder*="Ask"]',
                    'textarea[placeholder*="質問"]',
                    'textarea',
                    'div[contenteditable="true"]',
                ],
                service_label="Perplexity",
                target_url=target,
                login_markers=["login", "signin"]
            )
            if input_el is None:
                return False, "Perplexityの入力欄が検出できませんでした"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    '.prose',
                    'div[dir="auto"].markdown',
                    'main .markdown',
                ],
                send_selectors=[
                    'button[aria-label="Submit"]',
                    'button[aria-label*="送信"]',
                    'button[type="submit"]',
                ],
                stop_selectors=[
                    'button[aria-label*="Stop"]',
                ],
                service_label="Perplexity Web"
            )
        except Exception as e:
            return False, f"Perplexity Web連携エラー: {e}"

    @classmethod
    def _generate_via_copilot_web(cls, ai_page, prompt: str, custom_url: str = "") -> Tuple[bool, str]:
        """Microsoft Copilot Web UI (https://copilot.microsoft.com/) 連携"""
        try:
            target = custom_url or "https://copilot.microsoft.com/"
            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    'textarea#userInput',
                    'textarea[placeholder*="Copilot"]',
                    'textarea',
                    'div[contenteditable="true"]',
                ],
                service_label="Microsoft Copilot",
                target_url=target,
                login_markers=["login.live.com", "login.microsoftonline.com"]
            )
            if input_el is None:
                return False, "Copilotの入力欄が検出できませんでした"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    '[data-content="ai-message"]',
                    'div[class*="ai-message"]',
                    '.ac-textBlock',
                    '.prose',
                ],
                send_selectors=[
                    'button[data-testid="submit-button"]',
                    'button[aria-label*="Submit"]',
                    'button[aria-label*="送信"]',
                ],
                stop_selectors=[
                    'button[aria-label*="Stop"]',
                    'button[aria-label*="停止"]',
                ],
                service_label="Copilot Web"
            )
        except Exception as e:
            return False, f"Copilot Web連携エラー: {e}"

    @classmethod
    def _generate_via_duckai_web(cls, ai_page, prompt: str, custom_url: str = "") -> Tuple[bool, str]:
        """Duck.ai (https://duck.ai/ - 登録不要・無料WebAI) 連携"""
        try:
            target = custom_url or "https://duck.ai/"
            cur_url = ai_page.url or ""
            if "duck.ai" not in cur_url and "duckduckgo.com" not in cur_url:
                print(f"🌐 [AI専用ウィンドウ] Duck.ai ({target}) へ移動します...")
                ai_page.goto(target, timeout=35000, wait_until="domcontentloaded")
                time.sleep(2.5)

            # 初回訪問時の「始める / 同意する」ウィザードがあれば自動クリック
            for agree_sel in [
                'button:has-text("同意して続行")',
                'button:has-text("同意する")',
                'button:has-text("始める")',
                'button:has-text("I Agree")',
                'button:has-text("Get Started")',
                'button:has-text("Next")',
                'button:has-text("次へ")',
            ]:
                try:
                    btn = ai_page.locator(agree_sel)
                    if btn.count() > 0 and btn.first.is_visible():
                        btn.first.click(timeout=2500)
                        time.sleep(1.0)
                except Exception:
                    continue

            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    'textarea[name="user-prompt"]',
                    'textarea[placeholder*="Chat"]',
                    'textarea[placeholder*="メッセージ"]',
                    'textarea',
                ],
                service_label="Duck.ai",
                target_url=target,
                login_markers=[],
                new_chat_selectors=['button:has-text("新しいチャット")', 'button:has-text("New Chat")', 'button[aria-label*="New"]']
            )
            if input_el is None:
                return False, "Duck.aiの入力欄が検出できませんでした"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    'div[data-testid="message-content"]',
                    'div[class*="message"] .markdown',
                    '.prose',
                    'div[dir="auto"]',
                ],
                send_selectors=[
                    'button[aria-label="Send"]',
                    'button[aria-label*="送信"]',
                    'button[type="submit"]',
                ],
                stop_selectors=[
                    'button[aria-label*="Stop"]',
                    'button[aria-label*="停止"]',
                ],
                service_label="Duck.ai Web"
            )
        except Exception as e:
            return False, f"Duck.ai Web連携エラー: {e}"

    @classmethod
    def _generate_via_qwen_web(cls, ai_page, prompt: str, custom_url: str = "") -> Tuple[bool, str]:
        """Qwen Chat (https://chat.qwen.ai/) 連携"""
        try:
            target = custom_url or "https://chat.qwen.ai/"
            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    'textarea#chat-input',
                    'textarea[placeholder*="Qwen"]',
                    'textarea[placeholder*="Ask"]',
                    'textarea',
                    'div[contenteditable="true"]',
                ],
                service_label="Qwen Chat",
                target_url=target,
                login_markers=["login", "signin", "auth"]
            )
            if input_el is None:
                return False, "Qwen Chatの入力欄が検出できませんでした"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    '.markdown-body',
                    '.prose',
                    'div[class*="response"]',
                ],
                send_selectors=[
                    'button#send-message-button',
                    'button[type="submit"]',
                    'button[aria-label*="Send"]',
                    'button[aria-label*="送信"]',
                ],
                stop_selectors=[
                    'button[aria-label*="Stop"]',
                    'button[aria-label*="停止"]',
                ],
                service_label="Qwen Chat Web"
            )
        except Exception as e:
            return False, f"Qwen Chat Web連携エラー: {e}"

    @classmethod
    def _generate_via_mistral_web(cls, ai_page, prompt: str, custom_url: str = "") -> Tuple[bool, str]:
        """Mistral Le Chat (https://chat.mistral.ai/) 連携"""
        try:
            target = custom_url or "https://chat.mistral.ai/"
            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    'textarea[placeholder*="Ask"]',
                    'textarea[placeholder*="Le Chat"]',
                    'textarea',
                    'div[contenteditable="true"]',
                ],
                service_label="Mistral Le Chat",
                target_url=target,
                login_markers=["login", "signin", "auth"]
            )
            if input_el is None:
                return False, "Mistral Le Chatの入力欄が検出できませんでした"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    '.prose',
                    '.markdown',
                    'div[data-message-author-role="assistant"]',
                ],
                send_selectors=[
                    'button[type="submit"]',
                    'button[aria-label*="Send"]',
                    'button[aria-label*="送信"]',
                ],
                stop_selectors=[
                    'button[aria-label*="Stop"]',
                    'button[aria-label*="停止"]',
                ],
                service_label="Mistral Le Chat Web"
            )
        except Exception as e:
            return False, f"Mistral Le Chat Web連携エラー: {e}"

    @classmethod
    def _generate_via_generic_web_ai(cls, ai_page, prompt: str, target_url: str) -> Tuple[bool, str]:
        """接続URLに直接指定された任意のWeb AIチャットサイトとの汎用連携"""
        try:
            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    'textarea',
                    'div[contenteditable="true"]',
                    'div[role="textbox"]',
                    'input[type="text"]',
                ],
                service_label=f"Web AI ({target_url})",
                target_url=target_url,
                login_markers=["login", "signin", "auth"]
            )
            if input_el is None:
                return False, f"接続URL ({target_url}) の入力欄が検出できませんでした"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    '[data-message-author-role="assistant"] .markdown',
                    '.markdown',
                    '.prose',
                    'article',
                    'main p',
                ],
                send_selectors=[
                    'button[type="submit"]',
                    'button[aria-label*="送信"]',
                    'button[aria-label*="Send"]',
                ],
                stop_selectors=[
                    'button[aria-label*="停止"]',
                    'button[aria-label*="Stop"]',
                ],
                service_label="カスタムWeb AI"
            )
        except Exception as e:
            return False, f"カスタムWeb AI連携エラー: {e}"

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
        slot_id: int = 1,
        parent_post_text: str = ""
    ) -> Tuple[bool, str]:
        """
        AI専用ウィンドウで選択されたAIエンジン・接続URLを起動して文章を生成し、クリップボードおよび戻り値として返却
        """
        eff_eng, target_url = AIGenerator.resolve_engine_and_url(engine, api_key)
        if eff_eng in ["none", "off", "無効", ""]:
            return False, "AIエンジンが未指定または無効です"

        has_key = AIGenerator.has_api_key(eff_eng, api_key)
        try:
            ai_page = cls.get_or_create_ai_page(
                playwright,
                headless=headless,
                base_dir=base_dir,
                initial_engine=eff_eng,
                has_api_key=has_key,
                slot_id=slot_id,
                connection_url=api_key
            )

            ok = False
            result_text = ""

            # 1. APIキー設定済み または ローカルLLM の場合: APIで高速生成し、AI専用ウィンドウにも表示・クリップボード連携
            if has_key and eff_eng != "chrome_builtin":
                ok, result_text = AIGenerator.generate_comment(
                    engine=eff_eng,
                    post_url=post_url,
                    post_text=post_text,
                    action_type=action_type,
                    target_name=target_name,
                    api_key=api_key,
                    custom_prompt=custom_prompt,
                    page=ai_page,
                    use_dedicated_window=True,
                    parent_post_text=parent_post_text
                )
                if ok and result_text:
                    try:
                        parent_html = (
                            f"<div style='background:#fff;border:1px solid #cbd5e1;padding:12px;border-radius:8px;margin-bottom:12px;'>"
                            f"<b>元ポスト（会話の前提）:</b><br>{(parent_post_text or '').replace('<','&lt;')}<br><br>"
                            f"<b>対象者の返信内容:</b><br>{(post_text or '').replace('<','&lt;')}</div>"
                        ) if (parent_post_text and parent_post_text.strip()) else (
                            f"<div style='background:#fff;border:1px solid #cbd5e1;padding:12px;border-radius:8px;margin-bottom:12px;'>"
                            f"<b>元ポスト:</b><br>{(post_text or '').replace('<','&lt;')}</div>"
                        )
                        preview_html = f"""<html><body style='font-family:Meiryo,sans-serif;padding:24px;background:#f8fafc;'>
                        <h2 style='color:#312e81;'>AI専用ウィンドウ 生成結果 ({eff_eng})</h2>
                        <p><b>対象:</b> @{target_name} / {post_url}</p>
                        {parent_html}
                        <div style='background:#eef2ff;border:2px solid #6366f1;padding:14px;border-radius:8px;'>
                        <b>生成文章:</b><br>{result_text.replace('<','&lt;')}</div></body></html>"""
                        ai_page.set_content(preview_html)
                    except Exception:
                        pass

            # 2. Chrome内蔵AI指定時
            elif eff_eng == "chrome_builtin" and not AIGenerator.is_web_url(api_key):
                ok, result_text = AIGenerator.generate_comment(
                    engine=eff_eng,
                    post_url=post_url,
                    post_text=post_text,
                    action_type=action_type,
                    target_name=target_name,
                    api_key=api_key,
                    custom_prompt=custom_prompt,
                    page=ai_page,
                    use_dedicated_window=True,
                    parent_post_text=parent_post_text
                )
                if not ok:
                    prompt = AIGenerator.build_prompt(post_url, post_text, action_type, target_name, custom_prompt, parent_post_text=parent_post_text)
                    ok, result_text = cls._generate_via_gemini_web(ai_page, prompt)

            # 3. Web連携エンジンまたは接続URL指定時: AI専用ウィンドウ上の対象AIサイトで生成！
            else:
                prompt = AIGenerator.build_prompt(post_url, post_text, action_type, target_name, custom_prompt, parent_post_text=parent_post_text)
                custom_web_url = api_key.strip() if AIGenerator.is_web_url(api_key) else ""
                if eff_eng == "chatgpt":
                    ok, result_text = cls._generate_via_chatgpt_web(ai_page, prompt, custom_web_url)
                elif eff_eng == "claude":
                    ok, result_text = cls._generate_via_claude_web(ai_page, prompt, custom_web_url)
                elif eff_eng == "grok":
                    ok, result_text = cls._generate_via_grok_web(ai_page, prompt, custom_web_url)
                elif eff_eng == "deepseek":
                    ok, result_text = cls._generate_via_deepseek_web(ai_page, prompt, custom_web_url)
                elif eff_eng == "duckai":
                    ok, result_text = cls._generate_via_duckai_web(ai_page, prompt, custom_web_url)
                elif eff_eng == "qwen":
                    ok, result_text = cls._generate_via_qwen_web(ai_page, prompt, custom_web_url)
                elif eff_eng == "mistral":
                    ok, result_text = cls._generate_via_mistral_web(ai_page, prompt, custom_web_url)
                elif eff_eng == "perplexity":
                    ok, result_text = cls._generate_via_perplexity_web(ai_page, prompt, custom_web_url)
                elif eff_eng == "copilot":
                    ok, result_text = cls._generate_via_copilot_web(ai_page, prompt, custom_web_url)
                elif eff_eng == "gemini":
                    ok, result_text = cls._generate_via_gemini_web(ai_page, prompt, custom_web_url)
                    if not ok:
                        try:
                            txt = AIGenerator._call_chrome_builtin(ai_page, prompt)
                            clean = AIGenerator._sanitize_output(txt)
                            if clean:
                                ok, result_text = True, clean
                        except Exception:
                            pass
                elif custom_web_url:
                    ok, result_text = cls._generate_via_generic_web_ai(ai_page, prompt, custom_web_url)
                else:
                    ok, result_text = cls._generate_via_gemini_web(ai_page, prompt)

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
