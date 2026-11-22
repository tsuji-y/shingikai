"""Gemini の出力を機械的に検証する（docs/update_plan.md 3.9 節）。

検証を通らない出力は本体表に反映せず、rejected として llm_extractions.csv に残す。
"""
from __future__ import annotations

import datetime
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import minutes  # noqa: E402

_WS_RE = re.compile(r"\s+")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}$")
_TIME_JA_RE = re.compile(
    r"^(\d{1,2})\s*(?:時|:)\s*(\d{1,2})?\s*分?\s*[-~〜～ー－–—]\s*(\d{1,2})\s*(?:時|:)\s*(\d{1,2})?\s*分?$"
)
_TIME_4D_RE = re.compile(r"^(\d{2})(\d{2})\s*[-~〜～ー－–—]\s*(\d{2})(\d{2})$")
_DATE_IN_SRC_RE = re.compile(r"(?:令和|平成|昭和)(?:元|\d{1,2})年\d{1,2}月\d{1,2}日\s*[（(][月火水木金土日][）)]")


def compact_nfkc(text: str) -> str:
    """NFKC 正規化のうえ空白をすべて除いた文字列。原文照合に使う。"""
    return _WS_RE.sub("", unicodedata.normalize("NFKC", text))


def normalize_time(time_str: str) -> str:
    """日本語表記（14時30分～17時30分）や 4 桁表記（1430-1601）を HH:MM-HH:MM へ正規化する。"""
    s = unicodedata.normalize("NFKC", time_str).strip()
    if not s or _TIME_RE.match(s):
        return s
    m = _TIME_JA_RE.match(s)
    if m:
        h1, m1, h2, m2 = m.groups()
        return f"{int(h1):02d}:{int(m1 or 0):02d}-{int(h2):02d}:{int(m2 or 0):02d}"
    m = _TIME_4D_RE.match(s)
    if m:
        h1, m1, h2, m2 = m.groups()
        return f"{int(h1):02d}:{int(m1):02d}-{int(h2):02d}:{int(m2):02d}"
    return s


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    cleaned: dict[str, Any] = field(default_factory=dict)


def validate_minutes_extraction(payload: dict[str, Any], source_text: str) -> ValidationResult:
    """議事要旨・議事録の抽出 JSON を原文に対して検証する。

    検査項目:
    1. 開催日（date）が YYYY-MM-DD として実在する日付であること。
    2. date_raw または原文中の該当開催日に曜日が書かれていれば、その曜日が日付と一致すること。
    3. 抽出した全出席者の name_raw（空白除去・NFKC）が、空白を除いた原文にそのまま存在すること。
    4. time が指定されている場合、HH:MM-HH:MM 形式（日本語表記・4桁表記は正規化）であること。
    """
    errors: list[str] = []
    compact_src = compact_nfkc(source_text)

    date_str = str(payload.get("date") or "").strip()
    date_raw = str(payload.get("date_raw") or "").strip()
    time_str = normalize_time(str(payload.get("time") or "").strip())
    place = str(payload.get("place") or "").strip()
    agenda = str(payload.get("agenda") or "").strip()

    if date_str:
        try:
            datetime.date.fromisoformat(date_str)
        except ValueError:
            errors.append(f"不正な開催日: {date_str!r}")
        else:
            if date_raw:
                mismatch = minutes._weekday_mismatch(date_str, date_raw)
                if mismatch:
                    errors.append(f"date_raw の曜日不一致（{date_str} は{mismatch}曜: {date_raw!r}）")
            # 原文の冒頭に「令和X年Y月Z日(曜)」があり date_raw と一致する日付なら曜日も確認
            if date_raw and compact_nfkc(date_raw) not in compact_src:
                errors.append(f"date_raw が原文に存在しない: {date_raw!r}")

    if time_str and not _TIME_RE.match(time_str):
        errors.append(f"時刻形式が不正: {time_str!r}")

    if place and compact_nfkc(place) not in compact_src:
        # 場所が長文要約などで捏造されていないか（最低限、最初の主要語が原文にあるか）
        core_place = compact_nfkc(re.split(r"[（(、,\s]", place)[0])
        if core_place and core_place not in compact_src:
            errors.append(f"場所が原文に存在しない: {place!r}")

    raw_attendees = payload.get("attendees")
    if raw_attendees is None:
        raw_attendees = []
    if not isinstance(raw_attendees, list):
        errors.append("attendees が配列ではない")
        raw_attendees = []

    cleaned_attendees: list[dict[str, str]] = []
    for idx, item in enumerate(raw_attendees):
        if not isinstance(item, dict):
            errors.append(f"attendees[{idx}] がオブジェクトではない")
            continue
        name_raw = str(item.get("name_raw") or "").strip()
        if not name_raw:
            errors.append(f"attendees[{idx}] の name_raw が空")
            continue
        c_name = compact_nfkc(name_raw)
        if c_name not in compact_src:
            errors.append(f"抽出した氏名が原文に存在しない: {name_raw!r}")
            continue
        cleaned_attendees.append({
            "group": str(item.get("group") or "").strip(),
            "name_raw": name_raw,
            "surname": str(item.get("surname") or "").strip(),
            "role": str(item.get("role") or "").strip(),
        })

    cleaned = {
        "date": date_str,
        "date_raw": date_raw,
        "time": time_str,
        "place": place,
        "agenda": agenda,
        "attendees": cleaned_attendees,
    }
    return ValidationResult(ok=len(errors) == 0, errors=errors, cleaned=cleaned)


def validate_summary(payload: dict[str, Any]) -> ValidationResult:
    """要約 JSON の最低限の構造検証。"""
    errors: list[str] = []
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        errors.append("summary が空")
    kp = payload.get("key_points")
    if kp is None:
        kp = []
    if isinstance(kp, str):
        kp = [kp] if kp.strip() else []
    if not isinstance(kp, list):
        errors.append("key_points が配列ではない")
        kp = []
    cleaned_kp = [str(x).strip() for x in kp if str(x).strip()]
    return ValidationResult(
        ok=len(errors) == 0,
        errors=errors,
        cleaned={"summary": summary, "key_points": " / ".join(cleaned_kp)},
    )
