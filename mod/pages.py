"""保存済みの HTML（raw/mod/pages/）を読み、本文を「ブロック」の列に変換する。

ブロックは 3 種類:
  heading … 見出し（h1〜h6）
  row     … 表の 1 行。cells にセルごとのテキストとリンクを持つ
  text    … 表の外の段落・箇条書き 1 件

どのブロックも、その時点で有効な上位見出し（path）と、含まれるリンクを持つ。
ヘッダー・フッター・ナビゲーションは読み飛ばす。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin

BASE = "https://www.mod.go.jp"
REPO = Path(__file__).resolve().parent.parent
RAW_DIR = REPO / "raw" / "mod" / "pages"

# rt・rp はふりがな。本文に混ぜると「三島みしま 茂徳しげのり」のようになるので読み飛ばす
SKIP_TAGS = {"script", "style", "noscript", "header", "footer", "nav", "rt", "rp"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# 段落の切れ目になるタグ。表の中ではセル内の改行として扱う。
BREAK_TAGS = {"p", "li", "div", "section", "article", "ul", "ol", "dl", "dt", "dd", "br",
              "table", "caption", "blockquote", "pre", "hr", "figure", "figcaption"}
NEW_WINDOW = "別ウィンドウで開く"


@dataclass
class Link:
    text: str
    url: str  # 絶対 URL


@dataclass
class Cell:
    text: str
    links: list[Link] = field(default_factory=list)
    header: bool = False


@dataclass
class Block:
    kind: str  # heading / row / text
    text: str
    level: int = 0  # heading のときだけ 1〜6
    path: tuple[str, ...] = ()  # 上位見出し（h2 以下）
    cells: list[Cell] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    table: int = -1  # 同じ表の行は同じ番号


def clean(text: str) -> str:
    """行ごとに空白を詰め、空行を落とす。"""
    lines = [re.sub(r"[ \t\r\f\v　\xa0]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def nfkc(text: str) -> str:
    """全角英数字を半角にそろえる（照合用）。"""
    return unicodedata.normalize("NFKC", text)


class _Parser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.blocks: list[Block] = []
        self.skip = 0
        self.headings: dict[int, str] = {}
        self.heading_level = 0
        self.buf: list[str] = []
        self.links: list[Link] = []
        self.href: str | None = None
        self.link_buf: list[str] = []
        self.table_depth = 0
        self.table_no = -1
        self.row: list[Cell] | None = None
        self.cell_header = False
        self.in_cell = False

    # --- 状態 ---
    def path(self) -> tuple[str, ...]:
        return tuple(self.headings[k] for k in sorted(self.headings) if k >= 2)

    def flush_text(self) -> None:
        text = clean("".join(self.buf))
        links = self.links
        self.buf, self.links = [], []
        if text or links:
            self.blocks.append(Block("text", text, path=self.path(), links=links))

    def end_cell(self) -> None:
        if self.row is None or not self.in_cell:
            return
        self.row.append(Cell(clean("".join(self.buf)), self.links, self.cell_header))
        self.buf, self.links = [], []
        self.in_cell = False

    def end_row(self) -> None:
        self.end_cell()
        if self.row:
            links = [ln for c in self.row for ln in c.links]
            text = " | ".join(c.text.replace("\n", " / ") for c in self.row)
            self.blocks.append(Block("row", text, path=self.path(), cells=self.row,
                                     links=links, table=self.table_no))
        self.row = None

    # --- イベント ---
    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        a = dict(attrs)
        if tag == "table":
            if self.table_depth == 0:
                self.flush_text()
                self.table_no += 1
            self.table_depth += 1
            return
        if self.table_depth == 1 and tag == "tr":
            self.end_row()
            self.row = []
            return
        if self.table_depth == 1 and tag in ("td", "th"):
            if self.row is None:
                self.row = []
            self.end_cell()
            self.in_cell = True
            self.cell_header = tag == "th"
            return
        if tag in HEADING_TAGS and not self.table_depth:
            self.flush_text()
            self.heading_level = int(tag[1])
            return
        if tag == "a" and a.get("href"):
            self.href = a["href"]
            self.link_buf = []
            return
        if tag == "img" and self.href is not None and a.get("alt"):
            self.link_buf.append(a["alt"])
            return
        if tag in BREAK_TAGS:
            if self.table_depth:
                self.buf.append("\n")
            elif not self.heading_level:
                self.flush_text()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "table":
            self.table_depth = max(0, self.table_depth - 1)
            if self.table_depth == 0:
                self.end_row()
            return
        if self.table_depth == 1 and tag == "tr":
            self.end_row()
            return
        if self.table_depth == 1 and tag in ("td", "th"):
            self.end_cell()
            return
        if tag in HEADING_TAGS and self.heading_level and not self.table_depth:
            level = self.heading_level
            text = clean("".join(self.buf)).replace("\n", " ")
            links = self.links
            self.buf, self.links = [], []
            self.heading_level = 0
            for k in [k for k in self.headings if k >= level]:
                del self.headings[k]
            if text:
                self.headings[level] = text
                upper = tuple(self.headings[k] for k in sorted(self.headings) if 2 <= k < level)
                self.blocks.append(Block("heading", text, level=level, path=upper, links=links))
            return
        if tag == "a" and self.href is not None:
            text = clean("".join(self.link_buf)).replace("\n", " ")
            text = text.replace(NEW_WINDOW, "").strip()
            href = self.href.strip()
            self.href = None
            self.buf.append(text)
            if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                url = urldefrag(urljoin(self.base_url, href)).url
                self.links.append(Link(text, url))
            return
        if tag in BREAK_TAGS:
            if self.table_depth:
                self.buf.append("\n")
            elif not self.heading_level:
                self.flush_text()

    def handle_data(self, data):
        if self.skip:
            return
        if self.href is not None:
            self.link_buf.append(data)
        else:
            self.buf.append(data)

    def close(self):
        super().close()
        self.end_row()
        self.flush_text()


def decode(body: bytes) -> str:
    m = re.search(rb"charset=[\"']?([\w-]+)", body[:2048], re.I)
    enc = m.group(1).decode("ascii") if m else "utf-8"
    try:
        return body.decode(enc, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def parse(html: str, url: str) -> list[Block]:
    """url はサイト内パス（/j/...）または絶対 URL。相対リンクの解決に使う。"""
    parser = _Parser(urljoin(BASE, url))
    parser.feed(html)
    parser.close()
    return parser.blocks


def load(url: str, root: Path = RAW_DIR) -> list[Block]:
    """保存済みページをブロック列にして返す。url はサイト内パス。"""
    path = root.joinpath(*url.strip("/").split("/"))
    return parse(decode(path.read_bytes()), url)
