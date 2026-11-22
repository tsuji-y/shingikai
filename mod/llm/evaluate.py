#!/usr/bin/env python3
"""Phase 3 評価スクリプト：出席者を読み取り済みの 30 会議を Gemini で抽出し、既存の表と突き合わせる。

選び方は mod/make_check_sheet.py と同じ（会議体ごとに最低 1 件を含み、乱数種固定で 30 会議）。
一致率・検証通過率・食い違いの一覧（原文抜粋付き）を Markdown レポートに書き出す。

使い方:
    python3 -m mod.llm.evaluate --n 30 --out checks/llm_eval_30.md
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import make_check_sheet  # noqa: E402

from .client import GeminiClient, LLMError, QuotaExhaustedError
from .config import DATA, DEFAULT_MODEL, REPO, load_prompt
from .validate import compact_nfkc, validate_minutes_extraction


@dataclass
class EvalItem:
    meeting_id: str
    council_id: str
    source: str
    date_match: bool
    time_match: bool
    place_match: bool
    existing_n: int
    llm_n: int
    attendee_overlap: int
    validation_ok: bool
    validation_errors: list[str] = field(default_factory=list)
    existing_summary: dict[str, Any] = field(default_factory=dict)
    llm_summary: dict[str, Any] = field(default_factory=dict)
    excerpt_lines: list[str] = field(default_factory=list)
    error: str = ""


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _name_set(names: list[str]) -> set[str]:
    return {compact_nfkc(n) for n in names if compact_nfkc(n)}


def evaluate(client: Any, *, n: int = 30, seed: int = 20261119,
             repo: Path = REPO, data_dir: Path = DATA) -> list[EvalItem]:
    import time

    meetings = _read_csv(data_dir / "meetings.csv")
    attendance_by_mid: dict[str, list[dict[str, str]]] = {}
    for a in _read_csv(data_dir / "attendance.csv"):
        attendance_by_mid.setdefault(a["meeting_id"], []).append(a)
    texts_by_url = {d["url"]: d["text"] for d in _read_csv(data_dir / "documents.csv") if d.get("text")}

    candidates = [
        m for m in meetings
        if "pdf" in m.get("found_by", "").split("+") and m["meeting_id"] in attendance_by_mid
    ]
    chosen = make_check_sheet.sample(candidates, n, seed)
    prompt_ver, prompt_text = load_prompt("extract")

    results: list[EvalItem] = []
    total_chosen = len(chosen)
    for idx, m in enumerate(chosen, 1):
        mid = m["meeting_id"]
        att_rows = attendance_by_mid[mid]
        source = att_rows[0]["source"]
        text_rel = texts_by_url.get(source, "")
        if not text_rel or not (repo / text_rel).exists():
            continue
        source_text = (repo / text_rel).read_text(encoding="utf-8")
        t0 = time.monotonic()
        try:
            payload = client.generate_json(prompt_text, source_text[:12_000])
        except QuotaExhaustedError as e:
            print(f"[evaluate {idx}/{total_chosen}] {mid} QUOTA ({time.monotonic() - t0:.1f}s): {e}", flush=True)
            results.append(EvalItem(
                meeting_id=mid, council_id=m["council_id"], source=source,
                date_match=False, time_match=False, place_match=False,
                existing_n=len(att_rows), llm_n=0, attendee_overlap=0,
                validation_ok=False, error=str(e),
            ))
            break
        except LLMError as e:
            print(f"[evaluate {idx}/{total_chosen}] {mid} ERROR ({time.monotonic() - t0:.1f}s): {str(e)[:120]}", flush=True)
            results.append(EvalItem(
                meeting_id=mid, council_id=m["council_id"], source=source,
                date_match=False, time_match=False, place_match=False,
                existing_n=len(att_rows), llm_n=0, attendee_overlap=0,
                validation_ok=False, error=str(e),
            ))
            continue

        vres = validate_minutes_extraction(payload, source_text)
        c = vres.cleaned
        date_match = (c["date"] == m["date"])
        time_match = (c["time"] == m["time"])
        place_match = compact_nfkc(c["place"]) == compact_nfkc(m["place"])

        ex_names = [a["name_raw"] for a in att_rows]
        llm_names = [a["name_raw"] for a in c["attendees"]]
        overlap = len(_name_set(ex_names) & _name_set(llm_names))
        print(
            f"[evaluate {idx}/{total_chosen}] {mid} valid={vres.ok} "
            f"att={len(ex_names)}/{len(llm_names)}/{overlap} ({time.monotonic() - t0:.1f}s)",
            flush=True,
        )

        results.append(EvalItem(
            meeting_id=mid,
            council_id=m["council_id"],
            source=source,
            date_match=date_match,
            time_match=time_match,
            place_match=place_match,
            existing_n=len(ex_names),
            llm_n=len(llm_names),
            attendee_overlap=overlap,
            validation_ok=vres.ok,
            validation_errors=vres.errors,
            existing_summary={"date": m["date"], "time": m["time"], "place": m["place"], "attendees": ex_names},
            llm_summary={"date": c["date"], "time": c["time"], "place": c["place"], "attendees": llm_names},
            excerpt_lines=make_check_sheet.excerpt(source_text),
        ))
    return results


def format_report(items: list[EvalItem], model: str, seed: int) -> str:
    prompt_ver, _ = load_prompt("extract")
    total = len(items)
    if total == 0:
        return "# Phase 3 評価結果\n\n評価対象が 0 件でした。\n"

    n_valid = sum(1 for x in items if x.validation_ok)
    n_date = sum(1 for x in items if x.date_match)
    n_time = sum(1 for x in items if x.time_match)
    n_place = sum(1 for x in items if x.place_match)
    n_att_exact = sum(1 for x in items if x.existing_n == x.llm_n == x.attendee_overlap)
    n_err = sum(1 for x in items if x.error)

    lines = [
        "# Phase 3 Gemini 抽出評価レポート",
        "",
        f"- モデル: `{model}` / プロンプト版: `{prompt_ver}`",
        f"- サンプル: {total} 会議（seed={seed}、`make_check_sheet.py` と同一抽出方式）",
        "",
        "## 集計",
        "",
        "| 指標 | 件数 | 割合 |",
        "|---|---:|---:|",
        f"| 自動検証通過 | {n_valid} / {total} | {100.0 * n_valid / total:.1f}% |",
        f"| 開催日一致 | {n_date} / {total} | {100.0 * n_date / total:.1f}% |",
        f"| 時刻一致 | {n_time} / {total} | {100.0 * n_time / total:.1f}% |",
        f"| 場所一致 | {n_place} / {total} | {100.0 * n_place / total:.1f}% |",
        f"| 出席者完全一致 | {n_att_exact} / {total} | {100.0 * n_att_exact / total:.1f}% |",
        f"| API/解釈エラー | {n_err} / {total} | {100.0 * n_err / total:.1f}% |",
        "",
        "## 各会議の比較",
        "",
        "| # | meeting_id | 検証 | 日付 | 時刻 | 場所 | 出席者 (既存/LLM/一致) | 備考 |",
        "|---:|---|---|---|---|---|---:|---|",
    ]
    for i, x in enumerate(items, 1):
        ok = lambda b: "OK" if b else "DIFF"
        note = x.error or "; ".join(x.validation_errors)
        lines.append(
            f"| {i} | `{x.meeting_id}` | {ok(x.validation_ok)} | {ok(x.date_match)} | "
            f"{ok(x.time_match)} | {ok(x.place_match)} | {x.existing_n}/{x.llm_n}/{x.attendee_overlap} | {note[:80]} |"
        )

    diffs = [x for x in items if not (x.validation_ok and x.date_match and x.place_match and x.existing_n == x.llm_n == x.attendee_overlap)]
    if diffs:
        lines.extend(["", "## 食い違いの詳細（原文確認用）", ""])
        for x in diffs:
            lines.extend([
                f"### `{x.meeting_id}`",
                "",
                f"- 原資料: <{x.source}>",
                f"- 既存: date=`{x.existing_summary.get('date', '')}` time=`{x.existing_summary.get('time', '')}` place=`{x.existing_summary.get('place', '')}` ({x.existing_n}名)",
                f"- LLM: date=`{x.llm_summary.get('date', '')}` time=`{x.llm_summary.get('time', '')}` place=`{x.llm_summary.get('place', '')}` ({x.llm_n}名)",
            ])
            if x.validation_errors:
                lines.append(f"- 検証エラー: {'; '.join(x.validation_errors)}")
            if x.error:
                lines.append(f"- 実行エラー: {x.error}")
            lines.extend(["", "```text", *x.excerpt_lines[:25], "```", ""])

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20261119)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", type=Path, default=REPO / "checks" / "llm_eval_30.md")
    args = ap.parse_args()

    client = GeminiClient(model=args.model)
    items = evaluate(client, n=args.n, seed=args.seed)
    report = format_report(items, model=args.model, seed=args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8", newline="\n")
    print(f"評価レポート出力: {args.out} ({len(items)} 会議)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
