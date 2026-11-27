#!/usr/bin/env python3
"""過去分の下位 HTML・議事録 PDF・配布資料 PDF をチャンク分割で冪等にバックフィルするランナー。

ローカル Mac での長時間バックフィル実行を前提とし、以下の原則で動作する:
- 1 ドメイン 1 プロセス・2 秒間隔直列（複数省庁指定時は省庁単位で並列起動可能）。
- 各チャンクごとに「下位 HTML 取得→DB 再構築（内包 PDF 登録）→全種別 PDF 取得→テキスト抽出→
  DB 再構築→GitHub Releases へ 800MB シャード圧縮退避＆ローカル原本削除→統一索引（catalog）更新」
  をチェックポイント付きで実行する。
- 中断（Ctrl-C / 通信断 / 403・429）しても台帳が都度保存されるため、同じコマンドで途中から再開可能。
- WARP（warp.da.ndl.go.jp 等）は各省庁 hosts 外のため自動除外される。

推奨実行順（ウェーブ）:
    wave1 : ppc, mext, caa, cfa, mof, fsa, moj, cao, npa   （中小型 9 機関・全期間）
    wave2 : digital, mlit, maff, cas                       （中規模＋内閣官房 4 機関・全期間）
    wave3 : env, mhlw                                      （超大規模 2 機関・--since-year/--until-year で年代分割）

使い方:
    python3 mod/backfill.py --status-only
    python3 mod/backfill.py --agency ppc --archive --cleanup-local
    python3 mod/backfill.py --wave wave1 --parallel 4 --chunk-html 100 --chunk-pdf 100 --archive --cleanup-local
    python3 mod/backfill.py --agency mhlw --since-year 2024 --until-year 2026 --chunk-html 100 --chunk-pdf 100 --archive --cleanup-local
"""
from __future__ import annotations

import argparse
import concurrent.futures
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agencies  # noqa: E402
import archive_pdfs  # noqa: E402
import build_db  # noqa: E402
import build_index  # noqa: E402
import cross_agency  # noqa: E402
import fetch_files  # noqa: E402
import fetch_subpages  # noqa: E402
import persons  # noqa: E402
import update  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

WAVES: dict[str, list[str]] = {
    "wave1": ["ppc", "mext", "caa", "cfa", "mof", "fsa", "moj", "cao", "npa"],
    "wave2": ["digital", "mlit", "maff", "cas"],
    "wave3": ["env", "mhlw"],
    "all": [
        "ppc", "mext", "caa", "cfa", "mof", "fsa", "moj", "cao", "npa",
        "digital", "mlit", "maff", "cas", "env", "mhlw", "mod",
    ],
}


def _rebuild_agency_tables(ag: agencies.AgencyConfig) -> None:
    built = build_db.build(
        councils_path=ag.councils_csv,
        manifest=ag.pages_manifest,
        root=ag.raw_pages_dir,
        texts=ag.data_dir / "texts.csv",
        repo=ag.repo,
        base_url=ag.base_url,
        hosts=tuple(ag.hosts),
    )
    build_db.write_csv(ag.data_dir / "council_profiles.csv", build_db.PROFILE_FIELDS, built.profiles)
    build_db.write_csv(ag.data_dir / "members.csv", build_db.MEMBER_FIELDS, built.members)
    build_db.write_csv(ag.data_dir / "meetings.csv", build_db.MEETING_FIELDS, built.meetings)
    build_db.write_csv(ag.data_dir / "documents.csv", build_db.DOCUMENT_FIELDS, built.documents)
    build_db.write_csv(ag.data_dir / "attendance.csv", build_db.ATTENDANCE_FIELDS, built.attendance)
    build_db.write_csv(ag.data_dir / "persons.csv", persons.PERSON_FIELDS, built.persons)


def print_status(repo: Path = REPO, *, since_year: int = 0, until_year: int = 0) -> None:
    all_ags = agencies.load_agencies(repo / "agencies.json", repo=repo)
    yr_label = ""
    if since_year or until_year:
        yr_label = f" (years: {since_year or ''}..{until_year or ''})"
    print(f"=== Backfill Status{yr_label} ===")
    print(f"{'ag':7s} {'name':10s} | {'subHTML(done/rem)':>18s} | {'allPDF(done/rem)':>18s} | {'archPDF':>7s} | {'texts(arch/tot)':>15s}")
    print("-" * 90)
    tot_sh_done = tot_sh_rem = tot_pdf_done = tot_pdf_rem = tot_arch = tot_txt = tot_txt_arch = 0
    for aid, ag in all_ags.items():
        sp_done_rows = fetch_subpages.read_manifest(ag.subpages_manifest)
        sp_done = sum(1 for r in sp_done_rows.values() if r.get("sha256"))
        sp_all = fetch_subpages.collect_targets(ag, since_year=since_year, until_year=until_year, include_discovered=False)
        sp_rem = sum(1 for t in sp_all if t.url not in sp_done_rows)

        files_rows = fetch_files.read_manifest(ag.files_manifest)
        pdf_done = sum(1 for r in files_rows.values() if r.get("sha256"))
        pdf_arch = sum(1 for r in files_rows.values() if r.get("sha256") and r.get("release_url"))
        docs_csv = ag.data_dir / "documents.csv"
        pdf_urls = (
            fetch_files.targets(
                docs_csv, hosts=list(ag.hosts), since_year=since_year, until_year=until_year,
                excluded_urls=set(ag.excluded_urls),
            )
            if docs_csv.exists()
            else []
        )
        pdf_rem = len(fetch_files.select_targets(pdf_urls, files_rows, files_dir=ag.raw_files_dir))

        texts_rows = [r for r in build_index._read_csv(ag.data_dir / "texts.csv") if r.get("text")]
        n_txt = len(texts_rows)
        n_txt_arch = sum(1 for r in texts_rows if r.get("release_url"))

        print(f"{aid:7s} {ag.name:10s} | {sp_done:7d} / {sp_rem:<8d} | {pdf_done:7d} / {pdf_rem:<8d} | {pdf_arch:7d} | {n_txt_arch:6d} / {n_txt:<6d}")
        tot_sh_done += sp_done
        tot_sh_rem += sp_rem
        tot_pdf_done += pdf_done
        tot_pdf_rem += pdf_rem
        tot_arch += pdf_arch
        tot_txt += n_txt
        tot_txt_arch += n_txt_arch
    print("-" * 90)
    print(f"{'TOTAL':7s} {'全16省庁':10s} | {tot_sh_done:7d} / {tot_sh_rem:<8d} | {tot_pdf_done:7d} / {tot_pdf_rem:<8d} | {tot_arch:7d} | {tot_txt_arch:6d} / {tot_txt:<6d}")


def run_agency_backfill(
    agency_id: str,
    *,
    repo: Path = REPO,
    chunk_html: int = 100,
    chunk_pdf: int = 100,
    max_chunks: int = 0,
    since_year: int = 0,
    until_year: int = 0,
    include_discovered: bool = False,
    archive: bool = False,
    cleanup_local: bool = False,
    max_shard_mb: int = 800,
    interval: float = 2.0,
    retry_failed: bool = False,
    fail_log: Path | None = None,
    dry_run: bool = False,
) -> tuple[str, bool, str]:
    """1 省庁分のバックフィルをチャンクループで完了（または max_chunks 到達）まで実行する。"""
    ag = agencies.get_agency(agency_id, repo=repo)
    now = datetime.now(timezone.utc)
    pdf_tag = f"{ag.release_tag_prefix}-{now.year}-backfill"
    html_tag = f"{ag.html_release_tag_prefix}-{now.year}-backfill"
    text_tag = f"{ag.text_release_tag_prefix}-{now.year}-backfill"
    max_shard_bytes = max_shard_mb * 1024 * 1024

    chunk_no = 0
    total_html = 0
    total_pdf = 0
    total_extracted = 0

    try:
        while True:
            chunk_no += 1
            if max_chunks and chunk_no > max_chunks:
                break

            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-c{chunk_no:03d}"
            worked = False
            rebuilt = False

            # 1. 下位 HTML 取得（テキスト抽出 + 内包 PDF 発見）
            if chunk_html > 0:
                sp_res = fetch_subpages.fetch_subpages(
                    ag,
                    limit=chunk_html,
                    retry=retry_failed and chunk_no == 1,
                    since_year=since_year,
                    until_year=until_year,
                    include_discovered=include_discovered,
                    dry_run=dry_run,
                    interval=interval,
                    fail_log=fail_log,
                )
                if sp_res.checked > 0:
                    worked = True
                total_html += len(sp_res.saved)
                if not dry_run and sp_res.saved:
                    _rebuild_agency_tables(ag)
                    rebuilt = True

            # 2. 全種別 PDF 取得（議事録・議事要旨・名簿・配布資料・報告書などすべて、WARP 除く）
            if chunk_pdf > 0 and (ag.data_dir / "documents.csv").exists():
                pdf_res = fetch_files.fetch_new(
                    documents=ag.data_dir / "documents.csv",
                    manifest=ag.files_manifest,
                    files_dir=ag.raw_files_dir,
                    limit=chunk_pdf,
                    retry=retry_failed and chunk_no == 1,
                    hosts=list(ag.hosts),
                    doc_types=None,
                    since_year=since_year,
                    until_year=until_year,
                    excluded_urls=set(ag.excluded_urls),
                    dry_run=dry_run,
                    interval=interval,
                )
                if pdf_res.checked > 0:
                    worked = True
                total_pdf += len(pdf_res.saved)
                if fail_log and pdf_res.failed:
                    fail_log.parent.mkdir(parents=True, exist_ok=True)
                    with fail_log.open("a", encoding="utf-8", newline="\n") as f:
                        for u in pdf_res.failed:
                            f.write(f"{ag.agency_id}\tpdf\t{u}\tfailed\n")

                if not dry_run:
                    n_ext = update._extract_new_texts(
                        ag.files_manifest, ag.raw_files_dir, ag.data_dir / "text",
                        ag.data_dir / "texts.csv", ag.data_dir / "documents.csv", repo,
                    )
                    if n_ext:
                        total_extracted += n_ext
                        worked = True
                        _rebuild_agency_tables(ag)
                        rebuilt = True

            # 3. GitHub Releases へシャード圧縮退避 & ローカル削除（mod 以外のテキストも退避）
            if archive and not dry_run:
                archived_pdfs = archive_pdfs.upload_baseline_bundle(
                    manifest=ag.files_manifest,
                    files_dir=ag.raw_files_dir,
                    tag=pdf_tag,
                    agency_name=ag.name,
                    max_shard_bytes=max_shard_bytes,
                    chunk_id=stamp,
                    cleanup_local=cleanup_local,
                )
                archived_html = archive_pdfs.upload_subpages_bundle(
                    manifest=ag.subpages_manifest,
                    subpages_dir=ag.raw_subpages_dir,
                    tag=html_tag,
                    agency_name=ag.name,
                    max_shard_bytes=max_shard_bytes,
                    chunk_id=stamp,
                    cleanup_local=cleanup_local,
                )
                archived_texts: list[tuple[str, str]] = []
                if ag.agency_id != "mod" and (ag.data_dir / "texts.csv").exists():
                    # ローカルテキストを退避削除する前に必ず parsed_texts.json を最新化する
                    if not rebuilt and any(
                        r.get("text") and not r.get("release_url") and (repo / r["text"]).is_file()
                        for r in build_index._read_csv(ag.data_dir / "texts.csv")
                    ):
                        _rebuild_agency_tables(ag)
                        rebuilt = True
                    archived_texts = archive_pdfs.upload_texts_bundle(
                        texts_csv=ag.data_dir / "texts.csv",
                        text_dir=ag.data_dir / "text",
                        repo=repo,
                        tag=text_tag,
                        agency_name=ag.name,
                        max_shard_bytes=max_shard_bytes,
                        chunk_id=stamp,
                        cleanup_local=cleanup_local,
                    )
                if archived_pdfs or archived_html or archived_texts:
                    worked = True
                    print(
                        f"[{ag.agency_id}] Release 退避: PDF {len(archived_pdfs)} 件 ({pdf_tag}) / "
                        f"下位HTML {len(archived_html)} 件 ({html_tag}) / "
                        f"テキスト {len(archived_texts)} 件 ({text_tag})",
                        flush=True,
                    )

            if not dry_run and worked:
                build_index.build_agency_catalog(ag, write=True)

            if dry_run or not worked:
                break

        summary = f"HTML={total_html}, PDF={total_pdf}, extracted={total_extracted}, chunks={chunk_no - 1}"
        print(f"[{ag.agency_id}] バックフィル終了: {summary}", flush=True)
        return agency_id, True, summary
    except (fetch_subpages.Blocked, fetch_files.Blocked) as e:
        msg = f"遮断により中断: {e}"
        print(f"[{ag.agency_id}] {msg}", file=sys.stderr, flush=True)
        return agency_id, False, msg
    except Exception as e:
        msg = f"エラー: {type(e).__name__}: {e}"
        print(f"[{ag.agency_id}] {msg}", file=sys.stderr, flush=True)
        return agency_id, False, msg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agency", default="", help="対象省庁 ID（カンマ区切り可。--wave と排他）")
    ap.add_argument("--wave", choices=tuple(WAVES.keys()), default="", help="プリセットウェーブ（wave1 / wave2 / wave3 / all）")
    ap.add_argument("--status-only", action="store_true", help="取得済み・残り件数の一覧だけ表示して終了")
    ap.add_argument("--chunk-html", type=int, default=100, help="1 チャンクあたりの下位 HTML 上限（0 でスキップ）")
    ap.add_argument("--chunk-pdf", type=int, default=100, help="1 チャンクあたりの PDF 上限（0 でスキップ）")
    ap.add_argument("--max-chunks", type=int, default=0, help="省庁あたりの最大チャンク数（0 で残り 0 まで実行）")
    ap.add_argument("--since-year", type=int, default=0, help="対象会議年の下限（mhlw/env の年代分割用）")
    ap.add_argument("--until-year", type=int, default=0, help="対象会議年の上限（mhlw/env の年代分割用）")
    ap.add_argument("--include-discovered", action="store_true",
                    help="documents.csv に加え discover_html_links 経由の未取得 HTML も探索する")
    ap.add_argument("--parallel", type=int, default=1, help="並列実行する省庁数（1 省庁 = 1 ドメイン直列。推奨 1〜6）")
    ap.add_argument("--archive", action="store_true", help="各チャンクの取得原本を GitHub Releases へ .tar.gz 退避する")
    ap.add_argument("--cleanup-local", action="store_true", help="Release 退避後にローカル原本（raw/*/files, subpages）を削除する")
    ap.add_argument("--max-shard-mb", type=int, default=800, help="1 Release tar.gz シャードの最大サイズ（MB）")
    ap.add_argument("--interval", type=float, default=2.0, help="同一ドメインのリクエスト間隔（秒）")
    ap.add_argument("--retry-failed", action="store_true", help="初回チャンクで前回失敗 URL も再試行する")
    ap.add_argument("--fail-log", type=Path, default=REPO / "checks" / "backfill_failures.tsv",
                    help="失敗 URL の全件出力先（先頭打ち切りなし）")
    ap.add_argument("--skip-cross", action="store_true", help="末尾の省庁横断集計（cross_agency.py）を省略する")
    ap.add_argument("--dry-run", action="store_true", help="対象件数の確認のみ行い取得しない")
    args = ap.parse_args()

    if args.status_only:
        print_status(since_year=args.since_year, until_year=args.until_year)
        return 0

    if args.agency:
        target_ags = [a.strip() for a in args.agency.split(",") if a.strip()]
    elif args.wave:
        target_ags = list(WAVES[args.wave])
    else:
        ap.error("--agency / --wave / --status-only のいずれかを指定してください")

    workers = max(1, min(args.parallel, len(target_ags)))
    results: list[tuple[str, bool, str]] = []
    if workers == 1:
        for aid in target_ags:
            results.append(run_agency_backfill(
                aid,
                chunk_html=args.chunk_html,
                chunk_pdf=args.chunk_pdf,
                max_chunks=args.max_chunks,
                since_year=args.since_year,
                until_year=args.until_year,
                include_discovered=args.include_discovered,
                archive=args.archive,
                cleanup_local=args.cleanup_local,
                max_shard_mb=args.max_shard_mb,
                interval=args.interval,
                retry_failed=args.retry_failed,
                fail_log=args.fail_log,
                dry_run=args.dry_run,
            ))
    else:
        # 1 省庁 1 サブプロセスとして実行し、標準出力と例外を安全に分離する
        def _spawn(aid: str) -> tuple[str, bool, str]:
            cmd = [
                sys.executable, str(Path(__file__).resolve()),
                "--agency", aid,
                "--chunk-html", str(args.chunk_html),
                "--chunk-pdf", str(args.chunk_pdf),
                "--max-chunks", str(args.max_chunks),
                "--max-shard-mb", str(args.max_shard_mb),
                "--interval", str(args.interval),
                "--fail-log", str(args.fail_log),
                "--skip-cross",
            ]
            if args.since_year:
                cmd += ["--since-year", str(args.since_year)]
            if args.until_year:
                cmd += ["--until-year", str(args.until_year)]
            if args.include_discovered:
                cmd.append("--include-discovered")
            if args.archive:
                cmd.append("--archive")
            if args.cleanup_local:
                cmd.append("--cleanup-local")
            if args.retry_failed:
                cmd.append("--retry-failed")
            if args.dry_run:
                cmd.append("--dry-run")
            proc = subprocess.run(cmd)
            return aid, proc.returncode == 0, f"exit={proc.returncode}"

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_spawn, target_ags))

    if not args.dry_run and not args.skip_cross:
        cross_agency.build_cross_tables(write=True)

    failed = [r for r in results if not r[1]]
    print("\n=== Backfill Summary ===")
    for aid, ok, msg in results:
        print(f"  [{'OK' if ok else 'FAIL'}] {aid:7s}: {msg}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
