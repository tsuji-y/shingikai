"""Gemini API クライアント（google-genai SDK の薄いラッパー）。

API キーは環境変数 GEMINI_API_KEY から読み、ログには一切出力しない。
google-genai は遅延インポートするので、テストや --dry-run では標準ライブラリだけで動く。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from .config import DEFAULT_MODEL

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


class LLMError(RuntimeError):
    """Gemini API 呼び出しまたは応答解釈のエラー。"""


class QuotaExhaustedError(LLMError):
    """日次クォータ上限（RPD）または利用不可クォータに達したときのエラー。"""


def extract_json(text: str) -> dict[str, Any]:
    """モデル応答から JSON オブジェクトを取り出す。"""
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise LLMError(f"JSON オブジェクトが見つかりません: {cleaned[:160]!r}")
    try:
        obj = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON 解釈エラー: {e}") from e
    if not isinstance(obj, dict):
        raise LLMError("応答 JSON がオブジェクトではありません")
    return obj


class GeminiClient:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None,
                 min_interval: float = 4.2, max_retries: int = 3,
                 timeout_sec: float = 90.0):
        key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise LLMError("環境変数 GEMINI_API_KEY が設定されていません")
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except ImportError as e:
            raise LLMError(
                "google-genai が未インストールです。`pip install -r requirements-llm.txt` を実行してください"
            ) from e
        self.model = model
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout_sec = timeout_sec
        self._genai = genai
        self._types = types
        client_kwargs: dict[str, Any] = {"api_key": key}
        http_opts_cls = getattr(types, "HttpOptions", None)
        if http_opts_cls is not None and timeout_sec > 0:
            try:
                client_kwargs["http_options"] = http_opts_cls(timeout=int(timeout_sec * 1000))
            except (TypeError, ValueError):
                pass
        self._client = genai.Client(**client_kwargs)
        self._last_call = 0.0

    def _pace(self) -> None:
        if self.min_interval > 0 and self._last_call > 0:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
        self._last_call = time.monotonic()

    def _build_config(self, *, json_mode: bool) -> Any:
        kwargs: dict[str, Any] = {
            "response_mime_type": "application/json" if json_mode else "text/plain",
        }
        if "gemini-3" in self.model:
            thinking_cls = getattr(self._types, "ThinkingConfig", None)
            if thinking_cls is not None:
                level = "MINIMAL" if "flash-lite" in self.model else "LOW"
                try:
                    kwargs["thinking_config"] = thinking_cls(thinking_level=level)
                except (TypeError, ValueError):
                    pass
        else:
            kwargs["temperature"] = 0.0
        return self._types.GenerateContentConfig(**kwargs)

    def _call_with_retry(self, contents: Any, *, json_mode: bool = False) -> str:
        cfg = self._build_config(json_mode=json_mode)
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            self._pace()
            try:
                resp = self._client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=cfg,
                )
                text = getattr(resp, "text", None) or ""
                if not text.strip():
                    raise LLMError("モデル応答が空です")
                return text
            except Exception as e:
                last_err = e
                msg = str(e)
                # 認証エラー・権限エラー・モデル不在はリトライしない
                if any(code in msg for code in ("401", "403", "API_KEY_INVALID", "PERMISSION_DENIED")):
                    raise LLMError(f"Gemini API 認証・権限エラー: {type(e).__name__}") from e
                if any(code in msg for code in ("404", "NOT_FOUND")):
                    raise LLMError(f"Gemini API モデル未検出: {type(e).__name__}: {e}") from e
                # 日次クォータ上限（PerDay / limit: 0）は即終了して保存済みを守る
                if ("429" in msg or "RESOURCE_EXHAUSTED" in msg) and any(
                    tok in msg for tok in ("PerDay", "GenerateRequestsPerDay", "limit: 0")
                ):
                    raise QuotaExhaustedError(f"Gemini API 日次クォータ到達: {e}") from e
                if attempt + 1 < self.max_retries:
                    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                        time.sleep(15.0 * (attempt + 1))
                    else:
                        time.sleep(2.0 * (2 ** attempt))
                    continue
        raise LLMError(f"Gemini API 呼び出し失敗: {type(last_err).__name__}: {last_err}") from last_err

    def generate_text(self, prompt: str, content: str) -> str:
        return self._call_with_retry([prompt, content], json_mode=False)

    def generate_json(self, prompt: str, content: str) -> dict[str, Any]:
        raw = self._call_with_retry([prompt, content], json_mode=True)
        return extract_json(raw)

    def transcribe_pdf(self, prompt: str, pdf_bytes: bytes) -> str:
        part = self._types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        return self._call_with_retry([prompt, part], json_mode=False)
