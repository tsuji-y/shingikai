"""省庁別の設定（agencies.json）の読み込みと、一覧ページからの会議体（councils.csv）抽出。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

try:
    import truststore  # type: ignore

    truststore.inject_into_ssl()
except Exception:
    pass

import pages
from pages import Block

REPO = Path(__file__).resolve().parent.parent
AGENCIES_JSON = REPO / "agencies.json"

_MULTI_SLASH = re.compile(r"/+")
_SKIP_LINK_TEXT = re.compile(
    r"^(?:一覧|過去の.*一覧|廃止した.*一覧|こちら|ホーム|トップ|サイトマップ|新着情報一覧.*|開催案内一覧.*|"
    r"内閣官房の各種本部・会議等の活動情報はこちら.*|所掌事務一覧|法曹に関するポータルサイト|法務図書館蔵書検索|"
    r"平成13年1月5日以前の情報はこちら.*)$"
)
_NON_HTML_EXT = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "csv", "txt",
    "jpg", "jpeg", "png", "gif", "svg", "xml", "rdf", "rss", "mp4", "mp3",
}


@dataclass
class AgencyConfig:
    agency_id: str
    name: str
    tier: str
    stage: str
    base_url: str
    hosts: list[str]
    index_urls: list[str]
    page_prefixes: list[str]
    allow_extensionless_pages: bool = False
    max_depth: int = 2
    rss_feeds: dict[str, str] = field(default_factory=dict)
    excluded_urls: list[str] = field(default_factory=list)
    repo: Path = REPO

    @property
    def primary_host(self) -> str:
        return urlsplit(self.base_url).netloc or self.hosts[0]

    @property
    def data_dir(self) -> Path:
        return self.repo / "data" / self.agency_id

    @property
    def raw_dir(self) -> Path:
        return self.repo / "raw" / self.agency_id

    @property
    def raw_pages_dir(self) -> Path:
        return self.raw_dir / "pages"

    @property
    def pages_manifest(self) -> Path:
        return self.raw_dir / "pages.csv"

    @property
    def raw_files_dir(self) -> Path:
        return self.raw_dir / "files"

    @property
    def files_manifest(self) -> Path:
        return self.raw_dir / "files.csv"

    @property
    def raw_subpages_dir(self) -> Path:
        return self.raw_dir / "subpages"

    @property
    def subpages_manifest(self) -> Path:
        return self.raw_dir / "subpages.csv"

    @property
    def subpage_links_csv(self) -> Path:
        return self.raw_dir / "subpage_links.csv"

    @property
    def councils_csv(self) -> Path:
        return self.data_dir / "councils.csv"

    @property
    def release_tag_prefix(self) -> str:
        return "pdfs" if self.agency_id == "mod" else f"{self.agency_id}-pdfs"

    @property
    def html_release_tag_prefix(self) -> str:
        return f"{self.agency_id}-html"

    @property
    def text_release_tag_prefix(self) -> str:
        return f"{self.agency_id}-texts"


def load_agencies(path: Path = AGENCIES_JSON, repo: Path = REPO) -> dict[str, AgencyConfig]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, AgencyConfig] = {}
    for aid, cfg in raw.items():
        out[aid] = AgencyConfig(
            agency_id=aid,
            name=cfg["name"],
            tier=cfg.get("tier", "A"),
            stage=cfg.get("stage", ""),
            base_url=cfg["base_url"].rstrip("/"),
            hosts=list(cfg.get("hosts") or [urlsplit(cfg["base_url"]).netloc]),
            index_urls=list(cfg["index_urls"]),
            page_prefixes=list(cfg["page_prefixes"]),
            allow_extensionless_pages=bool(cfg.get("allow_extensionless_pages", False)),
            max_depth=int(cfg.get("max_depth", 2)),
            rss_feeds=dict(cfg.get("rss_feeds") or {}),
            excluded_urls=list(cfg.get("excluded_urls") or []),
            repo=repo,
        )
    return out


def get_agency(agency_id: str = "mod", path: Path = AGENCIES_JSON, repo: Path = REPO) -> AgencyConfig:
    all_cfg = load_agencies(path, repo=repo)
    if agency_id not in all_cfg:
        raise KeyError(f"未定義の agency_id です: {agency_id!r} (定義済み: {', '.join(all_cfg)})")
    return all_cfg[agency_id]


def normalize_path(path: str) -> str:
    """URL パスの連続スラッシュを正規化し、拡張子なしパスの末尾スラッシュをそろえる。"""
    if not path:
        return "/"
    p = _MULTI_SLASH.sub("/", path)
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1 and p.endswith("/"):
        # /council/ のようなディレクトリ指定は index 比較用に末尾スラッシュを落とさない場合もあるが、
        # 拡張子なし CMS (/councils/foo/) は /councils/foo に統一する
        trimmed = p.rstrip("/")
        last = trimmed.rsplit("/", 1)[-1]
        if "." not in last:
            return trimmed
    return p


def is_html_path(path: str, allow_extensionless: bool = True) -> bool:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in ("html", "htm"):
        return True
    if ext in _NON_HTML_EXT:
        return False
    return allow_extensionless and ext == ""


def matches_agency_prefixes(url: str, agency: AgencyConfig) -> str:
    """URL が対象省庁の巡回対象 HTML パスなら正規化したサイト内パス（/…）を返す。対象外なら ""。"""
    sp = urlsplit(url)
    if sp.query:
        return ""
    if sp.netloc and sp.netloc != agency.primary_host:
        return ""
    path = normalize_path(sp.path)
    if not is_html_path(path, allow_extensionless=agency.allow_extensionless_pages):
        return ""
    for pref in agency.page_prefixes:
        norm_pref = pref.rstrip("/")
        if path == norm_pref or path.startswith(norm_pref + "/") or path.startswith(pref):
            return path
    return ""


def derive_council_id(link: str, agency: AgencyConfig) -> str:
    """リンク（サイト内パスまたは外部 URL）から council_id を決める。"""
    if link.startswith(("http://", "https://")):
        sp = urlsplit(link)
        host = sp.netloc.split(".")
        host_tag = host[1] if len(host) >= 3 and host[0] == "www" else (host[0] if host else "ext")
        parts = [p for p in sp.path.strip("/").split("/") if p and not p.endswith((".html", ".htm"))]
        tail = parts[-1] if parts else "index"
        return f"{host_tag}-{tail}"

    rel = link
    for pref in sorted(agency.page_prefixes, key=len, reverse=True):
        if rel.startswith(pref):
            rel = rel[len(pref):]
            break
    rel = rel.strip("/")
    if rel.endswith(("/index.html", "/index.htm")):
        rel = rel.rsplit("/", 1)[0]
    elif rel.endswith((".html", ".htm")):
        rel = os.path.splitext(rel)[0]
    return rel or "index"


def extract_councils_generic(agency: AgencyConfig, index_pages: list[tuple[str, list[Block]]],
                             existing: list[dict[str, str]] | None = None,
                             now_iso: str = "") -> list[dict[str, str]]:
    """一覧ページ群（[(index_url, blocks), ...]）から会議体一覧（councils.csv の行）を抽出する。"""
    existing = existing or []
    by_link = {r["link"]: r for r in existing}
    by_id = {r["council_id"]: r for r in existing}
    index_set = {normalize_path(u) for u in agency.index_urls}

    rows: list[dict[str, str]] = []
    seen_links: set[str] = set()
    used_ids: set[str] = set()

    def register(name: str, link: str, section: str, note: str = "") -> None:
        name = re.sub(r"\s*別ウィンドウで開きます\s*$", "", name).strip()
        if not name or _SKIP_LINK_TEXT.match(name):
            return
        if link in seen_links:
            return
        prev = by_link.get(link)
        cid = prev["council_id"] if prev else derive_council_id(link, agency)
        if cid in used_ids:
            i = 2
            while f"{cid}_{i}" in used_ids:
                i += 1
            cid = f"{cid}_{i}"
        prev = prev or by_id.get(cid)
        seen_links.add(link)
        used_ids.add(cid)
        rows.append({
            "council_id": cid,
            "section": section,
            "name": name,
            "link": link,
            "note": note,
            "first_seen": prev["first_seen"] if prev and prev.get("first_seen") else now_iso,
        })

    # こども家庭庁の /councils/shingikai はそれ自体が「こども家庭審議会」本体のページでもある
    if agency.agency_id == "cfa" and "/councils/shingikai" in index_set:
        register("こども家庭審議会", "/councils/shingikai", "審議会")

    for index_url, blocks in index_pages:
        norm_idx = normalize_path(index_url)
        default_section = "廃止・過去" if ("abolition" in norm_idx or "kako" in norm_idx) else agency.name
        added_on_page = 0

        for b in blocks:
            if b.kind == "heading" and b.level == 1:
                continue
            section = b.path[-1] if b.path else default_section

            # こども家庭審議会ページでは「分科会・部会等」セクションのリンクだけを子会議体として拾う
            if agency.agency_id == "cfa" and norm_idx == "/councils/shingikai":
                if not any("分科会・部会" in p for p in b.path):
                    continue

            if not b.links:
                continue

            for ln in b.links:
                sp = urlsplit(ln.url)
                ext = os.path.splitext(sp.path)[1].lower().lstrip(".")
                if ext in _NON_HTML_EXT or sp.query:
                    continue
                norm_path = normalize_path(sp.path)
                if sp.netloc == agency.primary_host and norm_path in index_set:
                    continue

                if sp.netloc == agency.primary_host:
                    in_prefix = matches_agency_prefixes(ln.url, agency)
                    if not in_prefix and agency.agency_id not in ("cao", "cas"):
                        continue
                    # 内閣官房・内閣府の一覧にあるトップ・概要・案内リンクは除外
                    if norm_path in ("/", "/index.html") or any(
                        norm_path.startswith(x) for x in ("/jp/gaiyou/", "/jp/houdou/", "/jp/saiyou/", "/others/")
                    ):
                        continue
                    link = in_prefix or norm_path
                elif sp.netloc in agency.hosts or (
                    agency.agency_id in ("cas", "cao", "maff") and sp.netloc.endswith(".go.jp")
                ):
                    if sp.path in ("", "/", "/index.html") or "gyoukaku.go.jp/sonota/kanrenlink" in ln.url:
                        continue
                    link = ln.url
                else:
                    continue

                before_n = len(rows)
                register(ln.text, link, section)
                if len(rows) > before_n:
                    added_on_page += 1

        if added_on_page == 0:
            h1 = next((b.text for b in blocks if b.kind == "heading" and b.level == 1), "") or agency.name
            register(h1, norm_idx, default_section)

    return rows
