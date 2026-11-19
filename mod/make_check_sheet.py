#!/usr/bin/env python3
"""抜き取り検証用の確認表（checks/attendance_sample.md）を作る。

議事要旨・議事録から開催日時・場所・出席者を読み取った会議を無作為に選び、読み取った値と、
読み取り元の本文の冒頭を並べる。人が原資料（PDF）と見比べて、チェック欄に結果を書き込む。

会議体ごとに最低 1 件を選び、残りは全体から選ぶ。乱数の種を固定しているので、同じデータなら
同じ会議が選ばれる。

使い方:
    python3 mod/make_check_sheet.py            # 30 件
    python3 mod/make_check_sheet.py --n 50 --seed 1
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import minutes  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "mod"
OUT = REPO / "checks" / "attendance_sample.md"
EXCERPT_LINES = 35


def read(name: str) -> list[dict[str, str]]:
    with (DATA / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def sample(candidates: list[dict[str, str]], n: int, seed: int) -> list[dict[str, str]]:
    """会議体ごとに 1 件ずつ選んでから、残りを全体から選ぶ。戻り値は meetings.csv の順。"""
    rng = random.Random(seed)
    by_council: dict[str, list[dict[str, str]]] = {}
    for m in candidates:
        by_council.setdefault(m["council_id"], []).append(m)
    picked = {rng.choice(ms)["meeting_id"] for ms in by_council.values()}
    rest = [m["meeting_id"] for m in candidates if m["meeting_id"] not in picked]
    rng.shuffle(rest)
    picked.update(rest[:max(0, n - len(picked))])
    return [m for m in candidates if m["meeting_id"] in picked]


def excerpt(text: str) -> list[str]:
    """本文 1 ページ目の冒頭。本文の始まり（「議事」など）の 2 行先までで切る。"""
    lines = [ln for ln in text.split("\f", 1)[0].split("\n") if ln.strip()][:EXCERPT_LINES]
    seen_field = False
    for i, line in enumerate(lines):
        name, _, numbered = minutes._heading(line)
        if name == "body" and (numbered or seen_field):
            return lines[:i + 3]
        seen_field = seen_field or name in ("date", "place", "attendees", "agenda")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20261119)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    councils = {r["council_id"]: r["name"] for r in read("councils.csv")}
    attendance: dict[str, list[dict[str, str]]] = {}
    for a in read("attendance.csv"):
        attendance.setdefault(a["meeting_id"], []).append(a)
    texts = {d["url"]: d["text"] for d in read("documents.csv") if d["text"]}
    candidates = [m for m in read("meetings.csv")
                  if "pdf" in m["found_by"].split("+") and m["meeting_id"] in attendance]
    chosen = sample(candidates, args.n, args.seed)

    out = ["# 抜き取り検証：開催日時・場所・出席者", "",
           f"議事要旨・議事録から出席者を読み取った {len(candidates)} 会議のうち {len(chosen)} 会議を無作為に選んだ"
           f"（乱数の種 {args.seed}）。", "",
           "各会議について、「読み取った値」が「原資料」のリンク先の PDF と合っているかを確かめ、チェック欄に印を付ける。"
           "「本文の冒頭」は PDF から機械的に抽出したテキストで、読み取りはこのテキストに対して行っている。"
           "PDF とテキストが違っていれば、それも誤りとして記録する。", ""]
    for i, m in enumerate(chosen, 1):
        rows = attendance[m["meeting_id"]]
        source = rows[0]["source"]
        out += [f"## {i}. {m['meeting_id']}", "",
                f"{councils.get(m['council_id'], m['council_id'])}" + (f"（{m['body']}）" if m["body"] else ""), "",
                f"原資料：<{source}>", "",
                "| 項目 | 読み取った値 |", "|---|---|",
                f"| 開催日 | {m['date']} |", f"| 時刻 | {m['time']} |", f"| 場所 | {m['place']} |",
                f"| 出席者 | {len(rows)} 人 |", ""]
        group = None
        for a in rows:
            if a["group"] != group:
                group = a["group"]
                out.append(f"- **{group or '（区分なし）'}**")
            detail = " / ".join(x for x in (a["surname"], a["role"]) if x)
            out.append(f"  - {a['name_raw']}" + (f" → {detail}" if detail else ""))
        out += ["", "本文の冒頭：", "", "```text"]
        text_path = texts.get(source)
        out += excerpt((REPO / text_path).read_text(encoding="utf-8")) if text_path else ["（本文なし）"]
        out += ["```", "",
                "- [ ] 開催日・時刻・場所が原資料と一致", "- [ ] 出席者が過不足なく一致（区分・姓・役職を含む）",
                "- メモ：", ""]
    target = args.out.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(f"{target.relative_to(REPO).as_posix()}: {len(chosen)} 会議（候補 {len(candidates)} 会議、"
          f"{len({m['council_id'] for m in chosen})} 会議体）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
