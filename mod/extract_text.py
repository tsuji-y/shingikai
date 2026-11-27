#!/usr/bin/env python3
"""取得済みの PDF（raw/mod/files/）から本文を取り出し、data/mod/text/ にテキストとして保存する。

このスクリプトだけ pdfminer.six を使う（requirements.txt）。画像だけの PDF からは本文が取れないので、
台帳 data/mod/texts.csv の chars が 0 に近いものは OCR が必要な候補になる。
ページの区切りは改ページ文字（\\f）で残す。

使い方:
    python3 mod/extract_text.py             # 未抽出のものをすべて処理
    python3 mod/extract_text.py --limit 20
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parent.parent
FILES_DIR = REPO / "raw" / "mod" / "files"
FILES_MANIFEST = REPO / "raw" / "mod" / "files.csv"
TEXT_DIR = REPO / "data" / "mod" / "text"
TEXTS = REPO / "data" / "mod" / "texts.csv"
DOCUMENTS = REPO / "data" / "mod" / "documents.csv"
FIELDS = ["url", "text", "pages", "chars", "sha256", "note", "release_url"]
FIRST = ("議事録", "議事要旨", "議事次第", "名簿")  # 出席者・開催日の読み取りに使うものを先に処理する


def url_parts(url: str) -> list[str]:
    return [unquote(p) for p in urlsplit(url).path.strip("/").split("/") if p]


def text_path(url: str, root: Path = TEXT_DIR) -> Path:
    return root.joinpath(*url_parts(url)).with_suffix(".txt")


def normalize(text: str) -> str:
    """行末の空白を落とし、3 行以上続く空行を 1 行に詰める。改ページ（\\f）は残す。"""
    out, blank = [], 0
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.rstrip(" \t　")
        blank = blank + 1 if not line else 0
        if blank <= 1:
            out.append(line)
    return "\n".join(out).strip("\n") + "\n"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for r in sorted(rows, key=lambda r: r["url"]):
            writer.writerow({field: r.get(field, "") for field in FIELDS})
    tmp.replace(path)


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import agencies

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agency", default="mod", help="対象の省庁 ID（既定: mod）")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    ag = agencies.get_agency(args.agency)
    files_dir = ag.raw_files_dir
    files_manifest = ag.files_manifest
    text_dir = ag.data_dir / "text"
    texts_csv = ag.data_dir / "texts.csv"
    documents_csv = ag.data_dir / "documents.csv"

    from pdfminer.high_level import extract_text  # 標準ライブラリ外。ここでだけ読み込む

    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    done = {r["url"]: r for r in read_csv(texts_csv)}
    doc_types = {r["url"]: r["doc_type"] for r in read_csv(documents_csv)}
    todo = [r for r in read_csv(files_manifest)
            if r["sha256"] and done.get(r["url"], {}).get("sha256") != r["sha256"]]
    todo.sort(key=lambda r: (doc_types.get(r["url"]) not in FIRST, r["url"]))
    if args.limit:
        todo = todo[:args.limit]
    print(f"[{ag.agency_id}] 抽出済み {len(done)} 件 / 今回 {len(todo)} 件", flush=True)

    for i, r in enumerate(todo, 1):
        url = r["url"]
        src = files_dir.joinpath(*url_parts(url))
        row = {"url": url, "text": "", "pages": "", "chars": "0", "sha256": r["sha256"], "note": "", "release_url": ""}
        started = time.time()
        try:
            raw = extract_text(str(src))
        except Exception as e:  # 壊れた PDF・暗号化された PDF など。1 件の失敗で全体を止めない
            row["note"] = f"抽出失敗: {type(e).__name__}: {e}"[:200]
        else:
            text = normalize(raw)
            out = text_path(url, text_dir)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8", newline="\n")
            row["text"] = out.relative_to(REPO).as_posix()
            row["pages"] = str(raw.count("\f"))
            row["chars"] = str(sum(1 for ch in text if not ch.isspace()))
            if int(row["chars"]) < 20 * max(1, int(row["pages"])):
                row["note"] = "本文がほとんど取れない（画像の PDF とみられる）"
        done[url] = row
        took = time.time() - started
        if i % 20 == 0 or i == len(todo) or took > 20 or row["note"]:
            print(f"  {i}/{len(todo)} {row['pages']}p {row['chars']}字 {took:.0f}s {row['note']} {url[-60:]}", flush=True)
            write_csv(texts_csv, list(done.values()))
    write_csv(texts_csv, list(done.values()))
    print(f"[{ag.agency_id}] 台帳 {len(done)} 件", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
