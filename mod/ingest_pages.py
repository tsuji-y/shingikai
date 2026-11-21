#!/usr/bin/env python3
"""ブラウザで取得したページ束（JSON）を raw/mod/pages/ に展開し、台帳 raw/mod/pages.csv を更新する。

www.mod.go.jp の HTML はスクリプトから取得できない（README 参照）ので、取得はブラウザ側で行い、
このスクリプトは保存だけを担当する。束は次の形の配列:

    [{"url": "/j/policy/...", "status": 200, "last_modified": "...", "fetched_at": "...", "body_b64": "..."}]

本文は取得したバイト列のまま保存する（文字コード変換・改行変換をしない）。

使い方:
    python3 mod/ingest_pages.py bundle.json [bundle2.json ...]
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
import sys
from email.utils import parsedate_to_datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RAW_DIR = REPO / "raw" / "mod" / "pages"
MANIFEST = REPO / "raw" / "mod" / "pages.csv"
FIELDS = ["url", "status", "last_modified", "fetched_at", "bytes", "sha256", "content_sha256", "missing_count"]

_SCRIPT_RE = re.compile(rb"<script\b[^>]*>.*?</script>", re.S | re.I)
_CF_MARKER = b"cdn-cgi/challenge-platform"


def strip_cf_script(body: bytes) -> bytes:
    """Cloudflare が差し込む cdn-cgi/challenge-platform を含む <script>…</script> を除く。"""
    return _SCRIPT_RE.sub(lambda m: b"" if _CF_MARKER in m.group(0) else m.group(0), body)


def has_cf_script(body: bytes) -> bool:
    """Cloudflare の差し込みスクリプトを含むか。"""
    return any(_CF_MARKER in m.group(0) for m in _SCRIPT_RE.finditer(body))


def content_hash(body: bytes) -> str:
    """差し込みスクリプトを除いた本文の SHA-256。変更検知に使う。"""
    return hashlib.sha256(strip_cf_script(body)).hexdigest()


def local_path(url: str, root: Path = RAW_DIR) -> Path:
    """サイト内パス（/j/.../x.html）に対応する保存先。"""
    if not url.startswith("/") or ".." in url.split("/") or "\\" in url:
        raise ValueError(f"保存できないパス: {url!r}")
    return root.joinpath(*url.strip("/").split("/"))


def http_date_to_iso(value: str | None) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return ""


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
    tmp.replace(path)


def ingest(records: list[dict], rows: dict[str, dict[str, str]], root: Path = RAW_DIR) -> tuple[int, list[str]]:
    """200 のレコードだけ保存する。戻り値は（保存件数, 保存しなかった理由の一覧）。"""
    saved, skipped = 0, []
    for rec in records:
        url = rec.get("url", "")
        if rec.get("status") != 200 or "body_b64" not in rec:
            skipped.append(f"{url}: status={rec.get('status')} {rec.get('error', '')}".strip())
            continue
        body = base64.b64decode(rec["body_b64"])
        path = local_path(url, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        rows[url] = {
            "url": url,
            "status": "200",
            "last_modified": http_date_to_iso(rec.get("last_modified")),
            "fetched_at": rec.get("fetched_at", ""),
            "bytes": str(len(body)),
            "sha256": hashlib.sha256(body).hexdigest(),
            "content_sha256": content_hash(body),
            "missing_count": "",
        }
        saved += 1
    return saved, skipped


def backfill_content_hashes(rows: dict[str, dict[str, str]], root: Path = RAW_DIR) -> int:
    """保存済みの HTML から content_sha256 が空の行を埋める。"""
    updated = 0
    for url, row in rows.items():
        if row.get("content_sha256"):
            continue
        path = local_path(url, root)
        if not path.exists():
            continue
        row["content_sha256"] = content_hash(path.read_bytes())
        updated += 1
    return updated


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundles", nargs="*", type=Path)
    ap.add_argument("--backfill", action="store_true", help="保存済み HTML から content_sha256 を埋める")
    args = ap.parse_args()

    if not args.bundles and not args.backfill:
        ap.error("bundle を指定するか --backfill を付けてください")

    rows = read_manifest()
    total = 0
    for bundle in args.bundles:
        records = json.loads(bundle.read_text(encoding="utf-8"))
        saved, skipped = ingest(records, rows)
        total += saved
        print(f"{bundle.name}: 保存 {saved} 件 / 対象外 {len(skipped)} 件")
        for s in skipped:
            print(f"  {s}", file=sys.stderr)
    if args.backfill:
        filled = backfill_content_hashes(rows)
        print(f"content_sha256 補完: {filled} 件")
    write_manifest(rows)
    print(f"台帳 {len(rows)} 件（今回 {total} 件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
