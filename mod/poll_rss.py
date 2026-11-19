#!/usr/bin/env python3
"""防衛省 RSS（更新情報・お知らせ）を取得し、未収録の item を CSV に追記する。

RSS は直近約 2.5 か月分しか保持しないため、定期実行して更新ログを蓄積する。
item の全フィールドを保存するので、生の XML は残さない。

使い方:
    python3 mod/poll_rss.py            # 取得して data/mod/rss_items.csv に追記
    python3 mod/poll_rss.py --dry-run  # 追記せず、新規 item だけ表示
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE = "https://www.mod.go.jp"
FEEDS = {
    "update": f"{BASE}/j/rss/update.xml",  # サイト全体の更新情報
    "news": f"{BASE}/j/rss/news.xml",  # お知らせ（報道資料）
}
# Python 既定の User-Agent は 403 になる。
USER_AGENT = "rss-poller/1.0"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"

FIELDS = ["feed", "pub_date", "title", "link", "guid", "category",
          "description", "content", "council_related", "first_seen"]
KEY = ("feed", "pub_date", "link", "title")

MEETING_PREFIX = "/j/policy/agenda/meeting/"
# 件名による判定は目安。「懇談会食」（動静記事）は除く。
COUNCIL_TITLE = re.compile(r"審議会|分科会|検討会|検討委員会|有識者|懇談会(?!食)|審査会|調査会議|アドバイザリーボード")

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "mod" / "rss_items.csv"


class FeedError(RuntimeError):
    pass


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        hint = "（ボット検証の可能性）" if e.headers.get("cf-mitigated") else ""
        raise FeedError(f"{url}: HTTP {e.code}{hint}") from e
    except urllib.error.URLError as e:
        raise FeedError(f"{url}: {e.reason}") from e


def is_council_related(feed: str, title: str, link: str) -> bool:
    """審議会ページ配下の更新、または件名に会議体らしい語を含む item。"""
    return link.startswith(MEETING_PREFIX) or bool(COUNCIL_TITLE.search(title))


def parse(feed: str, raw: bytes, first_seen: str) -> list[dict[str, str]]:
    if b"<!DOCTYPE" in raw[:2048] or b"<!ENTITY" in raw:
        raise FeedError(f"{feed}: DTD/ENTITY を含む応答は処理しない")
    try:
        root = ET.fromstring(raw.decode("utf-8-sig"))
    except (ET.ParseError, UnicodeDecodeError) as e:
        raise FeedError(f"{feed}: XML として解釈できない: {e}") from e
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise FeedError(f"{feed}: RSS 2.0 ではない（root={root.tag}）")

    rows = []
    for item in channel.findall("item"):
        def text(tag: str) -> str:
            return (item.findtext(tag) or "").strip()

        pub = text("pubDate")
        if not pub or not text("link"):
            raise FeedError(f"{feed}: pubDate または link のない item: {text('title')!r}")
        rows.append({
            "feed": feed,
            "pub_date": parsedate_to_datetime(pub).isoformat(),
            "title": text("title"),
            "link": text("link"),
            "guid": text("guid"),
            "category": text("category"),
            "description": text("description"),
            "content": text(CONTENT_NS),
            "council_related": "1" if is_council_related(feed, text("title"), text("link")) else "0",
            "first_seen": first_seen,
        })
    if not rows:
        raise FeedError(f"{feed}: item が 0 件")
    return rows


def key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(row[k] for k in KEY)


def merge(existing: list[dict[str, str]], incoming: list[dict[str, str]]):
    """既存行を保ったまま未収録の行だけ加える。戻り値は（全行, 新規行）。"""
    seen = {key(r) for r in existing}
    new = []
    for row in incoming:
        if key(row) not in seen:
            seen.add(key(row))
            new.append(row)
    merged = sorted(existing + new, key=lambda r: (r["pub_date"], r["feed"], r["link"], r["title"]))
    return merged, new


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--dry-run", action="store_true", help="CSV を書き換えない")
    args = ap.parse_args()

    first_seen = datetime.now(timezone.utc).isoformat(timespec="seconds")
    incoming = []
    try:
        for feed, url in FEEDS.items():
            incoming += parse(feed, fetch(url), first_seen)
    except FeedError as e:
        # 片方でも失敗したら何も書かない（次回の実行で両方取り直す）。
        print(f"取得失敗: {e}", file=sys.stderr)
        return 1

    merged, new = merge(read_csv(args.out), incoming)
    if new and not args.dry_run:
        write_csv(args.out, merged)

    council = [r for r in new if r["council_related"] == "1"]
    print(f"取得 {len(incoming)} 件 / 新規 {len(new)} 件 / うち審議会関連 {len(council)} 件"
          + ("（dry-run）" if args.dry_run else ""))
    for r in council:
        print(f"  {r['pub_date'][:10]} [{r['feed']}] {r['title']} {BASE}{r['link']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
