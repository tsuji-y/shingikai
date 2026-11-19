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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parent.parent
DOCUMENTS = REPO / "data" / "mod" / "documents.csv"
FILES_DIR = REPO / "raw" / "mod" / "files"
MANIFEST = REPO / "raw" / "mod" / "files.csv"
FIELDS = ["url", "status", "content_type", "last_modified", "fetched_at", "bytes", "sha256", "note"]

HOST = "www.mod.go.jp"
USER_AGENT = "pdf-fetcher/1.0"
INTERVAL = 2.0  # 秒
MAX_BYTES = 50 * 1024 * 1024


class Blocked(RuntimeError):
    """403・429。続けて取りに行かない。"""


def targets(documents: Path = DOCUMENTS) -> list[str]:
    """取得対象の URL（重複なし、documents.csv の順）。"""
    seen: dict[str, None] = {}
    with documents.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["host"] == HOST and row["format"] == "pdf":
                seen.setdefault(row["url"])
    return list(seen)


def local_path(url: str, root: Path = FILES_DIR) -> Path:
    parts = [unquote(p) for p in urlsplit(url).path.strip("/").split("/")]
    if urlsplit(url).netloc != HOST or not parts or any(p in ("", ".", "..") or "\\" in p for p in parts):
        raise ValueError(f"保存できない URL: {url!r}")
    return root.joinpath(*parts)


def read_manifest(path: Path = MANIFEST) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {row["url"]: row for row in csv.DictReader(f)}


def write_manifest(rows: dict[str, dict[str, str]], path: Path = MANIFEST) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows[k] for k in sorted(rows))
    for attempt in range(10):
        try:
            tmp.replace(path)
            return
        except PermissionError:  # Windows では、ほかのプロセスが台帳を読んでいる間は置き換えられない
            if attempt == 9:
                raise
            time.sleep(0.5)


def fetch(url: str, max_bytes: int = MAX_BYTES) -> tuple[dict[str, str], bytes | None]:
    """1 件取得する。戻り値は（台帳の行, 本体）。保存しない場合の本体は None。"""
    row = {"url": url, "status": "", "content_type": "", "last_modified": "",
           "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "bytes": "", "sha256": "", "note": ""}
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
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
        if e.code in (403, 429):
            raise Blocked(f"{url}: HTTP {e.code}") from e
        row["note"] = e.reason or ""
        return row, None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=0, help="取得する件数の上限（0 は無制限）")
    ap.add_argument("--retry", action="store_true", help="前回 200 以外だったものも取り直す")
    args = ap.parse_args()

    rows = read_manifest()
    todo = []
    for url in targets():
        done = rows.get(url)
        if done and done["sha256"] and local_path(url).exists():
            continue
        if done and not done["sha256"] and not args.retry:
            continue
        todo.append(url)
    if args.limit:
        todo = todo[:args.limit]
    print(f"対象 {len(targets())} 件 / 今回取得 {len(todo)} 件", flush=True)

    saved = 0
    try:
        for i, url in enumerate(todo, 1):
            row, body = fetch(url)
            if body is not None:
                path = local_path(url)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
                saved += 1
            rows[url] = row
            write_manifest(rows)
            if body is None or i % 25 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} {row['status']} {row['bytes']} {row['note']} {url}", flush=True)
            if i < len(todo):
                time.sleep(INTERVAL)
    except Blocked as e:
        print(f"中断: {e}", file=sys.stderr)
        return 1
    print(f"保存 {saved} 件 / 保存できなかったもの {len(todo) - saved} 件", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
