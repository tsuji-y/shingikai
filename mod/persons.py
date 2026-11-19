"""委員名簿と出席記録から人物表を作り、各行を人物に結び付ける。

対象は会議体の委員・有識者だけ。省側の出席者は「林服務管理官」のように姓と職名の境目が決められず、
職名だけの記載も多いので、人物としては扱わない。

結び付けの根拠（basis）:
    full_name     氏名が書かれている。氏名が同じなら、会議体をまたいでも同じ人物とみなす
    surname       「太田会長」のように姓だけ。同じ会議体に、その姓の人物が 1 人だけ分かっている
    surname_only  姓だけで、氏名の分かる人物が同じ会議体にいない。会議体ごとに「会議体:姓」の仮の人物を置く
    ambiguous     姓だけで、同じ会議体にその姓の人物が複数いる。結び付けない

同姓同名の別人、姓だけが同じ別の時期の委員は区別できない。basis はその確からしさの目安。
"""
from __future__ import annotations

import re
import unicodedata

import minutes

# 同じ人物の表記ゆれとして扱う異体字（照合のときだけ置き換える。表示用の氏名は元の字のまま）
VARIANTS = str.maketrans({"髙": "高", "﨑": "崎", "德": "徳", "邊": "辺", "邉": "辺", "齋": "斎", "齊": "斉",
                          "濵": "浜", "濱": "浜", "廣": "広", "澤": "沢", "櫻": "桜", "眞": "真", "惠": "恵",
                          "國": "国", "藏": "蔵", "壽": "寿", "榮": "栄", "瀧": "滝", "條": "条"})
_BRACKETS = re.compile(r"[(\[【〔]([^()\[\]【】〔〕]*)[)\]】〕]")
_MEMBER_GROUP = re.compile(r"委員|有識者|座長|構成員")
_OFFICIAL_GROUP = re.compile(r"省|庁|官側|監察官|幹事|事務局|自衛隊|幕僚|内部部局|説明者|オブザーバー")
PERSON_FIELDS = ["person_id", "name", "surname", "basis", "councils", "roster_councils", "roles",
                 "affiliations", "n_meetings", "first_date", "last_date"]


def key(name: str) -> str:
    """照合用の氏名。空白を除き、全角英数字と異体字をそろえる。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name)).translate(VARIANTS)


def is_member(row: dict) -> bool:
    """出席記録の行が委員・有識者のものか。"""
    if row["role"]:
        return True
    return bool(_MEMBER_GROUP.search(row["group"])) and not _OFFICIAL_GROUP.search(row["group"])


def name_parts(name_raw: str) -> tuple[str, str]:
    """出席者の表記を（氏名, 所属）に分ける。「太田会長」のように姓だけなら氏名は ""。

    「青木 節子(慶應義塾大学大学院教授)」→（"青木 節子", "慶應義塾大学大学院教授"）
    「榊原定征(座長)」→（"榊原定征", ""）
    """
    token = unicodedata.normalize("NFKC", name_raw).strip()
    base, notes = _split_brackets(token)
    base = re.sub(r"(氏|様|さん)$", "", base.strip())
    compact = re.sub(r"\s+", "", base)
    for role in minutes.ROLES:
        if compact.endswith(role) and len(compact) > len(role):
            stem = base[:-len(role)].strip() if base.endswith(role) else ""
            if " " not in stem:
                return "", ""  # 「太田会長」
            base = stem  # 「山田 太郎委員」
            break
    name = " ".join(base.split())
    if not looks_like_name(name):
        return "", ""
    affiliation = " ".join(n for n in notes if re.sub(r"\s+", "", n) not in minutes.ROLES and n != "敬称略")
    return name, affiliation


_KANJI_FIRST = re.compile(r"^[㐀-鿿豈-﫿々]")
_NOT_A_NAME = re.compile(r"(長|官|員|役|教授|理事|顧問|参与|監事|主幹|代表|弁護士|会計士|アドバイザー|フェロー|リーダー|"
                         r"研究所|大学|会社|法人|機構|協会|センター)$")


def looks_like_name(name: str) -> bool:
    """氏名らしい表記か。所属や職名の行を人物として拾わないための確認。

    「青木 節子」（姓 4 字以内・名 5 字以内）か、「榊原定征」（空白なしで 2〜6 字）。漢字で始まり、
    英数字を含まず、「…長」「…教授」のような職名で終わらないこと。
    """
    parts = name.split(" ")
    compact = "".join(parts)
    if not _KANJI_FIRST.match(compact) or re.search(r"[0-9A-Za-z]", compact) or _NOT_A_NAME.search(compact):
        return False
    if len(parts) == 2:
        return len(parts[0]) <= 4 and len(parts[1]) <= 5
    return len(parts) == 1 and 2 <= len(compact) <= 6


def _split_brackets(token: str) -> tuple[str, list[str]]:
    """最初のかっこより前と、かっこ書きの中身（入れ子は外側の単位で）・かっこの外の続きに分ける。"""
    opens, closes = "([【〔", ")]】〕"
    start = next((i for i, ch in enumerate(token) if ch in opens), len(token))
    base, notes, depth, buf = token[:start], [], 0, ""
    for ch in token[start:]:
        if ch in opens:
            if depth == 0 and buf.strip():
                notes.append(buf.strip())
            buf = buf + ch if depth else ""
            depth += 1
        elif ch in closes and depth:
            depth -= 1
            if depth == 0:
                notes.append(buf.strip())
                buf = ""
            else:
                buf += ch
        else:
            buf += ch
    if buf.strip():
        notes.append(buf.strip())
    return base, [n for n in notes if n]


class People:
    def __init__(self) -> None:
        self.by_id: dict[str, dict] = {}
        self.in_council: dict[str, list[dict]] = {}

    def _person(self, pid: str, name: str, surname: str, basis: str) -> dict:
        p = self.by_id.get(pid)
        if p is None:
            p = self.by_id[pid] = {"person_id": pid, "name": name, "surname": surname, "basis": basis,
                                   "councils": [], "roster_councils": [], "roles": [], "affiliations": [],
                                   "meetings": set(), "dates": []}
        if " " in name and " " not in p["name"]:
            p["name"] = name  # 姓と名の区切りが分かる表記を優先する
        if surname and not p["surname"]:
            p["surname"] = surname
        return p

    def full(self, name: str, council: str) -> dict:
        """氏名の分かる人物。"""
        surname = name.split(" ")[0] if " " in name else ""
        p = self._person(key(name), name, surname, "full_name")
        self._join(p, council)
        return p

    def _join(self, p: dict, council: str) -> None:
        if council not in p["councils"]:
            p["councils"].append(council)
            self.in_council.setdefault(council, []).append(p)

    def by_surname(self, surname: str, council: str) -> tuple[dict | None, str]:
        """姓だけの記載を、同じ会議体の人物に結び付ける。戻り値は（人物, basis）。"""
        s = key(surname)
        known = [p for p in self.in_council.get(council, []) if p["basis"] == "full_name"
                 and (key(p["surname"]) == s if p["surname"] else p["person_id"].startswith(s) and len(p["person_id"]) > len(s))]
        if len(known) == 1:
            return known[0], "surname"
        if len(known) > 1:
            return None, "ambiguous"
        p = self._person(f"{council}:{s}", surname, surname, "surname_only")
        self._join(p, council)
        return p, "surname_only"

    @staticmethod
    def note(p: dict, *, role: str = "", affiliation: str = "", meeting: str = "", date: str = "") -> None:
        if role and role not in p["roles"]:
            p["roles"].append(role)
        if affiliation and affiliation not in p["affiliations"]:
            p["affiliations"].append(affiliation)
        if meeting:
            p["meetings"].add(meeting)
        if date:
            p["dates"].append(date)

    def rows(self, order: dict[str, int]) -> list[dict]:
        out = []
        for p in self.by_id.values():
            dates = sorted(p["dates"])
            out.append({"person_id": p["person_id"], "name": p["name"], "surname": p["surname"],
                        "basis": p["basis"], "councils": " ".join(sorted(p["councils"], key=order.get)),
                        "roster_councils": " ".join(sorted(p["roster_councils"], key=order.get)),
                        "roles": " / ".join(p["roles"]), "affiliations": " | ".join(p["affiliations"]),
                        "n_meetings": len(p["meetings"]), "first_date": dates[0] if dates else "",
                        "last_date": dates[-1] if dates else ""})
        out.sort(key=lambda r: (r["basis"] != "full_name", r["person_id"]))
        return out


def link(members: list[dict], attendance: list[dict], meeting_dates: dict[str, str],
         order: dict[str, int]) -> list[dict]:
    """members と attendance の各行に person_id（attendance には person_basis も）を入れ、人物表を返す。"""
    people = People()
    for r in members:  # 現在の名簿
        p = people.full(r["name"], r["council_id"])
        if r["council_id"] not in p["roster_councils"]:
            p["roster_councils"].append(r["council_id"])
        People.note(p, role=r["role"], affiliation=r["title"])
        r["person_id"] = p["person_id"]

    pending = []
    for a in attendance:
        a["person_id"], a["person_basis"] = "", ""
        if not is_member(a):
            continue
        name, affiliation = name_parts(a["name_raw"])
        if name:  # 氏名の書かれた出席記録
            p = people.full(name, a["council_id"])
            a["person_id"], a["person_basis"] = p["person_id"], "full_name"
            People.note(p, role=a["role"], affiliation=affiliation, meeting=a["meeting_id"],
                        date=meeting_dates.get(a["meeting_id"], ""))
        elif a["surname"]:
            pending.append(a)
    for a in pending:  # 姓だけの出席記録。氏名の分かる人物が出そろってから結び付ける
        p, basis = people.by_surname(a["surname"], a["council_id"])
        a["person_basis"] = basis
        if p is not None:
            a["person_id"] = p["person_id"]
            People.note(p, role=a["role"], meeting=a["meeting_id"], date=meeting_dates.get(a["meeting_id"], ""))
    return people.rows(order)
