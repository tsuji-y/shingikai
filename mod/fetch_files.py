#!/usr/bin/env python3
"""documents.csv に載っている www.mod.go.jp 上の PDF を取得し、raw/mod/files/ に保存する。

PDF は HTML と違ってスクリプトから取得できる（README 参照）。取得するのは documents.csv に
URL が載っているものだけで、URL を推測して探すことはしない。1 件ずつ間隔をあけて取得し、
403・429 が返ったらそこで止める。取得済みのものは飛ばすので、途中で止めても続きから再開できる。

本体（raw/mod/files/）は Git に入れず、台帳 raw/mod/files.csv だけを残す。

使い方:
    python3 mod/fetch_files.py              # 未取得のものをすべて取得
    python3 mod/fetch_files.py --limit 5    # 5 件だけ
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parent.parent
DOCUMENTS = REPO / "data" / "mod" / "documents.csv"
FILES_DIR = REPO / "raw" / "mod" / "files"
MANIFEST = REPO / "raw" / "mod" / "files.csv"
FIELDS = ["url", "status", "content_type", "last_modified", "fetched_at", "bytes", "sha256", "note", "release_url"]

HOST = "www.mod.go.jp"
USER_AGENT = "pdf-fetcher/1.0"
INTERVAL = 2.0  # 秒
MAX_BYTES = 50 * 1024 * 1024
RETRY_BACKOFF = (2.0, 4.0, 8.0)


class Blocked(RuntimeError):
    """403・429。続けて取りに行かない。"""


@dataclass
class FetchSummary:
    checked: int = 0
    saved: list[str] = field(default_factory=list)
    replaced: list[tuple[str, str, str]] = field(default_factory=list)  # (url, old_sha, new_sha)
    failed: list[str] = field(default_factory=list)


def iso_to_http_date(value: str) -> str:
    """ISO 8601 日時を HTTP の If-Modified-Since 形式（GMT）に変換する。"""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt.astimezone(timezone.utc), usegmt=True)


def targets(documents: Path = DOCUMENTS, council: str = "") -> list[str]:
    """取得対象の URL（重複なし、documents.csv の順）。"""
    seen: dict[str, None] = {}
    with documents.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if council and row.get("council_id") != council:
                continue
            if row["host"] == HOST and row["format"] == "pdf":
                seen.setdefault(row["url"])
    return list(seen)


def select_targets(urls: list[str], rows: dict[str, dict[str, str]], *,
                   retry: bool = False, refetch_missing: bool = False,
                   files_dir: Path = FILES_DIR) -> list[str]:
    """取得する URL を選ぶ。既定では台帳に SHA-256 があれば取得済みとして飛ばす。"""
    todo = []
    for url in urls:
        done = rows.get(url)
        if done and done.get("sha256"):
            if refetch_missing and not local_path(url, files_dir).exists():
                todo.append(url)
            continue
        if done and not done.get("sha256") and not retry:
            continue
        todo.append(url)
    return todo


def local_path(url: str, root: Path = FILES_DIR) -> Path:
    parts = [unquote(p) for p in urlsplit(url).path.strip("/").split("/")]
    if urlsplit(url).netloc != HOST or not parts or any(p in ("", ".", "..") or "\\" in p for p in parts):
        raise ValueError(f"保存できない URL: {url!r}")
    return root.joinpath(*parts)


def read_manifest(path: Path = MANIFEST) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows[row["url"]] = {k: row.get(k, "") for k in FIELDS}
    return rows


def write_manifest(rows: dict[str, dict[str, str]], path: Path = MANIFEST) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for k in sorted(rows):
            writer.writerow({field: rows[k].get(field, "") for field in FIELDS})
    for attempt in range(10):
        try:
            tmp.replace(path)
            return
        except PermissionError:  # Windows では、ほかのプロセスが台帳を読んでいる間は置き換えられない
            if attempt == 9:
                raise
            time.sleep(0.5)


def fetch(url: str, max_bytes: int = MAX_BYTES, *, if_modified_since: str = "",
          retry_backoff: tuple[float, ...] = RETRY_BACKOFF,
          opener=urllib.request.urlopen) -> tuple[dict[str, str], bytes | None]:
    """1 件取得する。戻り値は（台帳の行, 本体）。保存しない場合や 304 の本体は None。"""
    headers = {"User-Agent": USER_AGENT}
    ims = iso_to_http_date(if_modified_since) if if_modified_since else ""
    if ims:
        headers["If-Modified-Since"] = ims

    for attempt in range(len(retry_backoff) + 1):
        row = {"url": url, "status": "", "content_type": "", "last_modified": "",
               "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "bytes": "", "sha256": "", "note": ""}
        req = urllib.request.Request(url, headers=headers)
        try:
            with opener(req, timeout=120) as resp:
                if resp.headers.get("cf-mitigated"):
                    raise Blocked(f"{url}: cf-mitigated={resp.headers.get('cf-mitigated')}")
                row["status"] = str(resp.status)
                row["content_type"] = resp.headers.get("Content-Type", "")
                modified = resp.headers.get("Last-Modified")
                if modified:
                    try:
                        row["last_modified"] = parsedate_to_datetime(modified).isoformat()
                    except (TypeError, ValueError):
                        pass
                length = resp.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    row["bytes"], row["note"] = length, "上限を超えるため未保存"
                    return row, None
                body = resp.read(max_bytes + 1)
        except urllib.error.HTTPError as e:
            row["status"] = str(e.code)
            if e.code == 304:
                return row, None
            if e.code in (403, 429) or (e.headers and e.headers.get("cf-mitigated")):
                raise Blocked(f"{url}: HTTP {e.code}") from e
            if 500 <= e.code < 600 and attempt < len(retry_backoff):
                time.sleep(retry_backoff[attempt])
                continue
            row["note"] = e.reason or ""
            return row, None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < len(retry_backoff):
                time.sleep(retry_backoff[attempt])
                continue
            row["status"], row["note"] = "error", str(getattr(e, "reason", e))
            return row, None
        row["bytes"] = str(len(body))
        if len(body) > max_bytes:
            row["note"] = "上限を超えるため未保存"
            return row, None
        if not body.startswith(b"%PDF"):
            row["note"] = "PDF ではない応答"
            return row, None
        row["sha256"] = hashlib.sha256(body).hexdigest()
        return row, body
    return row, None  # pragma: no cover


def fetch_new(documents: Path = DOCUMENTS, manifest: Path = MANIFEST, files_dir: Path = FILES_DIR, *,
              limit: int = 0, retry: bool = False, refetch_missing: bool = False,
              council: str = "", dry_run: bool = False, interval: float = INTERVAL,
              fetcher=fetch) -> FetchSummary:
    """未取得の PDF を取得する。403・429 なら Blocked を送出する。"""
    rows = read_manifest(manifest)
    all_urls = targets(documents, council=council)
    todo = select_targets(all_urls, rows, retry=retry, refetch_missing=refetch_missing, files_dir=files_dir)
    if limit:
        todo = todo[:limit]
    summary = FetchSummary()
    print(f"対象 {len(all_urls)} 件 / 今回取得 {len(todo)} 件"
          + ("（dry-run）" if dry_run else ""), flush=True)
    if dry_run:
        for url in todo:
            print(f"  [dry-run] {url}", flush=True)
        return summary

    for i, url in enumerate(todo, 1):
        row, body = fetcher(url)
        summary.checked += 1
        if body is not None:
            path = local_path(url, files_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            summary.saved.append(url)
        else:
            summary.failed.append(url)
        rows[url] = row
        write_manifest(rows, manifest)
        if body is None or i % 25 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} {row['status']} {row['bytes']} {row['note']} {url}", flush=True)
        if i < len(todo) and interval > 0:
            time.sleep(interval)
    return summary


def check_replaced(documents: Path = DOCUMENTS, manifest: Path = MANIFEST, files_dir: Path = FILES_DIR, *,
                   limit: int = 0, council: str = "", dry_run: bool = False,
                   interval: float = INTERVAL, fetcher=fetch) -> FetchSummary:
    """取得済み PDF を条件付き取得で照合し、差し替えを検知する。"""
    rows = read_manifest(manifest)
    all_urls = targets(documents, council=council)
    to_check = [u for u in all_urls if rows.get(u, {}).get("sha256")]
    if limit:
        to_check = to_check[:limit]
    summary = FetchSummary()
    dirty = False
    print(f"PDF 差し替え照合: 対象 {len(to_check)} 件"
          + ("（dry-run）" if dry_run else ""), flush=True)

    for i, url in enumerate(to_check, 1):
        old = rows[url]
        row, body = fetcher(url, if_modified_since=old.get("last_modified", ""))
        summary.checked += 1
        if row["status"] == "304":
            pass
        elif body is not None:
            if row["sha256"] != old["sha256"]:
                summary.replaced.append((url, old["sha256"], row["sha256"]))
                if not dry_run:
                    path = local_path(url, files_dir)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(body)
                    rows[url] = row
                    dirty = True
                print(f"  差し替え検知: {url}", flush=True)
            elif row["last_modified"] and row["last_modified"] != old.get("last_modified", ""):
                if not dry_run:
                    old["last_modified"] = row["last_modified"]
                    dirty = True
        elif row["status"] not in ("200", "304"):
            summary.failed.append(url)
        if i < len(to_check) and interval > 0:
            time.sleep(interval)
    if dirty and not dry_run:
        write_manifest(rows, manifest)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=0, help="取得する件数の上限（0 は無制限）")
    ap.add_argument("--retry", action="store_true", help="前回 200 以外だったものも取り直す")
    ap.add_argument("--refetch-missing", action="store_true", help="台帳にあるが手元にない PDF を取り直す")
    ap.add_argument("--check-replaced", action="store_true", help="取得済み PDF を条件付き取得で照合し、差し替えを検知する")
    ap.add_argument("--council", default="", help="対象の会議体 ID を絞る")
    ap.add_argument("--dry-run", action="store_true", help="書き込みを行わない")
    args = ap.parse_args()

    try:
        if args.check_replaced:
            res = check_replaced(limit=args.limit, council=args.council, dry_run=args.dry_run)
            print(f"照合 {res.checked} 件 / 差し替え {len(res.replaced)} 件 / エラー {len(res.failed)} 件", flush=True)
        else:
            res = fetch_new(limit=args.limit, retry=args.retry, refetch_missing=args.refetch_missing,
                            council=args.council, dry_run=args.dry_run)
            print(f"保存 {len(res.saved)} 件 / 保存できなかったもの {len(res.failed)} 件", flush=True)
    except Blocked as e:
        print(f"中断: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
