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
import re
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
        "venice": "https://venice.ai/chat",
        "venice_ai": "https://venice.ai/chat",
        "deepai": "https://deepai.org/chat",
        "deep_ai": "https://deepai.org/chat",
        "talkai": "https://talkai.info/",
        "talk_ai": "https://talkai.info/",
        "qwen": "https://chat.qwen.ai/",
        "mistral": "https://chat.mistral.ai/",
        "lechat": "https://chat.mistral.ai/",
        "perplexity": "https://www.perplexity.ai/",
        "copilot": "https://copilot.microsoft.com/",
        "bing": "https://copilot.microsoft.com/",
        "chrome_builtin": "https://gemini.google.com/app",
        "chrome_ai": "https://gemini.google.com/app",
    }

    DEFAULT_HTTP_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    OPENAI_SYSTEM_PROMPTS = {
        "groq": "あなたはX(Twitter)の親切なユーザーです。自然で好意的な日本語の返信本文のみを出力します。",
        "openrouter": "あなたはX(Twitter)の自然なユーザーです。返信本文のみを出力します。",
        "deepseek": "あなたはX(Twitter)の親しみやすいユーザーです。返信本文のみを出力します。",
        "grok": "あなたは親しみやすいXユーザーです。返信本文のみを出力します。",
        "lmstudio": "返信本文のみを出力してください。",
    }

    _last_call_meta = threading.local()

    @staticmethod
    def estimate_tokens(text: str) -> int:
        s = text or ""
        if not s:
            return 0
        ascii_chars = sum(1 for ch in s if ord(ch) < 128)
        non_ascii_chars = len(s) - ascii_chars
        est = int(round((ascii_chars / 4.0) + (non_ascii_chars * 0.9)))
        return max(1, est)

    @classmethod
    def _reset_last_call_meta(cls, prompt_chars: int = 0):
        cls._last_call_meta.data = {
            "engine": "",
            "model": "",
            "prompt_tokens": None,
            "completion_tokens": None,
            "thoughts_tokens": None,
            "total_tokens": None,
            "prompt_chars": prompt_chars,
            "output_chars": 0,
            "estimated": False,
        }

    @classmethod
    def _set_last_call_meta(
        cls,
        engine: str = "",
        model: str = "",
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        thoughts_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        prompt_chars: int = 0,
        output_chars: int = 0,
        estimated: bool = False,
    ):
        pt = int(prompt_tokens) if prompt_tokens is not None else None
        ct = int(completion_tokens) if completion_tokens is not None else None
        th = int(thoughts_tokens) if thoughts_tokens is not None else None
        tt = int(total_tokens) if total_tokens is not None else None
        if tt is None and pt is not None and ct is not None:
            tt = pt + ct + (th or 0)
        cls._last_call_meta.data = {
            "engine": engine or "",
            "model": model or "",
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "thoughts_tokens": th,
            "total_tokens": tt,
            "prompt_chars": int(prompt_chars or 0),
            "output_chars": int(output_chars or 0),
            "estimated": bool(estimated),
        }

    @classmethod
    def get_last_call_meta(cls) -> dict:
        data = getattr(cls._last_call_meta, "data", None)
        return dict(data) if isinstance(data, dict) else {}

    @classmethod
    def format_last_usage_log(
        cls,
        used_label: str,
        prompt_text: str = "",
        output_text: str = "",
    ) -> str:
        meta = cls.get_last_call_meta()
        in_chars = meta.get("prompt_chars") or len(prompt_text or "")
        out_chars = len(output_text or "")
        pt = meta.get("prompt_tokens")
        ct = meta.get("completion_tokens")
        th = meta.get("thoughts_tokens")
        tt = meta.get("total_tokens")
        resolved_model = (meta.get("model") or "").strip()
        label_disp = used_label
        if resolved_model and resolved_model not in used_label:
            label_disp = f"{used_label} / {resolved_model}"

        if pt is not None or ct is not None:
            in_str = f"{pt} tok" if pt is not None else f"約{cls.estimate_tokens(prompt_text)} tok"
            out_str = f"{ct} tok" if ct is not None else f"約{cls.estimate_tokens(output_text)} tok"
            thought_str = f" (+思考: {th} tok)" if th else ""
            if tt is None and pt is not None and ct is not None:
                tt = pt + ct + (th or 0)
            tot_str = f"{tt} tok" if tt is not None else "-"
            tag = "目安" if meta.get("estimated") else "実績"
            if meta.get("estimated"):
                in_str = f"約{pt} tok" if pt is not None else in_str
                out_str = f"約{ct} tok" if ct is not None else out_str
                tot_str = f"約{tt} tok" if tt is not None else tot_str
            return (
                f"   📊 [AIトークン数{tag} / {label_disp}] "
                f"入力: {in_str} / 出力: {out_str}{thought_str} / 合計: {tot_str} "
                f"(文字数: {in_chars}→{out_chars}文字)"
            )

        est_in = cls.estimate_tokens(prompt_text)
        est_out = cls.estimate_tokens(output_text)
        return (
            f"   📊 [AIトークン数目安 / {label_disp}] "
            f"入力: 約{est_in} tok / 出力: 約{est_out} tok / 合計: 約{est_in + est_out} tok "
            f"(文字数: {in_chars}→{out_chars}文字)"
        )

    WEB_ONLY_ENGINES = {
        "chatgpt", "openai", "gpt",
        "claude", "anthropic",
        "duckai", "duck", "duckduckgo",
        "venice", "venice_ai",
        "deepai", "deep_ai",
        "talkai", "talk_ai",
        "qwen",
        "mistral", "lechat",
        "perplexity",
        "copilot", "bing",
    }

    @staticmethod
    def _clean_secret_value(val: str) -> str:
        s = (val or "").strip()
        for ch in ("\u200b", "\u200c", "\u200d", "\ufeff", "\u00a0", "\u3000"):
            s = s.replace(ch, "")
        return s.strip().strip('"\' ')

    @staticmethod
    def _format_http_error(he: urllib.error.HTTPError, provider: str = "API") -> str:
        code = getattr(he, "code", "?")
        reason = getattr(he, "reason", "") or ""
        raw_body = ""
        try:
            raw_body = he.read().decode("utf-8", errors="ignore").strip()
        except Exception:
            raw_body = ""
        detail = ""
        if raw_body:
            try:
                err_json = json.loads(raw_body)
                if isinstance(err_json, dict):
                    err_obj = err_json.get("error")
                    if isinstance(err_obj, dict):
                        detail = str(err_obj.get("message") or err_obj.get("code") or "").strip()
                    elif err_obj:
                        detail = str(err_obj).strip()
                    elif err_json.get("message"):
                        detail = str(err_json.get("message")).strip()
            except Exception:
                pass
            if not detail:
                compact = re.sub(r"\s+", " ", raw_body)
                detail = compact[:180]
        if detail:
            return f"{provider} HTTP {code}: {detail}"
        return f"{provider} HTTP {code}: {reason}".strip()

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
        if "venice.ai" in s:
            return "venice"
        if "deepai.org" in s:
            return "deepai"
        if "talkai.info" in s:
            return "talkai"
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
            "gemni": "gemini",
            "gemma": "gemini",
            "openai": "chatgpt",
            "gpt": "chatgpt",
            "anthropic": "claude",
            "xai": "grok",
            "bing": "copilot",
            "duck": "duckai",
            "duckduckgo": "duckai",
            "venice_ai": "venice",
            "deep_ai": "deepai",
            "talk_ai": "talkai",
            "lechat": "mistral",
            "groq_cloud": "groq",
            "groc": "groq",
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
    def infer_engine_from_model(cls, model_name: str, default_engine: str = "") -> str:
        m = (model_name or "").strip().lower()
        if not m:
            return cls.normalize_engine(default_engine) if default_engine else ""
        if m.endswith(":free") or any(p in m for p in ["anthropic/", "deepseek/deepseek-chat-v3", "google/gemini-"]):
            return "openrouter"
        if any(k in m for k in ["gpt-oss", "qwen3", "qwen/qwen", "llama-3", "llama-4", "meta-llama/", "whisper", "mixtral"]):
            return "groq"
        if m.startswith("gemma-") or m.startswith("gemini-"):
            return "gemini"
        if m.startswith("deepseek-"):
            return "deepseek"
        if m.startswith("grok-"):
            return "grok"
        return cls.normalize_engine(default_engine) if default_engine else ""

    @classmethod
    def _detect_engine_from_key_prefix(cls, token: str) -> str:
        t = (token or "").strip()
        if not t:
            return ""
        if t.startswith("gsk_"):
            return "groq"
        if t.startswith("AIza"):
            return "gemini"
        if t.startswith("sk-or-"):
            return "openrouter"
        if t.startswith("xai-"):
            return "grok"
        if cls.is_web_url(t) or cls.is_local_url(t):
            return cls.detect_engine_from_url(t)
        return ""

    @classmethod
    def extract_provider_keys(cls, raw_api_key: str = "") -> Tuple[dict, str]:
        """
        単一キー/URL、または複数キー(groq=gsk_..., gemini=AIza... / 改行・カンマ区切り)を解析し
        (プロバイダ別マップ, 単一デフォルト値) を返す
        """
        raw = (raw_api_key or "").strip()
        if not raw:
            return {}, ""

        # URL単体の場合はそのまま返す
        if cls.is_web_url(raw) or cls.is_local_url(raw):
            det = cls.detect_engine_from_url(raw)
            pmap = {det: raw} if det else {}
            return pmap, raw

        parts = [cls._clean_secret_value(p) for p in re.split(r"[\r\n,;]+", raw) if cls._clean_secret_value(p)]
        if len(parts) == 1 and "=" not in parts[0]:
            single = parts[0]
            det = cls._detect_engine_from_key_prefix(single)
            pmap = {det: single} if det else {}
            return pmap, single

        pmap = {}
        for part in parts:
            if "=" in part and not part.lower().startswith(("http://", "https://")):
                k_name, k_val = part.split("=", 1)
                eng = cls.normalize_engine(k_name)
                clean_kval = cls._clean_secret_value(k_val)
                if eng and clean_kval:
                    pmap[eng] = clean_kval
            else:
                det = cls._detect_engine_from_key_prefix(part)
                if det and det not in pmap:
                    pmap[det] = part
        return pmap, ""

    @classmethod
    def get_key_or_url_for_engine(cls, engine: str, raw_api_key: str = "", primary_engine: str = "") -> str:
        eff_eng = cls.normalize_engine(engine)
        pmap, single = cls.extract_provider_keys(raw_api_key)
        if eff_eng in pmap:
            return pmap[eff_eng]
        if single:
            if cls.is_web_url(single) or cls.is_local_url(single):
                det = cls.detect_engine_from_url(single)
                if not det or det == eff_eng:
                    return single
                return ""
            det_prefix = cls._detect_engine_from_key_prefix(single)
            if det_prefix:
                return single if det_prefix == eff_eng else ""
            if not primary_engine or cls.normalize_engine(primary_engine) == eff_eng:
                return single
        return ""

    @classmethod
    def resolve_engine_and_url(cls, engine: str = "gemini", api_key_or_url: str = "") -> Tuple[str, str]:
        """エンジン指定と接続URL/APIキーから (実効エンジン名, Web起動URL) を解決"""
        raw_eng_str = (engine or "gemini").strip()
        entries = cls.parse_engine_list(raw_eng_str)
        raw_eng = entries[0][0] if entries else raw_eng_str.lower()
        ov_key = entries[0][2] if entries else ""
        eff_norm = cls.normalize_engine(raw_eng)
        val = ov_key or cls.get_key_or_url_for_engine(eff_norm, api_key_or_url, primary_engine=eff_norm)

        if cls.is_web_url(val):
            detected = cls.detect_engine_from_url(val)
            eff_eng = detected or eff_norm
            return eff_eng, val

        if cls.is_local_url(val):
            detected = cls.detect_engine_from_url(val)
            eff_eng = detected or (eff_norm if eff_norm in ("ollama", "lmstudio") else "ollama")
            return eff_eng, ""

        default_url = cls.WEB_ENGINE_URLS.get(eff_norm, "")
        return eff_norm, default_url

    @classmethod
    def should_launch_ai_window(cls, engine: str = "gemini", api_key_or_url: str = "", dedicated_flag: bool = True) -> bool:
        """エンジンや接続URLがWeb画面起動型であるか、または専用ウィンドウ設定がONの場合にTrue"""
        eff_eng, target_url = cls.resolve_engine_and_url(engine, api_key_or_url)
        if eff_eng in ("none", "off", "無効", ""):
            return False
        scoped_val = cls.get_key_or_url_for_engine(eff_eng, api_key_or_url, primary_engine=eff_eng)
        if dedicated_flag:
            return True
        if cls.is_web_url(scoped_val):
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
        parent_post_text: str = "",
        prompt_config: Optional[dict] = None
    ) -> str:
        action_label = "リプライ（返信）" if "reply" in action_type.lower() else "引用リツイート（感想・紹介）"
        safe_name = target_name if target_name else "ユーザー"
        safe_url = post_url if post_url else "なし"
        has_parent = bool(parent_post_text and parent_post_text.strip())
        safe_parent = parent_post_text.strip() if has_parent else ""

        if has_parent:
            safe_post_body = post_text.strip() if post_text and post_text.strip() else "（返信本文取得不可・元ポストや対象者に合わせて自然な共感メッセージを作成）"
            default_context_block = f"""【対象の投稿情報（やり取り・返信への反応）】
・投稿者: @{safe_name}
・投稿URL: {safe_url}

【元ポスト（会話の前提）】
\"\"\"
{safe_parent}
\"\"\"

【対象者の返信内容】
\"\"\"
{safe_post_body}
\"\"\""""
            context_note = f"\n※【元ポスト（会話の前提）】と【対象者の返信内容】の両方の文脈を踏まえ、対象者（@{safe_name}）の返信内容に沿った自然な{action_label}を作成してください。"
        else:
            safe_post_body = post_text.strip() if post_text and post_text.strip() else "（投稿本文取得不可・投稿者名や文脈に合わせて自然な応援/共感メッセージを作成）"
            default_context_block = f"""【対象の投稿情報】
・投稿者: @{safe_name}
・投稿URL: {safe_url}
・投稿本文:
\"\"\"
{safe_post_body}
\"\"\""""
            context_note = ""

        def _apply_tags(tmpl: str) -> str:
            out = (tmpl or "").replace("{アクション}", action_label)
            out = out.replace("{投稿情報}", default_context_block)
            out = out.replace("{投稿者}", safe_name)
            out = out.replace("{投稿URL}", safe_url)
            out = out.replace("{投稿本文}", safe_post_body)
            out = out.replace("{元ポスト}", safe_parent if safe_parent else "なし")
            return out

        internal_selection_rule = (
            "【作成方針（自動選定）】\n"
            "設定されたキャラ・指示に沿った切り口を内部で3パターン（共感・補足・視点の共有など）検討し、"
            f"対象の投稿に最も自然で噛み合っている1つだけを選んでください。\n"
            f"※検討過程や他案・比較・番号（1. 2. 3. 等）は一切書かず、選んだ1つの{action_label}本文のみを出力してください。"
        )

        if isinstance(prompt_config, dict) and any(k in prompt_config for k in ("character", "tone_style", "post_format", "instruction", "examples", "ng_rules", "output_rule")):
            parts = []
            action_intro = f"以下の投稿に対して、魅力的な{action_label}のメッセージを作成してください。"
            char_raw = str(prompt_config.get("character", "") or "").strip()
            if not char_raw or ("{アクション}" not in char_raw and "作成してください" not in char_raw):
                parts.append(action_intro)
            if char_raw:
                rendered_char = _apply_tags(char_raw)
                if rendered_char.lstrip().startswith(("【キャラ", "【役割")):
                    parts.append(rendered_char)
                else:
                    parts.append(f"【キャラ設定】\n{rendered_char}")

            post_raw = str(prompt_config.get("post_format", "") or "").strip()
            if post_raw:
                rendered_post = _apply_tags(post_raw)
                if has_parent and "{元ポスト}" not in post_raw and "{投稿情報}" not in post_raw:
                    rendered_post += f'\n\n【元ポスト（会話の前提）】\n"""\n{safe_parent}\n"""'
                parts.append(rendered_post)
            else:
                parts.append(default_context_block)

            tone_raw = str(prompt_config.get("tone_style", "") or "").strip()
            if tone_raw:
                rendered_tone = _apply_tags(tone_raw)
                if rendered_tone.lstrip().startswith(("【口調", "【文体")):
                    parts.append(rendered_tone)
                else:
                    parts.append(f"【口調・文体ルール】\n{rendered_tone}")

            has_ai_sheet = bool(prompt_config.get("has_sheet"))
            instr_raw = str(prompt_config.get("instruction", "") or "").strip()
            if not has_ai_sheet and not instr_raw and custom_instruction:
                instr_raw = str(custom_instruction).strip()
            if instr_raw:
                rendered_instr = _apply_tags(instr_raw) + context_note
                if rendered_instr.lstrip().startswith(("【指示】", "【プロンプト】")):
                    parts.append(rendered_instr)
                else:
                    parts.append(f"【指示】\n{rendered_instr}")
            elif context_note.strip():
                parts.append(context_note.strip())

            examples_raw = str(prompt_config.get("examples", "") or "").strip()
            if examples_raw:
                rendered_examples = _apply_tags(examples_raw)
                if rendered_examples.lstrip().startswith(("【お手本", "【サンプル", "【参考例")):
                    parts.append(rendered_examples)
                else:
                    parts.append(f"【お手本サンプル（参考例）】\n{rendered_examples}")

            ng_raw = str(prompt_config.get("ng_rules", "") or "").strip()
            if ng_raw:
                rendered_ng = _apply_tags(ng_raw)
                if rendered_ng.lstrip().upper().startswith("【NG"):
                    parts.append(rendered_ng)
                else:
                    parts.append(f"【NGルール】\n{rendered_ng}")

            parts.append(internal_selection_rule)

            rule_raw = str(prompt_config.get("output_rule", "") or "").strip()
            if rule_raw:
                rendered_rule = _apply_tags(rule_raw)
                if rendered_rule.lstrip().startswith("【出力"):
                    parts.append(rendered_rule)
                else:
                    parts.append(f"【出力ルール】\n{rendered_rule}")

            if parts:
                return "\n\n".join(parts)

        has_custom = bool(custom_instruction and custom_instruction.strip())
        instruction = custom_instruction.strip() if has_custom else (
            "投稿者のポスト内容に深く共感し、親しみやすく丁寧な日本語の1〜2文で自然な会話口調を作成してください。\n"
            "ハッシュタグ（#）、絵文字の過剰使用、不自然な敬語、宣伝的な表現は避けてください。"
        )

        if has_custom:
            intro_line = f"以下の【設定・指示】と【対象の投稿情報】を踏まえ、最も自然で魅力的なX(Twitter)の{action_label}を1つだけ作成してください。"
            instr_heading = "【設定・指示】"
            length_rule = "・140文字以内（上記【設定・指示】に文字数条件がある場合はその範囲を優先しつつ最大140文字以内）"
        else:
            intro_line = (
                "あなたはX(Twitter)の親しみやすく礼儀正しいユーザーです。\n"
                f"以下の投稿に対して、魅力的な{action_label}のメッセージを1つだけ作成してください。"
            )
            instr_heading = "【指示】"
            length_rule = "・1〜2行（140文字以内）で自然な日本語にすること"

        prompt = f"""{intro_line}

{default_context_block}

{instr_heading}
{instruction}{context_note}

{internal_selection_rule}

【出力ルール（厳守）】
・実際にXへ投稿する本文のみを1つ返してください。
・「【返信案】」「返信案:」「了解しました」等の見出し・挨拶・前置き・引用符（「」""）・文字数メモ・補足説明は一切出力しないでください。
{length_rule}"""
        return prompt

    @classmethod
    def has_api_key(cls, engine: str, api_key: str = "") -> bool:
        entries = cls.parse_engine_list(engine)
        eff_eng = entries[0][0] if entries else cls.normalize_engine(engine)
        ov_key = entries[0][2] if entries else ""
        key = ov_key or cls.get_key_or_url_for_engine(eff_eng, api_key, primary_engine=eff_eng)
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
        parent_post_text: str = "",
        prompt_config: Optional[dict] = None,
        model_name: str = ""
    ) -> Tuple[bool, str]:
        """
        指定されたAIエンジンを使用して返信・引用文章を生成
        戻り値: (成功フラグ: bool, 生成テキストまたはエラーメッセージ: str)
        """
        eff_eng, _ = cls.resolve_engine_and_url(engine, api_key)
        if eff_eng in ["none", "off", "無効", ""]:
            return False, "AIエンジンが未指定または無効です"

        scoped_key = cls.get_key_or_url_for_engine(eff_eng, api_key, primary_engine=eff_eng)
        prompt = cls.build_prompt(
            post_url,
            post_text,
            action_type,
            target_name,
            custom_prompt,
            parent_post_text=parent_post_text,
            prompt_config=prompt_config,
        )

        try:
            # 1. Google Gemini API (推奨)
            if eff_eng == "gemini":
                key = ("" if cls.is_web_url(scoped_key) else (scoped_key or "").strip()) or os.environ.get("GEMINI_API_KEY", "").strip()
                if not key and page is not None:
                    try:
                        text = cls._call_chrome_builtin(page, prompt)
                        clean = cls._sanitize_output(text)
                        if clean:
                            return True, clean
                    except Exception:
                        pass
                text = cls._call_gemini(prompt, key, model_name=model_name)
                clean = cls._sanitize_output(text)
                return (True, clean) if clean else (False, "AIの生成結果が空でした")

            # 2. Groq Cloud (超高速推論)
            elif eff_eng == "groq":
                text = cls._call_groq(prompt, scoped_key, model_name=model_name)
                clean = cls._sanitize_output(text)
                return (True, clean) if clean else (False, "AIの生成結果が空でした")

            # 3. OpenRouter (各種LLM集約)
            elif eff_eng == "openrouter":
                text = cls._call_openrouter(prompt, scoped_key, model_name=model_name)
                clean = cls._sanitize_output(text)
                return (True, clean) if clean else (False, "AIの生成結果が空でした")

            # 4. Ollama (ローカルLLM)
            elif eff_eng == "ollama":
                endpoint = scoped_key if cls.is_local_url(scoped_key) else "http://localhost:11434"
                text = cls._call_ollama(prompt, endpoint, model_name=model_name)
                clean = cls._sanitize_output(text)
                return (True, clean) if clean else (False, "AIの生成結果が空でした")

            # 5. Chrome 内蔵 AI (Gemini Nano / window.ai)
            elif eff_eng == "chrome_builtin":
                if page is not None:
                    text = cls._call_chrome_builtin(page, prompt)
                    clean = cls._sanitize_output(text)
                    return (True, clean) if clean else (False, "AIの生成結果が空でした")
                else:
                    return False, "Chrome内蔵AIの実行にはブラウザページが必要です"

            # 6. xAI Grok API
            elif eff_eng == "grok":
                key = "" if cls.is_web_url(scoped_key) else scoped_key
                text = cls._call_grok(prompt, key, model_name=model_name)
                clean = cls._sanitize_output(text)
                return (True, clean) if clean else (False, "AIの生成結果が空でした")

            # 7. DeepSeek API
            elif eff_eng == "deepseek":
                key = "" if cls.is_web_url(scoped_key) else scoped_key
                text = cls._call_deepseek(prompt, key, model_name=model_name)
                clean = cls._sanitize_output(text)
                return (True, clean) if clean else (False, "AIの生成結果が空でした")

            # 8. LM Studio (ローカル)
            elif eff_eng == "lmstudio":
                endpoint = scoped_key if cls.is_local_url(scoped_key) else "http://localhost:1234"
                text = cls._call_lmstudio(prompt, endpoint, model_name=model_name)
                clean = cls._sanitize_output(text)
                return (True, clean) if clean else (False, "AIの生成結果が空でした")

            # 9. Webチャット専用エンジン (ChatGPT / Claude / Perplexity / Copilot)
            elif eff_eng in cls.WEB_ONLY_ENGINES or cls.is_web_url(scoped_key):
                return False, f"Webブラウザ連携エンジン ({eff_eng}) は「AI専用ウィンドウ連携=1」で自動実行されます"

            else:
                return False, f"未対応のAIエンジンです: {engine}"

        except Exception as e:
            err_msg = str(e)
            return False, f"AI生成エラー ({eff_eng}): {err_msg}"

    @classmethod
    def parse_fallback_specs(cls, fallback_spec: str, default_engine: str = "") -> list[Tuple[str, str, str]]:
        """
        カンマ/改行/矢印(→, ->)区切りのフォールバック候補文字列を
        [(engine, model_name, key_or_url_override), ...] のリストにパース
        """
        raw = (fallback_spec or "").strip()
        if not raw:
            return []
        low_raw = raw.lower()
        if low_raw in ["none", "off", "無効", "なし", "停止", "固定", "しない", "false", "0"]:
            return []

        tokens = [t.strip() for t in re.split(r"[,、\r\n→>]+|->", raw) if t.strip()]
        parsed = []
        for tok in tokens:
            if tok.lower() in ["none", "off", "無効", "なし", "定型", "テンプレート"]:
                continue
            key_ov = ""
            item = tok
            if "|" in tok:
                item, key_ov = [x.strip() for x in tok.split("|", 1)]

            if cls.is_web_url(item) or cls.is_local_url(item):
                det = cls.detect_engine_from_url(item) or cls.normalize_engine(default_engine or "gemini")
                parsed.append((det, "", key_ov or item))
                continue

            low_item = item.lower()
            known_engine_tokens = {
                "gemini", "gemini_api", "google", "gemni", "gemma",
                "chatgpt", "openai", "gpt",
                "claude", "anthropic",
                "grok", "xai",
                "deepseek",
                "duckai", "duck", "duckduckgo",
                "venice", "venice_ai",
                "deepai", "deep_ai",
                "talkai", "talk_ai",
                "qwen", "qwen_web",
                "mistral", "lechat",
                "perplexity",
                "copilot", "bing",
                "groq", "groq_cloud", "groc",
                "openrouter", "open_router",
                "ollama", "local_ollama",
                "lmstudio", "lm_studio", "local",
                "chrome_builtin", "chrome_ai", "chrome", "builtin", "gemini_nano",
            }
            if low_item in known_engine_tokens:
                eng = cls.normalize_engine(item)
                parsed.append((eng, "", key_ov))
                continue

            if ":" in item:
                left, right = [x.strip() for x in item.split(":", 1)]
                if right.lower() != "free" and "/" not in left:
                    eng = cls.normalize_engine(left)
                    parsed.append((eng, right, key_ov))
                    continue

            # モデルIDらしき記法ならエンジンを自動推測
            if "/" in item or any(low_item.startswith(pfx) for pfx in ["gemma-", "gemini-", "llama-", "deepseek-", "grok-", "qwen-", "qwen3", "gpt-oss"]):
                inferred = cls.infer_engine_from_model(item, default_engine=default_engine or "groq")
                parsed.append((inferred, item, key_ov))
            else:
                eng = cls.normalize_engine(item)
                parsed.append((eng, "", key_ov))
        return parsed

    @classmethod
    def parse_engine_list(cls, engine_spec: str = "gemini", primary_model: str = "") -> list[Tuple[str, str, str]]:
        """
        AIエンジン欄（単体またはカンマ/矢印区切り複数指定）を [(engine, model_name, key_or_url), ...] にパース。
        AIモデル名(primary_model)が指定されている場合は適合する最初のエンジンへ割り当てる。
        """
        raw = (engine_spec or "").strip()
        pm = (primary_model or "").strip()
        if not raw:
            return [("gemini", pm, "")]
        if raw.lower() in ["none", "off", "無効", "なし", "停止", "しない", "false", "0"]:
            return [("off", "", "")]

        parsed = cls.parse_fallback_specs(raw, default_engine="")
        if not parsed:
            return [(cls.normalize_engine(raw), pm, "")]

        if pm:
            inferred = cls.infer_engine_from_model(pm)
            assigned = False
            updated = []
            for idx, (e, m, ov) in enumerate(parsed):
                eff_e = cls.normalize_engine(e)
                if not m and not assigned:
                    if not inferred or inferred == eff_e:
                        m = pm
                        assigned = True
                updated.append((eff_e, m, ov))
            return updated

        return [(cls.normalize_engine(e), m, ov) for e, m, ov in parsed]

    @classmethod
    def build_fallback_chain(
        cls,
        primary_engine: str,
        primary_model: str = "",
        raw_api_key: str = "",
        fallback_spec: str = "",
        allow_web_fallback: bool = True
    ) -> list[Tuple[str, str, str]]:
        """
        プライマリ失敗時に順に試行する [(engine, model_name, effective_key_or_url), ...] を構築
        - primary_engine に「groq, gemini, duckai」等カンマ区切りで複数指定がある場合は2番目以降を最優先で順に追加
        - fallback_spec が 'off'/'none'/'無効' なら追加候補は積まない（primary_engine 内の2番目以降のみ）
        - fallback_spec が明示されていればその順序を続いて追加
        - fallback_spec が空欄なら同一API別モデル → 他APIキー保持プロバイダ → chrome_builtin → Web AI の順で自動補充
        """
        eng_entries = cls.parse_engine_list(primary_engine, primary_model=primary_model)
        prim_spec_eng, prim_mod, prim_ov = eng_entries[0]
        prim_eng, _ = cls.resolve_engine_and_url(prim_spec_eng, prim_ov or raw_api_key)

        def _resolve_key(cand_eng: str, cand_ov: str) -> str:
            if cand_ov:
                return cand_ov
            return cls.get_key_or_url_for_engine(cand_eng, raw_api_key, primary_engine=prim_eng)

        seen = {(prim_eng, prim_mod)}
        chain: list[Tuple[str, str, str]] = []

        def _append_unique(cand_eng: str, cand_mod: str, cand_ov: str = ""):
            e = cls.normalize_engine(cand_eng)
            if not e or e in ["none", "off", "無効"]:
                return
            m = (cand_mod or "").strip()
            k = _resolve_key(e, cand_ov)
            key_sig = (e, m)
            if key_sig in seen:
                return
            seen.add(key_sig)
            chain.append((e, m, k))

        # 0. AIエンジン欄に2番目以降の指定があれば最優先で順番に追加
        for e, m, ov in eng_entries[1:]:
            _append_unique(e, m, ov)

        raw_fb = (fallback_spec or "").strip()
        if raw_fb.lower() in ["none", "off", "無効", "なし", "停止", "固定", "しない", "false", "0"]:
            return chain

        explicit = cls.parse_fallback_specs(raw_fb, default_engine=prim_eng)
        if explicit:
            for e, m, ov in explicit:
                _append_unique(e, m, ov)
            return chain

        # --- 空欄（自動切替モード）: 安全で成功率の高い候補を自動補充 ---
        # 1. Groq APIキーが使える場合は同プロバイダ内の別モデルへ切替
        if prim_eng == "groq" and cls.has_api_key("groq", raw_api_key):
            for gm in ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile"]:
                _append_unique("groq", gm)

        # 2. Gemini APIキーが使える場合は同プロバイダ内の別モデルへ切替
        if prim_eng == "gemini" and cls.has_api_key("gemini", raw_api_key):
            for gm in ["gemini-2.5-flash", "gemma-3-27b-it", "gemma-4-26b-a4b-it", "gemma-4-31b-it", "gemini-2.5-flash-lite"]:
                _append_unique("gemini", gm)

        # 3. 他のAPIキー（複数キー入力または環境変数）が設定されているプロバイダへ切替
        api_defaults = [
            ("groq", "qwen/qwen3.8-27b"),
            ("groq", "openai/gpt-oss-120b"),
            ("gemini", "gemma-3-27b-it"),
            ("gemini", "gemini-2.5-flash"),
            ("openrouter", ""),
            ("deepseek", "deepseek-chat"),
            ("grok", "grok-3-mini"),
        ]
        for eng, def_mod in api_defaults:
            if cls.has_api_key(eng, raw_api_key):
                _append_unique(eng, def_mod)

        # 4. Webブラウザ連携・Chrome内蔵AIフォールバック（Playwrightが使える場合）
        if allow_web_fallback:
            _append_unique("chrome_builtin", "")
            # ログイン不要サイト (Duck.ai / Venice / DeepAI / TalkAI / ChatGPT) を最優先し、続いてQwen / Gemini Web等へ切替
            for web_eng in ["duckai", "venice", "deepai", "talkai", "chatgpt", "qwen", "gemini", "deepseek"]:
                # もし同じエンジンがWeb経由で既に失敗していたら後ろに回す（あるいはスキップ）
                if web_eng == prim_eng and not cls.has_api_key(prim_eng, raw_api_key):
                    continue
                _append_unique(web_eng, "")

        return chain

    @classmethod
    def generate_comment_with_fallback(
        cls,
        engine: str = "gemini",
        post_url: str = "",
        post_text: str = "",
        action_type: str = "reply",
        target_name: str = "",
        api_key: str = "",
        custom_prompt: str = "",
        playwright = None,
        page = None,
        use_dedicated_window: bool = False,
        base_dir: str = ".",
        slot_id: int = 1,
        headless: bool = False,
        parent_post_text: str = "",
        prompt_config: Optional[dict] = None,
        model_name: str = "",
        fallback_spec: str = "",
        logger = None
    ) -> Tuple[bool, str, str]:
        """
        プライマリAI生成を実行し、失敗/タイムアウト/上限時には別モデル・別AIへ自動切替する統合エントリーポイント。
        AIエンジン欄のカンマ区切り指定（例: groq, gemini, duckai）にも対応。
        戻り値: (成功フラグ, 生成テキストまたはエラーメッセージ, 実際に使用されたラベル)
        """
        def _log(msg: str):
            if logger:
                try:
                    logger(msg)
                    return
                except Exception:
                    pass
            print(msg)

        eng_entries = cls.parse_engine_list(engine, primary_model=model_name)
        prim_spec_eng, prim_mod, prim_ov = eng_entries[0]
        prim_key = prim_ov or api_key
        prim_eng, _ = cls.resolve_engine_and_url(prim_spec_eng, prim_key)
        prim_label = f"{prim_eng} ({prim_mod})" if prim_mod else prim_eng

        preview_prompt = cls.build_prompt(
            post_url,
            post_text,
            action_type,
            target_name,
            custom_prompt,
            parent_post_text=parent_post_text,
            prompt_config=prompt_config,
        )
        est_prompt_tok = cls.estimate_tokens(preview_prompt)
        _log(f"   📤 [AI送信プロンプト全文 / {prim_label}] (入力 {len(preview_prompt)}文字 / 目安約{est_prompt_tok} tok)")
        sys_msg = cls.OPENAI_SYSTEM_PROMPTS.get(prim_eng, "")
        if sys_msg and cls.has_api_key(prim_eng, prim_key):
            _log(f"   | [system] {sys_msg}")
        _log("   --- 送信プロンプト開始 ---")
        for _pline in (preview_prompt.splitlines() or [""]):
            _log(f"   | {_pline}")
        _log("   --- 送信プロンプト終了 ---")

        def _attempt_one(cand_eng: str, cand_mod: str, cand_key: str, is_fb: bool) -> Tuple[bool, str]:
            # API専用モデルID（例: gemma-3-27b-it）がフォールバック指定されているがAPIキーが無い場合、
            # Web版ログイン画面を開かず即スキップして次のログイン不要サイトへ進める
            norm_ce = cls.normalize_engine(cand_eng)
            if is_fb and cand_mod and norm_ce in ("gemini", "grok", "deepseek") and not cls.has_api_key(norm_ce, cand_key) and not cls.is_web_url(cand_key):
                return False, f"{norm_ce} ({cand_mod}): APIキー未設定のためスキップ"

            cls._reset_last_call_meta(prompt_chars=len(preview_prompt))
            use_win = cls.should_launch_ai_window(
                cand_eng,
                cand_key,
                use_dedicated_window if not is_fb else False
            )
            if use_win:
                if playwright is None:
                    return False, f"{cand_eng}: Web連携用のブラウザセッションがありません"
                return AIDedicatedWindowManager.generate_and_copy_via_ai_window(
                    playwright=playwright,
                    post_url=post_url,
                    post_text=post_text,
                    engine=cand_eng,
                    action_type=action_type,
                    target_name=target_name,
                    api_key=cand_key,
                    custom_prompt=custom_prompt,
                    headless=headless,
                    base_dir=base_dir,
                    slot_id=slot_id,
                    parent_post_text=parent_post_text,
                    prompt_config=prompt_config,
                    model_name=cand_mod,
                    is_fallback=is_fb
                )
            return cls.generate_comment(
                engine=cand_eng,
                post_url=post_url,
                post_text=post_text,
                action_type=action_type,
                target_name=target_name,
                api_key=cand_key,
                custom_prompt=custom_prompt,
                page=page,
                use_dedicated_window=use_dedicated_window,
                parent_post_text=parent_post_text,
                prompt_config=prompt_config,
                model_name=cand_mod
            )

        # 1. プライマリ試行
        ok, res = _attempt_one(prim_spec_eng, prim_mod, prim_key, is_fb=False)
        if ok and res and res.strip():
            out_clean = res.strip()
            _log(cls.format_last_usage_log(prim_label, prompt_text=preview_prompt, output_text=out_clean))
            return True, out_clean, prim_label

        last_err = res or "生成結果が空でした"
        candidates = cls.build_fallback_chain(
            primary_engine=engine,
            primary_model=model_name,
            raw_api_key=api_key,
            fallback_spec=fallback_spec,
            allow_web_fallback=(playwright is not None)
        )
        if not candidates:
            return False, last_err, prim_label

        _log(f"   ⚠️ AI生成失敗 ({prim_label} / 理由: {last_err}) → 別AI/モデルへ自動切替します")

        total = len(candidates)
        for idx, (cand_eng, cand_mod, cand_key) in enumerate(candidates, 1):
            cand_label = f"{cand_eng} ({cand_mod})" if cand_mod else cand_eng
            _log(f"   🔄 [AI自動切替 {idx}/{total}] {cand_label} で再試行中...")
            try:
                fb_ok, fb_res = _attempt_one(cand_eng, cand_mod, cand_key, is_fb=True)
                if fb_ok and fb_res and fb_res.strip():
                    out_clean = fb_res.strip()
                    _log(cls.format_last_usage_log(cand_label, prompt_text=preview_prompt, output_text=out_clean))
                    return True, out_clean, cand_label
                last_err = fb_res or "生成結果が空でした"
                _log(f"      ↳ 切替失敗 ({cand_label}): {last_err}")
            except Exception as fb_e:
                last_err = str(fb_e)
                _log(f"      ↳ 切替エラー ({cand_label}): {last_err}")

        return False, last_err, prim_label

    # -------------------------------------------------------------
    # 各エンジン別 API 実装
    # -------------------------------------------------------------

    @classmethod
    def _call_gemini(cls, prompt: str, api_key: str = "", model_name: str = "") -> str:
        key = cls._clean_secret_value(api_key) or cls._clean_secret_value(os.environ.get("GEMINI_API_KEY", ""))
        if not key:
            raise ValueError("Gemini APIキーが設定されていません。Excelの「AI APIキー」に入力するか「AI専用ウィンドウ連携=1」を使用してください。")

        pinned = (model_name or "").strip()
        model_candidates = [pinned] if pinned else ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
        last_err = None
        for m_name in model_candidates:
            m_low = m_name.lower()
            is_gemma4 = "gemma-4" in m_low
            is_thinking = is_gemma4 or ("gemini-2.5" in m_low) or ("gemini-3" in m_low)
            gen_cfg = {
                "temperature": 0.7,
                "maxOutputTokens": 600 if is_thinking else 240,
                "topP": 0.95
            }
            if is_gemma4:
                gen_cfg["thinkingConfig"] = {"thinkingLevel": "minimal"}

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": gen_cfg,
            }
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={key}"

            def _post_gemini(body: dict) -> str:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(body).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": cls.DEFAULT_HTTP_USER_AGENT,
                    }
                )
                with urllib.request.urlopen(req, timeout=18) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    candidates = data.get("candidates", [])
                    if not candidates:
                        raise ValueError("Geminiから有効な回答が得られませんでした")
                    content = candidates[0].get("content", {})
                    parts = content.get("parts", [])
                    if not parts:
                        raise ValueError("Geminiの回答パーツが空です")
                    non_thought_texts = [p.get("text", "") for p in parts if not p.get("thought") and p.get("text")]
                    out_txt = "\n".join(non_thought_texts) if non_thought_texts else parts[-1].get("text", "")
                    usage_meta = data.get("usageMetadata") or {}
                    cls._set_last_call_meta(
                        engine="gemini",
                        model=m_name,
                        prompt_tokens=usage_meta.get("promptTokenCount"),
                        completion_tokens=usage_meta.get("candidatesTokenCount"),
                        thoughts_tokens=usage_meta.get("thoughtsTokenCount"),
                        total_tokens=usage_meta.get("totalTokenCount"),
                        prompt_chars=len(prompt),
                        output_chars=len(out_txt),
                        estimated=False if usage_meta else True,
                    )
                    return out_txt

            try:
                return _post_gemini(payload)
            except urllib.error.HTTPError as he:
                if he.code == 400 and "thinkingConfig" in gen_cfg:
                    try:
                        he.close()
                    except Exception:
                        pass
                    try:
                        fallback_cfg = {k: v for k, v in gen_cfg.items() if k != "thinkingConfig"}
                        fallback_body = {"contents": payload["contents"], "generationConfig": fallback_cfg}
                        return _post_gemini(fallback_body)
                    except urllib.error.HTTPError as he2:
                        last_err = RuntimeError(cls._format_http_error(he2, "Gemini"))
                        if he2.code == 404 and not pinned:
                            continue
                        raise last_err
                last_err = RuntimeError(cls._format_http_error(he, "Gemini"))
                if he.code == 404 and not pinned:
                    continue
                raise last_err
            except Exception as e:
                last_err = e
                raise
        raise last_err or ValueError("Gemini API呼び出しに失敗しました")

    @classmethod
    def _call_groq(cls, prompt: str, api_key: str = "", model_name: str = "") -> str:
        key = cls._clean_secret_value(api_key) or cls._clean_secret_value(os.environ.get("GROQ_API_KEY", ""))
        if not key:
            raise ValueError("Groq APIキーが設定されていません。Excelの「AI APIキー / 接続URL」または環境変数 GROQ_API_KEY に入力してください。")

        eff_model = (model_name or "").strip() or os.environ.get("GROQ_MODEL", "").strip() or "llama-3.3-70b-versatile"
        m_low = eff_model.lower()
        messages = [
            {"role": "system", "content": cls.OPENAI_SYSTEM_PROMPTS["groq"]},
            {"role": "user", "content": prompt}
        ]

        url = "https://api.groq.com/openai/v1/chat/completions"
        is_qwen3 = "qwen3" in m_low
        is_gpt_oss = "gpt-oss" in m_low
        is_reasoning = is_qwen3 or is_gpt_oss or ("deepseek-r1" in m_low) or ("qwq" in m_low)

        payload = {
            "model": eff_model,
            "messages": messages,
            "temperature": 0.7,
        }
        if is_qwen3:
            # Qwen3/3.8系は思考OFF(none)にしてトークン消費を節約しつつ本文だけ取得
            payload["reasoning_effort"] = "none"
            payload["max_completion_tokens"] = 512
        elif is_gpt_oss:
            # gpt-oss系はlow + 十分な完了トークン上限で本文欠損を防ぐ
            payload["reasoning_effort"] = "low"
            payload["max_completion_tokens"] = 1024
        elif is_reasoning:
            # deepseek-r1 / qwq等の思考モデルはlow + hidden + 十分な完了トークン上限で本文欠損を防ぐ
            payload["reasoning_effort"] = "low"
            payload["reasoning_format"] = "hidden"
            payload["max_completion_tokens"] = 1024
        else:
            payload["max_tokens"] = 180

        def _post_groq(body: dict) -> str:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {key}",
                    # CloudflareがPython-urllibをError 1010 (403)でブロックするのを防止
                    "User-Agent": cls.DEFAULT_HTTP_USER_AGENT,
                }
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                out_txt = data["choices"][0]["message"].get("content") or ""
                usage = data.get("usage") or {}
                cls._set_last_call_meta(
                    engine="groq",
                    model=data.get("model") or eff_model,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    prompt_chars=len(prompt),
                    output_chars=len(out_txt),
                    estimated=False if usage else True,
                )
                return out_txt

        try:
            return _post_groq(payload)
        except urllib.error.HTTPError as he:
            # 一部モデルでreasoning_effort/reasoning_formatが非対応で400となった場合は最小構成で自動リトライ
            if he.code == 400 and is_reasoning:
                try:
                    he.close()
                except Exception:
                    pass
                fallback_payload = {
                    "model": eff_model,
                    "messages": messages,
                    "max_completion_tokens": 1024,
                    "temperature": 0.7,
                }
                try:
                    return _post_groq(fallback_payload)
                except urllib.error.HTTPError as he2:
                    raise RuntimeError(cls._format_http_error(he2, "Groq"))
            raise RuntimeError(cls._format_http_error(he, "Groq"))

    @classmethod
    def _call_openrouter(cls, prompt: str, api_key: str = "", model_name: str = "") -> str:
        key = cls._clean_secret_value(api_key) or cls._clean_secret_value(os.environ.get("OPENROUTER_API_KEY", ""))
        if not key:
            raise ValueError("OpenRouter APIキーが設定されていません。Excelの「AI APIキー / 接続URL」または環境変数 OPENROUTER_API_KEY に入力してください。")

        url = "https://openrouter.ai/api/v1/chat/completions"
        pinned = (model_name or "").strip()
        free_models = [pinned] if pinned else [
            "meta-llama/llama-3.3-70b-instruct:free",
            "google/gemini-2.0-flash-exp:free",
            "deepseek/deepseek-chat-v3-0324:free",
            "qwen/qwen-2.5-72b-instruct:free",
        ]
        last_err = None
        for m_name in free_models:
            payload = {
                "model": m_name,
                "messages": [
                    {"role": "system", "content": cls.OPENAI_SYSTEM_PROMPTS["openrouter"]},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 220,
                "temperature": 0.7
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {key}",
                    "User-Agent": cls.DEFAULT_HTTP_USER_AGENT,
                    "HTTP-Referer": "https://github.com/shinky0924-wq/XAutoTool_",
                    "X-Title": "XAutoTool"
                }
            )
            try:
                with urllib.request.urlopen(req, timeout=18) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    out_txt = data["choices"][0]["message"].get("content") or ""
                    usage = data.get("usage") or {}
                    cls._set_last_call_meta(
                        engine="openrouter",
                        model=data.get("model") or m_name,
                        prompt_tokens=usage.get("prompt_tokens"),
                        completion_tokens=usage.get("completion_tokens"),
                        total_tokens=usage.get("total_tokens"),
                        prompt_chars=len(prompt),
                        output_chars=len(out_txt),
                        estimated=False if usage else True,
                    )
                    return out_txt
            except urllib.error.HTTPError as he:
                last_err = RuntimeError(cls._format_http_error(he, "OpenRouter"))
                if he.code in (400, 404, 429, 503) and not pinned:
                    continue
                raise last_err
            except Exception as e:
                last_err = e
                if not pinned:
                    continue
                raise
        raise last_err or ValueError("OpenRouter API呼び出しに失敗しました")

    @classmethod
    def _call_deepseek(cls, prompt: str, api_key: str = "", model_name: str = "") -> str:
        key = cls._clean_secret_value(api_key) or cls._clean_secret_value(os.environ.get("DEEPSEEK_API_KEY", ""))
        if not key:
            raise ValueError("DeepSeek APIキーが設定されていません。https://chat.deepseek.com/ のWeb連携またはAPIキーを入力してください。")

        url = "https://api.deepseek.com/chat/completions"
        eff_model = (model_name or "").strip() or "deepseek-chat"
        payload = {
            "model": eff_model,
            "messages": [
                {"role": "system", "content": cls.OPENAI_SYSTEM_PROMPTS["deepseek"]},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 400 if "reasoner" in eff_model.lower() else 160,
            "temperature": 0.7
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {key}",
                "User-Agent": cls.DEFAULT_HTTP_USER_AGENT,
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=18) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                out_txt = data["choices"][0]["message"].get("content") or ""
                usage = data.get("usage") or {}
                cls._set_last_call_meta(
                    engine="deepseek",
                    model=data.get("model") or eff_model,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    prompt_chars=len(prompt),
                    output_chars=len(out_txt),
                    estimated=False if usage else True,
                )
                return out_txt
        except urllib.error.HTTPError as he:
            raise RuntimeError(cls._format_http_error(he, "DeepSeek"))

    @classmethod
    def _call_ollama(cls, prompt: str, endpoint: str = "http://localhost:11434", model_name: str = "") -> str:
        base = endpoint.rstrip("/")
        url = f"{base}/api/generate"
        eff_model = (model_name or "").strip() or "llama3"
        payload = {
            "model": eff_model,
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
            out_txt = data.get("response", "")
            pt = data.get("prompt_eval_count")
            ct = data.get("eval_count")
            cls._set_last_call_meta(
                engine="ollama",
                model=data.get("model") or eff_model,
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=(int(pt) + int(ct)) if (pt is not None and ct is not None) else None,
                prompt_chars=len(prompt),
                output_chars=len(out_txt),
                estimated=False if (pt is not None or ct is not None) else True,
            )
            return out_txt

    @classmethod
    def _call_chrome_builtin(cls, page, prompt: str) -> str:
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
        out_txt = str(res)
        cls._set_last_call_meta(
            engine="chrome_builtin",
            model="gemini-nano",
            prompt_tokens=cls.estimate_tokens(prompt),
            completion_tokens=cls.estimate_tokens(out_txt),
            prompt_chars=len(prompt),
            output_chars=len(out_txt),
            estimated=True,
        )
        return out_txt

    @classmethod
    def _call_grok(cls, prompt: str, api_key: str = "", model_name: str = "") -> str:
        key = cls._clean_secret_value(api_key) or cls._clean_secret_value(os.environ.get("XAI_API_KEY", ""))
        if not key:
            raise ValueError("xAI Grok APIキーが設定されていません。Excelの「AI APIキー / 接続URL」に入力してください。")

        url = "https://api.x.ai/v1/chat/completions"
        eff_model = (model_name or "").strip() or "grok-beta"
        payload = {
            "model": eff_model,
            "messages": [
                {"role": "system", "content": cls.OPENAI_SYSTEM_PROMPTS["grok"]},
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
                "Accept": "application/json",
                "Authorization": f"Bearer {key}",
                "User-Agent": cls.DEFAULT_HTTP_USER_AGENT,
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                out_txt = data["choices"][0]["message"].get("content") or ""
                usage = data.get("usage") or {}
                cls._set_last_call_meta(
                    engine="grok",
                    model=data.get("model") or eff_model,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    prompt_chars=len(prompt),
                    output_chars=len(out_txt),
                    estimated=False if usage else True,
                )
                return out_txt
        except urllib.error.HTTPError as he:
            raise RuntimeError(cls._format_http_error(he, "xAI Grok"))

    @classmethod
    def _call_lmstudio(cls, prompt: str, endpoint: str = "http://localhost:1234", model_name: str = "") -> str:
        base = endpoint.rstrip("/")
        url = f"{base}/v1/chat/completions"
        payload = {
            "messages": [
                {"role": "system", "content": cls.OPENAI_SYSTEM_PROMPTS["lmstudio"]},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 150
        }
        if (model_name or "").strip():
            payload["model"] = model_name.strip()

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            out_txt = data["choices"][0]["message"].get("content") or ""
            usage = data.get("usage") or {}
            cls._set_last_call_meta(
                engine="lmstudio",
                model=data.get("model") or (model_name or "").strip() or "lmstudio",
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                prompt_chars=len(prompt),
                output_chars=len(out_txt),
                estimated=False if usage else True,
            )
            return out_txt

    @staticmethod
    def _sanitize_output(text: str) -> str:
        """生成結果から見出し・挨拶・文字数メモ・引用符・<think>思考タグなどをクリーニングし、自然な本文のみを抽出"""
        clean = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not clean:
            return ""
        clean = re.sub(r"<think>.*?</think>", "", clean, flags=re.DOTALL | re.IGNORECASE).strip()
        if clean.lower().startswith("<think>"):
            return ""

        # 万が一3案の比較後に「最適案」「最終案」等を出力した場合は、その後の本文を優先抽出
        final_pick_match = re.search(
            r"(?:^|\n)\s*(?:【\s*(?:最適案|最終案|選んだ(?:返信|案|メッセージ)|採用案|ベスト案)\s*】|\*{0,2}(?:最適案|最終案|選んだ(?:返信|案|メッセージ)|採用案|ベスト案)\*{0,2}\s*[:：])\s*\n?(.*)$",
            clean,
            re.DOTALL,
        )
        if final_pick_match and final_pick_match.group(1).strip():
            clean = final_pick_match.group(1).strip()

        # 複数案が出力された場合は最初の案のみ採用
        split_multi = re.split(
            r"\n\s*(?:【\s*(?:案|候補|パターン)?\s*[2２②][^】]*】|(?:案|候補|パターン)\s*[2２②]\s*[:：]|[2２][\.．\)）]\s+|②\s*|---+\s*\n)",
            clean,
            maxsplit=1,
        )
        if len(split_multi) > 1 and split_multi[0].strip():
            clean = split_multi[0].strip()
            clean = re.sub(r"^(?:[1１][\.．\)）]\s+|①\s*)", "", clean).strip()

        # 先頭の前置き行・ラベル行（【返信案】、了解しました。、**返信案** 等）を段階的に除去
        header_line_re = re.compile(
            r"^(?:【\s*(?:返信案|リプライ案|引用案|作成案|回答|出力|メッセージ|案\s*[1１①]?)\s*】"
            r"|\[\s*(?:返信案|リプライ案|引用案|回答|出力)\s*\]"
            r"|\*\*\s*(?:返信案|リプライ案|引用案|回答|出力)\s*\*\*"
            r"|(?:了解しました|承知しました|かしこまりました|作成しました|以下(?:が|に|の通り)[^\n。]{0,30})[。！!：:\s]*)$"
        )
        inline_prefix_re = re.compile(
            r"^(?:(?:【\s*(?:返信案|リプライ案|引用案|回答|出力)\s*】"
            r"|(?:返信案|リプライ案|引用案|作成案|回答|返信|出力|メッセージ|案\s*[1１①]?|候補\s*[1１①]?|AI|Gemini(?:\s*の回答)?|ChatGPT|Claude|Grok))\s*[:：]?\s*)+"
        )

        lines = [ln.rstrip() for ln in clean.splitlines()]
        while lines:
            top = lines[0].strip()
            if not top:
                lines.pop(0)
                continue
            if header_line_re.match(top):
                lines.pop(0)
                continue
            stripped_top = inline_prefix_re.sub("", top).strip()
            if stripped_top != top:
                if stripped_top:
                    lines[0] = stripped_top
                else:
                    lines.pop(0)
                    continue
            break

        # 末尾の文字数メモやUIコピー補助文字を除去
        footer_line_re = re.compile(
            r"^(?:[（(]\s*(?:約\s*)?\d+\s*文字\s*[）)]|※\s*文字数[:：]?.*|コピー(?:する)?|Copy(?:\s*code)?)$",
            re.IGNORECASE,
        )
        while lines:
            bot = lines[-1].strip()
            if not bot or footer_line_re.match(bot):
                lines.pop()
                continue
            bot_clean = re.sub(r"\s*[（(]\s*(?:約\s*)?\d+\s*文字\s*[）)]\s*$", "", bot).strip()
            lines[-1] = bot_clean
            break

        clean = "\n".join(lines).strip()
        clean = re.sub(r"\n{3,}", "\n\n", clean)

        # 全体を囲む引用符を除去（最大2重まで）
        for _ in range(2):
            for lq, rq in [('"', '"'), ("'", "'"), ("「", "」"), ("『", "』"), ("“", "”")]:
                if clean.startswith(lq) and clean.endswith(rq) and len(clean) >= 2:
                    clean = clean[1:-1].strip()

        if len(clean) > 140:
            head = clean[:140]
            best_end = -1
            for sep in ("。", "！", "!", "？", "?"):
                pos = head.rfind(sep)
                if pos > best_end:
                    best_end = pos
            if best_end >= 55:
                clean = head[:best_end + 1].rstrip()
            else:
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
                        time.sleep(1.5)
                        break
                except Exception:
                    continue

        input_el = None
        login_notified = False
        max_wait_sec = float(getattr(cls._get_state(), "web_login_wait_sec", None) or 180.0)
        wait_start = time.time()
        while time.time() - wait_start < max_wait_sec:
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
    def _normalize_multiline_prompt(cls, prompt: str) -> str:
        """プロンプト内の改行構造を保ったまま余分な連続空行のみ整理"""
        text = (prompt or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

    @classmethod
    def _input_multiline_prompt(cls, ai_page, input_el, prompt: str) -> str:
        """
        改行を保持したままWeb AI入力欄へ安全に貼り付け/入力する。
        - <textarea>系: fill() で改行(\n)ごと一括セット（誤送信なし・高速）
        - contenteditable系 (Gemini/ChatGPT/Claude等): 行ごとに insert_text + Shift+Enter で改行を忠実再現
        """
        normalized_prompt = cls._normalize_multiline_prompt(prompt)
        input_el.click(timeout=4000)
        time.sleep(0.25)
        try:
            ai_page.keyboard.press("Control+A")
            ai_page.keyboard.press("Backspace")
            time.sleep(0.1)
        except Exception:
            pass

        is_textarea = False
        try:
            is_textarea = bool(input_el.evaluate("(el) => !!el && el.tagName === 'TEXTAREA'"))
        except Exception:
            is_textarea = False

        if is_textarea:
            try:
                input_el.fill(normalized_prompt)
                time.sleep(0.2)
                val = input_el.input_value()
                if val and "\n" in normalized_prompt and "\n" in val:
                    return normalized_prompt
                if val and "\n" not in normalized_prompt:
                    return normalized_prompt
            except Exception:
                pass

        # contenteditable または textarea のフォールバック: Shift+Enter + insert_text で改行を維持
        try:
            input_el.click(timeout=3000)
            ai_page.keyboard.press("Control+A")
            ai_page.keyboard.press("Backspace")
        except Exception:
            pass

        lines = normalized_prompt.split("\n")
        typed_ok = True
        try:
            for idx, line in enumerate(lines):
                if idx > 0:
                    ai_page.keyboard.press("Shift+Enter")
                if line:
                    ai_page.keyboard.insert_text(line)
        except Exception:
            typed_ok = False

        # 万一空になっていた場合の最終フォールバック
        try:
            cur_val = input_el.evaluate(
                "(el) => el ? (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' ? (el.value || '') : (el.innerText || el.textContent || '')) : ''"
            )
            if not typed_ok or not (cur_val or "").strip():
                input_el.fill(normalized_prompt)
        except Exception:
            if not typed_ok:
                try:
                    input_el.fill(normalized_prompt)
                except Exception:
                    pass

        return normalized_prompt

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

        normalized_prompt = cls._input_multiline_prompt(ai_page, input_el, prompt)
        prompt_ws = " ".join(normalized_prompt.split())
        time.sleep(0.5)

        sent = False
        send_deadline = time.time() + 1.8
        while time.time() < send_deadline and not sent:
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
                time.sleep(0.25)

        if not sent:
            try:
                input_el.click(timeout=2000)
            except Exception:
                pass
            ai_page.keyboard.press("Enter")

        print(f"   🤖 [AI専用ウィンドウ] {service_label} でリプライ/引用文を生成中（改行保持プロンプト送信済）...")
        eff_timeout = float(getattr(cls._get_state(), "web_stream_timeout_sec", None) or timeout_sec)
        gen_start = time.time()
        last_txt = ""
        stable_ticks = 0
        while time.time() - gen_start < eff_timeout:
            time.sleep(0.8)
            cur_txt = ""
            for rsel in resp_selectors:
                try:
                    loc = ai_page.locator(rsel)
                    cnt = loc.count()
                    if cnt > prev_counts.get(rsel, 0):
                        candidate = loc.last.inner_text().strip()
                        if candidate:
                            cand_ws = " ".join(candidate.split())
                            if cand_ws != prompt_ws and candidate != normalized_prompt:
                                cur_txt = candidate
                                break
                    elif cnt > 0:
                        candidate = loc.last.inner_text().strip()
                        if candidate and candidate != prev_last_texts.get(rsel, ""):
                            cand_ws = " ".join(candidate.split())
                            if cand_ws != prompt_ws and candidate != normalized_prompt:
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
                login_markers=["/i/flow/login", "/login"],
                new_chat_selectors=['a[href="/i/grok"]', 'button[aria-label*="新しいチャット"]', 'button[aria-label*="New chat"]']
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
                login_markers=["sign_in", "login"],
                new_chat_selectors=['button:has-text("New chat")', 'button:has-text("新しいチャット")', 'button[aria-label*="New chat"]']
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
                login_markers=["login", "signin"],
                new_chat_selectors=['a[href="/"]', 'button[aria-label*="New Thread"]', 'button[aria-label*="新しいスレッド"]']
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
                login_markers=["login.live.com", "login.microsoftonline.com"],
                new_chat_selectors=['button[aria-label*="New topic"]', 'button[aria-label*="新しいトピック"]', 'button[aria-label*="New chat"]']
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
    def _generate_via_venice_web(cls, ai_page, prompt: str, custom_url: str = "") -> Tuple[bool, str]:
        """Venice AI (https://venice.ai/chat - 登録不要ゲスト利用可) 連携"""
        try:
            target = custom_url or "https://venice.ai/chat"
            cur_url = ai_page.url or ""
            if "venice.ai" not in cur_url:
                print(f"🌐 [AI専用ウィンドウ] Venice AI ({target}) へ移動します...")
                ai_page.goto(target, timeout=35000, wait_until="domcontentloaded")
                time.sleep(2.5)

            for guest_sel in [
                'button:has-text("Try without")',
                'button:has-text("Continue as Guest")',
                'button:has-text("Start Chatting")',
                'button:has-text("Accept")',
                'button:has-text("I Agree")',
                'button:has-text("同意")',
            ]:
                try:
                    btn = ai_page.locator(guest_sel)
                    if btn.count() > 0 and btn.first.is_visible():
                        btn.first.click(timeout=2500)
                        time.sleep(1.0)
                except Exception:
                    continue

            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    'textarea[placeholder*="Ask"]',
                    'textarea[placeholder*="Message"]',
                    'textarea[placeholder*="Type"]',
                    'textarea',
                    'div[contenteditable="true"]',
                ],
                service_label="Venice AI",
                target_url=target,
                login_markers=[],
                new_chat_selectors=['button:has-text("New Chat")', 'button[aria-label*="New"]']
            )
            if input_el is None:
                return False, "Venice AIの入力欄が検出できませんでした"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    'div[class*="assistant"]',
                    '.prose',
                    '.markdown',
                    'article',
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
                service_label="Venice AI Web"
            )
        except Exception as e:
            return False, f"Venice AI Web連携エラー: {e}"

    @classmethod
    def _generate_via_deepai_web(cls, ai_page, prompt: str, custom_url: str = "") -> Tuple[bool, str]:
        """DeepAI Chat (https://deepai.org/chat - 登録不要・無料) 連携"""
        try:
            target = custom_url or "https://deepai.org/chat"
            cur_url = ai_page.url or ""
            if "deepai.org" not in cur_url:
                print(f"🌐 [AI専用ウィンドウ] DeepAI Chat ({target}) へ移動します...")
                ai_page.goto(target, timeout=35000, wait_until="domcontentloaded")
                time.sleep(2.5)

            for agree_sel in [
                'button:has-text("Accept")',
                'button:has-text("Agree")',
                'button:has-text("Continue")',
                'button:has-text("同意")',
            ]:
                try:
                    btn = ai_page.locator(agree_sel)
                    if btn.count() > 0 and btn.first.is_visible():
                        btn.first.click(timeout=2000)
                        time.sleep(0.8)
                except Exception:
                    continue

            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    'textarea.chatbox',
                    'textarea[placeholder*="Chat"]',
                    'textarea[placeholder*="Ask"]',
                    'textarea',
                    'div[contenteditable="true"]',
                ],
                service_label="DeepAI Chat",
                target_url=target,
                login_markers=[],
                new_chat_selectors=['button:has-text("New Chat")', 'button:has-text("Clear")']
            )
            if input_el is None:
                return False, "DeepAI Chatの入力欄が検出できませんでした"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    '.markdown-body',
                    '.outputBox',
                    'div[class*="chatMessage"]',
                    '.prose',
                    '.markdown',
                ],
                send_selectors=[
                    'button#chatSubmitButton',
                    'button[type="submit"]',
                    'button:has-text("Send")',
                    'button[aria-label*="Send"]',
                ],
                stop_selectors=[
                    'button[aria-label*="Stop"]',
                    'button:has-text("Stop")',
                ],
                service_label="DeepAI Chat Web"
            )
        except Exception as e:
            return False, f"DeepAI Chat Web連携エラー: {e}"

    @classmethod
    def _generate_via_talkai_web(cls, ai_page, prompt: str, custom_url: str = "") -> Tuple[bool, str]:
        """TalkAI (https://talkai.info/ - 登録不要・無料チャット) 連携"""
        try:
            target = custom_url or "https://talkai.info/"
            cur_url = ai_page.url or ""
            if "talkai.info" not in cur_url:
                print(f"🌐 [AI専用ウィンドウ] TalkAI ({target}) へ移動します...")
                ai_page.goto(target, timeout=35000, wait_until="domcontentloaded")
                time.sleep(2.5)

            input_el = cls._wait_for_input_or_login(
                ai_page,
                input_selectors=[
                    'textarea[placeholder*="Ask"]',
                    'textarea[placeholder*="Message"]',
                    'textarea',
                    'div[contenteditable="true"]',
                    'input[type="text"]',
                ],
                service_label="TalkAI",
                target_url=target,
                login_markers=[],
                new_chat_selectors=['button:has-text("New")', 'button:has-text("Clear")']
            )
            if input_el is None:
                return False, "TalkAIの入力欄が検出できませんでした"

            return cls._submit_and_stream_response(
                ai_page,
                input_el,
                prompt,
                resp_selectors=[
                    'div[class*="assistant"]',
                    'div[class*="message"]',
                    '.markdown',
                    '.prose',
                    'article',
                ],
                send_selectors=[
                    'button[type="submit"]',
                    'button:has-text("Send")',
                    'button[aria-label*="Send"]',
                ],
                stop_selectors=[
                    'button[aria-label*="Stop"]',
                ],
                service_label="TalkAI Web"
            )
        except Exception as e:
            return False, f"TalkAI Web連携エラー: {e}"

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
        parent_post_text: str = "",
        prompt_config: Optional[dict] = None,
        model_name: str = "",
        is_fallback: bool = False
    ) -> Tuple[bool, str]:
        """
        AI専用ウィンドウで選択されたAIエンジン・接続URLを起動して文章を生成し、クリップボードおよび戻り値として返却
        """
        eff_eng, _ = AIGenerator.resolve_engine_and_url(engine, api_key)
        if eff_eng in ["none", "off", "無効", ""]:
            return False, "AIエンジンが未指定または無効です"

        scoped_val = AIGenerator.get_key_or_url_for_engine(eff_eng, api_key, primary_engine=eff_eng)
        has_key = AIGenerator.has_api_key(eff_eng, api_key)
        st = cls._get_state()
        prev_login_wait = getattr(st, "web_login_wait_sec", None)
        prev_stream_timeout = getattr(st, "web_stream_timeout_sec", None)
        if is_fallback:
            st.web_login_wait_sec = 8.0
            st.web_stream_timeout_sec = 30.0
        try:
            ai_page = cls.get_or_create_ai_page(
                playwright,
                headless=headless,
                base_dir=base_dir,
                initial_engine=eff_eng,
                has_api_key=has_key,
                slot_id=slot_id,
                connection_url=scoped_val
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
                    parent_post_text=parent_post_text,
                    prompt_config=prompt_config,
                    model_name=model_name
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
            elif eff_eng == "chrome_builtin" and not AIGenerator.is_web_url(scoped_val):
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
                    parent_post_text=parent_post_text,
                    prompt_config=prompt_config,
                    model_name=model_name
                )
                if not ok and not is_fallback:
                    prompt = AIGenerator.build_prompt(
                        post_url, post_text, action_type, target_name, custom_prompt,
                        parent_post_text=parent_post_text, prompt_config=prompt_config
                    )
                    ok, result_text = cls._generate_via_gemini_web(ai_page, prompt)

            # 3. Web連携エンジンまたは接続URL指定時: AI専用ウィンドウ上の対象AIサイトで生成！
            else:
                prompt = AIGenerator.build_prompt(
                    post_url, post_text, action_type, target_name, custom_prompt,
                    parent_post_text=parent_post_text, prompt_config=prompt_config
                )
                custom_web_url = scoped_val.strip() if AIGenerator.is_web_url(scoped_val) else ""
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
                elif eff_eng == "venice":
                    ok, result_text = cls._generate_via_venice_web(ai_page, prompt, custom_web_url)
                elif eff_eng == "deepai":
                    ok, result_text = cls._generate_via_deepai_web(ai_page, prompt, custom_web_url)
                elif eff_eng == "talkai":
                    ok, result_text = cls._generate_via_talkai_web(ai_page, prompt, custom_web_url)
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
                    if not ok and not is_fallback:
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
                cur_meta = AIGenerator.get_last_call_meta()
                if not cur_meta.get("engine"):
                    used_prompt = prompt if 'prompt' in locals() else AIGenerator.build_prompt(
                        post_url, post_text, action_type, target_name, custom_prompt,
                        parent_post_text=parent_post_text, prompt_config=prompt_config
                    )
                    AIGenerator._set_last_call_meta(
                        engine=eff_eng,
                        model=(model_name or (custom_web_url if 'custom_web_url' in locals() and custom_web_url else "web")),
                        prompt_tokens=AIGenerator.estimate_tokens(used_prompt),
                        completion_tokens=AIGenerator.estimate_tokens(result_text),
                        prompt_chars=len(used_prompt),
                        output_chars=len(result_text),
                        estimated=True,
                    )
                try:
                    ai_page.evaluate("(text) => { try { navigator.clipboard && navigator.clipboard.writeText(text); } catch(e){} }", result_text)
                except Exception:
                    pass
                return True, result_text
            else:
                return False, result_text

        except Exception as e:
            return False, f"AI専用ウィンドウエラー: {str(e)}"
        finally:
            st.web_login_wait_sec = prev_login_wait
            st.web_stream_timeout_sec = prev_stream_timeout
