#!/usr/bin/env python3
"""防衛省審議会データベースの日次更新を 1 コマンドで実行する。

処理の流れ（docs/update_plan.md 3.2 節）:
1. 更新前の表と build_db 警告のスナップショットを取る
2. check_updates.py で台帳ページの更新チェック・変更ページ保存・新規ページ取得
3. 一覧ページが変わっていれば councils.csv を同期し、build_db.py で表を作り直す
4. 新規 PDF があれば fetch_files.py → extract_text.py → build_db.py（週次オプション時は差し替え照合も実施）
5. events.py で回帰の検査を行う（説明できない行の消失が 1 件でもあれば表を戻して終了コード 2 で失敗）
6. 名簿が変わっていれば変更前を members_history.csv に退避し、イベントを events.csv に追記する

使い方:
    python3 mod/update.py
    python3 mod/update.py --dry-run
    python3 mod/update.py --council kousei_nyusatu
    python3 mod/update.py --check-pdf-updates
    python3 mod/update.py --skip-fetch --changed-page /j/policy/agenda/meeting/kousei_nyusatu/index.html
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_db  # noqa: E402
import check_updates  # noqa: E402
import events  # noqa: E402
import extract_text  # noqa: E402
import fetch_files  # noqa: E402
import ingest_pages  # noqa: E402
import persons  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "mod"
TABLES = (
    "councils.csv",
    "council_profiles.csv",
    "members.csv",
    "meetings.csv",
    "documents.csv",
    "attendance.csv",
    "persons.csv",
)


@dataclass
class UpdateOutcome:
    ok: bool = True
    exit_code: int = 0
    changed_pages: list[str] = field(default_factory=list)
    new_pages: list[str] = field(default_factory=list)
    gone_pages: list[str] = field(default_factory=list)
    saved_pdfs: list[str] = field(default_factory=list)
    replaced_pdfs: list[str] = field(default_factory=list)
    events: list[dict[str, str]] = field(default_factory=list)
    archived_members: int = 0
    new_warnings: list[str] = field(default_factory=list)
    error: str = ""


def _write_built_tables(b: build_db.Builder, data_dir: Path) -> None:
    build_db.write_csv(data_dir / "council_profiles.csv", build_db.PROFILE_FIELDS, b.profiles)
    build_db.write_csv(data_dir / "members.csv", build_db.MEMBER_FIELDS, b.members)
    build_db.write_csv(data_dir / "meetings.csv", build_db.MEETING_FIELDS, b.meetings)
    build_db.write_csv(data_dir / "documents.csv", build_db.DOCUMENT_FIELDS, b.documents)
    build_db.write_csv(data_dir / "attendance.csv", build_db.ATTENDANCE_FIELDS, b.attendance)
    build_db.write_csv(data_dir / "persons.csv", persons.PERSON_FIELDS, b.persons)


def _backup_tables(data_dir: Path) -> dict[Path, bytes | None]:
    backups: dict[Path, bytes | None] = {}
    for name in TABLES:
        p = data_dir / name
        backups[p] = p.read_bytes() if p.exists() else None
    return backups


def _restore_tables(backups: dict[Path, bytes | None]) -> None:
    for p, content in backups.items():
        if content is None:
            if p.exists():
                p.unlink()
        else:
            p.write_bytes(content)


def _extract_new_texts(files_manifest: Path, files_dir: Path, text_dir: Path,
                       texts_csv: Path, documents_csv: Path, repo: Path) -> int:
    """未抽出または SHA-256 が変わった PDF の本文を抽出する。"""
    from pdfminer.high_level import extract_text as pdf_extract_text
    import logging

    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    done = {r["url"]: r for r in extract_text.read_csv(texts_csv)}
    doc_types = {r["url"]: r["doc_type"] for r in extract_text.read_csv(documents_csv)}
    todo = [
        r for r in extract_text.read_csv(files_manifest)
        if r.get("sha256") and done.get(r["url"], {}).get("sha256") != r["sha256"]
    ]
    todo.sort(key=lambda r: (doc_types.get(r["url"]) not in extract_text.FIRST, r["url"]))
    if not todo:
        return 0
    for r in todo:
        url = r["url"]
        src = files_dir.joinpath(*extract_text.url_parts(url))
        row = {"url": url, "text": "", "pages": "", "chars": "0", "sha256": r["sha256"], "note": ""}
        if not src.exists():
            row["note"] = "PDF 原本が手元にないため未抽出"
            done[url] = row
            continue
        try:
            raw = pdf_extract_text(str(src))
        except Exception as e:
            row["note"] = f"抽出失敗: {type(e).__name__}: {e}"[:200]
        else:
            text = extract_text.normalize(raw)
            out = extract_text.text_path(url, text_dir)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8", newline="\n")
            try:
                rel = out.relative_to(repo).as_posix()
            except ValueError:
                rel = str(out)
            row["text"] = rel
            row["pages"] = str(raw.count("\f"))
            row["chars"] = str(sum(1 for ch in text if not ch.isspace()))
            if int(row["chars"]) < 20 * max(1, int(row["pages"])):
                row["note"] = "本文がほとんど取れない（画像の PDF とみられる）"
        done[url] = row
    extract_text.write_csv(texts_csv, list(done.values()))
    return len(todo)


def run_update(*, repo: Path = REPO, data_dir: Path | None = None,
               pages_manifest: Path | None = None, raw_pages_dir: Path | None = None,
               files_manifest: Path | None = None, raw_files_dir: Path | None = None,
               council: str = "", limit: int = 0, max_new_pages: int = check_updates.MAX_NEW_PAGES,
               dry_run: bool = False, skip_fetch: bool = False, skip_pdf: bool = False,
               check_pdf_updates: bool = False, changed_pages_hint: list[str] | None = None,
               interval: float = check_updates.INTERVAL, summary_out: Path | None = None,
               page_fetcher=check_updates.fetch_page, pdf_fetcher=fetch_files.fetch,
               text_extractor=_extract_new_texts) -> UpdateOutcome:
    data_dir = data_dir or (repo / "data" / "mod")
    pages_manifest = pages_manifest or (repo / "raw" / "mod" / "pages.csv")
    raw_pages_dir = raw_pages_dir or (repo / "raw" / "mod" / "pages")
    files_manifest = files_manifest or (repo / "raw" / "mod" / "files.csv")
    raw_files_dir = raw_files_dir or (repo / "raw" / "mod" / "files")
    councils_csv = data_dir / "councils.csv"
    documents_csv = data_dir / "documents.csv"
    texts_csv = data_dir / "texts.csv"
    text_dir = data_dir / "text"
    events_csv = data_dir / "events.csv"
    members_history_csv = data_dir / "members_history.csv"

    outcome = UpdateOutcome()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1. 更新前のスナップショットと build_db 警告のベースラインを取得
    before = events.load_snapshot(data_dir, pages_manifest, files_manifest)
    baseline_warnings: list[str] = []
    if not skip_fetch and councils_csv.exists() and pages_manifest.exists():
        baseline_warnings = build_db.build(councils_csv, pages_manifest, raw_pages_dir, texts_csv, repo=repo).warnings
    backups = _backup_tables(data_dir)

    # 2. ページの更新チェック
    affected_pages: set[str] = set(changed_pages_hint or ())
    check_res: check_updates.CheckResult | None = None
    if not skip_fetch:
        try:
            check_res = check_updates.check_pages(
                manifest=pages_manifest,
                raw_dir=raw_pages_dir,
                councils_path=councils_csv,
                council=council,
                limit=limit,
                max_new_pages=max_new_pages,
                dry_run=dry_run,
                interval=interval,
                fetcher=page_fetcher,
            )
        except (check_updates.Blocked, check_updates.NoPagesChecked) as e:
            outcome.ok = False
            outcome.exit_code = 1
            outcome.error = str(e)
            return outcome
        outcome.changed_pages = list(check_res.changed_pages)
        outcome.new_pages = list(check_res.new_pages)
        outcome.gone_pages = list(check_res.gone_pages)
        affected_pages |= check_res.affected_pages

    if dry_run:
        return outcome

    # 変更がなく PDF 照合も未指定で未取得 PDF もなければ、表を作り直さずに終了する
    pending_pdfs = []
    if not skip_pdf and documents_csv.exists():
        pending_pdfs = fetch_files.select_targets(
            fetch_files.targets(documents_csv, council=council),
            fetch_files.read_manifest(files_manifest),
            files_dir=raw_files_dir,
        )
    if not skip_fetch and not affected_pages and not check_pdf_updates and not pending_pdfs:
        return outcome

    # 3. 表の作り直し（一覧ページが変わっていれば councils.csv も同期）
    index_url = build_db.ROOT + "index.html"
    if index_url in affected_pages or skip_fetch:
        index_file = ingest_pages.local_path(index_url, raw_pages_dir)
        if index_file.exists():
            build_db.sync_councils(councils_csv, raw_pages_dir, index_url=index_url, now_iso=now_iso, write=True)

    built = build_db.build(councils_csv, pages_manifest, raw_pages_dir, texts_csv, repo=repo)
    _write_built_tables(built, data_dir)

    # 4. PDF 取得・差し替え照合・本文抽出・再構築
    changed_doc_keys: set[str] = set()
    if not skip_pdf:
        try:
            pdf_res = fetch_files.fetch_new(
                documents_csv, files_manifest, raw_files_dir,
                council=council, dry_run=False, interval=interval, fetcher=pdf_fetcher,
            )
            outcome.saved_pdfs = list(pdf_res.saved)
            for u in pdf_res.saved:
                changed_doc_keys.add(build_db.doc_key(u))
            if check_pdf_updates:
                rep_res = fetch_files.check_replaced(
                    documents_csv, files_manifest, raw_files_dir,
                    council=council, dry_run=False, interval=interval, fetcher=pdf_fetcher,
                )
                outcome.replaced_pdfs = [u for u, _, _ in rep_res.replaced]
                for u in outcome.replaced_pdfs:
                    changed_doc_keys.add(build_db.doc_key(u))
        except fetch_files.Blocked as e:
            _restore_tables(backups)
            outcome.ok = False
            outcome.exit_code = 1
            outcome.error = f"PDF 取得が遮断されました: {e}"
            return outcome

        if outcome.saved_pdfs or outcome.replaced_pdfs:
            text_extractor(files_manifest, raw_files_dir, text_dir, texts_csv, documents_csv, repo)
            built = build_db.build(councils_csv, pages_manifest, raw_pages_dir, texts_csv, repo=repo)
            _write_built_tables(built, data_dir)

    # 5. 回帰の検査（3.8 節）
    after = events.load_snapshot(data_dir, pages_manifest, files_manifest)
    reg = events.check_regressions(before, after, affected_pages, changed_doc_keys)
    if not reg.ok:
        _restore_tables(backups)
        lines = [f"回帰の検査に失敗しました（説明できない行の消失 {len(reg.unexplained)} 件）:"]
        for d in reg.unexplained[:20]:
            lines.append(f"  - {d.table}: {d.key} (source={d.source}, reason={d.reason})")
        outcome.ok = False
        outcome.exit_code = 2
        outcome.error = "\n".join(lines)
        return outcome

    # 6. 警告の増加確認・名簿履歴の退避・イベント追記
    base_warn_set = set(baseline_warnings)
    outcome.new_warnings = [w for w in built.warnings if w not in base_warn_set] if baseline_warnings or not skip_fetch else []

    archived = events.archive_members_history(
        before.members, after.members, history_path=members_history_csv, recorded_at=now_iso, write=True,
    )
    outcome.archived_members = len(archived)

    ev_list = events.diff_snapshots(before, after, detected_at=now_iso)
    events.append_events(ev_list, path=events_csv)
    outcome.events = ev_list

    if summary_out and (ev_list or outcome.new_warnings):
        summary_out.parent.mkdir(parents=True, exist_ok=True)
        summary_out.write_text(
            events.format_issue_body(ev_list, new_warnings=outcome.new_warnings),
            encoding="utf-8",
            newline="\n",
        )

    return outcome


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="書き込み・push・LLM 呼び出しを行わない")
    ap.add_argument("--no-llm", action="store_true", help="Gemini の処理をスキップする（Phase 1 では既定）")
    ap.add_argument("--council", default="", help="対象の会議体 ID を絞る")
    ap.add_argument("--limit", type=int, default=0, help="チェックする既存ページ数の上限")
    ap.add_argument("--max-new-pages", type=int, default=check_updates.MAX_NEW_PAGES,
                    help="1 回に取得する新規ページの上限")
    ap.add_argument("--max-llm-calls", type=int, default=20, help="1 回の実行での LLM 呼び出し上限（Phase 3 用）")
    ap.add_argument("--check-pdf-updates", action="store_true",
                    help="取得済み PDF 全件を条件付き取得で照合し、差し替えを検知する")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="HTML 取得チェックを飛ばし、保存済みページから表再構築・イベント記録だけ行う")
    ap.add_argument("--skip-pdf", action="store_true", help="PDF 取得・本文抽出を飛ばす")
    ap.add_argument("--changed-page", action="append", default=[],
                    help="--skip-fetch 時に変更があったとみなすページのパス（複数指定可）")
    ap.add_argument("--interval", type=float, default=check_updates.INTERVAL, help="リクエスト間の待機秒数")
    ap.add_argument("--summary-out", type=Path, default=None,
                    help="変更通知用の Markdown（Issue 本文）を書き出すパス")
    args = ap.parse_args()

    out = run_update(
        council=args.council,
        limit=args.limit,
        max_new_pages=args.max_new_pages,
        dry_run=args.dry_run,
        skip_fetch=args.skip_fetch,
        skip_pdf=args.skip_pdf,
        check_pdf_updates=args.check_pdf_updates,
        changed_pages_hint=args.changed_page,
        interval=args.interval,
        summary_out=args.summary_out,
    )
    if not out.ok:
        print(out.error, file=sys.stderr)
        return out.exit_code

    tag = "（dry-run）" if args.dry_run else ""
    if not (out.changed_pages or out.new_pages or out.gone_pages or out.saved_pdfs or out.replaced_pdfs or out.events):
        print(f"変更なし{tag}")
        return 0

    print(
        f"更新完了{tag}: 変更ページ {len(out.changed_pages)} / 新規ページ {len(out.new_pages)} / "
        f"消失 {len(out.gone_pages)} / 新規PDF {len(out.saved_pdfs)} / 差し替えPDF {len(out.replaced_pdfs)} / "
        f"イベント {len(out.events)} 件 / 名簿履歴退避 {out.archived_members} 行"
    )
    for ev in out.events[:30]:
        print(f"  [{ev['event_type']}] {ev['council_id']} {ev['target_key']}: {ev['before']} -> {ev['after']}")
    if out.new_warnings:
        print(f"新たな警告 {len(out.new_warnings)} 件:", file=sys.stderr)
        for w in out.new_warnings:
            print(f"  {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
