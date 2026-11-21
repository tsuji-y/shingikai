#!/usr/bin/env python3
"""www.mod.go.jp の HTML（通常・条件付き）・PDF・RSS に 1 件ずつアクセスし、疎通結果を表示する。

Phase 0 の疎通確認用。直列で 2 秒以上の間隔をあけてアクセスし、403・429 のときはリトライしない。
HTML の通常取得が 403・429 またはボット検証（cf-mitigated）になった場合、条件付き取得は送らない。

使い方:
    python3 mod/check_access.py
    python3 mod/check_access.py --markdown
    python3 mod/check_access.py --json
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_files  # noqa: E402
import ingest_pages  # noqa: E402

BASE = "https://www.mod.go.jp"
DEFAULT_HTML_PATH = "/j/policy/agenda/meeting/index.html"
DEFAULT_PDF_URL = f"{BASE}/j/policy/agenda/meeting/blood-transfusion/pdf/gijiyoushi01.pdf"
DEFAULT_RSS_URL = f"{BASE}/j/rss/update.xml"
USER_AGENT = "rss-poller/1.0"
INTERVAL = 2.0
TIMEOUT = 30
MAX_READ = 2 * 1024 * 1024


@dataclass
class ProbeResult:
    kind: str
    url: str
    if_modified_since: str
    status: str
    cf_mitigated: str
    last_modified: str
    content_type: str
    bytes: int | None
    has_cf_script: bool | None
    skipped: bool
    error: str
    checked_at: str


def probe_once(kind: str, url: str, *, user_agent: str = USER_AGENT,
               if_modified_since: str = "", timeout: int = TIMEOUT,
               opener=urllib.request.urlopen) -> ProbeResult:
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    headers = {"User-Agent": user_agent}
    if if_modified_since:
        headers["If-Modified-Since"] = if_modified_since
    req = urllib.request.Request(url, headers=headers)
    try:
        with opener(req, timeout=timeout) as resp:
            body = resp.read(MAX_READ)
            cf = resp.headers.get("cf-mitigated", "")
            lm = resp.headers.get("Last-Modified", "")
            ct = resp.headers.get("Content-Type", "")
            has_script = ingest_pages.has_cf_script(body) if kind.startswith("html") else None
            return ProbeResult(
                kind=kind,
                url=url,
                if_modified_since=if_modified_since,
                status=str(resp.status),
                cf_mitigated=cf,
                last_modified=lm,
                content_type=ct,
                bytes=len(body),
                has_cf_script=has_script,
                skipped=False,
                error="",
                checked_at=checked_at,
            )
    except urllib.error.HTTPError as e:
        cf = e.headers.get("cf-mitigated", "") if e.headers else ""
        lm = e.headers.get("Last-Modified", "") if e.headers else ""
        ct = e.headers.get("Content-Type", "") if e.headers else ""
        return ProbeResult(
            kind=kind,
            url=url,
            if_modified_since=if_modified_since,
            status=str(e.code),
            cf_mitigated=cf,
            last_modified=lm,
            content_type=ct,
            bytes=0 if e.code == 304 else None,
            has_cf_script=None,
            skipped=False,
            error="" if e.code == 304 else (e.reason or ""),
            checked_at=checked_at,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return ProbeResult(
            kind=kind,
            url=url,
            if_modified_since=if_modified_since,
            status="error",
            cf_mitigated="",
            last_modified="",
            content_type="",
            bytes=None,
            has_cf_script=None,
            skipped=False,
            error=str(getattr(e, "reason", e)),
            checked_at=checked_at,
        )


def run_probes(*, html_path: str = DEFAULT_HTML_PATH, pdf_url: str = DEFAULT_PDF_URL,
               rss_url: str = DEFAULT_RSS_URL, manifest: Path = ingest_pages.MANIFEST,
               user_agent: str = USER_AGENT, interval: float = INTERVAL,
               opener=urllib.request.urlopen) -> list[ProbeResult]:
    """HTML（通常・条件付き）・PDF・RSS の順に 1 件ずつ確認する。"""
    rows = ingest_pages.read_manifest(manifest)
    saved_lm = rows.get(html_path, {}).get("last_modified", "")
    ims = fetch_files.iso_to_http_date(saved_lm) if saved_lm else ""
    html_url = BASE + html_path

    results: list[ProbeResult] = []

    # 1. HTML 通常取得
    r_html = probe_once("html", html_url, user_agent=user_agent, opener=opener)
    results.append(r_html)

    # 2. HTML 条件付き取得（通常取得が 403/429 または cf-mitigated ならスキップ）
    blocked_html = r_html.status in ("403", "429") or bool(r_html.cf_mitigated)
    cond_ims = ims or r_html.last_modified
    if blocked_html:
        results.append(ProbeResult(
            kind="html_conditional",
            url=html_url,
            if_modified_since=cond_ims,
            status="skipped",
            cf_mitigated="",
            last_modified="",
            content_type="",
            bytes=None,
            has_cf_script=None,
            skipped=True,
            error="HTML 通常取得が遮断されたため未実行",
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ))
    else:
        if interval > 0:
            time.sleep(interval)
        r_cond = probe_once("html_conditional", html_url, user_agent=user_agent,
                            if_modified_since=cond_ims, opener=opener)
        results.append(r_cond)

    # 3. PDF
    if interval > 0:
        time.sleep(interval)
    results.append(probe_once("pdf", pdf_url, user_agent=user_agent, opener=opener))

    # 4. RSS
    if interval > 0:
        time.sleep(interval)
    results.append(probe_once("rss", rss_url, user_agent=user_agent, opener=opener))

    return results


def format_markdown(results: list[ProbeResult], user_agent: str = USER_AGENT) -> str:
    lines = [
        "### www.mod.go.jp 疎通確認",
        "",
        f"- 実行日時（UTC）: `{results[0].checked_at if results else ''}`",
        f"- Python: `{platform.python_version()}` / OS: `{platform.system()} {platform.release()}`",
        f"- User-Agent: `{user_agent}`",
        "",
        "| 対象 | status | cf-mitigated | bytes | CF script | Last-Modified / 備考 |",
        "|---|---|---|---:|---|---|",
    ]
    for r in results:
        b_str = "" if r.bytes is None else str(r.bytes)
        cf_s = "" if r.has_cf_script is None else ("yes" if r.has_cf_script else "no")
        note = r.last_modified or r.error
        lines.append(f"| `{r.kind}` | `{r.status}` | `{r.cf_mitigated}` | {b_str} | {cf_s} | {note} |")
    return "\n".join(lines) + "\n"


def format_plain(results: list[ProbeResult]) -> str:
    lines = []
    for r in results:
        parts = [f"{r.kind:<16}", f"status={r.status}"]
        if r.cf_mitigated:
            parts.append(f"cf-mitigated={r.cf_mitigated}")
        if r.bytes is not None:
            parts.append(f"bytes={r.bytes}")
        if r.has_cf_script is not None:
            parts.append(f"cf_script={r.has_cf_script}")
        if r.last_modified:
            parts.append(f"last_modified={r.last_modified}")
        if r.error:
            parts.append(f"note={r.error}")
        lines.append("  ".join(parts))
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--user-agent", default=USER_AGENT, help="送信する User-Agent（自己申告名）")
    ap.add_argument("--interval", type=float, default=INTERVAL, help="リクエスト間の待機秒数（2 秒以上推奨）")
    ap.add_argument("--json", action="store_true", help="JSON 形式で出力する")
    ap.add_argument("--markdown", action="store_true", help="Markdown の表で出力する")
    ap.add_argument("--require-html", action="store_true", help="HTML が 200 でなければ終了コード 1 を返す")
    args = ap.parse_args()

    results = run_probes(user_agent=args.user_agent, interval=args.interval)
    if args.json:
        print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))
    elif args.markdown:
        print(format_markdown(results, user_agent=args.user_agent), end="")
    else:
        print(format_plain(results), end="")

    if args.require_html:
        html_ok = any(r.kind == "html" and r.status == "200" and not r.cf_mitigated for r in results)
        if not html_ok:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
