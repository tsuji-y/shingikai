#!/usr/bin/env python3
"""保存済みページ（raw/mod/pages/）から、会議体ごとの表を作る。

出力（data/mod/）:
    council_profiles.csv  概要の表（根拠法令・所掌事務など）を「会議体・項目・内容」の縦持ちで
    members.csv           委員名簿
    meetings.csv          各回の会議（回次・開催日・議題・場所・出席者）
    documents.csv         資料・議事録などへのリンク。会議に結び付くものは meeting_id を持つ
    attendance.csv        各回の出席者（1 人 1 行）

PDF の本文（data/mod/texts.csv、extract_text.py の出力）があれば、議事要旨・議事録の冒頭から
開催日時・場所・出席者を補う（meetings.csv の found_by に pdf が付く）。

ページの書き方は会議体ごとに違うので、抽出は次の手掛かりによる（meetings.csv の found_by に残す）:
    table    「年月日」「議題」などの見出し行を持つ表の 1 行
    heading  「第N回 …」という見出しと、その下のリンク
    link     リンクの文言に「第N回」や開催日が入っているもの
    page     1 回分の会議だけを載せたページ（h1 に「第N回」や開催日がある）

使い方:
    python3 mod/build_db.py            # data/mod/ に書き出す
    python3 mod/build_db.py --dry-run  # 件数と警告だけ表示
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import minutes  # noqa: E402
import pages  # noqa: E402
import persons  # noqa: E402
from pages import Block, Link, nfkc  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "mod"
MANIFEST = REPO / "raw" / "mod" / "pages.csv"
ROOT = "/j/policy/agenda/meeting/"

COUNCIL_FIELDS = ["council_id", "section", "name", "link", "note", "first_seen"]
PROFILE_FIELDS = ["council_id", "key", "value", "source"]
MEMBER_FIELDS = ["council_id", "body", "group", "as_of", "as_of_raw", "name", "kana", "mark", "role",
                 "title", "note", "person_id", "source"]
MEETING_FIELDS = ["meeting_id", "council_id", "body", "number", "label", "date", "date_raw", "time",
                  "agenda", "place", "attendees", "page", "n_documents", "found_by", "source"]
ATTENDANCE_FIELDS = ["meeting_id", "council_id", "group", "seq", "name_raw", "surname", "role",
                     "person_id", "person_basis", "source"]
MINUTES_TYPES = ("議事要旨", "議事録")  # 冒頭に日時・場所・出席者が書かれている資料。この順に当たる
DOCUMENT_FIELDS = ["council_id", "meeting_id", "section", "title", "doc_type", "url", "host",
                   "format", "text", "pages", "chars", "source", "doc_key"]

# ---------------------------------------------------------------- 日付と回次

ERAS = {"令和": 2018, "平成": 1988, "昭和": 1925, "R": 2018, "H": 1988, "S": 1925}
_DATE_PATTERNS = [
    (re.compile(r"(令和|平成|昭和)\s*(元|\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"), "era"),
    (re.compile(r"(?<![A-Za-z])([RHS])\s*(\d{1,2})\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{1,2})(?!\d)"), "era"),
    (re.compile(r"(?<!\d)((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"), "ymd"),
    (re.compile(r"(?<!\d)((?:19|20)\d{2})\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{1,2})(?!\d)"), "ymd"),
]
_SHORT_DATE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{1,2})(?![\d.])")
_URL_DATE = re.compile(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)")


def _iso(y: int, m: int, d: int) -> str:
    try:
        return datetime.date(y, m, d).isoformat()
    except ValueError:
        return ""


def parse_date(text: str, url: str = "") -> tuple[str, str]:
    """文字列から最初の日付を取り出す。戻り値は（ISO 形式, 元の表記）。なければ ("", "")。

    「08.9.3」のように元号のない和暦は、URL のファイル名に同じ月日の西暦日付があるときだけ採用する。
    """
    t = nfkc(text)
    best: tuple[int, str, str] | None = None
    for pattern, kind in _DATE_PATTERNS:
        m = pattern.search(t)
        if not m:
            continue
        if kind == "era":
            year = ERAS[m.group(1)] + (1 if m.group(2) == "元" else int(m.group(2)))
            iso = _iso(year, int(m.group(3)), int(m.group(4)))
        else:
            iso = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso and (best is None or m.start() < best[0]):
            best = (m.start(), iso, m.group(0))
    if best:
        return best[1], best[2]
    m = _SHORT_DATE.search(t)
    if m and url:
        for u in _URL_DATE.finditer(urlsplit(url).path.rsplit("/", 1)[-1]):
            y, mo, d = int(u.group(1)), int(u.group(2)), int(u.group(3))
            era_years = {y - base for base in (2018, 1988)}
            if (mo, d) == (int(m.group(2)), int(m.group(3))) and int(m.group(1)) in era_years:
                iso = _iso(y, mo, d)
                if iso:
                    return iso, m.group(0)
    return "", ""


def leading_date(line: str) -> tuple[str, str]:
    """行頭（開きかっこは無視）が日付で始まっていれば、その日付を返す。"""
    head = nfkc(line).lstrip("([【〔 ")
    for pattern, _ in _DATE_PATTERNS:
        m = pattern.match(head)
        if m:
            return parse_date(m.group(0))
    return "", ""


def is_date_only(text: str) -> bool:
    """セルの中身がほぼ日付だけか（「平成１２年１０月１８日（水）」「R3.4.15」など）。"""
    iso, raw = parse_date(text)
    return bool(iso) and len(nfkc(text).replace(nfkc(raw), "", 1).strip()) <= 6


_KANJI = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"(\d+|[一二三四五六七八九十百]+)"
_KAI = re.compile(r"第\s*" + _NUM + r"\s*回")
_RANGE = re.compile(r"第?\s*" + _NUM + r"\s*回?\s*[~〜～\-‐―ー－]\s*第?\s*" + _NUM + r"\s*回")


def _to_int(s: str) -> int:
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch == "百":
            total += (cur or 1) * 100
            cur = 0
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        else:
            cur = _KANJI[ch]
    return total + cur


def parse_number(text: str) -> int | None:
    """「第N回」の N。「第1回～第50回」のような範囲と、「第N回報告」（報告書の通し番号）は None。"""
    t = nfkc(text)
    if _RANGE.search(t):
        return None
    for m in _KAI.finditer(t):
        rest = t[m.end():].lstrip()
        if rest.startswith("報告") and not rest.startswith("報告会"):
            continue
        return _to_int(m.group(1))
    return None


# ---------------------------------------------------------------- 資料の種別

DOC_TYPES = [
    ("議事録", r"議事録|速記録"),
    ("議事要旨", r"議事要旨|議事概要|会議概要|開催概要|議事の概要|結果概要"),
    ("議事次第", r"議事次第|次第"),
    ("名簿", r"名簿|出席者|構成員"),
    ("開催案内", r"開催について|開催延期|開催中止|の中止について|開催のお知らせ"),
    ("計画・活動報告", r"開催計画|活動概要|活動報告"),
    ("報告書", r"報告書|報告|取りまとめ|とりまとめ|取り纏め|提言|答申|意見書"),
    ("発言", r"発言|挨拶|あいさつ|談話|訓示"),
    ("規程", r"要綱|要領|規則|規程|訓令|通達|法令|設置について|運営について|公開について"),
    ("資料", r"資料|別添|別紙|参考|概要|について"),
]
_DOC_TYPES = [(name, re.compile(p)) for name, p in DOC_TYPES]
# 回次がなく開催日だけのリンクや見出しを「会議のもの」とみなす条件。
# 「…の開催について（日付）」は別の会議の告知が混じるので含めない。
MINUTES_WORDS = re.compile(r"議事録|議事要旨|議事概要|議事次第|会議資料|配布資料")


def doc_type(title: str) -> str:
    for name, pattern in _DOC_TYPES:
        if pattern.search(title):
            return name
    return "その他"


def url_format(url: str) -> str:
    ext = os.path.splitext(urlsplit(url).path)[1].lower().lstrip(".")
    return ext or "html"


_WARP_PATH = re.compile(r"^/info:ndljp/pid/\d+/([^/]+)(/.*)$")


def doc_key(url: str) -> str:
    """資料を同定するキー。WARP の URL からは元のホストとパスを取り出す。"""
    sp = urlsplit(url)
    if sp.netloc == "warp.da.ndl.go.jp":
        m = _WARP_PATH.match(sp.path)
        if m:
            return m.group(1) + m.group(2)
    return sp.netloc + sp.path if sp.netloc else url


# ---------------------------------------------------------------- 名簿

MARKS = "◎○◯〇●◇□△☆★"
_MARK_NORMAL = str.maketrans({"◯": "○", "〇": "○"})
_LEAD_MARKS = re.compile(r"^[" + MARKS + r"\s]+")
_LEGEND = re.compile(r"([" + MARKS + r"])\s*印?\s*は\s*(.+?)(?=を|、|。|は|$)")
_AS_OF = re.compile(r"現在")
_KANA_NOTE = re.compile(r"\s*[(（]([ぁ-ゖァ-ヶー\s]+)[)）]$")


def parse_legend(text: str) -> dict[str, str]:
    """「◎は会長を、○は会長代理を示す。」→ {"◎": "会長", "○": "会長代理"}"""
    flat = text.replace("\n", "")
    if "示す" not in flat:
        return {}
    return {m.group(1).translate(_MARK_NORMAL): m.group(2).strip() for m in _LEGEND.finditer(flat)}


def split_marks(cell: str) -> tuple[str, str]:
    first = cell.split("\n", 1)[0]
    m = _LEAD_MARKS.match(first)
    marks = "".join(ch for ch in (m.group(0) if m else "") if not ch.isspace()).translate(_MARK_NORMAL)
    name = first[m.end():] if m else first
    return marks, re.sub(r"\s+", " ", name).strip()


# ---------------------------------------------------------------- 会議体の対応付け

def load_councils(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _external_council_id(url: str) -> str:
    sp = urlsplit(url)
    host = sp.netloc.split(".")
    host_tag = host[1] if len(host) >= 3 and host[0] == "www" else (host[0] if host else "ext")
    parts = [p for p in sp.path.strip("/").split("/") if p and not p.endswith((".html", ".htm"))]
    tail = parts[-1] if parts else "index"
    return f"{host_tag}-{tail}"


def parse_councils(blocks: list[Block], existing: list[dict[str, str]] | None = None,
                   now_iso: str = "") -> list[dict[str, str]]:
    """一覧ページ（/j/policy/agenda/meeting/index.html）のブロック列から会議体一覧を作る。"""
    existing = existing or []
    by_link = {r["link"]: r for r in existing}
    by_id = {r["council_id"]: r for r in existing}
    prefix = pages.BASE + ROOT
    rows: list[dict[str, str]] = []
    for b in blocks:
        if b.kind != "text" or not b.path:
            continue
        section = b.path[-1]
        if b.links:
            for ln in b.links:
                if ln.url.startswith(prefix):
                    link = ln.url[len(pages.BASE):]
                    cid = link[len(ROOT):].rsplit("/", 1)[0]
                else:
                    link = ln.url
                    prev = by_link.get(link)
                    cid = prev["council_id"] if prev else _external_council_id(link)
                prev = by_link.get(link) or by_id.get(cid)
                rows.append({
                    "council_id": cid,
                    "section": section,
                    "name": ln.text,
                    "link": link,
                    "note": "",
                    "first_seen": prev["first_seen"] if prev and prev.get("first_seen") else now_iso,
                })
        elif rows:
            rows[-1]["note"] = (rows[-1]["note"] + " " + b.text).strip()
    return rows


def sync_councils(councils_path: Path = DATA / "councils.csv", root: Path = pages.RAW_DIR,
                  index_url: str = ROOT + "index.html", now_iso: str = "",
                  write: bool = True) -> list[dict[str, str]]:
    """保存済みの一覧ページから councils.csv を更新する。"""
    existing = load_councils(councils_path) if councils_path.exists() else []
    if not now_iso:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    blocks = pages.load(index_url, root)
    updated = parse_councils(blocks, existing=existing, now_iso=now_iso)
    if write and updated:
        write_csv(councils_path, COUNCIL_FIELDS, updated)
    return updated


def council_dir(link: str) -> str:
    """一覧のリンク（…/meeting/<dir>/index.html）から <dir>/ を返す。サイト外は ""。"""
    if not link.startswith(ROOT):
        return ""
    return link[len(ROOT):].rsplit("/", 1)[0] + "/"


class Councils:
    def __init__(self, rows: list[dict[str, str]]):
        self.rows = rows
        self.order = {r["council_id"]: i for i, r in enumerate(rows)}
        self.by_dir = sorted(((council_dir(r["link"]), r) for r in rows if council_dir(r["link"])),
                             key=lambda x: -len(x[0]))

    def of(self, url: str) -> dict[str, str] | None:
        rest = url[len(ROOT):] if url.startswith(ROOT) else None
        if rest is None:
            return None
        for prefix, row in self.by_dir:
            if rest.startswith(prefix):
                return row
        return None


# ---------------------------------------------------------------- 組み立て

SECTION_KEYS = [("date", re.compile(r"^(年月日|日時|開催日時|開催日|日程)$")),
                ("place", re.compile(r"^(場所|開催場所|会場)$")),
                ("attendees", re.compile(r"^(出席者|出席委員|参加者)")),
                ("agenda", re.compile(r"^(議題|議事|議事等|議題等)$"))]
_LEAD_NUMBERING = re.compile(r"^[\s(（]*[0-9０-９一二三四五六七八九十]+[\s)）.．、]*")


PROFILE_KEY = re.compile(r"主管|担当|庶務|根拠|設置|目的|趣旨|所掌|決裁|決済|会合日|部会|委員|構成|任期|定数|事務局")


def section_key(heading: str) -> str:
    h = _LEAD_NUMBERING.sub("", heading).strip()
    for key, pattern in SECTION_KEYS:
        if pattern.search(h):
            return key
    return ""


def flat(text: str) -> str:
    return " / ".join(ln for ln in text.split("\n") if ln)


class Builder:
    def __init__(self, councils: Councils, known_pages: set[str]):
        self.councils = councils
        self.known_pages = known_pages
        self.profiles: list[dict] = []
        self.members: list[dict] = []
        self.meetings: list[dict] = []
        self.documents: list[dict] = []
        self.by_number: dict[tuple, dict] = {}
        self.by_date: dict[tuple, dict] = {}
        self.by_number_date: dict[tuple, dict] = {}
        self.recent: dict[tuple, dict] = {}
        self.attendance: list[dict] = []
        self.persons: list[dict] = []
        self.warnings: list[str] = []

    # --- 会議 ---
    def _new(self, cid: str, body: str, number: int | None) -> dict:
        m = {"council_id": cid, "body": body, "number": number, "label": "", "date": "",
             "date_raw": "", "time": "", "agenda": "", "place": "", "attendees": "", "page": "",
             "found_by": [], "source": []}
        self.meetings.append(m)
        return m

    def meeting(self, cid: str, body: str, number: int | None, date: str, source: str, how: str,
                **fields: str) -> dict | None:
        """（会議体, 部会, 回次）または（会議体, 部会, 開催日）で同じ会議を 1 件にまとめる。"""
        if number is None and not date:
            return None
        if number is not None:
            first = self.by_number.get((cid, body, number))
            if first is None:
                cand = self.by_date.get((cid, body, date)) if date else None
                if cand is not None and cand["number"] is None:
                    m = cand  # 開催日だけで拾っていた会議に回次が付いた
                    m["number"] = number
                else:
                    m = self._new(cid, body, number)
                self.by_number[(cid, body, number)] = m
            elif date and first["date"] and first["date"] != date:
                # 同じ回次で開催日が違う。回次の振り直しか掲載側の誤記なので、まとめずに別の会議として残す。
                m = self.by_number_date.get((cid, body, number, date))
                if m is None:
                    self.warnings.append(f"{cid} {body} 第{number}回: 開催日が食い違う "
                                         f"{first['date']}（{first['source'][0]}） / {date}（{source}）")
                    m = self._new(cid, body, number)
                    self.by_number_date[(cid, body, number, date)] = m
            elif not date:
                # 開催日のない言及は、同じ回次で直前に出てきた会議のものとみなす
                m = self.recent.get((cid, body, number), first)
            else:
                m = first
            self.recent[(cid, body, number)] = m
        else:
            m = self.by_date.get((cid, body, date)) or self._new(cid, body, None)
        if date and not m["date"]:
            m["date"] = date
            m["date_raw"] = fields.get("date_raw", "")
            self.by_date.setdefault((cid, body, date), m)
        for key in ("label", "place", "attendees", "page"):
            if fields.get(key) and not m[key]:
                m[key] = fields[key]
        if len(fields.get("agenda", "")) > len(m["agenda"]):
            m["agenda"] = fields["agenda"]
        if how not in m["found_by"]:
            m["found_by"].append(how)
        if source not in m["source"]:
            m["source"].append(source)
        return m

    def document(self, cid: str, meeting: dict | None, section: str, link: Link, source: str) -> None:
        self.documents.append({"council_id": cid, "meeting": meeting, "section": section,
                               "title": link.text, "doc_type": doc_type(link.text), "url": link.url,
                               "host": urlsplit(link.url).netloc, "format": url_format(link.url),
                               "source": source, "doc_key": doc_key(link.url)})

    # --- 1 ページ ---
    def page(self, url: str, blocks: list[Block], split: frozenset[str] = frozenset(),
             split_all: bool = False) -> None:
        """1 ページ分を取り込む。

        split は、回次を別々に数えている見出し（「総会」「部会」、年度ごとの見出しなど）の集合。
        その見出しの下の会議は、見出しを body に入れて別系列として扱う。split_all は全見出しをそう扱う。
        """
        council = self.councils.of(url)
        if council is None:
            return
        cid = council["council_id"]
        h1 = next((b.text for b in blocks if b.kind == "heading" and b.level == 1), "")
        is_index = url == council["link"]
        h1n, name_n = nfkc(h1), nfkc(council["name"])
        h1_number = parse_number(h1)
        h1_date, h1_date_raw = parse_date(h1)
        range_h1 = bool(_RANGE.search(h1n)) or h1n.startswith("過去")

        body = ""
        if not is_index and h1 and name_n not in h1n and h1_number is None and not range_h1:
            body = h1

        page_meeting = None
        if not is_index and not range_h1 and (
                h1_number is not None or (h1_date and MINUTES_WORDS.search(h1))):
            page_meeting = self.meeting(cid, "", h1_number, h1_date, url, "page",
                                        date_raw=h1_date_raw, label=h1, page=pages.BASE + url)
            if MINUTES_WORDS.search(h1):
                self.document(cid, page_meeting, "", Link(h1, pages.BASE + url), url)
            body = ""
        page_body = body

        def body_of(section: str) -> str:
            if split_all:
                return section
            if section and section in split:
                return f"{page_body}／{section}" if page_body else section
            return page_body

        tables: dict[int, tuple[str, list[str], list[str]]] = {}  # 表番号 → (種類, 列の役割, 列見出し)
        last_in_table: dict[int, dict] = {}
        heading_meeting: tuple[dict, int] | None = None
        as_of = ("", "")
        page_members: list[dict] = []
        sections: dict[str, list[str]] = {}

        for b in blocks:
            if b.kind == "heading":
                if b.level == 1:
                    continue
                if heading_meeting and b.level <= heading_meeting[1]:
                    heading_meeting = None
                number = parse_number(b.text)
                if number is not None and page_meeting is None:
                    date, raw = parse_date(b.text)
                    m = self.meeting(cid, body_of(b.path[-1] if b.path else ""), number, date, url,
                                     "heading", date_raw=raw, label=b.text)
                    heading_meeting = (m, b.level)
                    for ln in b.links:
                        self.document(cid, m, b.text, ln, url)
                continue

            context = heading_meeting[0] if heading_meeting else page_meeting
            section = b.path[-1] if b.path else ""
            body = body_of(section)

            if b.kind == "text":
                stripped = b.text.strip("[]［］〔〕()（） ")
                if len(b.text) < 40 and (
                    _AS_OF.search(b.text)
                    or (section and re.search(r"委員|名簿|構成員", section)
                        and b.text != stripped and is_date_only(stripped))
                ):
                    iso, _ = parse_date(stripped)
                    if iso:
                        as_of = (iso, stripped)
                legend = parse_legend(b.text)
                if legend:
                    for row in page_members:
                        if row["mark"] and not row["role"]:
                            row["role"] = "・".join(legend[ch] for ch in row["mark"] if ch in legend)
                if page_meeting is not None and section_key(section):
                    sections.setdefault(section_key(section), []).append(flat(b.text))
                for ln in b.links:
                    self.link(cid, body, context, section, ln, url, near=b.text)
                continue

            # --- 表の行 ---
            if all(c.header for c in b.cells) and len(b.cells) >= 2:
                heads = [flat(c.text) for c in b.cells]
                tables[b.table] = (table_kind(heads), column_roles(heads), heads)
                continue
            kind, roles, heads = tables.get(b.table, ("", [], []))
            key_value = len(b.cells) == 2 and b.cells[0].header and not b.cells[1].header
            key = b.cells[0].text
            if (kind == "" and key_value and PROFILE_KEY.search(key) and not is_date_only(key)
                    and (parse_number(key) is None or "会合日" in key)):
                kind = "profile"
                tables[b.table] = (kind, [], [])
            if kind == "members":
                row = self.member_row(cid, page_body, section, as_of, b, heads, url)
                if row:
                    page_members.append(row)
                continue
            if kind == "meetings" and context is None:
                m = self.meeting_row(cid, body, b, roles, heads, url, last_in_table.get(b.table))
                if m is not None:
                    last_in_table[b.table] = m
                continue
            if kind == "profile":
                if key_value:
                    self.profiles.append({"council_id": cid, "key": flat(b.cells[0].text),
                                          "value": flat(b.cells[1].text), "source": url})
                for ln in b.links:
                    self.link(cid, body, context, flat(b.cells[0].text), ln, url, detect=False)
                continue
            if kind == "" and context is None:
                m = self.meeting_row(cid, body, b, [], [], url, last_in_table.get(b.table))
                if m is not None:
                    last_in_table[b.table] = m
                    continue
            for c in b.cells:
                for ln in c.links:
                    self.link(cid, body, context, section, ln, url, near=c.text)

        if page_meeting is not None:
            m = page_meeting
            date, raw = parse_date(" ".join(sections.get("date", [])))
            if date and not m["date"]:
                m["date"], m["date_raw"] = date, raw
                self.by_date.setdefault((cid, "", date), m)
            for key in ("place", "attendees", "agenda"):
                value = " / ".join(sections.get(key, []))
                if value and not m[key]:
                    m[key] = value

    def link(self, cid: str, body: str, context: dict | None, section: str, ln: Link, source: str,
             detect: bool = True, near: str = "") -> None:
        """リンク 1 件を資料として登録する。会議の文脈がなければ、文言から会議を探す。

        near はリンクを含む段落やセルの本文。リンクの直後に書かれた開催日を拾うのに使う。
        """
        meeting = context
        if meeting is None and detect:
            number = parse_number(ln.text)
            date, raw = parse_date(ln.text, ln.url)
            minutes = bool(MINUTES_WORDS.search(ln.text))
            if not date and ln.text and ln.text in near:
                at = near.index(ln.text)
                after = near[at + len(ln.text):].split("\n", 1)[0][:30]
                line = near[near.rfind("\n", 0, at) + 1:].split("\n", 1)[0]
                if nfkc(after).lstrip().startswith(("(", "[", "【", "〔")):
                    date, raw = parse_date(after)  # 「…議事要旨（平成26年8月28日）」
                elif leading_date(line)[0]:
                    date, raw = leading_date(line)  # 「（平成27年9月15日）議事録｜議決書」
                    minutes = bool(MINUTES_WORDS.search(line))
            if number is not None or (date and minutes):
                meeting = self.meeting(cid, body, number, date, source, "link", date_raw=raw)
        split = urlsplit(ln.url)
        in_site_page = (split.netloc == "www.mod.go.jp" and url_format(ln.url) == "html")
        if meeting is None and (in_site_page or split.netloc not in ("www.mod.go.jp", "warp.da.ndl.go.jp")):
            return  # 会議に結び付かないサイト内ページ・外部サイトへのリンクは案内なので載せない
        self.document(cid, meeting, section, ln, source)

    def member_row(self, cid: str, body: str, section: str, as_of: tuple[str, str], b: Block,
                   heads: list[str], source: str) -> dict | None:
        cols = {"name": None, "title": None, "note": None}
        for i, h in enumerate(heads):
            if "氏名" in h or "委員名" in h:
                cols["name"] = i if cols["name"] is None else cols["name"]
            elif "備考" in h:
                cols["note"] = i
            elif cols["title"] is None:
                cols["title"] = i
        if cols["name"] is None or cols["name"] >= len(b.cells):
            return None
        mark, name = split_marks(b.cells[cols["name"]].text)
        kana = _KANA_NOTE.search(name)  # 「中村 義人（なかむら よしと）」
        if kana:
            name, kana = name[:kana.start()].strip(), " ".join(kana.group(1).split())
        if not name:
            return None

        def cell(key: str) -> str:
            i = cols[key]
            return flat(b.cells[i].text) if i is not None and i < len(b.cells) else ""

        row = {"council_id": cid, "body": body, "group": section, "as_of": as_of[0],
               "as_of_raw": as_of[1], "name": name, "kana": kana or "", "mark": mark, "role": "",
               "title": cell("title"),
               "note": cell("note"), "source": source}
        self.members.append(row)
        return row

    def meeting_row(self, cid: str, body: str, b: Block, roles: list[str], heads: list[str],
                    source: str, previous: dict | None) -> dict | None:
        cells = b.cells
        offset = len(roles) - len(cells) if roles and len(cells) < len(roles) else 0  # rowspan の続き
        number, label, date, date_raw, agenda, page = None, "", "", "", "", ""
        for i, c in enumerate(cells):
            role = roles[i + offset] if roles and i + offset < len(roles) else ""
            # 見出し行のない表では、先頭セルが短い「第N回」のとき、または日付だけのセルのときに限って会議の行とみなす
            starts_with_number = bool(re.match(r"\s*第\s*" + _NUM + r"\s*回", nfkc(c.text)))
            guess_label = not roles and i == 0 and starts_with_number and len(nfkc(c.text)) <= 25
            guess_date = not roles and i <= 1 and is_date_only(c.text)
            unnamed = bool(roles) and role == "" and starts_with_number
            if (role == "label" or guess_label or unnamed) and number is None:
                n = parse_number(c.text)
                if n is not None:
                    number, label = n, flat(c.text)
                    for ln in c.links:
                        path = urlsplit(ln.url).path
                        if path in self.known_pages:
                            page = ln.url
            if (role == "date" or guess_date or (roles and role == "" and is_date_only(c.text))) and not date:
                d, raw = parse_date(c.text)
                if d:
                    date, date_raw = d, flat(c.text)
            if role == "label" and not label and c.text and number is None:
                label = c.text.split("\n", 1)[0]
            if role in ("agenda", "mixed"):
                link_texts = {ln.text for ln in c.links}
                lines = [ln for ln in c.text.split("\n") if ln and ln not in link_texts]
                agenda = " / ".join(lines)
        if number is None and not date:
            if previous is None or not b.links:
                return None
            m = previous  # 回次も開催日もない行は、直前の会議の続き（資料だけの行）
        else:
            m = self.meeting(cid, body, number, date, source, "table", date_raw=date_raw, label=label,
                             agenda=agenda, page=page)
        for i, c in enumerate(cells):
            head = heads[i + offset] if heads and i + offset < len(heads) else ""
            for ln in c.links:
                if ln.url == page:
                    continue
                self.document(cid, m, head, ln, source)
        return m


def table_kind(heads: list[str]) -> str:
    joined = " ".join(heads)
    if "氏名" in joined or "委員名" in joined:
        return "members"
    if re.search(r"年月日|開催日|日時|議事次第|議事概要|議事録|議事要旨|会議資料", joined):
        return "meetings"
    return "other"


def column_roles(heads: list[str]) -> list[str]:
    roles = []
    for i, h in enumerate(heads):
        if re.search(r"年月日|開催日|日時", h):
            roles.append("date")
        elif re.search(r"開催回|区分|回数|回次", h) or (h == "" and i == 0):
            roles.append("label")
        elif re.search(r"議題", h) and re.search(r"議事録|議事要旨|資料", h):
            roles.append("mixed")
        elif re.search(r"議題|表題|案件|内容", h):
            roles.append("agenda")
        elif h == "":
            roles.append("")
        else:
            roles.append("docs")
    return roles


def build(councils_path: Path = DATA / "councils.csv", manifest: Path = MANIFEST,
          root: Path = pages.RAW_DIR, texts: Path = DATA / "texts.csv",
          repo: Path = REPO) -> Builder:
    councils = Councils(load_councils(councils_path))
    with manifest.open(encoding="utf-8", newline="") as f:
        urls = [r["url"] for r in csv.DictReader(f)]
    builder = Builder(councils, set(urls))

    def order(url: str):
        c = councils.of(url)
        return (councils.order[c["council_id"]] if c else 10**6, url != (c or {}).get("link"), url)

    for url in sorted(urls, key=order):
        blocks = pages.load(url, root)
        builder.page(url, blocks, split=separate_series(councils, builder.known_pages, url, blocks))
    finish(builder)
    apply_minutes(builder, texts, repo=repo)
    return builder


def apply_minutes(b: Builder, texts_path: Path = DATA / "texts.csv", repo: Path = REPO) -> None:
    """議事要旨・議事録の本文（extract_text.py の出力）の冒頭から、開催日時・場所・出席者を補う。

    ページから読み取った値は上書きしない。PDF の開催日がページの記載と違うときは警告だけ出す。
    出席者は 1 人 1 行にして b.attendance に入れる。
    """
    b.attendance = []
    extracted: dict[str, dict[str, str]] = {}
    if texts_path.exists():
        with texts_path.open(encoding="utf-8", newline="") as f:
            extracted = {doc_key(r["url"]): r for r in csv.DictReader(f)}
    texts = {k: r["text"] for k, r in extracted.items() if r["text"]}
    by_meeting: dict[str, list[dict]] = {}
    for d in b.documents:
        dk = d.get("doc_key") or doc_key(d["url"])
        d["doc_key"] = dk
        row = extracted.get(dk, {})
        d["text"], d["pages"], d["chars"] = row.get("text", ""), row.get("pages", ""), row.get("chars", "")
        if d["meeting_id"] and d["doc_type"] in MINUTES_TYPES and dk in texts:
            by_meeting.setdefault(d["meeting_id"], []).append(d)
    def attend(m: dict, raw: str, source: str) -> None:
        for seq, (group, token) in enumerate(minutes.split_attendees(raw), 1):
            surname, role = minutes.split_name(token)
            if not role and group.split("/")[-1] in minutes.ROLES:
                role = group.split("/")[-1]  # 「座長：山谷 清志氏」のように、区分が役職になっている書き方
            b.attendance.append({"meeting_id": m["meeting_id"], "council_id": m["council_id"],
                                 "group": group, "seq": seq, "name_raw": token, "surname": surname,
                                 "role": role, "source": source})

    dated_now: list[dict] = []  # 今回、資料から開催日が分かった会議
    for m in b.meetings:
        used = False
        had_date = bool(m["date"])
        docs = sorted(by_meeting.get(m["meeting_id"], []), key=lambda d: MINUTES_TYPES.index(d["doc_type"]))
        for d in docs:
            text = (repo / texts[d["doc_key"]]).read_text(encoding="utf-8")
            title = " ".join([ln for ln in text.split("\n") if ln.strip()][:3])
            title_number = parse_number(title)
            # 合同開催（「第5回 / 第6回(総会)合同」）では、表題がもう一方の回次になっていることがある
            in_label = {_to_int(n) for n in _KAI.findall(nfkc(m["label"]))}
            if (title_number is not None and m["number"] != "" and title_number != m["number"]
                    and title_number not in in_label):
                b.warnings.append(f"{m['meeting_id']}: 資料の表題は第{title_number}回（{d['url']}）")
                continue
            h = minutes.parse_header(text)
            date, _ = parse_date(h["date_raw"])
            weekday = minutes._weekday_mismatch(date, h["date_raw"])
            if weekday:
                b.warnings.append(f"{m['meeting_id']}: 資料の記載「{h['date_raw'][:24]}」の曜日が日付と合わない"
                                  f"（{date} は{weekday}曜）。開催日は要確認（{d['url']}）")
            if date and not m["date"]:
                m["date"], m["date_raw"], used = date, h["date_raw"], True
            elif date and m["date"] != date:
                b.warnings.append(f"{m['meeting_id']}: ページの開催日 {m['date']} と資料の開催日 {date} が"
                                  f"食い違う（{d['url']}）")
            for key in ("time", "place", "agenda"):
                if h[key] and not m[key]:
                    m[key], used = h[key], True
            if h["attendees"] and not m["attendees"]:
                m["attendees"] = " / ".join(" ".join(ln.split()) for ln in h["attendees"].split("\n") if ln.strip())
                used = True
                attend(m, h["attendees"], d["url"])
        if used:
            m["found_by"] += "+pdf"
        elif m["attendees"]:  # 議事録が HTML で載っているページから読み取った出席者
            attend(m, m["attendees"].replace(" / ", "\n"), m["page"])
        if m["date"] and not had_date and m["number"] != "":
            dated_now.append(m)
    merge_dated(b, dated_now)
    b.persons = persons.link(b.members, b.attendance, {m["meeting_id"]: m["date"] for m in b.meetings},
                             b.councils.order)


def merge_dated(b: Builder, dated_now: list[dict]) -> None:
    """回次だけで拾っていた会議の開催日が資料から分かったとき、開催日だけで拾っていた同じ日の行とまとめる。

    一覧ページが「開催日と議題の表」と「第N回議事録へのリンク」に分かれている会議体では、
    同じ会議が 2 行になっている。開催日が一致すれば同じ会議とみなす。
    """
    undated = {(m["council_id"], m["body"], m["date"]): m for m in b.meetings
               if m["number"] == "" and m["date"]}
    drop: dict[str, str] = {}
    for m in dated_now:
        dup = undated.pop((m["council_id"], m["body"], m["date"]), None)
        if dup is None:
            continue
        for key in ("label", "agenda", "place", "attendees", "page", "time"):
            if dup[key] and not m[key]:
                m[key] = dup[key]
        m["found_by"] = "+".join(dict.fromkeys(dup["found_by"].split("+") + m["found_by"].split("+")))
        m["source"] = " ".join(dict.fromkeys(m["source"].split(" ") + dup["source"].split(" ")))
        drop[dup["meeting_id"]] = m["meeting_id"]
    if not drop:
        return
    b.meetings = [m for m in b.meetings if m["meeting_id"] not in drop]
    seen = {(d["meeting_id"], d["url"]) for d in b.documents if d["meeting_id"] not in drop}
    docs = []
    for d in b.documents:
        if d["meeting_id"] in drop:
            d["meeting_id"] = drop[d["meeting_id"]]
            if (d["meeting_id"], d["url"]) in seen:
                continue
            seen.add((d["meeting_id"], d["url"]))
        docs.append(d)
    b.documents = docs
    for a in b.attendance:
        a["meeting_id"] = drop.get(a["meeting_id"], a["meeting_id"])
    counts: dict[str, int] = {}
    for d in b.documents:
        counts[d["meeting_id"]] = counts.get(d["meeting_id"], 0) + 1
    for m in b.meetings:
        m["n_documents"] = counts.get(m["meeting_id"], 0)


def separate_series(councils: Councils, known_pages: set[str], url: str, blocks: list[Block]) -> frozenset[str]:
    """回次を別々に数えている見出しを探す。

    見出しごとに会議を拾ってみて、同じ回次なのに開催日が違う見出しの組があれば、
    その見出しは別系列（総会と部会、年度ごとの通し番号など）とみなす。
    """
    probe = Builder(councils, known_pages)
    probe.page(url, blocks, split_all=True)
    dates: dict[str, dict[int, str]] = {}
    for m in probe.meetings:
        if m["number"] is not None and m["date"]:
            dates.setdefault(m["body"], {})[m["number"]] = m["date"]
    found = set()
    names = list(dates)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if any(dates[b].get(n, d) != d for n, d in dates[a].items()):
                found.update((a, b))
    return frozenset(found)


def finish(b: Builder) -> None:
    """meeting_id を振り、資料の重複を落とし、並べ替える。"""
    order = b.councils.order
    used = set()
    for m in b.meetings:
        body = f"/{m['body']}" if m["body"] else ""
        tail = f"#{m['number']}" if m["number"] is not None else f"@{m['date']}"
        mid = f"{m['council_id']}{body}{tail}"
        if mid in used:  # 同じ回次で開催日が違うもの
            mid = f"{mid}@{m['date']}"
        used.add(mid)
        m["meeting_id"] = mid
    seen, docs = set(), []
    for d in b.documents:
        meeting = d.pop("meeting")
        d["meeting_id"] = meeting["meeting_id"] if meeting else ""
        key = (d["council_id"], d["meeting_id"], d["url"])
        if key in seen:
            continue
        seen.add(key)
        docs.append(d)
    attached = {(d["council_id"], d["url"]) for d in docs if d["meeting_id"]}
    docs = [d for d in docs if d["meeting_id"] or (d["council_id"], d["url"]) not in attached]
    counts: dict[str, int] = {}
    for d in docs:
        counts[d["meeting_id"]] = counts.get(d["meeting_id"], 0) + 1
    for m in b.meetings:
        m["n_documents"] = counts.get(m["meeting_id"], 0)
        m["number"] = "" if m["number"] is None else m["number"]
        m["found_by"] = "+".join(m["found_by"])
        m["source"] = " ".join(m["source"])
    b.documents = docs
    b.meetings.sort(key=lambda m: (order[m["council_id"]], m["body"],
                                   m["number"] if m["number"] != "" else 10**6, m["date"]))


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="CSV を書き換えない")
    args = ap.parse_args()

    b = build()
    if not args.dry_run:
        write_csv(DATA / "council_profiles.csv", PROFILE_FIELDS, b.profiles)
        write_csv(DATA / "members.csv", MEMBER_FIELDS, b.members)
        write_csv(DATA / "meetings.csv", MEETING_FIELDS, b.meetings)
        write_csv(DATA / "documents.csv", DOCUMENT_FIELDS, b.documents)
        write_csv(DATA / "attendance.csv", ATTENDANCE_FIELDS, b.attendance)
        write_csv(DATA / "persons.csv", persons.PERSON_FIELDS, b.persons)
    with_meeting = sum(1 for d in b.documents if d["meeting_id"])
    print(f"概要 {len(b.profiles)} 行 / 委員 {len(b.members)} 行 / 会議 {len(b.meetings)} 件 / "
          f"資料 {len(b.documents)} 件（うち会議に結び付くもの {with_meeting} 件） / "
          f"出席者 {len(b.attendance)} 行（{len({a['meeting_id'] for a in b.attendance})} 会議）"
          + ("（dry-run）" if args.dry_run else ""))
    for w in b.warnings:
        print(f"  警告: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
