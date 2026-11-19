"""議事録・議事要旨の本文（テキスト）の冒頭から、開催日時・場所・出席者・議題を読み取る。

冒頭の書き方は会議体ごとに違う。扱うのは次の 3 通り:

    １ 日時            １ 会議の日時及び場所        日時：令和７年７月２８日（月）１３３０〜１５００
      令和６年…        （１）日時                   場所：防衛省会議室
    ２ 場所              令和７年…                  出席者：（部会委員）…
    ３ 出席者          （２）場所
      （委員）…        ２ 出席者

「議事」「審議の概要」など本文の始まりにあたる見出しが出たら、そこで読むのをやめる。
"""
from __future__ import annotations

import re
import unicodedata

HEADER_LINES = 80  # 冒頭のこの行数だけを見る

_SPACES = re.compile(r"\s+")
_NUMBERING = re.compile(r"^[(（]?[0-9一二三四五六七八九十]{1,2}[)）]?[.．、]?")
_PLAIN_NUMBERED = re.compile(r"^\s*[0-9一二三四五六七八九十]{1,2}(\s+|[.．、]\s*)(?![0-9])\S")
_FIELDS = [
    ("container", re.compile(r"^(会議の)?(開催)?(日時|開催日)(等|(及び|・|、|並びに)(開催)?場所)")),
    ("date", re.compile(r"^(開催)?(日時|年月日|期日|開催日)")),
    ("place", re.compile(r"^(開催)?(場所|会場)|^開催(要領|方法|形式|形態)")),  # 「開催要領：オンライン会議」
    ("attendees", re.compile(r"^(出席者|出席委員|参加者|出席)")),
    ("agenda", re.compile(r"^(議題|議事次第|審議事項|審議案件|案件)")),
]
_BODY_START = re.compile(r"^(議事|議事概要|議事の概要|議事内容|議事要旨|審議の?概要|審議内容|審議結果|概要|内容|質疑|主な意見|"
                         r"議事録|発言要旨|会議経過|配布資料|配付資料|資料)(及び.*|等|の.*)?$")
_SEPARATOR = re.compile(r"^[:：]")
_HIRAGANA = re.compile(r"[ぁ-ん]")
_SENTENCE = re.compile(r"について|により|に引き続き|を行[いっう]|であ[るり]|ました|ます|され[たて]|として|において|のため|"
                       r"を踏まえ|があ[っり]|となっ|を受け|に対し|を実施|を確認|から説明|より説明|の説明")
_DATE_LINE = re.compile(r"^[(（]?(令和|平成|昭和)(元|\d{1,2})年\d{1,2}月\d{1,2}日|^[(（]?(19|20)\d{2}年\d{1,2}月\d{1,2}日")
_LIMITS = {"date": 3, "place": 3, "attendees": 30, "agenda": 20}

_TIME = re.compile(r"(\d{1,2})\s*[:時]\s*(\d{2})?\s*分?\s*(?:[~〜～\-－―]|から)\s*(\d{1,2})\s*[:時]\s*(\d{2})?")
_TIME_4 = re.compile(r"(?<!\d)([0-2]\d)([0-5]\d)\s*[~〜～\-－―]\s*([0-2]\d)([0-5]\d)(?!\d)")

_GROUP_PAREN = re.compile(r"^[(（【〔\[]([^)）】〕\]]{1,25})[)）】〕\]]\s*(.*)$")
_GROUP_COLON = re.compile(r"^([^、,，:：()（）]{1,15})[:：]\s*(.*)$")
# 「・有識者」「・防衛省（主要な参加者）」のように、行頭に印の付いた区分の名前だけの行
_TOP_LABEL = r"(?:[^、,，:：\s()]*(?:側|省|庁)|(?:部外)?有識者|事務局|オブザーバー)"
_GROUP_BULLET = re.compile(r"^[・○●◯■◆□◇]\s*((?:" + _TOP_LABEL + r"|委員|専門委員|臨時委員|幹事|説明者|関係者|その他)"
                           r"(?:\([^)]*\))?)()$")
# 印のない区分の名前だけの行（「有識者（敬称略）」「防衛省側」）。「委員」だけの行は「山本／委員」の折り返しなので含めない
_GROUP_BARE = re.compile(r"^(" + _TOP_LABEL + r"(?:\([^)]*\))?)()$")
_TOP_LEVEL = re.compile(r"^" + _TOP_LABEL + r"$|^委員$|^(分科会|部会|審議会|専門|臨時)委員$")
_ANNOTATION = re.compile(r"\s*\((?:五十音順|50音順|敬称略|順不同)\)")
_OTHER_LABEL = re.compile(r"^(会議資料|配布資料|配付資料|資料|議事|議事概要|議事次第|その他)[:：]")
_ETC = re.compile(r"\s*(ほか|他|外|等)(\d+名)?$")
_TITLED = re.compile(r"(会長|座長|委員長|委員|代理|顧問|参与|[長官員])$")  # 「田内会長」「林委員」のように肩書きで終わる
# 会議体での役職。「政策参与」「顧問」は省側の職名にも使われるので含めない
ROLES = ["分科会長代理", "部会長代理", "委員長代理", "会長代理", "座長代理", "分科会長", "部会長", "委員長",
         "会長", "座長", "専門委員", "臨時委員", "特別委員", "委員"]
_MEMBER_GROUP = re.compile(r"委員|有識者|座長|構成員")
_OFFICIAL_GROUP = re.compile(r"省|庁|官側|監察官|幹事|事務局|自衛隊|幕僚|内部部局|説明者|オブザーバー")
# 「岩﨑 洋一」「金子 博文(座長代理) 一般財団法人…」のように、姓と名を空白で区切った氏名で始まる行
_NAME_LINE = re.compile(r"^[^\s()\[\]、]{1,4}\s+[^\s()\[\]、]{1,5}(?:氏|様)?(?:\s*[(\[【〔].*|\s{2,}.*)?$")


def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _compact(line: str) -> str:
    return _SPACES.sub("", nfkc(line))


def _heading(line: str) -> tuple[str, str, bool]:
    """行が項目の見出しなら（項目名, 同じ行に続く内容, 番号付きか）を返す。"""
    compact = _compact(line)
    rest = _NUMBERING.sub("", compact)
    numbered = rest != compact
    # 「４　審議の概要」のように番号の後ろが空いている行だけを、項目以外の見出しとみなす
    plain_numbered = bool(_PLAIN_NUMBERED.match(nfkc(line)))
    for name, pattern in _FIELDS:
        m = pattern.match(rest)
        if not m:
            continue
        tail = rest[m.end():]
        if name == "container":
            # 「開催日及び場所 令和５年３月１４日（火）…」のように、同じ行に日付が続く書き方もある
            return name, (_SEPARATOR.sub("", tail) if _DATE_LINE.match(_SEPARATOR.sub("", tail)) else ""), numbered
        value = tail
        if _SEPARATOR.match(tail):  # 「日時：…」。空白を残した元の表記から取り直す
            src = nfkc(line)
            value = src[re.search(r"[:：]", src).end():].strip()
        elif tail:
            spaced = _after_gap(line, m.group(0))
            if spaced:  # 「２．場 所　　第１省議室」。見出しと内容の間が空いている
                value = spaced
            elif name != "date" or not re.match(r"^\(?(令和|平成|昭和|\d{4}年)", tail):
                # 「出席者名簿のとおり」のような文や、「場所の変更について」のような別の見出しは項目とみなさない
                continue
        return name, value, numbered
    if (numbered or len(rest) <= 12) and _BODY_START.match(rest.rstrip(":：")):
        return "body", "", numbered
    if _OTHER_LABEL.match(rest):
        return "other", "", numbered
    if plain_numbered and len(rest) <= 25 and "。" not in rest and "、" not in rest:
        return "other", "", True
    return "", "", False


def _is_sentence(line: str) -> bool:
    """出席者の並びではなく、本文の文になっている行か。

    句点があるか、文にしか出ない言い回しを含む行。ひらがなの多さだけでは決めない
    （「護衛艦「くまの」艦長、潜水艦「ずいりゅう」艦長ほか」は名前の並び）。
    """
    if "。" in line:
        return True
    plain = re.sub(r"「[^」]*」", "", line).replace("ほか", "")
    return bool(_SENTENCE.search(plain)) or len(_HIRAGANA.findall(plain)) >= 12


def _after_gap(line: str, keyword: str) -> str:
    """「２．場 所　　第１省議室」のように、見出しの語のあと 2 文字以上空けて続く内容を返す。なければ ""。"""
    src = nfkc(line).strip()
    for gap in re.finditer(r"\s{2,}", src):
        if _NUMBERING.sub("", _SPACES.sub("", src[:gap.start()])) == keyword:
            return src[gap.end():].strip()
    return ""


def _weekday_mismatch(date_iso: str, raw: str) -> str:
    """「令和６年１０月２２日（金）」の曜日が日付と合わなければ、正しい曜日を返す。合っていれば ""。"""
    m = re.search(r"日\s*[(（]\s*([月火水木金土日])", nfkc(raw))
    if not m or not date_iso:
        return ""
    import datetime
    actual = "月火水木金土日"[datetime.date.fromisoformat(date_iso).weekday()]
    return "" if actual == m.group(1) else actual


def parse_header(text: str) -> dict[str, str]:
    """本文の冒頭から {date_raw, time, place, attendees, agenda} を読み取る。取れない項目は ""。"""
    lines = [ln for ln in text.split("\f", 1)[0].split("\n") if ln.strip()][:HEADER_LINES]
    fields: dict[str, list[str]] = {}
    limits = dict(_LIMITS)
    current = ""
    for line in lines:
        name, value, numbered = _heading(line)
        if name == "body":
            if numbered or fields:
                break
            continue  # 表題の一部としての「議事概要」「議事録」
        if name == "container" and value and "date" not in fields:
            fields["date"] = [value]  # 「開催日及び場所 令和５年…」。次の 1 行が場所
            current = "place" if "place" not in fields else ""
            fields.setdefault("place", [])
            limits["place"] = 1
            continue
        if name in ("container", "other"):
            current = ""
            continue
        if name:
            if fields.get(name):  # 同じ項目が 2 度出たら、最初のものを採る
                current = ""
                continue
            current = name
            fields[name] = [value] if value else []
            continue
        if not current:
            continue
        stripped = nfkc(line).strip()
        if current == "attendees" and _is_sentence(stripped):
            current = ""  # 名前の並びが終わって本文に入った（文になっている行）
            continue
        if len(fields[current]) >= limits[current]:
            current = ""
            continue
        fields[current].append(stripped)

    date_text = " ".join(fields.get("date", []))
    if not date_text:
        # 項目立てのない書き方（逐語の議事録など）。表題のすぐ下に日付だけの行があれば、それを開催日とみなす
        for line in lines[:6]:
            compact = _compact(line)
            if _DATE_LINE.match(compact) and len(compact) <= 30:
                date_text = nfkc(line).strip()
                break
    return {
        "date_raw": " ".join(date_text.split()),
        "time": parse_time(date_text),
        "place": " ".join(" ".join(fields.get("place", [])).split()),
        "attendees": "\n".join(fields.get("attendees", [])),
        "agenda": " / ".join(_items(fields.get("agenda", []))),
    }


def _items(lines: list[str]) -> list[str]:
    """箇条書きを 1 項目 1 要素にまとめる。番号で始まらない行は、前の項目の折り返しとみなす。"""
    items: list[str] = []
    for line in lines:
        if items and not re.match(r"^[(（]?[0-9一二三四五六七八九十]{1,2}[)）.．、]|^[・○◯●■□]", line):
            items[-1] += line
        else:
            items.append(line)
    return items


def parse_time(text: str) -> str:
    """「１４時００分～１６時００分」「14:00～16:00」「１３３０〜１５００」→ "14:00-16:00"。"""
    t = nfkc(text)
    m = _TIME.search(t)
    if m:
        h1, m1, h2, m2 = m.group(1), m.group(2) or "00", m.group(3), m.group(4) or "00"
    else:
        m = _TIME_4.search(t)
        if not m:
            return ""
        h1, m1, h2, m2 = m.groups()
    if int(h1) > 24 or int(h2) > 24:
        return ""
    return f"{int(h1):02d}:{m1}-{int(h2):02d}:{m2}"


def split_attendees(raw: str) -> list[tuple[str, str]]:
    """出席者の記載を（区分, 名前の表記）の並びにする。

    「（委員）太田会長、能勢委員」「防衛省側：林服務管理官ほか」のような区分の見出しを拾い、
    読点で区切る。名前が行をまたいで続く場合もあるので、行は区分の見出しがあるときだけ切る。
    """
    out: list[tuple[str, str]] = []
    group, parent, parent_level, lines = "", "", 0, []

    def add(token: str) -> None:
        token = _ANNOTATION.sub("", _SPACES.sub(" ", token)).strip(" ・")
        token = re.sub(r"\s*(?:ほか|他)(?:\d+名)?\)$", ")", token)  # 「氏名(所属 他)」
        token = _ETC.sub("", token).strip()
        if token and token not in ("ほか", "他", "外", "等"):
            out.append((group, token))

    def has_role(token: str) -> bool:
        base = re.sub(r"[(\[【〔].*$", "", token)
        return any(base.endswith(role) and len(base) > len(role) for role in ROLES)

    def flush() -> None:
        nonlocal lines
        if any(re.search(r"[、,，]", ln) for ln in lines):
            # 読点で区切る書き方。行の折り返しは語の途中でも起きるので、そのままつなぐ。
            # ただし閉じかっこで終わる行は、そこで 1 人分が終わっているとみなす
            joined = ""
            for ln in lines:
                joined += ("、" if joined.endswith((")", "）")) else "") + ln
            for token in re.split(r"[、,，]", joined):
                add(token)
        else:
            # 読点のない書き方。空白で並べてあれば分け、そうでなければ 1 行を 1 人とみなす。
            # 2〜3 文字だけの行は、前の行の折り返し（「…技術計画調／整官」）
            # かっこが閉じていない行も、次の行に続いている
            merged: list[str] = []
            for ln in lines:
                open_paren = bool(merged) and merged[-1].count("(") > merged[-1].count(")")
                if merged and (open_paren or (len(ln) <= 3 and not merged[-1].endswith((")", "]")))):
                    merged[-1] += ln
                else:
                    merged.append(ln)
            member_group = bool(_MEMBER_GROUP.search(group)) and not _OFFICIAL_GROUP.search(group)
            merged = _attach_affiliations(merged, wraps=member_group)
            for ln in merged:
                singles = ln.split()
                wide = re.split(r"\s{2,}", ln)
                if len(singles) >= 2 and all(has_role(t) for t in singles):
                    tokens = singles  # 「田内会長 柴山会長代理 石田委員」。空白 1 つでも区切り
                elif len(wide) >= 2 and all(_TITLED.search(t) for t in wide):
                    tokens = wide
                else:
                    tokens = [ln]
                for token in tokens:
                    add(token)
        lines = []

    for line in raw.split("\n"):
        line = nfkc(line).strip()
        if not line:
            continue
        # 見出しの強さ: 2 = 【】や印つき・語だけの行、1 = （）、0 = 「○○：」
        m, level = _GROUP_PAREN.match(line), 1 if line[0] == "(" else 2
        if m and m.group(2).startswith(("、", ",", "，")):
            m = None  # 行頭に折り返された注記（「…政務官／（途中出席）、若宮補佐官」）。区分の見出しではない
        if m is None:
            m, level = _GROUP_BULLET.match(line) or _GROUP_BARE.match(line), 2
        if m is None:
            m, level = _GROUP_COLON.match(line), 0
        if m:
            flush()
            label = _SPACES.sub("", _ANNOTATION.sub("", m.group(1)))
            # より強い見出しの下にある弱い見出しは、その内訳。
            # 「防衛省側」の下の「（内部部局）」、「【オブザーバー】」の下の「（文部科学省）」、「・有識者」の下の「座長：」
            if parent and level < parent_level and (label in ROLES or _OFFICIAL_GROUP.search(parent)):
                group = f"{parent}/{label}"
            else:
                group = label
                if label not in ROLES:
                    parent, parent_level = label, level
            line = m.group(2).strip()
        if line:
            lines.append(line)
    flush()
    return out


def _attach_affiliations(lines: list[str], wraps: bool) -> list[str]:
    """氏名の行の下に所属が別の行で書かれている名簿を、1 人 1 行にまとめる。

        岩﨑  洋一                         →  岩﨑 洋一(元海上自衛隊補給本部副本部長)
        元海上自衛隊補給本部副本部長

    氏名だけの行の次の 1 行を所属とみなす。wraps が真なら、所属の折り返し（2 行目以降）もつなぐ。
    省側の区分は職名だけの行が 1 人分のことがあるので、折り返しまではつながない。
    """
    out: list[str] = []
    named = False  # 直前の 1 人分が、氏名の行で始まっている
    bare = False   # その氏名の行に、所属がまだ付いていない
    for ln in lines:
        if _NAME_LINE.match(ln):
            out.append(ln)
            named, bare = True, bool(re.fullmatch(r"\S+\s+\S+", ln))
        elif named and bare:
            out[-1] += f"({ln}"
            bare = False
            named = wraps
        elif named:
            out[-1] += ln  # 所属の折り返し
        else:
            out.append(ln)  # 「田内会長」のような姓だけの記載、職名だけの記載など
    return [ln + ")" if ln.count("(") > ln.count(")") else ln for ln in out]


def split_name(token: str) -> tuple[str, str]:
    """名前の表記を（姓, 会議体での役職）に分ける。分からないものは ""。

    「太田会長」→（"太田", "会長"）、「青木 節子（慶應義塾大学大学院教授）」→（"青木", ""）。
    「林服務管理官」のような省側の職名は、姓との境目が決められないので分けない。
    """
    token = nfkc(token).strip()
    # かっこ書きより前が名前。「山田委員（オンライン）」「藤井 聡（京都大学教授）［会長］」
    base = re.sub(r"(氏|様|さん)$", "", re.sub(r"\s*[(\[【〔].*$", "", token).strip())
    noted_role = next((r for r in (_SPACES.sub("", x) for x in re.findall(r"[(\[【〔]([^()\[\]【】〔〕]*)[)\]】〕]", token))
                       if r in ROLES), "")
    compact = _SPACES.sub("", base)
    for role in ROLES:
        if compact.endswith(role) and len(compact) > len(role):
            stem = base[:-len(role)].split() if base.endswith(role) else []
            # 「西田 一平太 座長」は姓と名が分かる。「齋野委員（座長）」はかっこ内の役職を採る
            surname = stem[0] if len(stem) == 2 else compact[:-len(role)]
            return surname, noted_role or role
    parts = base.split()
    if len(parts) == 2 and len(parts[0]) <= 4 and len(parts[1]) <= 5 and not _TITLED.search(parts[1]):
        return parts[0], noted_role  # 姓と名を空白で区切った書き方
    return "", noted_role  # 「榊原定征（座長）」。姓と名の境目は分からないが、役職は分かる
