#!/usr/bin/env python3
"""Phase 3（Gemini）処理の統合 CLI。

使い方:
    # 日次の新規・変更分のみ処理（既定: --max-llm-calls 20）
    python3 -m mod.llm.run --task all --events-since 2026-11-21T00:00:00+00:00

    # 既存 421 件のさかのぼり要約・補完（明示的に --backfill を指定）
    python3 -m mod.llm.run --task all --backfill --max-llm-calls 100

    # 1 会議体だけ少数テスト（docs/update_plan.md Phase 3 手順 4）
    python3 -m mod.llm.run --task all --council rinri --backfill --max-llm-calls 5

    # 30 件の精度評価
    python3 -m mod.llm.run --task evaluate --out checks/llm_eval_30.md
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build_db  # noqa: E402
import events  # noqa: E402
import update  # noqa: E402

from .client import GeminiClient, LLMError
from .config import DATA, DEFAULT_MAX_CALLS, DEFAULT_MODEL, LLM_EXTRACTIONS_CSV, REPO, SUMMARIES_CSV
from .evaluate import evaluate, format_report
from .tasks import (
    CallBudget,
    extract_missing_attendance,
    ocr_image_pdfs,
    review_changes,
    summarize_documents,
)


def _recent_events(events_csv: Path, since_iso: str) -> list[dict[str, str]]:
    if not events_csv.exists():
        return []
    with events_csv.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not since_iso:
        return []
    return [r for r in rows if r.get("detected_at", "") >= since_iso]


def _urls_from_events(ev_rows: list[dict[str, str]], documents_csv: Path) -> set[str]:
    if not ev_rows or not documents_csv.exists():
        return set()
    target_dks = {
        e["target_key"] for e in ev_rows
        if e.get("event_type") in ("document_added", "document_replaced")
    }
    if not target_dks:
        return set()
    urls: set[str] = set()
    with documents_csv.open(encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            dk = d.get("doc_key") or build_db.doc_key(d["url"])
            if dk in target_dks:
                urls.add(d["url"])
    return urls


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--task", choices=("all", "ocr", "extract", "summarize", "review", "evaluate"),
                    default="all", help="実行するタスク")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="使用する Gemini モデル")
    ap.add_argument("--max-llm-calls", type=int, default=DEFAULT_MAX_CALLS,
                    help="1 回の実行での API 呼び出し上限（既定 20）")
    ap.add_argument("--council", default="", help="対象の会議体 ID を絞る")
    ap.add_argument("--backfill", action="store_true",
                    help="既存の未処理本文（最大 421 件）も対象に含める")
    ap.add_argument("--events-since", default="",
                    help="この日時（UTC ISO）以降のイベントを対象に新規資料・検討メモを処理する")
    ap.add_argument("--review-out", type=Path, default=None,
                    help="変更検討メモ（Markdown）の出力先")
    ap.add_argument("--out", type=Path, default=REPO / "checks" / "llm_eval_30.md",
                    help="--task evaluate の出力先")
    ap.add_argument("--dry-run", action="store_true", help="API 呼び出しや書き込みを行わない")
    args = ap.parse_args()

    if args.task == "evaluate":
        if args.dry_run:
            print("evaluate（dry-run）: 実行をスキップしました")
            return 0
        try:
            client = GeminiClient(model=args.model)
        except LLMError as e:
            print(f"エラー: {e}", file=sys.stderr)
            return 1
        items = evaluate(client, n=min(30, args.max_llm_calls) if args.max_llm_calls < 30 else 30)
        report = format_report(items, model=args.model, seed=20261119)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8", newline="\n")
        print(f"評価レポート出力: {args.out} ({len(items)} 会議)")
        return 0

    if args.dry_run:
        class _DryClient:
            model = args.model
        client = _DryClient()
    else:
        try:
            client = GeminiClient(model=args.model)
        except LLMError as e:
            print(f"エラー: {e}", file=sys.stderr)
            return 1

    budget = CallBudget(limit=args.max_llm_calls)
    ev_rows = _recent_events(DATA / "events.csv", args.events_since)
    target_urls = _urls_from_events(ev_rows, DATA / "documents.csv") if not args.backfill else None

    if args.task in ("all", "ocr"):
        st = ocr_image_pdfs(client, budget, council=args.council, dry_run=args.dry_run)
        print(f"[ocr] 処理 {st.processed} / 採用 {st.accepted} / 却下 {st.rejected} / スキップ {st.skipped}")
        for err in st.errors:
            print(f"  {err}", file=sys.stderr)

    extracted_accepted = 0
    if args.task in ("all", "extract"):
        st = extract_missing_attendance(
            client, budget, council=args.council, target_urls=target_urls,
            backfill=args.backfill, dry_run=args.dry_run,
        )
        extracted_accepted = st.accepted
        print(f"[extract] 処理 {st.processed} / 採用 {st.accepted} / 却下 {st.rejected} / スキップ {st.skipped}")
        for err in st.errors:
            print(f"  {err}", file=sys.stderr)

    if extracted_accepted > 0 and not args.dry_run:
        # 検証を通過した抽出結果を既存の規則で本体表へ反映（ID は既存規則で付与）
        built = build_db.build()
        update._write_built_tables(built, DATA)
        print(f"[build_db] 検証済み LLM 抽出 {extracted_accepted} 件を本体表へ反映")

    if args.task in ("all", "summarize"):
        st = summarize_documents(
            client, budget, council=args.council, target_urls=target_urls,
            backfill=args.backfill, dry_run=args.dry_run,
        )
        print(f"[summarize] 処理 {st.processed} / 採用 {st.accepted} / 却下 {st.rejected} / スキップ {st.skipped}")
        for err in st.errors:
            print(f"  {err}", file=sys.stderr)

    if args.task in ("all", "review") and ev_rows:
        memo = review_changes(client, budget, ev_rows, summaries_csv=SUMMARIES_CSV, dry_run=args.dry_run)
        if memo:
            if args.review_out:
                args.review_out.parent.mkdir(parents=True, exist_ok=True)
                args.review_out.write_text(memo, encoding="utf-8", newline="\n")
                print(f"[review] 検討メモ出力: {args.review_out}")
            else:
                print(memo)

    print(f"LLM 呼び出し合計: {budget.used} / {budget.limit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
