#!/usr/bin/env python3
"""取得済み PDF 原本（raw/mod/files/）を GitHub Releases に保管し、台帳 raw/mod/files.csv の release_url を更新する。

2 つのモードを持つ:
1. 増分アップロード（既定）:
   手元にある未アーカイブ（または差し替え後）の PDF を年別 Release（pdfs-YYYY）に個別添付する。
   アセット名は会議体パス＋SHA-256 先頭 8 桁で一意にし、会議体間のファイル名衝突や差し替え時の上書きを防ぐ。
2. ベースライン一括アーカイブ（--bundle）:
   既存の 421 件などの原本を 1 本の .tar.gz にまとめ、pdfs-2026-baseline 等の Release にアップロードする。

使い方:
    python3 mod/archive_pdfs.py --dry-run                       # 対象確認
    python3 mod/archive_pdfs.py                                 # 年別 Release（pdfs-YYYY）へ増分アップロード
    python3 mod/archive_pdfs.py --bundle --tag pdfs-2026-baseline  # 手元の全原本を tar.gz で一括アーカイブ
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_files  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MEETING_PREFIX = "/j/policy/agenda/meeting/"
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def detect_repo_slug(cwd: Path = REPO) -> str:
    """git remote origin から owner/repo を取得する。"""
    out = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=cwd, text=True).strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", out)
    if not m:
        raise RuntimeError(f"GitHub リポジトリ名を判定できません: {out!r}")
    return m.group(1)


def asset_name(url: str, sha256: str) -> str:
    """衝突しない GitHub Release アセット名を作る（例: rinri__pdf__giji_105__a1b2c3d4.pdf）。"""
    path = unquote(urlsplit(url).path)
    if path.startswith(MEETING_PREFIX):
        rel = path[len(MEETING_PREFIX):]
    else:
        rel = path.lstrip("/")
    stem = rel[:-4] if rel.lower().endswith(".pdf") else rel
    parts = [_SAFE_RE.sub("_", p).strip("_") for p in stem.split("/") if p]
    slug = "__".join(p for p in parts if p)
    if not slug:
        slug = "file"
    if len(slug) > 140:
        slug = slug[:140]
    short_hash = (sha256 or hashlib.sha256(url.encode()).hexdigest())[:8]
    return f"{slug}__{short_hash}.pdf"


def needs_upload(row: dict[str, str], files_dir: Path = fetch_files.FILES_DIR) -> bool:
    """ローカルに PDF 原本があり、現在の sha256 が未アーカイブか。"""
    sha = row.get("sha256", "")
    if not sha:
        return False
    url = row.get("url", "")
    try:
        path = fetch_files.local_path(url, files_dir)
    except ValueError:
        return False
    if not path.exists():
        return False
    rel_url = row.get("release_url", "")
    if not rel_url:
        return True
    # 差し替え後（sha256 先頭 8 桁が個別アセット名に含まれない、かつベースライン束でもない場合）
    if "#" not in rel_url and f"__{sha[:8]}.pdf" not in rel_url:
        return True
    return False


def ensure_release(tag: str, repo_slug: str, title: str, notes: str, *, runner=subprocess.run) -> None:
    check = runner(
        ["gh", "release", "view", tag, "--repo", repo_slug],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check.returncode == 0:
        return
    runner(
        ["gh", "release", "create", tag, "--repo", repo_slug, "--title", title, "--notes", notes],
        check=True,
    )


def upload_incremental(*, manifest: Path = fetch_files.MANIFEST, files_dir: Path = fetch_files.FILES_DIR,
                       tag: str = "", agency_name: str = "防衛省", repo_slug: str = "",
                       dry_run: bool = False, cleanup_local: bool = False,
                       runner=subprocess.run) -> list[tuple[str, str]]:
    """未アーカイブのローカル PDF を年別または月別 Release にアップロードし、台帳の release_url を更新する。"""
    rows = fetch_files.read_manifest(manifest)
    todo = [r for r in rows.values() if needs_upload(r, files_dir)]
    todo.sort(key=lambda r: r["url"])
    if not todo:
        return []

    year = datetime.now(timezone.utc).year
    tag = tag or f"pdfs-{year}"
    repo_slug = repo_slug or detect_repo_slug()
    uploaded: list[tuple[str, str]] = []

    if not dry_run:
        ensure_release(
            tag,
            repo_slug,
            title=f"{agency_name} 審議会等 PDF ({tag})",
            notes=f"{agency_name}審議会等の取得済み PDF 原本アーカイブ（出典：{agency_name}ウェブサイト、PDL1.0）。",
            runner=runner,
        )

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        for row in todo:
            url = row["url"]
            src = fetch_files.local_path(url, files_dir)
            name = asset_name(url, row["sha256"])
            dl_url = f"https://github.com/{repo_slug}/releases/download/{tag}/{name}"
            if not dry_run:
                staged = tmp_dir / name
                shutil.copy2(src, staged)
                runner(
                    ["gh", "release", "upload", tag, str(staged), "--repo", repo_slug, "--clobber"],
                    check=True,
                )
                staged.unlink(missing_ok=True)
                row["release_url"] = dl_url
                if cleanup_local:
                    src.unlink(missing_ok=True)
            uploaded.append((url, dl_url))

    if uploaded and not dry_run:
        fetch_files.write_manifest(rows, manifest)
    return uploaded


def _partition_shards(items: list[tuple[dict[str, str], Path]], max_shard_bytes: int) -> list[list[tuple[dict[str, str], Path]]]:
    """ファイル列を max_shard_bytes 以下のシャードに分割する（0 以下なら単一束）。"""
    if max_shard_bytes <= 0:
        return [items] if items else []
    shards: list[list[tuple[dict[str, str], Path]]] = []
    cur: list[tuple[dict[str, str], Path]] = []
    cur_bytes = 0
    for row, src in items:
        sz = src.stat().st_size if src.exists() else 0
        if cur and cur_bytes + sz > max_shard_bytes:
            shards.append(cur)
            cur = []
            cur_bytes = 0
        cur.append((row, src))
        cur_bytes += sz
    if cur:
        shards.append(cur)
    return shards


def upload_baseline_bundle(*, manifest: Path = fetch_files.MANIFEST, files_dir: Path = fetch_files.FILES_DIR,
                           tag: str = "pdfs-2026-baseline", agency_name: str = "防衛省",
                           bundle_name: str = "", repo_slug: str = "", dry_run: bool = False,
                           max_shard_bytes: int = 800 * 1024 * 1024, chunk_id: str = "",
                           cleanup_local: bool = False,
                           runner=subprocess.run) -> list[tuple[str, str]]:
    """手元の取得済み PDF 原本を tar.gz（上限超過時はシャード分割）にまとめて Release へアップロードする。"""
    rows = fetch_files.read_manifest(manifest)
    todo = [r for r in rows.values() if needs_upload(r, files_dir)]
    todo.sort(key=lambda r: r["url"])
    if not todo:
        return []

    repo_slug = repo_slug or detect_repo_slug()
    pairs = [(row, fetch_files.local_path(row["url"], files_dir)) for row in todo]
    shards = _partition_shards(pairs, max_shard_bytes)
    base_stem = bundle_name[:-7] if bundle_name.endswith(".tar.gz") else (bundle_name or f"shingikai-{tag}")
    if chunk_id:
        base_stem = f"{base_stem}-{chunk_id}"
    archived: list[tuple[str, str]] = []

    if dry_run:
        for idx, shard in enumerate(shards, 1):
            sname = f"{base_stem}.tar.gz" if len(shards) == 1 else f"{base_stem}-p{idx:03d}.tar.gz"
            dl_base = f"https://github.com/{repo_slug}/releases/download/{tag}/{sname}"
            for row, src in shard:
                rel = src.relative_to(files_dir).as_posix()
                archived.append((row["url"], f"{dl_base}#{rel}"))
        return archived

    ensure_release(
        tag,
        repo_slug,
        title=f"{agency_name} 審議会等 PDF Baseline ({tag})",
        notes=f"{agency_name}審議会等の PDF 原本一括アーカイブ（出典：{agency_name}ウェブサイト、PDL1.0）。",
        runner=runner,
    )
    for idx, shard in enumerate(shards, 1):
        sname = f"{base_stem}.tar.gz" if len(shards) == 1 else f"{base_stem}-p{idx:03d}.tar.gz"
        dl_base = f"https://github.com/{repo_slug}/releases/download/{tag}/{sname}"
        shard_archived: list[tuple[str, str, Path]] = []
        with tempfile.TemporaryDirectory() as td:
            tar_path = Path(td) / sname
            with tarfile.open(tar_path, "w:gz") as tf:
                for row, src in shard:
                    rel = src.relative_to(files_dir).as_posix()
                    tf.add(src, arcname=rel)
                    shard_archived.append((row["url"], f"{dl_base}#{rel}", src))
            runner(
                ["gh", "release", "upload", tag, str(tar_path), "--repo", repo_slug, "--clobber"],
                check=True,
            )
        for url, rel_url, src in shard_archived:
            rows[url]["release_url"] = rel_url
            archived.append((url, rel_url))
            if cleanup_local:
                src.unlink(missing_ok=True)
        # シャードごとに台帳をチェックポイント保存
        fetch_files.write_manifest(rows, manifest)
    return archived


def upload_subpages_bundle(*, manifest: Path, subpages_dir: Path,
                           tag: str, agency_name: str = "防衛省",
                           repo_slug: str = "", dry_run: bool = False,
                           max_shard_bytes: int = 800 * 1024 * 1024, chunk_id: str = "",
                           cleanup_local: bool = False,
                           runner=subprocess.run) -> list[tuple[str, str]]:
    """取得済み下位 HTML 原本（raw/<ag>/subpages/）を tar.gz で GitHub Releases へ保管する。"""
    import fetch_subpages

    rows = fetch_subpages.read_manifest(manifest)
    pairs: list[tuple[dict[str, str], Path]] = []
    for url in sorted(rows):
        row = rows[url]
        if not row.get("sha256") or row.get("release_url"):
            continue
        try:
            src = fetch_subpages.local_path(url, subpages_dir)
        except ValueError:
            continue
        if src.exists():
            pairs.append((row, src))
    if not pairs:
        return []

    repo_slug = repo_slug or detect_repo_slug()
    shards = _partition_shards(pairs, max_shard_bytes)
    base_stem = f"shingikai-{tag}"
    if chunk_id:
        base_stem = f"{base_stem}-{chunk_id}"
    archived: list[tuple[str, str]] = []

    if dry_run:
        for idx, shard in enumerate(shards, 1):
            sname = f"{base_stem}.tar.gz" if len(shards) == 1 else f"{base_stem}-p{idx:03d}.tar.gz"
            dl_base = f"https://github.com/{repo_slug}/releases/download/{tag}/{sname}"
            for row, src in shard:
                rel = src.relative_to(subpages_dir).as_posix()
                archived.append((row["url"], f"{dl_base}#{rel}"))
        return archived

    ensure_release(
        tag,
        repo_slug,
        title=f"{agency_name} 審議会等 下位HTMLアーカイブ ({tag})",
        notes=f"{agency_name}審議会等の下位 HTML 原本アーカイブ（出典：{agency_name}ウェブサイト、PDL1.0）。",
        runner=runner,
    )
    for idx, shard in enumerate(shards, 1):
        sname = f"{base_stem}.tar.gz" if len(shards) == 1 else f"{base_stem}-p{idx:03d}.tar.gz"
        dl_base = f"https://github.com/{repo_slug}/releases/download/{tag}/{sname}"
        shard_archived: list[tuple[str, str, Path]] = []
        with tempfile.TemporaryDirectory() as td:
            tar_path = Path(td) / sname
            with tarfile.open(tar_path, "w:gz") as tf:
                for row, src in shard:
                    rel = src.relative_to(subpages_dir).as_posix()
                    tf.add(src, arcname=rel)
                    shard_archived.append((row["url"], f"{dl_base}#{rel}", src))
            runner(
                ["gh", "release", "upload", tag, str(tar_path), "--repo", repo_slug, "--clobber"],
                check=True,
            )
        for url, rel_url, src in shard_archived:
            rows[url]["release_url"] = rel_url
            archived.append((url, rel_url))
            if cleanup_local:
                src.unlink(missing_ok=True)
        fetch_subpages.write_manifest(rows, manifest)
    return archived


def main() -> int:
    import agencies

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agency", default="mod", help="対象の省庁 ID（既定: mod）")
    ap.add_argument("--tag", default="", help="アップロード先の Release タグ（既定: <agency>-pdfs-YYYY）")
    ap.add_argument("--monthly", action="store_true", help="タグを月別（<prefix>-YYYY-MM）にして 1000 アセット上限を回避する")
    ap.add_argument("--repo", default="", help="GitHub リポジトリ（owner/repo。省略時は origin から自動判定）")
    ap.add_argument("--bundle", action="store_true", help="手元の対象 PDF を tar.gz（800MB シャード分割）でアップロードする")
    ap.add_argument("--subpages", action="store_true", help="下位 HTML 原本（raw/<ag>/subpages/）を tar.gz でアップロードする")
    ap.add_argument("--chunk-id", default="", help="バンドルファイル名に付与するチャンク識別子（既存アセットの上書き防止）")
    ap.add_argument("--max-shard-mb", type=int, default=800, help="1 シャードの最大サイズ（MB。既定: 800）")
    ap.add_argument("--cleanup-local", action="store_true", help="アップロード成功後にローカルの原本ファイルを削除する")
    ap.add_argument("--dry-run", action="store_true", help="アップロードと台帳更新を行わず対象だけ表示する")
    args = ap.parse_args()

    ag = agencies.get_agency(args.agency)
    now = datetime.now(timezone.utc)
    period = now.strftime("%Y-%m") if args.monthly else str(now.year)
    max_shard_bytes = args.max_shard_mb * 1024 * 1024

    if args.subpages:
        tag = args.tag or f"{ag.html_release_tag_prefix}-{period}"
        res = upload_subpages_bundle(
            manifest=ag.subpages_manifest, subpages_dir=ag.raw_subpages_dir,
            tag=tag, agency_name=ag.name, repo_slug=args.repo, dry_run=args.dry_run,
            max_shard_bytes=max_shard_bytes, chunk_id=args.chunk_id,
            cleanup_local=args.cleanup_local,
        )
        kind = "下位HTML"
    elif args.bundle:
        tag = args.tag or (f"{ag.release_tag_prefix}-{period}" if args.monthly else f"{ag.release_tag_prefix}-{period}-baseline")
        res = upload_baseline_bundle(
            manifest=ag.files_manifest, files_dir=ag.raw_files_dir,
            tag=tag, agency_name=ag.name, repo_slug=args.repo, dry_run=args.dry_run,
            max_shard_bytes=max_shard_bytes, chunk_id=args.chunk_id,
            cleanup_local=args.cleanup_local,
        )
        kind = "PDF"
    else:
        tag = args.tag or f"{ag.release_tag_prefix}-{period}"
        res = upload_incremental(
            manifest=ag.files_manifest, files_dir=ag.raw_files_dir,
            tag=tag, agency_name=ag.name, repo_slug=args.repo, dry_run=args.dry_run,
            cleanup_local=args.cleanup_local,
        )
        kind = "PDF"

    tag_str = "（dry-run）" if args.dry_run else ""
    print(f"[{ag.agency_id}] {kind} アーカイブ対象: {len(res)} 件{tag_str}")
    for url, rel_url in res[:20]:
        print(f"  {url} -> {rel_url}")
    if len(res) > 20:
        print(f"  ... ほか {len(res) - 20} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
