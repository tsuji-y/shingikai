"""Phase 3 の 4 つの Gemini 処理（docs/update_plan.md 3.9 節）。

1. ocr_image_pdfs:            画像だけの PDF の文字起こし → data/mod/text_llm/ と texts.csv / llm_extractions.csv
2. extract_missing_attendance: 出席者未抽出の議事要旨・議事録の補完 → llm_extractions.csv（検証通過分のみ本体反映）
3. summarize_documents:       本文の要約 → data/mod/summaries.csv
4. review_changes:            変更があった日の検討メモ → Markdown 出力（Issue コメント用、表には入れない）

すべての処理で:
- 入力（本文または PDF）の SHA-256 をキーにして重複呼び出しを防ぐ。
- CallBudget で 1 回の実行あたりの API 呼び出し上限（--max-llm-calls）を厳守する。
- モデル名・プロンプト版・生成日時・入力 SHA-256 を全行に記録する。
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build_db  # noqa: E402
import extract_text  # noqa: E402
import fetch_files  # noqa: E402

from .client import LLMError
from .config import (
    DATA,
    DEFAULT_MAX_CALLS,
    EXTRACTION_FIELDS,
    LLM_EXTRACTIONS_CSV,
    MAX_INPUT_CHARS,
    RAW_FILES,
    REPO,
    SUMMARIES_CSV,
    SUMMARY_FIELDS,
    TEXT_LLM_DIR,
    load_prompt,
)
from .validate import validate_minutes_extraction, validate_summary


@dataclass
class CallBudget:
    limit: int = DEFAULT_MAX_CALLS
    used: int = 0

    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def consume(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


@dataclass
class TaskStats:
    processed: int = 0
    accepted: int = 0
    rejected: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fields})
    tmp.replace(path)


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_image_pdf_row(row: dict[str, str]) -> bool:
    note = row.get("note", "")
    if "画像の PDF" in note:
        return True
    try:
        chars = int(row.get("chars") or "0")
        pages_n = max(1, int(row.get("pages") or "1"))
        return chars < 20 * pages_n and bool(row.get("sha256"))
    except ValueError:
        return False


def ocr_image_pdfs(client: Any, budget: CallBudget, *,
                   repo: Path = REPO, data_dir: Path = DATA, raw_files_dir: Path = RAW_FILES,
                   text_llm_dir: Path = TEXT_LLM_DIR, extractions_csv: Path = LLM_EXTRACTIONS_CSV,
                   council: str = "", dry_run: bool = False,
                   allow_fetch_pdf: bool = True) -> TaskStats:
    """画像だけの PDF を Gemini で文字起こしし、data/mod/text_llm/ と台帳に記録する。"""
    import time

    stats = TaskStats()
    texts_csv = data_dir / "texts.csv"
    docs_csv = data_dir / "documents.csv"
    texts_rows = _read_csv(texts_csv)
    docs_by_url = {r["url"]: r for r in _read_csv(docs_csv)}
    ext_rows = _read_csv(extractions_csv)
    done_keys = {(r["task"], r["url"], r["input_sha256"]) for r in ext_rows if r.get("task") == "ocr"}

    prompt_ver, prompt_text = load_prompt("ocr")
    dirty_texts = False
    fetched_any = False

    for tr in texts_rows:
        if not _is_image_pdf_row(tr):
            continue
        url = tr["url"]
        doc = docs_by_url.get(url, {})
        if council and doc.get("council_id") != council:
            continue
        pdf_sha = tr.get("sha256", "")
        if not pdf_sha or ("ocr", url, pdf_sha) in done_keys:
            stats.skipped += 1
            continue
        try:
            pdf_path = fetch_files.local_path(url, raw_files_dir)
        except ValueError:
            stats.skipped += 1
            continue
        if not pdf_path.exists() and not allow_fetch_pdf:
            stats.errors.append(f"OCR スキップ（原本 PDF 不在）: {url}")
            stats.skipped += 1
            continue
        if not budget.consume():
            break

        stats.processed += 1
        if dry_run:
            continue

        if pdf_path.exists():
            pdf_bytes = pdf_path.read_bytes()
        else:
            if fetched_any:
                time.sleep(fetch_files.INTERVAL)
            fetched_any = True
            try:
                _, pdf_bytes = fetch_files.fetch(url)
            except fetch_files.Blocked as e:
                stats.errors.append(f"OCR 用 PDF 取得が遮断されたため中断: {e}")
                break
            if pdf_bytes is None:
                stats.errors.append(f"OCR 用 PDF 取得失敗: {url}")
                continue
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(pdf_bytes)

        generated_at = _now_iso()
        try:
            transcribed = client.transcribe_pdf(prompt_text, pdf_bytes)
        except LLMError as e:
            stats.errors.append(f"OCR 失敗 ({url}): {e}")
            continue

        normalized = extract_text.normalize(transcribed)
        chars = sum(1 for ch in normalized if not ch.isspace())
        if chars < 10:
            ext_rows.append({
                "task": "ocr", "url": url, "doc_key": build_db.doc_key(url),
                "council_id": doc.get("council_id", ""), "meeting_id": doc.get("meeting_id", ""),
                "date": "", "date_raw": "", "time": "", "place": "", "agenda": "",
                "attendees_json": "", "text_path": "",
                "validation_status": "rejected", "validation_error": f"文字起こし結果が短すぎる ({chars}字)",
                "model": client.model, "prompt_version": prompt_ver,
                "generated_at": generated_at, "input_sha256": pdf_sha,
            })
            stats.rejected += 1
        else:
            out_path = extract_text.text_path(url, text_llm_dir)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(normalized, encoding="utf-8", newline="\n")
            try:
                rel_path = out_path.relative_to(repo).as_posix()
            except ValueError:
                rel_path = str(out_path)
            ext_rows.append({
                "task": "ocr", "url": url, "doc_key": build_db.doc_key(url),
                "council_id": doc.get("council_id", ""), "meeting_id": doc.get("meeting_id", ""),
                "date": "", "date_raw": "", "time": "", "place": "", "agenda": "",
                "attendees_json": "", "text_path": rel_path,
                "validation_status": "accepted", "validation_error": "",
                "model": client.model, "prompt_version": prompt_ver,
                "generated_at": generated_at, "input_sha256": pdf_sha,
            })
            tr["note"] = f"LLM文字起こし済み({client.model}/{prompt_ver}): {rel_path}"
            dirty_texts = True
            stats.accepted += 1

        done_keys.add(("ocr", url, pdf_sha))
        _write_csv(extractions_csv, EXTRACTION_FIELDS, ext_rows)
        if dirty_texts:
            extract_text.write_csv(texts_csv, texts_rows)

    return stats


def extract_missing_attendance(client: Any, budget: CallBudget, *,
                               repo: Path = REPO, data_dir: Path = DATA,
                               extractions_csv: Path = LLM_EXTRACTIONS_CSV,
                               council: str = "", target_urls: set[str] | None = None,
                               backfill: bool = False, dry_run: bool = False) -> TaskStats:
    """既存パーサで出席者を読み取れなかった議事要旨・議事録を Gemini で補完する。"""
    stats = TaskStats()
    docs = _read_csv(data_dir / "documents.csv")
    att_mids = {r["meeting_id"] for r in _read_csv(data_dir / "attendance.csv")}
    ext_rows = _read_csv(extractions_csv)
    done_keys = {(r["task"], r["url"], r["input_sha256"]) for r in ext_rows if r.get("task") == "extract"}

    prompt_ver, prompt_text = load_prompt("extract")

    for d in docs:
        if d.get("doc_type") not in build_db.MINUTES_TYPES:
            continue
        mid = d.get("meeting_id", "")
        if not mid or mid in att_mids:
            continue
        if council and d.get("council_id") != council:
            continue
        url = d["url"]
        if not backfill and target_urls is not None and url not in target_urls:
            continue
        text_rel = d.get("text", "")
        if not text_rel:
            continue
        text_file = repo / text_rel
        if not text_file.exists():
            continue
        source_text = text_file.read_text(encoding="utf-8")
        if sum(1 for ch in source_text if not ch.isspace()) < 30:
            continue
        input_sha = _text_sha256(source_text)
        if ("extract", url, input_sha) in done_keys:
            stats.skipped += 1
            continue
        if not budget.consume():
            break

        stats.processed += 1
        if dry_run:
            continue

        generated_at = _now_iso()
        excerpt = source_text[:12_000]
        try:
            payload = client.generate_json(prompt_text, excerpt)
        except LLMError as e:
            stats.errors.append(f"抽出失敗 ({url}): {e}")
            continue

        vres = validate_minutes_extraction(payload, source_text)
        c = vres.cleaned
        row = {
            "task": "extract",
            "url": url,
            "doc_key": d.get("doc_key") or build_db.doc_key(url),
            "council_id": d.get("council_id", ""),
            "meeting_id": mid,
            "date": c.get("date", ""),
            "date_raw": c.get("date_raw", ""),
            "time": c.get("time", ""),
            "place": c.get("place", ""),
            "agenda": c.get("agenda", ""),
            "attendees_json": json.dumps(c.get("attendees", []), ensure_ascii=False),
            "text_path": text_rel,
            "validation_status": "accepted" if vres.ok else "rejected",
            "validation_error": "; ".join(vres.errors),
            "model": client.model,
            "prompt_version": prompt_ver,
            "generated_at": generated_at,
            "input_sha256": input_sha,
        }
        ext_rows.append(row)
        done_keys.add(("extract", url, input_sha))
        if vres.ok:
            stats.accepted += 1
        else:
            stats.rejected += 1
        _write_csv(extractions_csv, EXTRACTION_FIELDS, ext_rows)

    return stats


def summarize_documents(client: Any, budget: CallBudget, *,
                        repo: Path = REPO, data_dir: Path = DATA,
                        summaries_csv: Path = SUMMARIES_CSV,
                        extractions_csv: Path = LLM_EXTRACTIONS_CSV,
                        council: str = "", target_urls: set[str] | None = None,
                        backfill: bool = False, dry_run: bool = False) -> TaskStats:
    """本文を Gemini で要約し、data/mod/summaries.csv に追記する。

    backfill=False かつ target_urls=None のときは、未要約の文書が大量にあっても
    誤って既存 421 件を自動処理しないよう対象外とする（既存分は --backfill で明示指定する）。
    """
    stats = TaskStats()
    if not backfill and target_urls is None:
        return stats

    docs = _read_csv(data_dir / "documents.csv")
    texts_by_url = {r["url"]: r for r in _read_csv(data_dir / "texts.csv")}
    ocr_by_url = {
        r["url"]: r["text_path"]
        for r in _read_csv(extractions_csv)
        if r.get("task") == "ocr" and r.get("validation_status") == "accepted" and r.get("text_path")
    }
    sum_rows = _read_csv(summaries_csv)
    done_keys = {(r["url"], r["input_sha256"]) for r in sum_rows}
    prompt_ver, prompt_text = load_prompt("summarize")

    # 議事録・議事要旨を優先して要約する
    seen_urls: set[str] = set()
    candidates: list[dict[str, str]] = []
    for d in docs:
        url = d["url"]
        if url in seen_urls:
            continue
        if council and d.get("council_id") != council:
            continue
        if not backfill and target_urls is not None and url not in target_urls:
            continue
        text_rel = ocr_by_url.get(url) or d.get("text") or texts_by_url.get(url, {}).get("text", "")
        if not text_rel:
            continue
        seen_urls.add(url)
        candidates.append({**d, "_text_rel": text_rel})

    candidates.sort(key=lambda d: (d.get("doc_type") not in extract_text.FIRST, d["council_id"], d["url"]))

    for d in candidates:
        url = d["url"]
        text_file = repo / d["_text_rel"]
        if not text_file.exists():
            continue
        source_text = text_file.read_text(encoding="utf-8")
        if sum(1 for ch in source_text if not ch.isspace()) < 30:
            stats.skipped += 1
            continue
        input_sha = _text_sha256(source_text)
        if (url, input_sha) in done_keys:
            stats.skipped += 1
            continue
        if not budget.consume():
            break

        stats.processed += 1
        if dry_run:
            continue

        generated_at = _now_iso()
        header = (
            f"会議体ID: {d.get('council_id', '')}\n"
            f"会議ID: {d.get('meeting_id', '')}\n"
            f"種別: {d.get('doc_type', '')}\n"
            f"表題: {d.get('title', '')}\n\n"
            "本文:\n"
        )
        content = header + source_text[:MAX_INPUT_CHARS]
        try:
            payload = client.generate_json(prompt_text, content)
        except LLMError as e:
            stats.errors.append(f"要約失敗 ({url}): {e}")
            continue

        vres = validate_summary(payload)
        if not vres.ok:
            stats.rejected += 1
            stats.errors.append(f"要約検証失敗 ({url}): {'; '.join(vres.errors)}")
            continue

        sum_rows.append({
            "url": url,
            "doc_key": d.get("doc_key") or build_db.doc_key(url),
            "council_id": d.get("council_id", ""),
            "meeting_id": d.get("meeting_id", ""),
            "doc_type": d.get("doc_type", ""),
            "title": d.get("title", ""),
            "summary": vres.cleaned["summary"],
            "key_points": vres.cleaned["key_points"],
            "model": client.model,
            "prompt_version": prompt_ver,
            "generated_at": generated_at,
            "input_sha256": input_sha,
        })
        done_keys.add((url, input_sha))
        stats.accepted += 1
        _write_csv(summaries_csv, SUMMARY_FIELDS, sum_rows)

    return stats


def review_changes(client: Any, budget: CallBudget, events_rows: list[dict[str, str]], *,
                   summaries_csv: Path = SUMMARIES_CSV, dry_run: bool = False) -> str:
    """変更イベントがある日だけ検討メモ（Markdown）を生成する。表には入れない。"""
    if not events_rows:
        return ""
    if not budget.consume():
        return ""
    if dry_run:
        return "（dry-run: 検討メモの生成をスキップ）\n"

    prompt_ver, prompt_text = load_prompt("review")
    sum_by_dk = {r.get("doc_key", ""): r for r in _read_csv(summaries_csv)}

    lines = ["## 更新イベント"]
    for ev in events_rows[:100]:
        lines.append(
            f"- [{ev['event_type']}] council={ev['council_id']} meeting={ev['meeting_id']} "
            f"key={ev['target_key']} before={ev['before']!r} after={ev['after']!r}"
        )
    related_summaries = []
    for ev in events_rows:
        s = sum_by_dk.get(ev.get("target_key", ""))
        if s:
            related_summaries.append(f"- {s['title']} ({s['council_id']}): {s['summary']}")
    if related_summaries:
        lines.extend(["", "## 関連資料の要約"])
        lines.extend(related_summaries[:20])

    memo = client.generate_text(prompt_text, "\n".join(lines)).strip()
    footer = f"\n\n---\n*この検討メモは Gemini（`{client.model}` / `{prompt_ver}`）による機械生成です。*"
    return memo + footer + "\n"
