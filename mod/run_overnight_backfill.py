#!/usr/bin/env python3
"""Wave 1 → Wave 2 → Wave 3（年代分割）を夜間・長時間にわたり自動完走させ、ステージごとに commit & push するオーケストレータ。

安全設計:
- 各ステージの完了ごとに `cross_agency.py` で横断表・`data/all/index.json` を更新し、`raw/`・`data/`
  （`data/*/rss_items.csv` は除く）を commit & push する。
- 並行稼働する GitHub Actions（日次更新・RSS）が先に push していた場合は自動で `git pull --rebase` し、
  `data/all/*` の自動生成ファイルで競合した場合は再生成して rebase を自動完遂する。
- 進捗と PID を `/tmp/shingikai_overnight/status.json` と `pid` に書き出すため、いつでも状態確認できる。
- 中断しても各省庁の台帳がチェックポイント済みのため、同じコマンドで途中から冪等に再開できる。

使い方:
    caffeinate -i -s .venv/bin/python -u mod/run_overnight_backfill.py
    .venv/bin/python mod/run_overnight_backfill.py --dry-run
    .venv/bin/python mod/run_overnight_backfill.py --start-stage wave2
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backfill  # noqa: E402
import extract_text  # noqa: E402
import fetch_files  # noqa: E402
import fetch_subpages  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
STATE_DIR = Path("/tmp/shingikai_overnight")
STATUS_FILE = STATE_DIR / "status.json"
PID_FILE = STATE_DIR / "pid"


@dataclass(frozen=True)
class Stage:
    name: str
    description: str
    args: tuple[str, ...]


STAGES: tuple[Stage, ...] = (
    Stage(
        name="wave1",
        description="Wave 1: 中小型 8 機関（caa, fsa, mext, moj, cao, cfa, mof, npa）全期間",
        args=("--wave", "wave1", "--parallel", "8", "--chunk-html", "100", "--chunk-pdf", "100"),
    ),
    Stage(
        name="wave2",
        description="Wave 2: 中規模＋内閣官房 4 機関（mlit, digital, maff, cas）全期間",
        args=("--wave", "wave2", "--parallel", "4", "--chunk-html", "100", "--chunk-pdf", "100"),
    ),
    Stage(
        name="wave3_2024_2026",
        description="Wave 3-1: 環境省・厚労省（2024〜2026年）",
        args=("--wave", "wave3", "--parallel", "2", "--since-year", "2024", "--until-year", "2026", "--chunk-html", "100", "--chunk-pdf", "100"),
    ),
    Stage(
        name="wave3_2022_2023",
        description="Wave 3-2: 環境省・厚労省（2022〜2023年）",
        args=("--wave", "wave3", "--parallel", "2", "--since-year", "2022", "--until-year", "2023", "--chunk-html", "100", "--chunk-pdf", "100"),
    ),
    Stage(
        name="wave3_2020_2021",
        description="Wave 3-3: 環境省・厚労省（2020〜2021年）",
        args=("--wave", "wave3", "--parallel", "2", "--since-year", "2020", "--until-year", "2021", "--chunk-html", "100", "--chunk-pdf", "100"),
    ),
    Stage(
        name="wave3_2018_2019",
        description="Wave 3-4: 環境省・厚労省（2018〜2019年）",
        args=("--wave", "wave3", "--parallel", "2", "--since-year", "2018", "--until-year", "2019", "--chunk-html", "100", "--chunk-pdf", "100"),
    ),
    Stage(
        name="wave3_2015_2017",
        description="Wave 3-5: 環境省・厚労省（2015〜2017年）",
        args=("--wave", "wave3", "--parallel", "2", "--since-year", "2015", "--until-year", "2017", "--chunk-html", "100", "--chunk-pdf", "100"),
    ),
    Stage(
        name="wave3_2010_2014",
        description="Wave 3-6: 環境省・厚労省（2010〜2014年）",
        args=("--wave", "wave3", "--parallel", "2", "--since-year", "2010", "--until-year", "2014", "--chunk-html", "100", "--chunk-pdf", "100"),
    ),
    Stage(
        name="wave3_pre2010",
        description="Wave 3-7: 環境省・厚労省（〜2009年）",
        args=("--wave", "wave3", "--parallel", "2", "--since-year", "1995", "--until-year", "2009", "--chunk-html", "100", "--chunk-pdf", "100"),
    ),
    Stage(
        name="wave3_remaining",
        description="Wave 3-8: 環境省・厚労省（年代不明・残余全件）",
        args=("--wave", "wave3", "--parallel", "2", "--chunk-html", "100", "--chunk-pdf", "100"),
    ),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_status(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now_iso()
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATUS_FILE)


def _run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO, check=check)


def _git_output(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


_MANIFEST_SPECS: dict[str, tuple[tuple[str, ...], list[str]]] = {
    "files.csv": (("url",), fetch_files.FIELDS),
    "subpages.csv": (("url",), fetch_subpages.SUBPAGE_FIELDS),
    "subpage_links.csv": (("source_url", "url"), fetch_subpages.SUBPAGE_LINK_FIELDS),
    "texts.csv": (("url",), extract_text.FIELDS),
}
_DERIVED_DATA_FILES = frozenset({
    "council_profiles.csv", "members.csv", "meetings.csv", "documents.csv",
    "attendance.csv", "persons.csv", "catalog.csv", "catalog.jsonl",
})


def merge_manifest_csv(ours_text: str, theirs_text: str, *,
                       key_cols: tuple[str, ...], fieldnames: list[str]) -> str:
    """日次 Actions 側とローカルバックフィル側の台帳 CSV をキー単位で欠損なくユニオン統合する。"""
    def _parse(text: str) -> dict[tuple[str, ...], dict[str, str]]:
        if not text.strip():
            return {}
        out: dict[tuple[str, ...], dict[str, str]] = {}
        for row in csv.DictReader(io.StringIO(text)):
            key = tuple((row.get(c) or "").strip() for c in key_cols)
            if any(key):
                out[key] = {f: row.get(f, "") for f in fieldnames}
        return out

    def _score(r: dict[str, str]) -> tuple[int, int, str, int]:
        has_body = int(bool(r.get("sha256") or r.get("text")))
        has_rel = int(bool(r.get("release_url")))
        fetched = r.get("fetched_at", "")
        non_empty = sum(1 for v in r.values() if v)
        return (has_body, has_rel, fetched, non_empty)

    ours = _parse(ours_text)
    theirs = _parse(theirs_text)
    merged: dict[tuple[str, ...], dict[str, str]] = dict(ours)
    for k, trow in theirs.items():
        if k not in merged:
            merged[k] = trow
            continue
        orow = merged[k]
        best, other = (trow, orow) if _score(trow) >= _score(orow) else (orow, trow)
        combined = dict(best)
        # どちらかの側が release_url 等を持っていれば欠落を補完
        if not combined.get("release_url") and other.get("release_url"):
            if not combined.get("sha256") or not other.get("sha256") or combined["sha256"] == other["sha256"]:
                combined["release_url"] = other["release_url"]
        for col in fieldnames:
            if not combined.get(col) and other.get(col):
                if col not in ("note", "status"):
                    combined[col] = other[col]
        merged[k] = combined

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for k in sorted(merged):
        writer.writerow({f: merged[k].get(f, "") for f in fieldnames})
    return buf.getvalue()


def merge_parsed_cache(ours_text: str, theirs_text: str) -> str:
    """parsed_texts.json の minutes / rosters キャッシュをユニオン統合する。"""
    def _load(text: str) -> dict[str, dict]:
        if not text.strip():
            return {"minutes": {}, "rosters": {}}
        data = json.loads(text)
        return {
            "minutes": dict(data.get("minutes") or {}),
            "rosters": dict(data.get("rosters") or {}),
        }

    ours = _load(ours_text)
    theirs = _load(theirs_text)
    for section in ("minutes", "rosters"):
        for k, v in theirs[section].items():
            if k not in ours[section] or not ours[section][k]:
                ours[section][k] = v
            elif isinstance(v, dict) and v.get("sha256") and not ours[section][k].get("sha256"):
                ours[section][k] = v
    payload = {
        "minutes": dict(sorted(ours["minutes"].items())),
        "rosters": dict(sorted(ours["rosters"].items())),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def _git_show_stage(stage_num: int, rel_path: str) -> str:
    res = subprocess.run(
        ["git", "show", f":{stage_num}:{rel_path}"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return res.stdout if res.returncode == 0 else ""


def resolve_data_conflicts(unmerged: list[str], python_exe: str) -> tuple[bool, str]:
    """rebase 時の raw/*/data/* 競合を台帳ユニオン統合＋派生表再構築で自動解消する。"""
    affected_agencies: set[str] = set()
    for rel in unmerged:
        parts = Path(rel).parts
        if len(parts) == 3 and parts[0] == "raw" and parts[2] in _MANIFEST_SPECS:
            key_cols, fields = _MANIFEST_SPECS[parts[2]]
            merged_txt = merge_manifest_csv(
                _git_show_stage(2, rel), _git_show_stage(3, rel),
                key_cols=key_cols, fieldnames=fields,
            )
            (REPO / rel).write_text(merged_txt, encoding="utf-8", newline="\n")
            affected_agencies.add(parts[1])
        elif len(parts) == 3 and parts[0] == "data" and parts[2] == "texts.csv":
            key_cols, fields = _MANIFEST_SPECS["texts.csv"]
            merged_txt = merge_manifest_csv(
                _git_show_stage(2, rel), _git_show_stage(3, rel),
                key_cols=key_cols, fieldnames=fields,
            )
            (REPO / rel).write_text(merged_txt, encoding="utf-8", newline="\n")
            affected_agencies.add(parts[1])
        elif len(parts) == 3 and parts[0] == "data" and parts[2] == "parsed_texts.json":
            merged_json = merge_parsed_cache(_git_show_stage(2, rel), _git_show_stage(3, rel))
            (REPO / rel).write_text(merged_json, encoding="utf-8", newline="\n")
            affected_agencies.add(parts[1])
        elif len(parts) >= 2 and parts[0] == "data" and parts[1] == "all":
            _run(["git", "checkout", "--ours", "--", rel], check=False)
        elif len(parts) == 3 and parts[0] == "data" and parts[2] in _DERIVED_DATA_FILES:
            _run(["git", "checkout", "--ours", "--", rel], check=False)
            affected_agencies.add(parts[1])
        else:
            return False, f"non_auto_conflict:{rel}"

    for ag in sorted(affected_agencies):
        r1 = _run([python_exe, "mod/build_db.py", "--agency", ag])
        if r1.returncode != 0:
            return False, f"rebuild_failed:{ag}"
        r2 = _run([python_exe, "mod/build_index.py", "--agency", ag])
        if r2.returncode != 0:
            return False, f"index_failed:{ag}"

    _run([python_exe, "mod/cross_agency.py"], check=False)
    _run(["git", "add", "raw/", "data/"], check=True)
    _run(["git", "reset", "--", "data/*/rss_items.csv"], check=False)
    return True, f"merged:{','.join(sorted(affected_agencies))}"


def commit_and_push_stage(stage: Stage, python_exe: str) -> tuple[bool, str]:
    """ステージの成果（raw/ と data/、rss_items.csv は除外）を安全に commit & push する。"""
    # 横断表と index.json を最新化
    _run([python_exe, "mod/cross_agency.py"], check=False)

    # 差分チェック
    status = _git_output(["status", "--porcelain", "--", "raw/", "data/", ":(exclude)data/*/rss_items.csv"])
    if status:
        _run(["git", "add", "raw/", "data/"], check=True)
        _run(["git", "reset", "--", "data/*/rss_items.csv"], check=False)
        diff_cached = _run(["git", "diff", "--cached", "--quiet"])
        if diff_cached.returncode != 0:
            today = datetime.now().strftime("%Y-%m-%d")
            msg = f"Backfill council database ({stage.name}, {today})"
            _run(["git", "commit", "-m", msg], check=True)

    # Push（Actions の並行 push と衝突した場合は rebase + 台帳ユニオン統合で最大 3 回リトライ）
    for attempt in range(1, 4):
        push_res = _run(["git", "push", "origin", "main"])
        if push_res.returncode == 0:
            rev = _git_output(["rev-parse", "--short", "HEAD"])
            return True, f"pushed:{rev}"

        print(f"[git] push 拒否（試行 {attempt}/3）。origin/main を取り込んで再同期します...", flush=True)
        _run(["git", "fetch", "origin", "main"], check=True)
        rebase_res = _run(["git", "rebase", "origin/main"])
        while rebase_res.returncode != 0:
            unmerged = [
                ln for ln in _git_output(["diff", "--name-only", "--diff-filter=U"]).splitlines() if ln.strip()
            ]
            if not unmerged:
                _run(["git", "rebase", "--abort"], check=False)
                return False, "rebase_failed_no_unmerged"
            ok_res, detail = resolve_data_conflicts(unmerged, python_exe)
            if not ok_res:
                _run(["git", "rebase", "--abort"], check=False)
                return False, f"rebase_conflict:{detail}"
            print(f"[git] 競合を自動ユニオン解消しました ({detail})", flush=True)
            env = os.environ.copy()
            env["GIT_EDITOR"] = "true"
            rebase_res = subprocess.run(["git", "rebase", "--continue"], cwd=REPO, env=env)
        time.sleep(2)

    return False, "push_failed_after_retries"


def _stage_backfill_args(stage: Stage, skip_agencies: set[str]) -> list[str] | None:
    """--skip-agencies 指定時に --wave を対象省庁リストへ展開して除外する。全除外なら None。"""
    args = list(stage.args)
    if not skip_agencies or "--wave" not in args:
        return args
    idx = args.index("--wave")
    wave_name = args[idx + 1]
    remaining = [a for a in backfill.WAVES[wave_name] if a not in skip_agencies]
    if not remaining:
        return None
    args[idx : idx + 2] = ["--agency", ",".join(remaining)]
    return args


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start-stage", default="", help="指定ステージから開始する（例: wave2, wave3_2024_2026）")
    ap.add_argument("--end-stage", default="", help="指定ステージ完了後に停止する")
    ap.add_argument("--skip-agencies", default="", help="除外する省庁 ID（カンマ区切り。例: maff）")
    ap.add_argument("--no-push", action="store_true", help="commit/push をスキップする（ローカル検証用）")
    ap.add_argument("--dry-run", action="store_true", help="実行計画と対象件数のみ表示")
    args = ap.parse_args()
    skip_agencies = {a.strip() for a in args.skip_agencies.split(",") if a.strip()}

    stage_names = [s.name for s in STAGES]
    start_idx = 0
    end_idx = len(STAGES) - 1
    if args.start_stage:
        if args.start_stage not in stage_names:
            ap.error(f"不明なステージ: {args.start_stage} (候補: {', '.join(stage_names)})")
        start_idx = stage_names.index(args.start_stage)
    if args.end_stage:
        if args.end_stage not in stage_names:
            ap.error(f"不明なステージ: {args.end_stage} (候補: {', '.join(stage_names)})")
        end_idx = stage_names.index(args.end_stage)

    selected = STAGES[start_idx : end_idx + 1]
    python_exe = sys.executable

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{os.getpid()}\n", encoding="utf-8")

    state = {
        "pid": os.getpid(),
        "started_at": _now_iso(),
        "status": "running",
        "current_stage": "",
        "stages": {s.name: {"status": "pending", "description": s.description} for s in selected},
    }
    _write_status(state)

    print(f"=== Overnight Backfill Runner (PID={os.getpid()}, stages={len(selected)}) ===", flush=True)
    for idx, stage in enumerate(selected, 1):
        print(f"\n[{idx}/{len(selected)}] >>> {stage.name}: {stage.description}", flush=True)
        state["current_stage"] = stage.name
        state["stages"][stage.name]["status"] = "running"
        state["stages"][stage.name]["started_at"] = _now_iso()
        _write_status(state)

        stage_args = _stage_backfill_args(stage, skip_agencies)
        if stage_args is None:
            state["stages"][stage.name]["status"] = "skipped"
            _write_status(state)
            continue

        cmd = [python_exe, "-u", "mod/backfill.py", *stage_args, "--archive", "--cleanup-local"]
        if args.dry_run:
            cmd.append("--dry-run")

        proc = _run(cmd)
        bf_ok = proc.returncode == 0
        state["stages"][stage.name]["backfill_exit_code"] = proc.returncode

        if args.dry_run:
            state["stages"][stage.name]["status"] = "dry_run_ok" if bf_ok else "dry_run_failed"
            _write_status(state)
            continue

        if args.no_push:
            state["stages"][stage.name]["git"] = "skipped"
            git_ok = True
        else:
            git_ok, git_msg = commit_and_push_stage(stage, python_exe)
            state["stages"][stage.name]["git"] = git_msg
            print(f"[{stage.name}] git result: {git_msg}", flush=True)

        state["stages"][stage.name]["finished_at"] = _now_iso()
        state["stages"][stage.name]["status"] = "done" if (bf_ok and git_ok) else "partial_or_failed"
        _write_status(state)

    # 最終サマリ出力
    print("\n=== Final Backfill Status ===", flush=True)
    _run([python_exe, "mod/backfill.py", "--status-only"])
    any_fail = any(v["status"] != "done" and not args.dry_run for v in state["stages"].values())
    state["current_stage"] = ""
    state["status"] = "completed_with_warnings" if any_fail else "completed"
    state["finished_at"] = _now_iso()
    _write_status(state)
    print(f"\n=== Overnight Backfill Finished: {state['status']} ===", flush=True)
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
