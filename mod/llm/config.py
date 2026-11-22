"""Phase 3（Gemini）の固定設定・スキーマ・プロンプト読み込み。"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DATA = REPO / "data" / "mod"
RAW_FILES = REPO / "raw" / "mod" / "files"
TEXT_LLM_DIR = DATA / "text_llm"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

SUMMARIES_CSV = DATA / "summaries.csv"
LLM_EXTRACTIONS_CSV = DATA / "llm_extractions.csv"

# モデルは設定で固定する。変えるときは Phase 3 の 30 件評価をやり直す（docs/update_plan.md 3.9 節）。
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_MAX_CALLS = 20
MAX_INPUT_CHARS = 120_000  # 1 資料あたりモデルへ渡す最大文字数

PROMPT_FILES = {
    "ocr": ("ocr-v1", "ocr_v1.txt"),
    "extract": ("extract-v1", "extract_minutes_v1.txt"),
    "summarize": ("summarize-v1", "summarize_v1.txt"),
    "review": ("review-v1", "review_changes_v1.txt"),
}

SUMMARY_FIELDS = [
    "url", "doc_key", "council_id", "meeting_id", "doc_type", "title",
    "summary", "key_points", "model", "prompt_version", "generated_at", "input_sha256",
]

EXTRACTION_FIELDS = [
    "task", "url", "doc_key", "council_id", "meeting_id",
    "date", "date_raw", "time", "place", "agenda", "attendees_json",
    "text_path", "validation_status", "validation_error",
    "model", "prompt_version", "generated_at", "input_sha256",
]


def load_prompt(task: str) -> tuple[str, str]:
    """(prompt_version, prompt_text) を返す。"""
    version, filename = PROMPT_FILES[task]
    text = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
    return version, text
