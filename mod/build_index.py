#!/usr/bin/env python3
"""審議会データベースの統一検索索引（省庁別 catalog.csv / catalog.jsonl と横断 data/all/index.json）を生成する。

各省庁の councils.csv・meetings.csv・documents.csv・texts.csv・raw/files.csv・raw/subpages.csv を
1 つの正規化ビューへ結合し、議事録テキスト・配布資料の検索と GitHub Releases 原本取得を 1 ファイルで完結させる。

使い方:
    python3 mod/build_index.py --agency digital
    python3 mod/build_index.py --agency all
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agencies  # noqa: E402
import build_db  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

CATALOG_FIELDS = [
    "agency_id", "agency_name", "council_id", "council_name",
    "meeting_id", "meeting_date", "meeting_label",
    "doc_type", "title", "format", "host", "url",
    "fetched", "status", "sha256", "bytes",
    "release_url", "text_path", "text_release_url", "text_chars", "text_pages",
    "note", "source_page",
]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def build_agency_catalog(ag: agencies.AgencyConfig, *, write: bool = True) -> list[dict[str, str]]:
    """1 省庁分の統一索引（catalog.csv / catalog.jsonl）を構築して書き出す。"""
    councils_by_id = {r["council_id"]: r for r in _read_csv(ag.councils_csv)}
    meetings_by_id = {r["meeting_id"]: r for r in _read_csv(ag.data_dir / "meetings.csv")}
    documents = _read_csv(ag.data_dir / "documents.csv")
    texts_by_key = {build_db.doc_key(r["url"]): r for r in _read_csv(ag.data_dir / "texts.csv")}
    files_by_url = {r["url"]: r for r in _read_csv(ag.files_manifest)}
    subpages_by_url = {r["url"]: r for r in _read_csv(ag.subpages_manifest)}

    rows: list[dict[str, str]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for d in documents:
        url = (d.get("url") or "").strip()
        if not url:
            continue
        cid = d.get("council_id", "")
        mid = d.get("meeting_id", "")
        dedup_key = (cid, mid, url)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        c = councils_by_id.get(cid, {})
        m = meetings_by_id.get(mid, {})
        dk = d.get("doc_key") or build_db.doc_key(url)
        t = texts_by_key.get(dk, {})
        f_row = files_by_url.get(url) or subpages_by_url.get(url) or {}

        sha = f_row.get("sha256") or t.get("sha256") or ""
        text_path = d.get("text") or t.get("text") or f_row.get("text") or ""
        fetched = "true" if (sha or text_path) else "false"
        note = " / ".join(x for x in (f_row.get("note", ""), t.get("note", "")) if x)

        rows.append({
            "agency_id": ag.agency_id,
            "agency_name": ag.name,
            "council_id": cid,
            "council_name": c.get("name", ""),
            "meeting_id": mid,
            "meeting_date": m.get("date", ""),
            "meeting_label": m.get("label", ""),
            "doc_type": d.get("doc_type", ""),
            "title": d.get("title", ""),
            "format": d.get("format", ""),
            "host": d.get("host", "") or urlsplit(url).netloc,
            "url": url,
            "fetched": fetched,
            "status": f_row.get("status", ""),
            "sha256": sha,
            "bytes": f_row.get("bytes", ""),
            "release_url": f_row.get("release_url", ""),
            "text_path": text_path,
            "text_release_url": t.get("release_url", ""),
            "text_chars": d.get("chars") or t.get("chars") or "",
            "text_pages": d.get("pages") or t.get("pages") or "",
            "note": note,
            "source_page": d.get("source", ""),
        })

    # documents.csv に直接乗っていない取得済み下位 HTML も索引に含める
    indexed_urls = {r["url"] for r in rows}
    for url, sp_row in sorted(subpages_by_url.items()):
        if url in indexed_urls or not sp_row.get("sha256"):
            continue
        cid = sp_row.get("council_id", "")
        mid = sp_row.get("meeting_id", "")
        c = councils_by_id.get(cid, {})
        m = meetings_by_id.get(mid, {})
        t = texts_by_key.get(build_db.doc_key(url), {})
        rows.append({
            "agency_id": ag.agency_id,
            "agency_name": ag.name,
            "council_id": cid,
            "council_name": c.get("name", ""),
            "meeting_id": mid,
            "meeting_date": sp_row.get("meeting_date") or m.get("date", ""),
            "meeting_label": m.get("label", ""),
            "doc_type": sp_row.get("doc_type") or "下位HTML",
            "title": "",
            "format": "html",
            "host": urlsplit(url).netloc,
            "url": url,
            "fetched": "true",
            "status": sp_row.get("status", ""),
            "sha256": sp_row.get("sha256", ""),
            "bytes": sp_row.get("bytes", ""),
            "release_url": sp_row.get("release_url", ""),
            "text_path": sp_row.get("text") or t.get("text", ""),
            "text_release_url": t.get("release_url", ""),
            "text_chars": t.get("chars", ""),
            "text_pages": t.get("pages", "1"),
            "note": sp_row.get("note") or t.get("note", ""),
            "source_page": "",
        })

    rows.sort(key=lambda r: (r["council_id"], r["meeting_date"], r["meeting_id"], r["doc_type"], r["url"]))

    if write:
        ag.data_dir.mkdir(parents=True, exist_ok=True)
        build_db.write_csv(ag.data_dir / "catalog.csv", CATALOG_FIELDS, rows)
        jsonl_path = ag.data_dir / "catalog.jsonl"
        tmp = jsonl_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        tmp.replace(jsonl_path)

    return rows


def build_all_index(repo: Path = REPO, *, rebuild_catalogs: bool = False, write: bool = True) -> dict:
    """全 16 省庁の収録状況・索引ファイルパス・カバレッジをまとめた data/all/index.json を生成する。"""
    all_cfgs = agencies.load_agencies(repo / "agencies.json", repo=repo)
    agencies_meta: list[dict] = []
    totals = {
        "councils": 0,
        "meetings": 0,
        "documents": 0,
        "pdfs_target": 0,
        "pdfs_fetched": 0,
        "pdfs_archived": 0,
        "subpages_fetched": 0,
        "subpages_archived": 0,
        "texts": 0,
        "texts_archived": 0,
    }

    for aid, ag in all_cfgs.items():
        if rebuild_catalogs and ag.data_dir.exists():
            build_agency_catalog(ag, write=write)

        councils = _read_csv(ag.councils_csv)
        meetings = _read_csv(ag.data_dir / "meetings.csv")
        documents = _read_csv(ag.data_dir / "documents.csv")
        files_rows = _read_csv(ag.files_manifest)
        subpages_rows = _read_csv(ag.subpages_manifest)
        texts_rows = _read_csv(ag.data_dir / "texts.csv")
        pages_rows = _read_csv(ag.pages_manifest)

        allowed = set(ag.hosts)
        pdf_target_urls = {
            r["url"] for r in documents
            if r.get("format") == "pdf" and r.get("host") in allowed
        }
        pdfs_fetched = sum(1 for r in files_rows if r.get("sha256"))
        pdfs_archived = sum(1 for r in files_rows if r.get("sha256") and r.get("release_url"))
        sub_fetched = sum(1 for r in subpages_rows if r.get("sha256"))
        sub_archived = sum(1 for r in subpages_rows if r.get("sha256") and r.get("release_url"))
        texts_ok = sum(1 for r in texts_rows if r.get("text"))
        texts_archived = sum(1 for r in texts_rows if r.get("text") and r.get("release_url"))
        last_fetched = max(
            [r.get("fetched_at", "") for r in pages_rows]
            + [r.get("fetched_at", "") for r in files_rows]
            + [r.get("fetched_at", "") for r in subpages_rows],
            default="",
        )

        entry = {
            "agency_id": aid,
            "name": ag.name,
            "tier": ag.tier,
            "base_url": ag.base_url,
            "release_tag_prefix": ag.release_tag_prefix,
            "html_release_tag_prefix": ag.html_release_tag_prefix,
            "text_release_tag_prefix": ag.text_release_tag_prefix,
            "counts": {
                "councils": len(councils),
                "meetings": len(meetings),
                "documents": len(documents),
                "pdfs_target": len(pdf_target_urls),
                "pdfs_fetched": pdfs_fetched,
                "pdfs_archived": pdfs_archived,
                "pdf_coverage_pct": round(100.0 * pdfs_fetched / len(pdf_target_urls), 1) if pdf_target_urls else 100.0,
                "pages": sum(1 for r in pages_rows if r.get("status") == "200"),
                "subpages_fetched": sub_fetched,
                "subpages_archived": sub_archived,
                "texts": texts_ok,
                "texts_archived": texts_archived,
            },
            "last_fetched_at": last_fetched,
            "files": {
                "catalog_csv": f"data/{aid}/catalog.csv",
                "catalog_jsonl": f"data/{aid}/catalog.jsonl",
                "councils_csv": f"data/{aid}/councils.csv",
                "meetings_csv": f"data/{aid}/meetings.csv",
                "documents_csv": f"data/{aid}/documents.csv",
                "texts_csv": f"data/{aid}/texts.csv",
                "files_manifest": f"raw/{aid}/files.csv",
                "subpages_manifest": f"raw/{aid}/subpages.csv",
            },
        }
        agencies_meta.append(entry)
        totals["councils"] += len(councils)
        totals["meetings"] += len(meetings)
        totals["documents"] += len(documents)
        totals["pdfs_target"] += len(pdf_target_urls)
        totals["pdfs_fetched"] += pdfs_fetched
        totals["pdfs_archived"] += pdfs_archived
        totals["subpages_fetched"] += sub_fetched
        totals["subpages_archived"] += sub_archived
        totals["texts"] += texts_ok
        totals["texts_archived"] += texts_archived

    totals["pdf_coverage_pct"] = (
        round(100.0 * totals["pdfs_fetched"] / totals["pdfs_target"], 1) if totals["pdfs_target"] else 100.0
    )

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "storage_policy": {
            "indexes_and_structured_tables": "Git repository (data/<agency>/*.csv, catalog.csv, catalog.jsonl, parsed_texts.json, data/all/index.json)",
            "extracted_texts": "GitHub Releases (<agency>-texts-YYYY-MM / backfill tar.gz shards), linked via text_release_url (mod keeps data/mod/text/ in Git)",
            "pdf_originals": "GitHub Releases (<agency>-pdfs-YYYY-MM / backfill tar.gz shards), linked via release_url",
            "subpage_html_originals": "GitHub Releases (<agency>-html-YYYY-MM / backfill tar.gz shards), linked via release_url",
            "warp_policy": "Excluded from automated fetching (links preserved in documents.csv / catalog)",
        },
        "catalog_fields": CATALOG_FIELDS,
        "totals": totals,
        "agencies": agencies_meta,
    }

    if write:
        out = repo / "data" / "all" / "index.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        tmp.replace(out)

    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agency", default="all", help="対象の省庁 ID（all なら全省庁＋横断 index.json）")
    ap.add_argument("--dry-run", action="store_true", help="ファイルを書き出さない")
    args = ap.parse_args()

    if args.agency == "all":
        payload = build_all_index(rebuild_catalogs=True, write=not args.dry_run)
        t = payload["totals"]
        print(
            f"[all] 索引生成完了: 資料 {t['documents']} 件 / PDF {t['pdfs_fetched']}/{t['pdfs_target']} "
            f"({t['pdf_coverage_pct']}%) / 下位HTML {t['subpages_fetched']} 件 / テキスト {t['texts']} 件",
            flush=True,
        )
    else:
        ag = agencies.get_agency(args.agency)
        rows = build_agency_catalog(ag, write=not args.dry_run)
        print(f"[{ag.agency_id}] catalog 生成完了: {len(rows)} 件", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
