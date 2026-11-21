#!/usr/bin/env python3
"""表の差分からイベントを作り、回帰の検査・名簿履歴の退避・RSS 取り込み漏れ検査を行う。

docs/update_plan.md 3.7・3.8・3.10 節に対応。
イベントはすべて表の差分から機械的に生成する。
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_db  # noqa: E402
import fetch_files  # noqa: E402
import ingest_pages  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "mod"
EVENTS_CSV = DATA / "events.csv"
MEMBERS_HISTORY_CSV = DATA / "members_history.csv"

EVENT_FIELDS = ["detected_at", "event_type", "council_id", "meeting_id", "target_key", "before", "after", "source"]
MEMBER_HISTORY_FIELDS = [
    "recorded_at", "council_id", "body", "group", "as_of", "as_of_raw",
    "name", "kana", "mark", "role", "title", "note", "person_id", "source",
]


@dataclass
class Snapshot:
    councils: list[dict[str, str]] = field(default_factory=list)
    profiles: list[dict[str, str]] = field(default_factory=list)
    members: list[dict[str, str]] = field(default_factory=list)
    meetings: list[dict[str, str]] = field(default_factory=list)
    documents: list[dict[str, str]] = field(default_factory=list)
    attendance: list[dict[str, str]] = field(default_factory=list)
    persons: list[dict[str, str]] = field(default_factory=list)
    pages: dict[str, dict[str, str]] = field(default_factory=dict)
    files: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class DisappearedRow:
    table: str
    key: str
    source: str
    explained: bool
    reason: str = ""


@dataclass
class RegressionReport:
    disappeared: list[DisappearedRow] = field(default_factory=list)

    @property
    def unexplained(self) -> list[DisappearedRow]:
        return [d for d in self.disappeared if not d.explained]

    @property
    def ok(self) -> bool:
        return len(self.unexplained) == 0


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _append_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        if not exists:
            writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fields})


def ensure_csv_header(path: Path, fields: list[str]) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()


def load_snapshot(data_dir: Path = DATA, pages_manifest: Path = ingest_pages.MANIFEST,
                  files_manifest: Path = fetch_files.MANIFEST) -> Snapshot:
    return Snapshot(
        councils=_read_csv(data_dir / "councils.csv"),
        profiles=_read_csv(data_dir / "council_profiles.csv"),
        members=_read_csv(data_dir / "members.csv"),
        meetings=_read_csv(data_dir / "meetings.csv"),
        documents=_read_csv(data_dir / "documents.csv"),
        attendance=_read_csv(data_dir / "attendance.csv"),
        persons=_read_csv(data_dir / "persons.csv"),
        pages=ingest_pages.read_manifest(pages_manifest),
        files=fetch_files.read_manifest(files_manifest),
    )


def _doc_key_of(row: dict[str, str]) -> str:
    return row.get("doc_key") or build_db.doc_key(row.get("url", ""))


def _member_map(rows: list[dict[str, str]]) -> dict[tuple[str, str, str, str, int], dict[str, str]]:
    counts: dict[tuple[str, str, str, str], int] = defaultdict(int)
    out: dict[tuple[str, str, str, str, int], dict[str, str]] = {}
    for r in rows:
        base = (r["council_id"], r.get("body", ""), r.get("group", ""), r["name"])
        idx = counts[base]
        counts[base] += 1
        out[(*base, idx)] = r
    return out


def _profile_map(rows: list[dict[str, str]]) -> dict[tuple[str, str, str, int], dict[str, str]]:
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    out: dict[tuple[str, str, str, int], dict[str, str]] = {}
    for r in rows:
        base = (r["council_id"], r["key"], r.get("source", ""))
        idx = counts[base]
        counts[base] += 1
        out[(*base, idx)] = r
    return out


def diff_snapshots(before: Snapshot, after: Snapshot, detected_at: str = "") -> list[dict[str, str]]:
    """更新前後のスナップショットを比較し、イベント行のリストを返す。"""
    if not detected_at:
        detected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    events: list[dict[str, str]] = []
    councils_obj = build_db.Councils(after.councils or before.councils)

    def add(event_type: str, council_id: str, meeting_id: str, target_key: str,
            old_val: str, new_val: str, source: str) -> None:
        events.append({
            "detected_at": detected_at,
            "event_type": event_type,
            "council_id": council_id,
            "meeting_id": meeting_id,
            "target_key": target_key,
            "before": old_val,
            "after": new_val,
            "source": source,
        })

    # 1. 会議体の追加・名称変更・区分変更・一覧からの削除
    index_source = build_db.ROOT + "index.html"
    old_c = {r["council_id"]: r for r in before.councils}
    new_c = {r["council_id"]: r for r in after.councils}
    for cid, r in new_c.items():
        if cid not in old_c:
            add("council_added", cid, "", cid, "", r["name"], index_source)
        else:
            if old_c[cid]["name"] != r["name"]:
                add("council_renamed", cid, "", cid, old_c[cid]["name"], r["name"], index_source)
            if old_c[cid]["section"] != r["section"]:
                add("council_section_changed", cid, "", cid, old_c[cid]["section"], r["section"], index_source)
    for cid, r in old_c.items():
        if cid not in new_c:
            add("council_removed", cid, "", cid, r["name"], "", index_source)

    # 2. ページの追加・消失
    for url, r in sorted(after.pages.items()):
        c = councils_obj.of(url)
        cid = c["council_id"] if c else ""
        if url not in before.pages:
            add("page_added", cid, "", url, "", r.get("status", "200"), url)
        else:
            old_st = before.pages[url].get("status", "")
            new_st = r.get("status", "")
            if old_st == "200" and new_st in ("404", "410"):
                add("page_gone", cid, "", url, old_st, new_st, url)

    # 3. 会議（回次）の追加
    old_m = {r["meeting_id"]: r for r in before.meetings}
    new_m = {r["meeting_id"]: r for r in after.meetings}
    for mid, r in new_m.items():
        if mid not in old_m:
            summary = " ".join(x for x in (r.get("label", ""), r.get("date", "")) if x).strip()
            add("meeting_added", r["council_id"], mid, mid, "", summary, r.get("source", ""))

    # 4. 資料の追加・WARP への移動
    old_docs: dict[tuple[str, str], dict[str, str]] = {}
    for d in before.documents:
        old_docs.setdefault((d["council_id"], _doc_key_of(d)), d)
    new_docs: dict[tuple[str, str], dict[str, str]] = {}
    for d in after.documents:
        new_docs.setdefault((d["council_id"], _doc_key_of(d)), d)

    for (cid, dk), d in new_docs.items():
        if (cid, dk) not in old_docs:
            val = f"{d.get('doc_type', '')}: {d.get('title', '')}".strip(": ")
            add("document_added", cid, d.get("meeting_id", ""), dk, "", val, d.get("source", ""))
        else:
            prev = old_docs[(cid, dk)]
            if prev.get("host") != "warp.da.ndl.go.jp" and d.get("host") == "warp.da.ndl.go.jp":
                add("document_moved_to_warp", cid, d.get("meeting_id", ""), dk,
                    prev.get("url", ""), d.get("url", ""), d.get("source", ""))

    # 5. 資料の差し替え（files.csv で同一 URL の SHA-256 が変わった）
    doc_by_url = {d["url"]: d for d in after.documents}
    for url, new_f in sorted(after.files.items()):
        old_f = before.files.get(url)
        if old_f and old_f.get("sha256") and new_f.get("sha256") and old_f["sha256"] != new_f["sha256"]:
            d = doc_by_url.get(url, {})
            cid = d.get("council_id", "")
            mid = d.get("meeting_id", "")
            src = d.get("source", "") or url
            add("document_replaced", cid, mid, build_db.doc_key(url), old_f["sha256"], new_f["sha256"], src)

    # 6. 委員名簿の変更（時点の変更と、個別の追加・削除・役職等の変更を分けて記録）
    def roster_as_of(rows: list[dict[str, str]]) -> dict[tuple[str, str, str], tuple[str, str, str]]:
        out: dict[tuple[str, str, str], tuple[str, str, str]] = {}
        for r in rows:
            key = (r["council_id"], r.get("body", ""), r.get("group", ""))
            if key not in out and (r.get("as_of") or r.get("as_of_raw")):
                out[key] = (r.get("as_of", ""), r.get("as_of_raw", ""), r.get("source", ""))
        return out

    old_as_of = roster_as_of(before.members)
    new_as_of = roster_as_of(after.members)
    for key in sorted(set(old_as_of) & set(new_as_of)):
        o_iso, o_raw, _ = old_as_of[key]
        n_iso, n_raw, src = new_as_of[key]
        if (o_iso, o_raw) != (n_iso, n_raw):
            cid, body, grp = key
            tkey = "/".join(x for x in (cid, body, grp) if x)
            add("roster_as_of_changed", cid, "", tkey, o_raw or o_iso, n_raw or n_iso, src)

    old_mem = _member_map(before.members)
    new_mem = _member_map(after.members)
    for key, r in new_mem.items():
        cid, body, grp, name, _ = key
        tkey = "/".join(x for x in (cid, body, grp, name) if x)
        if key not in old_mem:
            detail = " / ".join(x for x in (r.get("role", ""), r.get("title", "")) if x)
            add("member_added", cid, "", tkey, "", detail or name, r.get("source", ""))
        else:
            o = old_mem[key]
            changed_fields = []
            for f in ("mark", "role", "title", "note"):
                if o.get(f, "") != r.get(f, ""):
                    changed_fields.append((f, o.get(f, ""), r.get(f, "")))
            if changed_fields:
                o_str = "; ".join(f"{f}={ov}" for f, ov, _ in changed_fields)
                n_str = "; ".join(f"{f}={nv}" for f, _, nv in changed_fields)
                add("member_changed", cid, "", tkey, o_str, n_str, r.get("source", ""))
    for key, o in old_mem.items():
        if key not in new_mem:
            cid, body, grp, name, _ = key
            tkey = "/".join(x for x in (cid, body, grp, name) if x)
            detail = " / ".join(x for x in (o.get("role", ""), o.get("title", "")) if x)
            add("member_removed", cid, "", tkey, detail or name, "", o.get("source", ""))

    # 7. 会議体の概要（council_profiles.csv）の変更
    old_p = _profile_map(before.profiles)
    new_p = _profile_map(after.profiles)
    for key, r in new_p.items():
        cid, pkey, src, _ = key
        tkey = f"{cid}:{pkey}"
        if key not in old_p:
            add("profile_added", cid, "", tkey, "", r.get("value", ""), src)
        elif old_p[key].get("value", "") != r.get("value", ""):
            add("profile_changed", cid, "", tkey, old_p[key].get("value", ""), r.get("value", ""), src)
    for key, o in old_p.items():
        if key not in new_p:
            cid, pkey, src, _ = key
            tkey = f"{cid}:{pkey}"
            add("profile_removed", cid, "", tkey, o.get("value", ""), "", src)

    return events


def archive_members_history(before_members: list[dict[str, str]], after_members: list[dict[str, str]],
                            history_path: Path = MEMBERS_HISTORY_CSV,
                            recorded_at: str = "", write: bool = True) -> list[dict[str, str]]:
    """名簿が変わった (council_id, body) について、変更前の名簿を履歴に追加する。"""
    if not recorded_at:
        recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def group_by_roster(rows: list[dict[str, str]]) -> dict[tuple[str, str], list[dict[str, str]]]:
        grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for r in rows:
            grouped[(r["council_id"], r.get("body", ""))].append(r)
        return grouped

    def signature(rows: list[dict[str, str]]) -> list[tuple[str, ...]]:
        cols = ("group", "as_of", "as_of_raw", "name", "kana", "mark", "role", "title", "note")
        return [tuple(r.get(c, "") for c in cols) for r in rows]

    before_g = group_by_roster(before_members)
    after_g = group_by_roster(after_members)

    existing_history = _read_csv(history_path)
    existing_keys = {
        (r.get("council_id", ""), r.get("body", ""), r.get("group", ""), r.get("as_of", ""),
         r.get("name", ""), r.get("role", ""), r.get("title", ""))
        for r in existing_history
    }

    to_archive: list[dict[str, str]] = []
    for roster_key, old_rows in before_g.items():
        new_rows = after_g.get(roster_key, [])
        if signature(old_rows) == signature(new_rows):
            continue
        for r in old_rows:
            dedup_key = (r.get("council_id", ""), r.get("body", ""), r.get("group", ""), r.get("as_of", ""),
                         r.get("name", ""), r.get("role", ""), r.get("title", ""))
            if dedup_key in existing_keys:
                continue
            existing_keys.add(dedup_key)
            rec = dict(r)
            rec["recorded_at"] = recorded_at
            to_archive.append(rec)

    if write and to_archive:
        _append_csv(history_path, MEMBER_HISTORY_FIELDS, to_archive)
    return to_archive


def append_events(events: list[dict[str, str]], path: Path = EVENTS_CSV) -> None:
    if events:
        _append_csv(path, EVENT_FIELDS, events)


def check_regressions(before: Snapshot, after: Snapshot, changed_pages: set[str],
                      changed_doc_keys: set[str] | None = None) -> RegressionReport:
    """回帰の検査（docs/update_plan.md 3.8 節）。

    前回の表にあって今回ない行を列挙し、変更のあったページまたは doc_key の変更（および
    開催日判明による会議の統合）で説明できるかを確かめる。
    """
    changed_doc_keys = set(changed_doc_keys or ())
    report = RegressionReport()
    index_url = build_db.ROOT + "index.html"
    councils_obj = build_db.Councils(after.councils or before.councils)

    changed_councils: set[str] = set()
    for p in changed_pages:
        c = councils_obj.of(p)
        if c:
            changed_councils.add(c["council_id"])
    doc_councils: dict[str, set[str]] = defaultdict(set)
    for d in before.documents + after.documents:
        doc_councils[_doc_key_of(d)].add(d["council_id"])
    for dk in changed_doc_keys:
        changed_councils |= doc_councils.get(dk, set())

    # 1. councils.csv
    new_c_ids = {r["council_id"] for r in after.councils}
    for r in before.councils:
        if r["council_id"] not in new_c_ids:
            explained = index_url in changed_pages
            report.disappeared.append(DisappearedRow(
                "councils.csv", r["council_id"], index_url, explained,
                "一覧ページ変更" if explained else "一覧ページは未変更",
            ))

    # 2. council_profiles.csv
    new_profiles = set(_profile_map(after.profiles))
    for key, r in _profile_map(before.profiles).items():
        if key not in new_profiles:
            src = r.get("source", "")
            explained = src in changed_pages
            report.disappeared.append(DisappearedRow(
                "council_profiles.csv", f"{r['council_id']}:{r['key']}", src, explained,
                "変更ページ" if explained else "元ページは未変更",
            ))

    # 3. members.csv
    new_members = set(_member_map(after.members))
    for key, r in _member_map(before.members).items():
        if key not in new_members:
            src = r.get("source", "")
            explained = src in changed_pages
            report.disappeared.append(DisappearedRow(
                "members.csv", f"{r['council_id']}/{r['name']}", src, explained,
                "変更ページ" if explained else "元ページは未変更",
            ))

    # 4. meetings.csv
    new_m_ids = {r["meeting_id"] for r in after.meetings}
    new_numbered_by_date = {
        (r["council_id"], r.get("body", ""), r["date"]): r["meeting_id"]
        for r in after.meetings if r.get("number") != "" and r.get("date")
    }
    merged_meeting_ids: set[str] = set()
    old_meeting_sources = {r["meeting_id"]: set(r.get("source", "").split()) for r in before.meetings}
    for r in before.meetings:
        mid = r["meeting_id"]
        if mid not in new_m_ids:
            srcs = old_meeting_sources[mid]
            merged_to = (new_numbered_by_date.get((r["council_id"], r.get("body", ""), r["date"]))
                         if r.get("number") == "" and r.get("date") else None)
            if srcs & changed_pages:
                explained, reason = True, "変更ページ"
            elif merged_to and (changed_doc_keys or changed_pages):
                explained, reason = True, f"会議統合 ({merged_to})"
                merged_meeting_ids.add(mid)
            else:
                explained, reason = False, "元ページは未変更"
            report.disappeared.append(DisappearedRow("meetings.csv", mid, " ".join(sorted(srcs)), explained, reason))

    # 5. documents.csv (keyed by (council_id, doc_key) so WARP moves are NOT disappearances)
    new_doc_keys = {(d["council_id"], _doc_key_of(d)) for d in after.documents}
    for d in before.documents:
        dk = _doc_key_of(d)
        if (d["council_id"], dk) not in new_doc_keys:
            src = d.get("source", "")
            explained = src in changed_pages or dk in changed_doc_keys
            reason = "変更ページまたは doc_key 変更" if explained else "元ページは未変更"
            report.disappeared.append(DisappearedRow("documents.csv", f"{d['council_id']}:{dk}", src, explained, reason))

    # 6. attendance.csv
    new_att = {(a["meeting_id"], a.get("group", ""), a["name_raw"]) for a in after.attendance}
    for a in before.attendance:
        key = (a["meeting_id"], a.get("group", ""), a["name_raw"])
        if key not in new_att:
            src = a.get("source", "")
            src_dk = build_db.doc_key(src) if src.startswith("http") else ""
            src_path = src[len(build_db.pages.BASE):] if src.startswith(build_db.pages.BASE) else src
            meeting_srcs = old_meeting_sources.get(a["meeting_id"], set())
            if src_dk and src_dk in changed_doc_keys:
                explained, reason = True, "資料(doc_key)の変更"
            elif src_path in changed_pages or (meeting_srcs & changed_pages):
                explained, reason = True, "変更ページ"
            elif a["meeting_id"] in merged_meeting_ids:
                explained, reason = True, "会議統合"
            else:
                explained, reason = False, "元ページ・資料とも未変更"
            report.disappeared.append(DisappearedRow(
                "attendance.csv", f"{a['meeting_id']}:{a['name_raw']}", src, explained, reason,
            ))

    # 7. persons.csv
    new_persons = {p["person_id"] for p in after.persons}
    for p in before.persons:
        if p["person_id"] not in new_persons:
            p_councils = {c.strip() for c in p.get("councils", "").split(",") if c.strip()}
            explained = bool(p_councils & changed_councils)
            reason = "関連会議体の変更" if explained else "関連会議体は未変更"
            report.disappeared.append(DisappearedRow(
                "persons.csv", p["person_id"], p.get("councils", ""), explained, reason,
            ))

    return report


def find_stale_rss_pages(rss_path: Path = DATA / "rss_items.csv",
                         pages_manifest: Path = ingest_pages.MANIFEST, *,
                         now: datetime | None = None,
                         grace_days: int = 3) -> list[dict[str, str]]:
    """RSS の審議会ページ更新告知から grace_days 日以上たっても台帳に取り込まれていないページを探す。"""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=grace_days)
    pages_rows = ingest_pages.read_manifest(pages_manifest)
    rss_rows = _read_csv(rss_path)

    # ページごとに最新の告知（cutoff 以前のもの）を集める
    latest_by_link: dict[str, tuple[datetime, dict[str, str]]] = {}
    for r in rss_rows:
        link = r.get("link", "")
        if not link.startswith(build_db.ROOT):
            continue
        if not link.endswith((".html", ".htm")):
            continue
        try:
            pub_dt = datetime.fromisoformat(r["pub_date"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        if pub_dt > cutoff:
            continue
        prev = latest_by_link.get(link)
        if prev is None or pub_dt > prev[0]:
            latest_by_link[link] = (pub_dt, r)

    stale: list[dict[str, str]] = []
    for link, (pub_dt, r) in sorted(latest_by_link.items()):
        page_row = pages_rows.get(link)
        if page_row is None:
            stale.append({
                "link": link,
                "pub_date": r["pub_date"],
                "title": r.get("title", ""),
                "fetched_at": "",
                "reason": "台帳に未収録",
            })
            continue
        fetched_raw = page_row.get("fetched_at", "")
        try:
            fetched_dt = datetime.fromisoformat(fetched_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            fetched_dt = None
        if fetched_dt is None or fetched_dt < pub_dt:
            stale.append({
                "link": link,
                "pub_date": r["pub_date"],
                "title": r.get("title", ""),
                "fetched_at": fetched_raw,
                "reason": "告知後に未取得",
            })
    return stale


def format_issue_body(events: list[dict[str, str]], new_warnings: list[str] | None = None,
                      title_note: str = "") -> str:
    """変更通知用の Issue 本文（Markdown）を組み立てる。"""
    lines = ["## 検知した変更", ""]
    if title_note:
        lines.extend([title_note, ""])
    lines.append(f"- イベント件数: {len(events)} 件")
    by_type: dict[str, int] = defaultdict(int)
    for ev in events:
        by_type[ev["event_type"]] += 1
    if by_type:
        summary = ", ".join(f"`{k}`: {v}" for k, v in sorted(by_type.items()))
        lines.append(f"- 内訳: {summary}")
    lines.extend([
        "",
        "| 種類 | 会議体 | 会議 | 対象 | 変更前 | 変更後 |",
        "|---|---|---|---|---|---|",
    ])
    for ev in events[:100]:
        def esc(s: str) -> str:
            return s.replace("|", "\\|").replace("\n", " ")[:120]
        lines.append(
            f"| `{ev['event_type']}` | `{ev['council_id']}` | `{ev['meeting_id']}` | "
            f"{esc(ev['target_key'])} | {esc(ev['before'])} | {esc(ev['after'])} |"
        )
    if len(events) > 100:
        lines.append(f"\n（ほか {len(events) - 100} 件。全件は `data/mod/events.csv` を参照）")
    if new_warnings:
        lines.extend(["", "## 新たに増えた build_db の警告", ""])
        for w in new_warnings:
            lines.append(f"- {w}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check-stale-rss", action="store_true",
                    help="RSS 告知から一定日数たっても未取り込みのページを表示する")
    ap.add_argument("--grace-days", type=int, default=3, help="取り込み漏れの猶予日数（既定 3 日）")
    ap.add_argument("--markdown", action="store_true", help="Markdown 形式で出力する")
    args = ap.parse_args()

    if args.check_stale_rss:
        stale = find_stale_rss_pages(grace_days=args.grace_days)
        if not stale:
            print("取り込み漏れなし")
            return 0
        if args.markdown:
            print(f"## RSS 告知から {args.grace_days} 日以上未取り込みの審議会ページ（{len(stale)} 件）\n")
            print("| ページ | 告知日時 | 最終取得日時 | 理由 | 件名 |")
            print("|---|---|---|---|---|")
            for s in stale:
                print(f"| `{s['link']}` | {s['pub_date']} | {s['fetched_at']} | {s['reason']} | {s['title']} |")
        else:
            print(f"未取り込み {len(stale)} 件:")
            for s in stale:
                print(f"  {s['link']} (告知={s['pub_date']}, 取得={s['fetched_at'] or 'なし'}, {s['reason']})")
        return 2
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
