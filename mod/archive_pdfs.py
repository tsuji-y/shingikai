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
                       dry_run: bool = False, runner=subprocess.run) -> list[tuple[str, str]]:
    """未アーカイブのローカル PDF を年別 Release にアップロードし、台帳の release_url を更新する。"""
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
            uploaded.append((url, dl_url))

    if uploaded and not dry_run:
        fetch_files.write_manifest(rows, manifest)
    return uploaded


def upload_baseline_bundle(*, manifest: Path = fetch_files.MANIFEST, files_dir: Path = fetch_files.FILES_DIR,
                           tag: str = "pdfs-2026-baseline", agency_name: str = "防衛省",
                           bundle_name: str = "", repo_slug: str = "", dry_run: bool = False,
                           runner=subprocess.run) -> list[tuple[str, str]]:
    """手元の取得済み PDF 原本を 1 本の tar.gz にまとめて Release へアップロードする。"""
    rows = fetch_files.read_manifest(manifest)
    todo = [r for r in rows.values() if needs_upload(r, files_dir)]
    todo.sort(key=lambda r: r["url"])
    if not todo:
        return []

    repo_slug = repo_slug or detect_repo_slug()
    bundle_name = bundle_name or f"shingikai-{tag}.tar.gz"
    dl_base = f"https://github.com/{repo_slug}/releases/download/{tag}/{bundle_name}"
    archived: list[tuple[str, str]] = []

    if dry_run:
        for row in todo:
            rel = fetch_files.local_path(row["url"], files_dir).relative_to(files_dir).as_posix()
            archived.append((row["url"], f"{dl_base}#{rel}"))
        return archived

    ensure_release(
        tag,
        repo_slug,
        title=f"{agency_name} 審議会等 PDF Baseline ({tag})",
        notes=f"{agency_name}審議会等の既存 PDF 原本一括アーカイブ（{len(todo)} 件。出典：{agency_name}ウェブサイト、PDL1.0）。",
        runner=runner,
    )
    with tempfile.TemporaryDirectory() as td:
        tar_path = Path(td) / bundle_name
        with tarfile.open(tar_path, "w:gz") as tf:
            for row in todo:
                src = fetch_files.local_path(row["url"], files_dir)
                rel = src.relative_to(files_dir).as_posix()
                tf.add(src, arcname=rel)
                archived.append((row["url"], f"{dl_base}#{rel}"))
        runner(
            ["gh", "release", "upload", tag, str(tar_path), "--repo", repo_slug, "--clobber"],
            check=True,
        )

    for url, rel_url in archived:
        rows[url]["release_url"] = rel_url
    fetch_files.write_manifest(rows, manifest)
    return archived


def main() -> int:
    import agencies

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agency", default="mod", help="対象の省庁 ID（既定: mod）")
    ap.add_argument("--tag", default="", help="アップロード先の Release タグ（既定: <agency>-pdfs-YYYY）")
    ap.add_argument("--repo", default="", help="GitHub リポジトリ（owner/repo。省略時は origin から自動判定）")
    ap.add_argument("--bundle", action="store_true", help="手元の対象 PDF を tar.gz にまとめて 1 本でアップロードする")
    ap.add_argument("--dry-run", action="store_true", help="アップロードと台帳更新を行わず対象だけ表示する")
    args = ap.parse_args()

    ag = agencies.get_agency(args.agency)
    year = datetime.now(timezone.utc).year
    if args.bundle:
        tag = args.tag or f"{ag.release_tag_prefix}-{year}-baseline"
        res = upload_baseline_bundle(
            manifest=ag.files_manifest, files_dir=ag.raw_files_dir,
            tag=tag, agency_name=ag.name, repo_slug=args.repo, dry_run=args.dry_run,
        )
    else:
        tag = args.tag or f"{ag.release_tag_prefix}-{year}"
        res = upload_incremental(
            manifest=ag.files_manifest, files_dir=ag.raw_files_dir,
            tag=tag, agency_name=ag.name, repo_slug=args.repo, dry_run=args.dry_run,
        )

    tag_str = "（dry-run）" if args.dry_run else ""
    print(f"[{ag.agency_id}] PDF アーカイブ対象: {len(res)} 件{tag_str}")
    for url, rel_url in res[:20]:
        print(f"  {url} -> {rel_url}")
    if len(res) > 20:
        print(f"  ... ほか {len(res) - 20} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
