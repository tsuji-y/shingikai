#!/usr/bin/env python3
"""全省庁（data/<agency>/）の表を横断集計し、data/all/ の 3 表を生成する。

出力する表:
- data/all/agencies.csv      : 省庁別の収録件数サマリ（会議体・会議・資料・委員・人物・HTML・PDF）
- data/all/persons_cross.csv : 省庁横断の人物名寄せ表（複数省庁の審議会を兼任する有識者等を一覧化）
- data/all/recent_events.csv : 全省庁の直近更新イベント一覧

使い方:
    python3 mod/cross_agency.py
    python3 mod/cross_agency.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agencies  # noqa: E402
import build_db  # noqa: E402
import build_index  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
ALL_DIR = REPO / "data" / "all"

AGENCY_SUMMARY_FIELDS = [
    "agency_id", "name", "tier", "stage", "base_url",
    "councils", "meetings", "documents", "members", "persons",
    "pages", "pdfs", "last_fetched_at", "pdfs_target", "subpages", "texts",
]
PERSON_CROSS_FIELDS = [
    "person_id", "name", "agency_count", "agencies",
    "council_count", "councils", "roles", "affiliations",
    "n_meetings",
]
RECENT_EVENT_FIELDS = [
    "detected_at", "agency_id", "event_type", "council_id",
    "meeting_id", "target_key", "before", "after", "source",
]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def build_cross_tables(repo: Path = REPO, *, max_events: int = 1000,
                       write: bool = True) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    all_cfgs = agencies.load_agencies(repo / "agencies.json", repo=repo)
    agency_rows: list[dict[str, str]] = []
    person_by_id: dict[str, dict] = {}
    all_events: list[dict[str, str]] = []

    for aid, ag in all_cfgs.items():
        councils = _read_csv(ag.data_dir / "councils.csv")
        meetings = _read_csv(ag.data_dir / "meetings.csv")
        documents = _read_csv(ag.data_dir / "documents.csv")
        members = _read_csv(ag.data_dir / "members.csv")
        persons_rows = _read_csv(ag.data_dir / "persons.csv")
        pages_rows = _read_csv(ag.pages_manifest)
        files_rows = _read_csv(ag.files_manifest)
        subpages_rows = _read_csv(ag.subpages_manifest)
        texts_rows = _read_csv(ag.data_dir / "texts.csv")
        events_rows = _read_csv(ag.data_dir / "events.csv")

        ok_pages = [r for r in pages_rows if r.get("status") == "200"]
        ok_pdfs = [r for r in files_rows if r.get("sha256")]
        ok_subpages = [r for r in subpages_rows if r.get("sha256")]
        ok_texts = [r for r in texts_rows if r.get("text")]
        allowed = set(ag.hosts)
        pdfs_target = len({r["url"] for r in documents if r.get("format") == "pdf" and r.get("host") in allowed})
        last_fetched = max(
            [r.get("fetched_at", "") for r in pages_rows]
            + [r.get("fetched_at", "") for r in files_rows]
            + [r.get("fetched_at", "") for r in subpages_rows],
            default="",
        )

        agency_rows.append({
            "agency_id": aid,
            "name": ag.name,
            "tier": ag.tier,
            "stage": ag.stage,
            "base_url": ag.base_url,
            "councils": str(len(councils)),
            "meetings": str(len(meetings)),
            "documents": str(len(documents)),
            "members": str(len(members)),
            "persons": str(len(persons_rows)),
            "pages": str(len(ok_pages)),
            "pdfs": str(len(ok_pdfs)),
            "last_fetched_at": last_fetched,
            "pdfs_target": str(pdfs_target),
            "subpages": str(len(ok_subpages)),
            "texts": str(len(ok_texts)),
        })

        for p in persons_rows:
            pid = p["person_id"]
            if p.get("basis") == "surname_only":
                pid = f"{aid}:{pid}"
            entry = person_by_id.get(pid)
            if entry is None:
                entry = {
                    "person_id": pid,
                    "name": p["name"],
                    "agencies": [],
                    "councils": [],
                    "roles": [],
                    "affiliations": [],
                    "n_meetings": 0,
                }
                person_by_id[pid] = entry
            if " " in p["name"] and " " not in entry["name"]:
                entry["name"] = p["name"]
            if aid not in entry["agencies"]:
                entry["agencies"].append(aid)
            for c in p.get("councils", "").replace(",", " ").split():
                tagged = f"{aid}:{c}"
                if tagged not in entry["councils"]:
                    entry["councils"].append(tagged)
            for r in (x.strip() for x in p.get("roles", "").split("/") if x.strip()):
                if r not in entry["roles"]:
                    entry["roles"].append(r)
            for t in (x.strip() for x in p.get("affiliations", "").split("|") if x.strip()):
                if t not in entry["affiliations"]:
                    entry["affiliations"].append(t)
            entry["n_meetings"] += int(p.get("n_meetings") or "0")

        for ev in events_rows:
            all_events.append({
                "detected_at": ev.get("detected_at", ""),
                "agency_id": aid,
                "event_type": ev.get("event_type", ""),
                "council_id": ev.get("council_id", ""),
                "meeting_id": ev.get("meeting_id", ""),
                "target_key": ev.get("target_key", ""),
                "before": ev.get("before", ""),
                "after": ev.get("after", ""),
                "source": ev.get("source", ""),
            })

    person_cross_rows: list[dict[str, str]] = []
    for entry in sorted(
        person_by_id.values(),
        key=lambda e: (-len(e["agencies"]), -len(e["councils"]), e["person_id"]),
    ):
        person_cross_rows.append({
            "person_id": entry["person_id"],
            "name": entry["name"],
            "agency_count": str(len(entry["agencies"])),
            "agencies": ", ".join(entry["agencies"]),
            "council_count": str(len(entry["councils"])),
            "councils": ", ".join(entry["councils"]),
            "roles": " / ".join(entry["roles"]),
            "affiliations": " | ".join(entry["affiliations"]),
            "n_meetings": str(entry["n_meetings"]),
        })

    all_events.sort(key=lambda r: (r["detected_at"], r["agency_id"], r["council_id"]), reverse=True)
    if max_events > 0:
        all_events = all_events[:max_events]

    if write:
        out_dir = repo / "data" / "all"
        out_dir.mkdir(parents=True, exist_ok=True)
        build_db.write_csv(out_dir / "agencies.csv", AGENCY_SUMMARY_FIELDS, agency_rows)
        build_db.write_csv(out_dir / "persons_cross.csv", PERSON_CROSS_FIELDS, person_cross_rows)
        build_db.write_csv(out_dir / "recent_events.csv", RECENT_EVENT_FIELDS, all_events)
        build_index.build_all_index(repo=repo, rebuild_catalogs=False, write=True)

    return agency_rows, person_cross_rows, all_events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="ファイルを書き出さず件数だけ表示する")
    ap.add_argument("--max-events", type=int, default=1000, help="recent_events.csv に残す上限件数")
    args = ap.parse_args()

    ag_rows, p_rows, ev_rows = build_cross_tables(max_events=args.max_events, write=not args.dry_run)
    multi_ag_persons = sum(1 for r in p_rows if int(r["agency_count"]) >= 2)
    active_agencies = sum(1 for r in ag_rows if int(r["councils"]) > 0)
    tag = "（dry-run）" if args.dry_run else ""
    print(
        f"横断集計{tag}: 収録省庁 {active_agencies}/{len(ag_rows)} / "
        f"総人物 {len(p_rows)} 人（うち複数省庁兼任 {multi_ag_persons} 人） / "
        f"直近イベント {len(ev_rows)} 件"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
