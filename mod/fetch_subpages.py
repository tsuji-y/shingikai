#!/usr/bin/env python3
"""下位 HTML ページ（会議詳細・HTML 議事録・配布資料一覧等）を取得し、本文テキスト抽出と内包 PDF リンク収集を行う。

設計方針:
- 生 HTML 原本は raw/<agency>/subpages/（.gitignore 対象）に保存し、GitHub Releases へバンドル退避する。
- ナビゲーション・ヘッダ・フッタを除外した本文テキストを data/<agency>/text/ に保存し、data/<agency>/texts.csv に登録する。
- 下位 HTML 内に含まれる同一省庁ホストの PDF リンクを raw/<agency>/subpage_links.csv に記録し、
  build_db.py が data/<agency>/documents.csv へ統合できるようにする（WARP 等外部ドメインは除外）。
- 1 ドメイン 1 プロセス・2 秒間隔直列を厳守し、403/429・cf-mitigated は即時中断（Blocked）する。
- 台帳 raw/<agency>/subpages.csv を都度更新するため、中断しても途中から冪等に再開できる。

使い方:
    python3 mod/fetch_subpages.py --agency digital --limit 50
    python3 mod/fetch_subpages.py --agency mhlw --since-year 2024 --until-year 2026 --limit 100
    python3 mod/fetch_subpages.py --agency cas --dry-run
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agencies  # noqa: E402
import build_db  # noqa: E402
import check_updates  # noqa: E402
import extract_text  # noqa: E402
import fetch_files  # noqa: E402
import ingest_pages  # noqa: E402
import pages  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
USER_AGENT = "rss-poller/1.0"
INTERVAL = 2.0
MAX_BYTES = 10 * 1024 * 1024
TIMEOUT = 60
RETRY_BACKOFF = (3.0, 6.0, 12.0, 24.0, 48.0)

SUBPAGE_FIELDS = [
    "url", "council_id", "meeting_id", "meeting_date", "doc_type",
    "status", "last_modified", "fetched_at", "bytes", "sha256",
    "text", "discovered_pdfs", "note", "release_url",
]
SUBPAGE_LINK_FIELDS = [
    "source_url", "council_id", "meeting_id", "section",
    "doc_type", "title", "url", "format", "host",
]

_YEAR_RE = re.compile(r"(20[0-3]\d|199\d)")


class Blocked(RuntimeError):
    """403・429 またはボット検証。その場で停止する。"""


@dataclass
class SubpageTarget:
    url: str
    council_id: str = ""
    meeting_id: str = ""
    meeting_date: str = ""
    doc_type: str = ""
    title: str = ""


@dataclass
class SubpageSummary:
    checked: int = 0
    saved: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    discovered_pdfs: int = 0


def local_path(url: str, root: Path) -> Path:
    """下位 HTML のローカル保存先。拡張子なし URL は <path>/index.html に保存する。"""
    sp = urlsplit(url)
    path = sp.path or "/"
    return ingest_pages.local_path(path, root)


def html_text_path(url: str, text_dir: Path) -> Path:
    """下位 HTML から抽出したテキストの保存先。PDF テキストと衝突しない一意なパスを返す。"""
    sp = urlsplit(url)
    parts = [unquote(p) for p in sp.path.strip("/").split("/") if p]
    if not parts or sp.path.endswith("/"):
        parts.append("index.html")
    last = parts[-1]
    if last.lower().endswith((".html", ".htm")):
        parts[-1] = last.rsplit(".", 1)[0] + ".txt"
    else:
        parts[-1] = last + ".txt"
    if any(p in ("", ".", "..") or "\\" in p for p in parts):
        raise ValueError(f"テキスト保存先を生成できない URL: {url!r}")
    return text_dir.joinpath(*parts)


def blocks_to_text(blocks: list[pages.Block]) -> str:
    """pages.parse() のブロック列をクリーンなプレーンテキストへ変換する。"""
    lines: list[str] = []
    for b in blocks:
        if b.kind == "heading":
            txt = " ".join(b.text.split())
            if txt:
                prefix = "#" * min(max(b.level, 1), 6)
                lines.append(f"\n{prefix} {txt}")
        elif b.kind == "row":
            cells = [" ".join(c.text.split()) for c in b.cells]
            row_txt = " | ".join(c for c in cells if c)
            if row_txt:
                lines.append(row_txt)
        else:
            txt = b.text.strip()
            if txt:
                lines.append(txt)
    return extract_text.normalize("\n".join(lines)) if lines else ""


def extract_pdf_links(blocks: list[pages.Block], *, source_url: str, council_id: str,
                      meeting_id: str, allowed_hosts: set[str]) -> list[dict[str, str]]:
    """下位 HTML ブロック内の同一省庁ホスト PDF リンクを抽出する（WARP 等の外部ホストは除外）。"""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for b in blocks:
        section = b.path[-1] if b.path else ""
        for ln in b.links:
            sp = urlsplit(ln.url)
            if sp.netloc not in allowed_hosts:
                continue
            fmt = build_db.url_format(ln.url)
            if fmt != "pdf":
                continue
            if ln.url in seen:
                continue
            seen.add(ln.url)
            title = " ".join(ln.text.split())
            dt = build_db.doc_type(title)
            if dt == "その他" and section:
                dt = build_db.doc_type(section)
            out.append({
                "source_url": source_url,
                "council_id": council_id,
                "meeting_id": meeting_id,
                "section": section,
                "doc_type": dt,
                "title": title,
                "url": ln.url,
                "format": "pdf",
                "host": sp.netloc,
            })
    return out


def read_manifest(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows[row["url"]] = {k: row.get(k, "") for k in SUBPAGE_FIELDS}
    return rows


def write_manifest(rows: dict[str, dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUBPAGE_FIELDS, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for k in sorted(rows):
            writer.writerow({field: rows[k].get(field, "") for field in SUBPAGE_FIELDS})
    tmp.replace(path)


def read_links(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    """(source_url, url) をキーに subpage_links.csv を読み込む。"""
    if not path.exists():
        return {}
    rows: dict[tuple[str, str], dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = (row.get("source_url", ""), row.get("url", ""))
            rows[key] = {k: row.get(k, "") for k in SUBPAGE_LINK_FIELDS}
    return rows


def write_links(rows: dict[tuple[str, str], dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUBPAGE_LINK_FIELDS, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for k in sorted(rows):
            writer.writerow({field: rows[k].get(field, "") for field in SUBPAGE_LINK_FIELDS})
    tmp.replace(path)


def _infer_year(date_str: str, url: str, title: str) -> int | None:
    if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
        return int(date_str[:4])
    iso, _ = build_db.parse_date(title)
    if iso and len(iso) >= 4 and iso[:4].isdigit():
        return int(iso[:4])
    m = _YEAR_RE.search(url)
    if m:
        return int(m.group(1))
    return None


def collect_targets(ag: agencies.AgencyConfig, *, council: str = "",
                    since_year: int = 0, until_year: int = 0,
                    include_discovered: bool = True) -> list[SubpageTarget]:
    """未取得・対象の下位 HTML URL リストを返す（新しい開催日の会議を優先）。"""
    pages_rows = ingest_pages.read_manifest(ag.pages_manifest)
    known_paths = set(pages_rows.keys()) | {agencies.normalize_path(u) for u in pages_rows.keys()}
    allowed_hosts = set(ag.hosts)

    meeting_dates: dict[str, str] = {}
    meetings_csv = ag.data_dir / "meetings.csv"
    if meetings_csv.exists():
        with meetings_csv.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("meeting_id") and r.get("date"):
                    meeting_dates[r["meeting_id"]] = r["date"]

    targets_by_url: dict[str, SubpageTarget] = {}
    docs_csv = ag.data_dir / "documents.csv"
    if docs_csv.exists():
        with docs_csv.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if council and r.get("council_id") != council:
                    continue
                u = (r.get("url") or "").strip()
                if not u:
                    continue
                sp = urlsplit(u)
                if sp.netloc not in allowed_hosts or sp.query:
                    continue
                if not agencies.is_html_path(sp.path, allow_extensionless=ag.allow_extensionless_pages):
                    continue
                norm = agencies.normalize_path(sp.path)
                if sp.path in known_paths or norm in known_paths:
                    continue
                clean_url = f"{sp.scheme}://{sp.netloc}{sp.path}"
                if clean_url in targets_by_url:
                    continue
                mdate = meeting_dates.get(r.get("meeting_id", ""), "")
                targets_by_url[clean_url] = SubpageTarget(
                    url=clean_url,
                    council_id=r.get("council_id", ""),
                    meeting_id=r.get("meeting_id", ""),
                    meeting_date=mdate,
                    doc_type=r.get("doc_type", ""),
                    title=r.get("title", ""),
                )

    if include_discovered and ag.pages_manifest.exists():
        councils_rows = build_db.load_councils(ag.councils_csv) if ag.councils_csv.exists() else []
        councils_obj = build_db.Councils(councils_rows) if councils_rows else None
        for p_url in sorted(pages_rows):
            local = pages.resolve_local_path(p_url, ag.raw_pages_dir)
            if not local.exists():
                continue
            for lk in check_updates.discover_html_links(p_url, local.read_bytes(), agency=ag, council_rows=councils_rows):
                norm = agencies.normalize_path(lk)
                if lk in known_paths or norm in known_paths:
                    continue
                cid = ""
                if councils_obj:
                    c = councils_obj.of(lk)
                    cid = c["council_id"] if c else ""
                if council and cid != council:
                    continue
                full_url = f"{ag.base_url}{lk}"
                if full_url not in targets_by_url:
                    targets_by_url[full_url] = SubpageTarget(url=full_url, council_id=cid)

    items = list(targets_by_url.values())
    if since_year or until_year:
        filtered: list[SubpageTarget] = []
        for t in items:
            yr = _infer_year(t.meeting_date, t.url, t.title)
            if yr is None:
                # 年不明分は until_year 指定がない最新帯（since_year のみ）ではスキップせず含めるか制御:
                # until_year が指定されている過去帯ではスキップ、since_year のみなら含める
                if until_year:
                    continue
                filtered.append(t)
                continue
            if since_year and yr < since_year:
                continue
            if until_year and yr > until_year:
                continue
            filtered.append(t)
        items = filtered

    # 新しい開催日 → 議事録・議事要旨優先 → URL 順で処理する
    priority = {"議事録": 0, "議事要旨": 1, "名簿": 2, "議事次第": 3, "資料": 4}
    items.sort(key=lambda t: (
        t.meeting_date == "",
        "" if not t.meeting_date else "".join(chr(ord("9") - (ord(c) - ord("0"))) if c.isdigit() else c for c in t.meeting_date),
        priority.get(t.doc_type, 9),
        t.url,
    ))
    return items


def fetch_html(url: str, *, if_modified_since: str = "", user_agent: str = USER_AGENT,
               timeout: int = TIMEOUT, max_bytes: int = MAX_BYTES,
               retry_backoff: tuple[float, ...] = RETRY_BACKOFF,
               opener=urllib.request.urlopen) -> check_updates.FetchResponse:
    """1 件の下位 HTML を取得する。403/429・cf-mitigated は即時 Blocked を送出する。"""
    headers = {"User-Agent": user_agent}
    ims = fetch_files.iso_to_http_date(if_modified_since) if if_modified_since else ""
    if ims:
        headers["If-Modified-Since"] = ims

    for attempt in range(len(retry_backoff) + 1):
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        req = urllib.request.Request(url, headers=headers)
        try:
            with opener(req, timeout=timeout) as resp:
                if resp.headers.get("cf-mitigated"):
                    raise Blocked(f"{url}: cf-mitigated={resp.headers.get('cf-mitigated')}")
                ct = (resp.headers.get("Content-Type") or "").lower()
                body = resp.read(max_bytes + 1)
                if len(body) > max_bytes:
                    return check_updates.FetchResponse(status=0, fetched_at=fetched_at, error="上限サイズ超過")
                if ct and "html" not in ct and "text/" not in ct and "xml" not in ct:
                    return check_updates.FetchResponse(status=0, fetched_at=fetched_at, error=f"HTML ではない応答 ({ct[:40]})")
                lm = ingest_pages.http_date_to_iso(resp.headers.get("Last-Modified"))
                return check_updates.FetchResponse(status=resp.status, body=body, last_modified=lm, fetched_at=fetched_at)
        except urllib.error.HTTPError as e:
            cf = e.headers.get("cf-mitigated", "") if e.headers else ""
            if e.code == 304:
                return check_updates.FetchResponse(status=304, fetched_at=fetched_at)
            if e.code in (403, 429) or cf:
                raise Blocked(f"{url}: HTTP {e.code}" + (f" ({cf})" if cf else "")) from e
            if e.code in (404, 410):
                return check_updates.FetchResponse(status=e.code, fetched_at=fetched_at, error=e.reason or "")
            if 500 <= e.code < 600 and attempt < len(retry_backoff):
                ra = e.headers.get("Retry-After") if e.headers else None
                wait_s = retry_backoff[attempt]
                if ra and ra.isdigit():
                    wait_s = min(90.0, max(wait_s, float(ra)))
                time.sleep(wait_s)
                continue
            return check_updates.FetchResponse(status=e.code, fetched_at=fetched_at, error=e.reason or f"HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < len(retry_backoff):
                time.sleep(retry_backoff[attempt])
                continue
            return check_updates.FetchResponse(status=0, fetched_at=fetched_at, error=str(getattr(e, "reason", e)))
    return check_updates.FetchResponse(status=0, error="リトライ上限超過")  # pragma: no cover


def fetch_subpages(ag: agencies.AgencyConfig, *, limit: int = 0, retry: bool = False,
                   council: str = "", since_year: int = 0, until_year: int = 0,
                   include_discovered: bool = True, dry_run: bool = False,
                   interval: float = INTERVAL, fetcher=fetch_html,
                   fail_log: Path | None = None) -> SubpageSummary:
    """未取得の下位 HTML を取得し、本文テキスト抽出と PDF リンク抽出を実行する。"""
    manifest_rows = read_manifest(ag.subpages_manifest)
    link_rows = read_links(ag.subpage_links_csv)
    texts_csv = ag.data_dir / "texts.csv"
    text_dir = ag.data_dir / "text"
    texts_done = {r["url"]: r for r in extract_text.read_csv(texts_csv)}

    all_targets = collect_targets(
        ag, council=council, since_year=since_year, until_year=until_year,
        include_discovered=include_discovered,
    )
    todo: list[SubpageTarget] = []
    for t in all_targets:
        prev = manifest_rows.get(t.url)
        if prev and prev.get("sha256"):
            continue
        if prev and not prev.get("sha256") and not retry:
            continue
        todo.append(t)

    if limit:
        todo = todo[:limit]

    summary = SubpageSummary()
    print(f"[{ag.agency_id}] 下位HTML 対象 {len(all_targets)} 件 / 今回取得 {len(todo)} 件"
          + ("（dry-run）" if dry_run else ""), flush=True)
    if dry_run:
        for t in todo[:30]:
            print(f"  [dry-run] {t.meeting_date or '----'} {t.doc_type or '-'} {t.url}", flush=True)
        if len(todo) > 30:
            print(f"  ... ほか {len(todo) - 30} 件", flush=True)
        return summary

    allowed_hosts = set(ag.hosts)
    texts_dirty = False
    links_dirty = False

    for i, t in enumerate(todo, 1):
        resp = fetcher(t.url, if_modified_since="")
        summary.checked += 1
        row = {
            "url": t.url,
            "council_id": t.council_id,
            "meeting_id": t.meeting_id,
            "meeting_date": t.meeting_date,
            "doc_type": t.doc_type,
            "status": str(resp.status),
            "last_modified": resp.last_modified,
            "fetched_at": resp.fetched_at,
            "bytes": "",
            "sha256": "",
            "text": "",
            "discovered_pdfs": "0",
            "note": resp.error,
            "release_url": "",
        }
        if resp.status == 200 and resp.body is not None:
            sha = hashlib.sha256(resp.body).hexdigest()
            raw_out = local_path(t.url, ag.raw_subpages_dir)
            raw_out.parent.mkdir(parents=True, exist_ok=True)
            raw_out.write_bytes(resp.body)
            row["bytes"] = str(len(resp.body))
            row["sha256"] = sha

            sp = urlsplit(t.url)
            base_for_url = f"{sp.scheme}://{sp.netloc}"
            blocks = pages.parse(pages.decode(resp.body), sp.path or "/", base_url=base_for_url)
            text = blocks_to_text(blocks)
            txt_out = html_text_path(t.url, text_dir)
            txt_out.parent.mkdir(parents=True, exist_ok=True)
            txt_out.write_text(text, encoding="utf-8", newline="\n")
            try:
                rel_txt = txt_out.relative_to(ag.repo).as_posix()
            except ValueError:
                rel_txt = str(txt_out)
            chars = sum(1 for ch in text if not ch.isspace())
            row["text"] = rel_txt
            texts_done[t.url] = {
                "url": t.url,
                "text": rel_txt,
                "pages": "1",
                "chars": str(chars),
                "sha256": sha,
                "note": "HTML本文" if chars >= 20 else "本文がほとんど取れない（HTML）",
            }
            texts_dirty = True

            pdfs = extract_pdf_links(
                blocks, source_url=t.url, council_id=t.council_id,
                meeting_id=t.meeting_id, allowed_hosts=allowed_hosts,
            )
            row["discovered_pdfs"] = str(len(pdfs))
            for pdf in pdfs:
                key = (pdf["source_url"], pdf["url"])
                if key not in link_rows:
                    summary.discovered_pdfs += 1
                link_rows[key] = pdf
                links_dirty = True
            summary.saved.append(t.url)
        else:
            err_msg = resp.error or f"HTTP {resp.status}"
            summary.failed.append((t.url, err_msg))

        manifest_rows[t.url] = row
        # チェックポイント保存（10 件ごと・エラー時・末尾）
        if resp.body is None or i % 10 == 0 or i == len(todo):
            write_manifest(manifest_rows, ag.subpages_manifest)
            if texts_dirty:
                extract_text.write_csv(texts_csv, list(texts_done.values()))
                texts_dirty = False
            if links_dirty:
                write_links(link_rows, ag.subpage_links_csv)
                links_dirty = False
            if resp.body is None or i % 25 == 0 or i == len(todo):
                print(
                    f"  [{ag.agency_id}] {i}/{len(todo)} {row['status']} {row['bytes']} "
                    f"pdfs={row['discovered_pdfs']} {row['note']} {t.url}",
                    flush=True,
                )

        if i < len(todo) and interval > 0:
            time.sleep(interval)

    if fail_log and summary.failed:
        fail_log.parent.mkdir(parents=True, exist_ok=True)
        with fail_log.open("a", encoding="utf-8", newline="\n") as f:
            for u, err in summary.failed:
                f.write(f"{ag.agency_id}\tsubpage\t{u}\t{err}\n")

    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agency", default="mod", help="対象の省庁 ID（既定: mod）")
    ap.add_argument("--limit", type=int, default=0, help="今回取得する上限件数（0 は無制限）")
    ap.add_argument("--retry", action="store_true", help="前回失敗した URL も再試行する")
    ap.add_argument("--council", default="", help="対象の会議体 ID に絞る")
    ap.add_argument("--since-year", type=int, default=0, help="対象会議年の下限（例: 2020）")
    ap.add_argument("--until-year", type=int, default=0, help="対象会議年の上限（例: 2023）")
    ap.add_argument("--no-discovered", action="store_true", help="discover_html_links 経由の探索を無効化")
    ap.add_argument("--interval", type=float, default=INTERVAL, help="リクエスト間の待機秒数")
    ap.add_argument("--fail-log", type=Path, default=None, help="全失敗 URL を追記出力するファイル")
    ap.add_argument("--dry-run", action="store_true", help="書き込みを行わない")
    args = ap.parse_args()

    ag = agencies.get_agency(args.agency)
    try:
        res = fetch_subpages(
            ag,
            limit=args.limit,
            retry=args.retry,
            council=args.council,
            since_year=args.since_year,
            until_year=args.until_year,
            include_discovered=not args.no_discovered,
            dry_run=args.dry_run,
            interval=args.interval,
            fail_log=args.fail_log,
        )
        print(
            f"[{ag.agency_id}] 下位HTML 完了: 保存 {len(res.saved)} 件 / "
            f"新規PDF発見 {res.discovered_pdfs} 件 / 失敗 {len(res.failed)} 件",
            flush=True,
        )
    except Blocked as e:
        print(f"[{ag.agency_id}] 遮断により中断: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
