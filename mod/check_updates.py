#!/usr/bin/env python3
"""台帳（raw/mod/pages.csv）の全ページを条件付き取得でチェックし、変更ページと新規ページを保存する。

規則（docs/update_plan.md 3.2〜3.4 節）:
- 1 件ずつ直列に、2 秒以上の間隔をあけて取得する。
- If-Modified-Since を付けて取得し、304 なら変更なし。
- 200 のときは cdn-cgi/challenge-platform の差し込みスクリプトを除いた本文ハッシュ（content_sha256）を
  台帳と比べる。同じなら変更なし（last_modified のみ更新）、違えば変更ありとして生バイト列を保存する。
- 404・410 は消失候補とし、2 回続けて同じ結果なら消失として記録する（保存済みファイルは消さない）。
- 403・429 または cf-mitigated ヘッダが返ったらその場で停止し、リトライしない（未確認として失敗にする）。
- タイムアウトと 5xx のみ、間隔を広げながら最大 3 回リトライする。
- 変更ページのリンクのうち /j/policy/agenda/meeting/ 配下の未収録 HTML を 1 回あたり最大 30 件まで取得する。

使い方:
    python3 mod/check_updates.py
    python3 mod/check_updates.py --dry-run
    python3 mod/check_updates.py --council kousei_nyusatu
    python3 mod/check_updates.py --limit 10
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agencies  # noqa: E402
import build_db  # noqa: E402
import fetch_files  # noqa: E402
import ingest_pages  # noqa: E402
import pages  # noqa: E402

BASE = "https://www.mod.go.jp"
HOST = "www.mod.go.jp"
ROOT = "/j/policy/agenda/meeting/"
USER_AGENT = "rss-poller/1.0"
INTERVAL = 2.0
MAX_NEW_PAGES = 30
MAX_BYTES = 10 * 1024 * 1024
TIMEOUT = 60
RETRY_BACKOFF = (2.0, 4.0, 8.0)


class Blocked(RuntimeError):
    """403・429 またはボット検証。その場で停止する。"""


class NoPagesChecked(RuntimeError):
    """チェックできたページが 0 件。"""


@dataclass
class FetchResponse:
    status: int  # 200, 304, 404, 410, 5xx, 0(network error)
    body: bytes | None = None
    last_modified: str = ""
    fetched_at: str = ""
    error: str = ""


@dataclass
class CheckResult:
    checked: int = 0
    unchanged: int = 0
    metadata_only: list[str] = field(default_factory=list)
    changed_pages: list[str] = field(default_factory=list)
    new_pages: list[str] = field(default_factory=list)
    missing_candidates: list[str] = field(default_factory=list)
    gone_pages: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    manifest_dirty: bool = False

    @property
    def affected_pages(self) -> set[str]:
        return set(self.changed_pages) | set(self.new_pages) | set(self.gone_pages)

    @property
    def has_content_changes(self) -> bool:
        return bool(self.changed_pages or self.new_pages or self.gone_pages)


def fetch_page(url_path: str, *, base_url: str = BASE, if_modified_since: str = "",
               user_agent: str = USER_AGENT, timeout: int = TIMEOUT, max_bytes: int = MAX_BYTES,
               retry_backoff: tuple[float, ...] = RETRY_BACKOFF,
               opener=urllib.request.urlopen) -> FetchResponse:
    """1 ページ取得する。403・429・cf-mitigated はリトライせず即座に Blocked を送出する。"""
    full_url = url_path if url_path.startswith(("http://", "https://")) else base_url + url_path
    headers = {"User-Agent": user_agent}
    ims = fetch_files.iso_to_http_date(if_modified_since) if if_modified_since else ""
    if ims:
        headers["If-Modified-Since"] = ims

    for attempt in range(len(retry_backoff) + 1):
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        req = urllib.request.Request(full_url, headers=headers)
        try:
            with opener(req, timeout=timeout) as resp:
                if resp.headers.get("cf-mitigated"):
                    raise Blocked(f"{url_path}: cf-mitigated={resp.headers.get('cf-mitigated')}")
                body = resp.read(max_bytes + 1)
                if len(body) > max_bytes:
                    return FetchResponse(status=0, fetched_at=fetched_at, error="上限サイズ超過")
                lm = ingest_pages.http_date_to_iso(resp.headers.get("Last-Modified"))
                return FetchResponse(status=resp.status, body=body, last_modified=lm, fetched_at=fetched_at)
        except urllib.error.HTTPError as e:
            cf = e.headers.get("cf-mitigated", "") if e.headers else ""
            if e.code == 304:
                return FetchResponse(status=304, fetched_at=fetched_at)
            if e.code in (403, 429) or cf:
                raise Blocked(f"{url_path}: HTTP {e.code}" + (f" ({cf})" if cf else "")) from e
            if e.code in (404, 410):
                return FetchResponse(status=e.code, fetched_at=fetched_at, error=e.reason or "")
            if 500 <= e.code < 600 and attempt < len(retry_backoff):
                time.sleep(retry_backoff[attempt])
                continue
            return FetchResponse(status=e.code, fetched_at=fetched_at, error=e.reason or f"HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < len(retry_backoff):
                time.sleep(retry_backoff[attempt])
                continue
            return FetchResponse(status=0, fetched_at=fetched_at, error=str(getattr(e, "reason", e)))
    return FetchResponse(status=0, error="リトライ上限超過")  # pragma: no cover


def discover_html_links(url_path: str, body: bytes, *,
                        agency: agencies.AgencyConfig | None = None,
                        council_rows: list[dict[str, str]] | None = None) -> list[str]:
    """HTML 内のリンクから、巡回対象配下の HTML パスを抽出する。"""
    if agency is None or agency.agency_id == "mod":
        blocks = pages.parse(pages.decode(body), url_path)
        found: list[str] = []
        seen: set[str] = set()
        for b in blocks:
            for ln in b.links:
                sp = urlsplit(ln.url)
                if sp.netloc != HOST or sp.query:
                    continue
                path = sp.path
                if not path.startswith(ROOT):
                    continue
                if build_db.url_format(ln.url) not in ("html", "htm"):
                    continue
                if path.endswith("/"):
                    path = path + "index.html"
                try:
                    ingest_pages.local_path(path)
                except ValueError:
                    continue
                if path not in seen:
                    seen.add(path)
                    found.append(path)
        return found

    blocks = pages.parse(pages.decode(body), url_path, base_url=agency.base_url)
    index_set = {agencies.normalize_path(u) for u in agency.index_urls}
    norm_url = agencies.normalize_path(url_path)
    found = []
    seen = set()

    if norm_url in index_set:
        extracted = agencies.extract_councils_generic(agency, [(url_path, blocks)], existing=council_rows)
        if council_rows is not None:
            known_links = {r["link"] for r in council_rows}
            for r in extracted:
                if r["link"] not in known_links:
                    council_rows.append(r)
                    known_links.add(r["link"])
        for r in extracted:
            link = r["link"]
            if not link.startswith("/"):
                continue
            if link.endswith("/") and not agency.allow_extensionless_pages:
                link = link + "index.html"
            else:
                link = agencies.normalize_path(link)
            if link in index_set:
                continue
            try:
                ingest_pages.local_path(link)
            except ValueError:
                continue
            if link not in seen:
                seen.add(link)
                found.append(link)
        return found

    councils_obj = build_db.Councils(council_rows or [])
    owner = councils_obj.of(norm_url)
    owner_dir = (
        build_db.council_dir(owner["link"]).rstrip("/")
        if owner
        else build_db.council_dir(norm_url).rstrip("/")
    )

    for b in blocks:
        for ln in b.links:
            sp = urlsplit(ln.url)
            if sp.netloc != agency.primary_host or sp.query:
                continue
            raw_path = sp.path
            if raw_path.endswith("/") and not agency.allow_extensionless_pages:
                raw_path = raw_path + "index.html"
            if not agencies.is_html_path(raw_path, allow_extensionless=agency.allow_extensionless_pages):
                continue
            cand = agencies.normalize_path(raw_path)
            if cand in index_set or cand == norm_url:
                continue
            if owner:
                cand_owner = councils_obj.of(cand)
                if not cand_owner or cand_owner["council_id"] != owner["council_id"]:
                    continue
            else:
                if not cand.startswith(owner_dir + "/"):
                    continue
            if owner_dir and cand.startswith(owner_dir + "/"):
                rel_tail = cand[len(owner_dir) + 1:]
                for idx_suffix in ("/index.html", "/index.htm", "index.html", "index.htm"):
                    if rel_tail.endswith(idx_suffix):
                        rel_tail = rel_tail[:-len(idx_suffix)].rstrip("/")
                        break
                depth = len([p for p in rel_tail.split("/") if p])
                if depth > agency.max_depth:
                    continue
            try:
                ingest_pages.local_path(cand)
            except ValueError:
                continue
            if cand not in seen:
                seen.add(cand)
                found.append(cand)
    return found


def filter_urls(urls: list[str], *, council: str = "",
                councils_path: Path = build_db.DATA / "councils.csv") -> list[str]:
    """--council 指定時に該当会議体のページだけに絞る。"""
    if not council:
        return urls
    councils = build_db.Councils(build_db.load_councils(councils_path))
    matched = []
    for u in urls:
        c = councils.of(u)
        if c and c["council_id"] == council:
            matched.append(u)
    return matched


def check_pages(*, agency_id: str = "mod", manifest: Path | None = None,
                raw_dir: Path | None = None, councils_path: Path | None = None,
                council: str = "", limit: int = 0, max_new_pages: int = MAX_NEW_PAGES,
                discover_unfetched: bool = False, tier_mode: str = "all",
                recent_days: int = 60, dry_run: bool = False,
                interval: float = INTERVAL, fetcher=fetch_page) -> CheckResult:
    """台帳のページをチェックし、変更・新規ページを保存する。

    403・429 で遮断された場合はファイルや台帳を書き換えずに Blocked を送出する。
    """
    from datetime import timedelta

    ag = agencies.get_agency(agency_id)
    manifest = manifest if manifest is not None else ag.pages_manifest
    raw_dir = raw_dir if raw_dir is not None else ag.raw_pages_dir
    councils_path = councils_path if councils_path is not None else ag.councils_csv

    def call_fetcher(url: str, *, if_modified_since: str = "") -> FetchResponse:
        if fetcher is fetch_page and ag.base_url != BASE:
            return fetch_page(url, base_url=ag.base_url, if_modified_since=if_modified_since)
        return fetcher(url, if_modified_since=if_modified_since)

    rows = ingest_pages.read_manifest(manifest)
    # 既存の行に content_sha256 がなければ保存済みファイルから補っておく
    for u, r in rows.items():
        if not r.get("content_sha256"):
            p = pages.resolve_local_path(u, raw_dir)
            if p.exists():
                r["content_sha256"] = ingest_pages.content_hash(p.read_bytes())

    council_rows: list[dict[str, str]] = (
        build_db.load_councils(councils_path) if councils_path.exists() else []
    )
    if not council_rows and ag.agency_id != "mod":
        idx_loaded = []
        for idx_u in ag.index_urls:
            p = pages.resolve_local_path(idx_u, raw_dir)
            if p.exists():
                idx_loaded.append((idx_u, pages.load(idx_u, raw_dir, base_url=ag.base_url)))
        if idx_loaded:
            council_rows = agencies.extract_councils_generic(ag, idx_loaded)

    target_urls = filter_urls(sorted(rows), council=council, councils_path=councils_path)
    if tier_mode == "top_and_recent" and target_urls:
        top_set = set(ag.index_urls) | {agencies.normalize_path(u) for u in ag.index_urls}
        for r in council_rows:
            lk = r.get("link", "")
            if lk.startswith("/"):
                top_set.add(lk)
                top_set.add(agencies.normalize_path(lk))
                if lk.endswith("/"):
                    top_set.add(lk + "index.html")
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=recent_days)).isoformat(timespec="seconds")
        filtered = [
            u for u in target_urls
            if u in top_set or (rows[u].get("last_modified") or rows[u].get("fetched_at") or "") >= cutoff_iso
        ]
        if filtered:
            target_urls = filtered
    if limit:
        target_urls = target_urls[:limit]

    result = CheckResult()
    pending_writes: dict[str, bytes] = {}
    new_queue: deque[str] = deque()
    queued_or_known: set[str] = set(rows)
    first_request = True

    # 初回ブートストラップ（台帳が空）のときは一覧ページをシードにする
    councils_obj = build_db.Councils(council_rows) if council_rows else None
    if not rows:
        for idx_u in ag.index_urls:
            if idx_u not in queued_or_known:
                queued_or_known.add(idx_u)
                new_queue.append(idx_u)
    elif council and not target_urls and council_rows:
        for r in council_rows:
            if r["council_id"] == council and r["link"].startswith("/"):
                lk = r["link"] + "index.html" if (r["link"].endswith("/") and not ag.allow_extensionless_pages) else agencies.normalize_path(r["link"])
                if lk not in queued_or_known:
                    queued_or_known.add(lk)
                    new_queue.append(lk)
    elif discover_unfetched:
        idx_set = set(ag.index_urls) | {agencies.normalize_path(u) for u in ag.index_urls}
        ordered_existing = [u for u in ag.index_urls if u in rows] + [
            u for u in sorted(rows) if u not in idx_set
        ]
        for u in ordered_existing:
            p = pages.resolve_local_path(u, raw_dir)
            if p.exists():
                for link_path in discover_html_links(u, p.read_bytes(), agency=ag, council_rows=council_rows):
                    if council and councils_obj:
                        c = councils_obj.of(link_path)
                        if not c or c["council_id"] != council:
                            continue
                    if link_path not in queued_or_known:
                        queued_or_known.add(link_path)
                        new_queue.append(link_path)

    def pace() -> None:
        nonlocal first_request
        if not first_request and interval > 0:
            time.sleep(interval)
        first_request = False

    # 1. 台帳の全対象ページをチェック
    for url in target_urls:
        old = rows[url]
        pace()
        resp = call_fetcher(url, if_modified_since=old.get("last_modified", ""))
        if resp.status == 304:
            result.checked += 1
            result.unchanged += 1
            if old.get("missing_count") or old.get("status") != "200":
                old["status"] = "200"
                old["missing_count"] = ""
                result.manifest_dirty = True
            continue

        if resp.status == 200 and resp.body is not None:
            result.checked += 1
            new_content_sha = ingest_pages.content_hash(resp.body)
            old_content_sha = old.get("content_sha256", "")
            if old_content_sha and new_content_sha == old_content_sha:
                result.unchanged += 1
                changed_meta = False
                if resp.last_modified and resp.last_modified != old.get("last_modified", ""):
                    old["last_modified"] = resp.last_modified
                    old["fetched_at"] = resp.fetched_at
                    changed_meta = True
                if old.get("missing_count") or old.get("status") != "200":
                    old["status"] = "200"
                    old["missing_count"] = ""
                    changed_meta = True
                if changed_meta:
                    result.metadata_only.append(url)
                    result.manifest_dirty = True
            else:
                pending_writes[url] = resp.body
                rows[url] = {
                    "url": url,
                    "status": "200",
                    "last_modified": resp.last_modified or old.get("last_modified", ""),
                    "fetched_at": resp.fetched_at,
                    "bytes": str(len(resp.body)),
                    "sha256": hashlib.sha256(resp.body).hexdigest(),
                    "content_sha256": new_content_sha,
                    "missing_count": "",
                }
                result.changed_pages.append(url)
                result.manifest_dirty = True
                for link_path in discover_html_links(url, resp.body, agency=ag, council_rows=council_rows):
                    if link_path not in queued_or_known:
                        queued_or_known.add(link_path)
                        new_queue.append(link_path)
            continue

        if resp.status in (404, 410):
            result.checked += 1
            prev_missing = int(old.get("missing_count") or "0")
            if old.get("status") in ("404", "410") and prev_missing >= 2:
                # すでに消失として確定済み。毎回の commit を防ぐため台帳は書き換えない
                continue
            new_missing = prev_missing + 1
            if new_missing >= 2:
                old["status"] = str(resp.status)
                old["missing_count"] = "2"
                old["fetched_at"] = resp.fetched_at
                result.gone_pages.append(url)
                result.manifest_dirty = True
            else:
                old["missing_count"] = "1"
                result.missing_candidates.append(url)
                result.manifest_dirty = True
            continue

        # 5xx / タイムアウト / 通信エラー: 消失扱いにはしない
        result.errors.append((url, resp.error or f"HTTP {resp.status}"))

    if result.checked == 0 and not new_queue:
        raise NoPagesChecked("チェックできたページが 0 件です")

    # 2. 変更ページ（または初回一覧シード）から見つかった未収録の HTML を取得（上限 max_new_pages）
    while new_queue and len(result.new_pages) < max_new_pages:
        new_url = new_queue.popleft()
        pace()
        resp = call_fetcher(new_url, if_modified_since="")
        if resp.status == 200 and resp.body is not None:
            pending_writes[new_url] = resp.body
            rows[new_url] = {
                "url": new_url,
                "status": "200",
                "last_modified": resp.last_modified,
                "fetched_at": resp.fetched_at,
                "bytes": str(len(resp.body)),
                "sha256": hashlib.sha256(resp.body).hexdigest(),
                "content_sha256": ingest_pages.content_hash(resp.body),
                "missing_count": "",
            }
            result.new_pages.append(new_url)
            result.manifest_dirty = True
            for link_path in discover_html_links(new_url, resp.body, agency=ag, council_rows=council_rows):
                if link_path not in queued_or_known:
                    queued_or_known.add(link_path)
                    new_queue.append(link_path)
        else:
            result.errors.append((new_url, resp.error or f"HTTP {resp.status}"))

    if result.checked == 0 and not result.new_pages:
        raise NoPagesChecked("チェックできたページが 0 件です")

    # 3. ここまで遮断されずに完了したらディスクに反映する
    if not dry_run:
        for url, body in pending_writes.items():
            path = ingest_pages.local_path(url, raw_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        if result.manifest_dirty:
            ingest_pages.write_manifest(rows, manifest)

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agency", default="mod", help="対象の省庁 ID（既定: mod）")
    ap.add_argument("--dry-run", action="store_true", help="ファイルや台帳を書き換えない")
    ap.add_argument("--council", default="", help="対象の会議体 ID を絞る")
    ap.add_argument("--limit", type=int, default=0, help="チェックする既存ページ数の上限（0 は無制限）")
    ap.add_argument("--max-new-pages", type=int, default=MAX_NEW_PAGES, help="1 回に取得する新規ページの上限")
    ap.add_argument("--discover-unfetched", action="store_true",
                    help="保存済みページ内の未取得リンクも新規取得キューに加える")
    ap.add_argument("--tier-mode", choices=("all", "top_and_recent"), default="all",
                    help="2段階チェック（top_and_recent: 一覧・会議体トップ＋直近更新ページのみ巡回）")
    ap.add_argument("--recent-days", type=int, default=60,
                    help="--tier-mode top_and_recent 時に巡回する直近日数（既定: 60）")
    ap.add_argument("--interval", type=float, default=INTERVAL, help="リクエスト間の待機秒数")
    args = ap.parse_args()

    try:
        res = check_pages(
            agency_id=args.agency,
            council=args.council,
            limit=args.limit,
            max_new_pages=args.max_new_pages,
            discover_unfetched=args.discover_unfetched,
            tier_mode=args.tier_mode,
            recent_days=args.recent_days,
            dry_run=args.dry_run,
            interval=args.interval,
        )
    except Blocked as e:
        print(f"遮断により中断（未確認）: {e}", file=sys.stderr)
        return 1
    except NoPagesChecked as e:
        print(f"失敗: {e}", file=sys.stderr)
        return 1

    tag = "（dry-run）" if args.dry_run else ""
    print(
        f"[{args.agency}] チェック {res.checked} 件 / 変更 {len(res.changed_pages)} 件 / "
        f"新規 {len(res.new_pages)} 件 / 消失 {len(res.gone_pages)} 件 / "
        f"消失候補 {len(res.missing_candidates)} 件 / メタのみ更新 {len(res.metadata_only)} 件 / "
        f"エラー {len(res.errors)} 件{tag}"
    )
    for u in res.changed_pages:
        print(f"  [変更] {u}")
    for u in res.new_pages:
        print(f"  [新規] {u}")
    for u in res.gone_pages:
        print(f"  [消失] {u}")
    for u in res.missing_candidates:
        print(f"  [消失候補] {u}")
    for u, err in res.errors:
        print(f"  [エラー] {u}: {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
