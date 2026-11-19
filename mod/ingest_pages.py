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
import sys
from email.utils import parsedate_to_datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RAW_DIR = REPO / "raw" / "mod" / "pages"
MANIFEST = REPO / "raw" / "mod" / "pages.csv"
FIELDS = ["url", "status", "last_modified", "fetched_at", "bytes", "sha256"]


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
    with path.open(encoding="utf-8", newline="") as f:
        return {row["url"]: row for row in csv.DictReader(f)}


def write_manifest(rows: dict[str, dict[str, str]], path: Path = MANIFEST) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows[k] for k in sorted(rows))
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
        }
        saved += 1
    return saved, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundles", nargs="+", type=Path)
    args = ap.parse_args()

    rows = read_manifest()
    total = 0
    for bundle in args.bundles:
        records = json.loads(bundle.read_text(encoding="utf-8"))
        saved, skipped = ingest(records, rows)
        total += saved
        print(f"{bundle.name}: 保存 {saved} 件 / 対象外 {len(skipped)} 件")
        for s in skipped:
            print(f"  {s}", file=sys.stderr)
    write_manifest(rows)
    print(f"台帳 {len(rows)} 件（今回 {total} 件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
