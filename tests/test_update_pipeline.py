import csv
import hashlib
import io
import shutil
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mod"))
import build_db  # noqa: E402
import check_access  # noqa: E402
import check_updates  # noqa: E402
import events  # noqa: E402
import fetch_files  # noqa: E402
import ingest_pages  # noqa: E402
import pages  # noqa: E402
import update  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
REAL_RAW = REPO / "raw" / "mod" / "pages"
REAL_KOUSEI = "/j/policy/agenda/meeting/kousei_nyusatu/index.html"
REAL_RINRI = "/j/policy/agenda/meeting/rinri/index.html"
REAL_INDEX = "/j/policy/agenda/meeting/index.html"


def _make_cf_script(ray: str) -> bytes:
    return (
        f"<script>(function(){{var u='/cdn-cgi/challenge-platform/scripts/jsd/main.js?ray={ray}';}})();</script>"
    ).encode("utf-8")


class ContentHashTest(unittest.TestCase):
    def test_strip_cf_script_ignores_dynamic_challenge_script(self):
        saved = (REAL_RAW / REAL_KOUSEI.lstrip("/")).read_bytes()
        # 差し込みスクリプトの有無・レイID違いで content_hash が一致すること
        stripped = ingest_pages.strip_cf_script(saved)
        variant_a = stripped.replace(b"</body>", _make_cf_script("aaa111") + b"</body>")
        variant_b = stripped.replace(b"</body>", _make_cf_script("bbb222") + b"</body>")
        self.assertNotEqual(hashlib.sha256(variant_a).hexdigest(), hashlib.sha256(variant_b).hexdigest())
        self.assertEqual(ingest_pages.content_hash(variant_a), ingest_pages.content_hash(variant_b))
        self.assertEqual(ingest_pages.content_hash(saved), ingest_pages.content_hash(variant_a))

    def test_real_body_change_changes_content_hash(self):
        saved = (REAL_RAW / REAL_KOUSEI.lstrip("/")).read_bytes()
        modified = saved.replace("公正入札調査会議".encode("utf-8"), "公正入札調査会議（改）".encode("utf-8"), 1)
        self.assertNotEqual(ingest_pages.content_hash(saved), ingest_pages.content_hash(modified))


class CheckUpdatesTest(unittest.TestCase):
    def _setup_workspace(self, tmp: Path, urls: list[str]):
        raw_dir = tmp / "raw" / "mod" / "pages"
        manifest = tmp / "raw" / "mod" / "pages.csv"
        rows = {}
        for url in urls:
            src = REAL_RAW / url.lstrip("/")
            body = src.read_bytes()
            dst = ingest_pages.local_path(url, raw_dir)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(body)
            rows[url] = {
                "url": url,
                "status": "200",
                "last_modified": "2026-10-28T01:00:00+00:00",
                "fetched_at": "2026-11-19T05:47:00+00:00",
                "bytes": str(len(body)),
                "sha256": hashlib.sha256(body).hexdigest(),
                "content_sha256": ingest_pages.content_hash(body),
                "missing_count": "",
            }
        ingest_pages.write_manifest(rows, manifest)
        return raw_dir, manifest

    def test_unchanged_304(self):
        with tempfile.TemporaryDirectory() as td:
            raw_dir, manifest = self._setup_workspace(Path(td), [REAL_KOUSEI])
            before_manifest = manifest.read_text(encoding="utf-8")

            def fake_fetch(url, if_modified_since=""):
                self.assertEqual(if_modified_since, "2026-10-28T01:00:00+00:00")
                return check_updates.FetchResponse(status=304, fetched_at="2026-11-21T00:00:00+00:00")

            res = check_updates.check_pages(manifest=manifest, raw_dir=raw_dir, interval=0, fetcher=fake_fetch)
            self.assertEqual((res.checked, res.unchanged, len(res.changed_pages)), (1, 1, 0))
            self.assertFalse(res.manifest_dirty)
            self.assertEqual(manifest.read_text(encoding="utf-8"), before_manifest)

    def test_cf_script_only_diff_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            raw_dir, manifest = self._setup_workspace(Path(td), [REAL_KOUSEI])
            orig = ingest_pages.local_path(REAL_KOUSEI, raw_dir).read_bytes()
            with_new_cf = ingest_pages.strip_cf_script(orig).replace(b"</body>", _make_cf_script("zzz999") + b"</body>")

            def fake_fetch(url, if_modified_since=""):
                return check_updates.FetchResponse(
                    status=200, body=with_new_cf,
                    last_modified="2026-10-28T01:00:00+00:00", fetched_at="2026-11-21T00:00:00+00:00",
                )

            res = check_updates.check_pages(manifest=manifest, raw_dir=raw_dir, interval=0, fetcher=fake_fetch)
            self.assertEqual(res.changed_pages, [])
            self.assertEqual(res.unchanged, 1)
            self.assertFalse(res.has_content_changes)
            # 既存ファイルは上書きしない
            self.assertEqual(ingest_pages.local_path(REAL_KOUSEI, raw_dir).read_bytes(), orig)

    def test_last_modified_only_diff_updates_metadata_only(self):
        with tempfile.TemporaryDirectory() as td:
            raw_dir, manifest = self._setup_workspace(Path(td), [REAL_KOUSEI])
            orig = ingest_pages.local_path(REAL_KOUSEI, raw_dir).read_bytes()

            def fake_fetch(url, if_modified_since=""):
                return check_updates.FetchResponse(
                    status=200, body=orig,
                    last_modified="2026-10-28T01:00:01+00:00", fetched_at="2026-11-21T00:00:00+00:00",
                )

            res = check_updates.check_pages(manifest=manifest, raw_dir=raw_dir, interval=0, fetcher=fake_fetch)
            self.assertEqual(res.changed_pages, [])
            self.assertEqual(res.metadata_only, [REAL_KOUSEI])
            self.assertFalse(res.has_content_changes)
            rows = ingest_pages.read_manifest(manifest)
            self.assertEqual(rows[REAL_KOUSEI]["last_modified"], "2026-10-28T01:00:01+00:00")

    def test_body_change_saves_raw_bytes_and_discovers_new_page(self):
        with tempfile.TemporaryDirectory() as td:
            kousei_youkou = "/j/policy/agenda/meeting/kousei_nyusatu/youkou.html"
            kousei_koukai = "/j/policy/agenda/meeting/kousei_nyusatu/koukai.html"
            raw_dir, manifest = self._setup_workspace(Path(td), [REAL_KOUSEI, kousei_youkou, kousei_koukai])
            orig = ingest_pages.local_path(REAL_KOUSEI, raw_dir).read_bytes()
            new_link_html = b'<p><a href="new_meeting_2026.html">new</a></p></body>'
            modified = orig.replace(b"</body>", new_link_html)
            new_page_url = "/j/policy/agenda/meeting/kousei_nyusatu/new_meeting_2026.html"
            new_page_body = b"<html><body><h1>New Meeting</h1></body></html>"

            def fake_fetch(url, if_modified_since=""):
                if url == REAL_KOUSEI:
                    return check_updates.FetchResponse(
                        status=200, body=modified,
                        last_modified="2026-11-20T02:54:00+00:00", fetched_at="2026-11-21T00:00:00+00:00",
                    )
                if url in (kousei_youkou, kousei_koukai):
                    return check_updates.FetchResponse(status=304, fetched_at="2026-11-21T00:00:01+00:00")
                if url == new_page_url:
                    return check_updates.FetchResponse(
                        status=200, body=new_page_body,
                        last_modified="2026-11-20T02:54:00+00:00", fetched_at="2026-11-21T00:00:02+00:00",
                    )
                raise AssertionError(f"unexpected url: {url}")

            res = check_updates.check_pages(manifest=manifest, raw_dir=raw_dir, interval=0, fetcher=fake_fetch)
            self.assertEqual(res.changed_pages, [REAL_KOUSEI])
            self.assertEqual(res.new_pages, [new_page_url])
            self.assertEqual(ingest_pages.local_path(REAL_KOUSEI, raw_dir).read_bytes(), modified)
            self.assertEqual(ingest_pages.local_path(new_page_url, raw_dir).read_bytes(), new_page_body)

    def test_stops_immediately_on_403_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            raw_dir, manifest = self._setup_workspace(Path(td), [REAL_KOUSEI, REAL_RINRI])
            orig_manifest = manifest.read_text(encoding="utf-8")
            calls = []

            def fake_fetch(url, if_modified_since=""):
                calls.append(url)
                if len(calls) == 1:
                    raise check_updates.Blocked(f"{url}: HTTP 403 (challenge)")
                return check_updates.FetchResponse(status=304)

            with self.assertRaises(check_updates.Blocked):
                check_updates.check_pages(manifest=manifest, raw_dir=raw_dir, interval=0, fetcher=fake_fetch)
            # 1 件目で止まり、2 件目へ進まない
            self.assertEqual(len(calls), 1)
            self.assertEqual(manifest.read_text(encoding="utf-8"), orig_manifest)

    def test_fetch_page_does_not_retry_403_but_retries_503(self):
        # 403 はリトライしない
        calls_403 = []

        def opener_403(req, timeout=60):
            calls_403.append(req.full_url)
            hdrs = Message()
            hdrs["cf-mitigated"] = "challenge"
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", hdrs, io.BytesIO(b""))

        with self.assertRaises(check_updates.Blocked):
            check_updates.fetch_page(REAL_KOUSEI, retry_backoff=(0.0, 0.0, 0.0), opener=opener_403)
        self.assertEqual(len(calls_403), 1)

        # 503 は最大 3 回リトライ（計 4 回試行）し、Blocked ではなく通常エラーを返す
        calls_503 = []

        def opener_503(req, timeout=60):
            calls_503.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", Message(), io.BytesIO(b""))

        resp = check_updates.fetch_page(REAL_KOUSEI, retry_backoff=(0.0, 0.0, 0.0), opener=opener_503)
        self.assertEqual(len(calls_503), 4)
        self.assertEqual(resp.status, 503)

    def test_consecutive_404_marks_gone_without_deleting_file(self):
        with tempfile.TemporaryDirectory() as td:
            raw_dir, manifest = self._setup_workspace(Path(td), [REAL_KOUSEI])
            saved_file = ingest_pages.local_path(REAL_KOUSEI, raw_dir)

            def fetch_404(url, if_modified_since=""):
                return check_updates.FetchResponse(status=404, fetched_at="2026-11-21T00:00:00+00:00")

            # 1 回目: 消失候補（status は 200 のまま）
            r1 = check_updates.check_pages(manifest=manifest, raw_dir=raw_dir, interval=0, fetcher=fetch_404)
            self.assertEqual(r1.missing_candidates, [REAL_KOUSEI])
            self.assertEqual(r1.gone_pages, [])
            m1 = ingest_pages.read_manifest(manifest)[REAL_KOUSEI]
            self.assertEqual((m1["status"], m1["missing_count"]), ("200", "1"))
            self.assertTrue(saved_file.exists())

            # 2 回目: 消失確定（ファイルは消さない）
            r2 = check_updates.check_pages(manifest=manifest, raw_dir=raw_dir, interval=0, fetcher=fetch_404)
            self.assertEqual(r2.gone_pages, [REAL_KOUSEI])
            m2 = ingest_pages.read_manifest(manifest)[REAL_KOUSEI]
            self.assertEqual((m2["status"], m2["missing_count"]), ("404", "2"))
            self.assertTrue(saved_file.exists())

            # 3 回目: すでに消失確定済みなので台帳を再更新しない
            r3 = check_updates.check_pages(manifest=manifest, raw_dir=raw_dir, interval=0, fetcher=fetch_404)
            self.assertEqual(r3.gone_pages, [])
            self.assertFalse(r3.manifest_dirty)

    def test_zero_checked_pages_fails(self):
        with tempfile.TemporaryDirectory() as td:
            raw_dir, manifest = self._setup_workspace(Path(td), [REAL_KOUSEI])

            def fetch_500(url, if_modified_since=""):
                return check_updates.FetchResponse(status=500, error="Internal Server Error")

            with self.assertRaises(check_updates.NoPagesChecked):
                check_updates.check_pages(manifest=manifest, raw_dir=raw_dir, interval=0, fetcher=fetch_500)


class WarpAndDocKeyTest(unittest.TestCase):
    def test_doc_key_matches_mod_and_warp_urls(self):
        mod_url = "https://www.mod.go.jp/j/policy/agenda/meeting/rinri/pdf/giji_105.pdf"
        warp_url = "https://warp.da.ndl.go.jp/info:ndljp/pid/14500123/www.mod.go.jp/j/policy/agenda/meeting/rinri/pdf/giji_105.pdf"
        self.assertEqual(build_db.doc_key(mod_url), build_db.doc_key(warp_url))
        self.assertEqual(build_db.doc_key(warp_url), "www.mod.go.jp/j/policy/agenda/meeting/rinri/pdf/giji_105.pdf")

    def test_warp_migration_preserves_text_attendance_and_records_event(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            data_dir = tmp / "data" / "mod"
            raw_dir = tmp / "raw" / "mod" / "pages"
            text_dir = data_dir / "text" / "j" / "policy" / "agenda" / "meeting" / "x" / "pdf"
            text_dir.mkdir(parents=True, exist_ok=True)
            txt_file = text_dir / "giji_01.txt"
            txt_file.write_text(
                "第1回 テスト審議会 議事要旨\n"
                "１ 日時：令和8年5月10日(日) 10:00〜11:00\n"
                "２ 場所：防衛省A棟大会議室\n"
                "３ 出席者：\n"
                "委員：山田 太郎、鈴木 花子\n",
                encoding="utf-8",
            )
            councils_csv = data_dir / "councils.csv"
            build_db.write_csv(councils_csv, build_db.COUNCIL_FIELDS, [{
                "council_id": "x", "section": "審議会・有識者会議等", "name": "テスト審議会",
                "link": "/j/policy/agenda/meeting/x/index.html", "note": "", "first_seen": "2026-11-19T00:00:00+00:00",
            }])
            texts_csv = data_dir / "texts.csv"
            mod_pdf = "https://www.mod.go.jp/j/policy/agenda/meeting/x/pdf/giji_01.pdf"
            warp_pdf = "https://warp.da.ndl.go.jp/info:ndljp/pid/9999999/www.mod.go.jp/j/policy/agenda/meeting/x/pdf/giji_01.pdf"
            build_db.write_csv(texts_csv, ["url", "text", "pages", "chars", "sha256", "note"], [{
                "url": mod_pdf,
                "text": txt_file.relative_to(tmp).as_posix(),
                "pages": "1",
                "chars": "80",
                "sha256": "abc123",
                "note": "",
            }])

            page_url = "/j/policy/agenda/meeting/x/index.html"
            html_before = (
                "<html><body><h1>テスト審議会</h1>"
                "<table><tr><th>開催回</th><th>年月日</th><th>資料</th></tr>"
                f"<tr><td>第1回</td><td>令和8年5月10日</td><td><a href='{mod_pdf}'>議事要旨</a></td></tr>"
                "</table></body></html>"
            ).encode("utf-8")
            html_after = html_before.replace(mod_pdf.encode(), warp_pdf.encode())

            p_file = ingest_pages.local_path(page_url, raw_dir)
            p_file.parent.mkdir(parents=True, exist_ok=True)
            p_file.write_bytes(html_before)
            pages_manifest = tmp / "raw" / "mod" / "pages.csv"
            ingest_pages.write_manifest({page_url: {
                "url": page_url, "status": "200", "last_modified": "2026-05-11T00:00:00+00:00",
                "fetched_at": "2026-11-19T00:00:00+00:00", "bytes": str(len(html_before)),
                "sha256": hashlib.sha256(html_before).hexdigest(),
                "content_sha256": ingest_pages.content_hash(html_before), "missing_count": "",
            }}, pages_manifest)

            # 移行前ビルド
            b1 = build_db.build(councils_csv, pages_manifest, raw_dir, texts_csv, repo=tmp)
            update._write_built_tables(b1, data_dir)
            snap_before = events.load_snapshot(data_dir, pages_manifest, tmp / "raw" / "mod" / "files.csv")
            self.assertEqual(b1.meetings[0]["place"], "防衛省A棟大会議室")
            self.assertEqual(len(b1.attendance), 2)

            # WARP へリンク書き換え後のビルド
            p_file.write_bytes(html_after)
            b2 = build_db.build(councils_csv, pages_manifest, raw_dir, texts_csv, repo=tmp)
            update._write_built_tables(b2, data_dir)
            snap_after = events.load_snapshot(data_dir, pages_manifest, tmp / "raw" / "mod" / "files.csv")

            # 本文・場所・出席者が維持される
            self.assertEqual(b2.meetings[0]["place"], "防衛省A棟大会議室")
            self.assertEqual(len(b2.attendance), 2)
            self.assertEqual(b2.documents[0]["host"], "warp.da.ndl.go.jp")
            self.assertEqual(b2.documents[0]["text"], txt_file.relative_to(tmp).as_posix())

            # イベントは document_moved_to_warp のみ（document_added ではない）
            evs = events.diff_snapshots(snap_before, snap_after, detected_at="2026-11-21T00:00:00+00:00")
            self.assertEqual([e["event_type"] for e in evs], ["document_moved_to_warp"])
            self.assertEqual(evs[0]["before"], mod_pdf)
            self.assertEqual(evs[0]["after"], warp_pdf)

            # 回帰の検査も通過する
            reg = events.check_regressions(snap_before, snap_after, {page_url})
            self.assertTrue(reg.ok, reg.unexplained)


class FetchFilesSkipTest(unittest.TestCase):
    def test_skips_manifest_sha256_without_local_file_unless_refetch_missing(self):
        with tempfile.TemporaryDirectory() as td:
            files_dir = Path(td) / "files"
            url_done = "https://www.mod.go.jp/j/policy/agenda/meeting/x/a.pdf"
            url_new = "https://www.mod.go.jp/j/policy/agenda/meeting/x/b.pdf"
            url_404 = "https://www.mod.go.jp/j/policy/agenda/meeting/x/c.pdf"
            rows = {
                url_done: {"url": url_done, "status": "200", "sha256": "deadbeef"},
                url_404: {"url": url_404, "status": "404", "sha256": ""},
            }
            urls = [url_done, url_new, url_404]
            self.assertEqual(fetch_files.select_targets(urls, rows, files_dir=files_dir), [url_new])
            self.assertEqual(
                fetch_files.select_targets(urls, rows, refetch_missing=True, files_dir=files_dir),
                [url_done, url_new],
            )
            self.assertEqual(
                fetch_files.select_targets(urls, rows, retry=True, files_dir=files_dir),
                [url_new, url_404],
            )

    def test_check_replaced_detects_new_pdf_content(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            url = "https://www.mod.go.jp/j/policy/agenda/meeting/x/a.pdf"
            docs = tmp / "documents.csv"
            build_db.write_csv(docs, build_db.DOCUMENT_FIELDS, [{
                "council_id": "x", "meeting_id": "x#1", "section": "", "title": "資料",
                "doc_type": "資料", "url": url, "host": "www.mod.go.jp", "format": "pdf",
                "text": "", "pages": "", "chars": "", "source": "/j/policy/agenda/meeting/x/index.html",
                "doc_key": build_db.doc_key(url),
            }])
            manifest = tmp / "files.csv"
            fetch_files.write_manifest({url: {
                "url": url, "status": "200", "content_type": "application/pdf",
                "last_modified": "2026-10-01T00:00:00+00:00", "fetched_at": "2026-11-19T00:00:00+00:00",
                "bytes": "10", "sha256": "oldsha", "note": "",
            }}, manifest)

            new_pdf = b"%PDF-1.7 replaced content"
            new_sha = hashlib.sha256(new_pdf).hexdigest()

            def fake_fetch(u, if_modified_since=""):
                return ({
                    "url": u, "status": "200", "content_type": "application/pdf",
                    "last_modified": "2026-11-20T00:00:00+00:00", "fetched_at": "2026-11-21T00:00:00+00:00",
                    "bytes": str(len(new_pdf)), "sha256": new_sha, "note": "",
                }, new_pdf)

            res = fetch_files.check_replaced(docs, manifest, tmp / "files", interval=0, fetcher=fake_fetch)
            self.assertEqual(res.replaced, [(url, "oldsha", new_sha)])
            self.assertEqual(fetch_files.read_manifest(manifest)[url]["sha256"], new_sha)


class EndToEndUpdateAndRegressionTest(unittest.TestCase):
    def _build_mini_repo(self, tmp: Path):
        data_dir = tmp / "data" / "mod"
        raw_dir = tmp / "raw" / "mod" / "pages"
        pages_manifest = tmp / "raw" / "mod" / "pages.csv"
        files_manifest = tmp / "raw" / "mod" / "files.csv"
        texts_csv = data_dir / "texts.csv"

        # 実在の保存済み HTML（index + kousei_nyusatu + rinri）を使う
        councils = [r for r in build_db.load_councils(REPO / "data" / "mod" / "councils.csv")
                    if r["council_id"] in ("kousei_nyusatu", "rinri")]
        build_db.write_csv(data_dir / "councils.csv", build_db.COUNCIL_FIELDS, councils)

        rows = {}
        kousei_youkou = "/j/policy/agenda/meeting/kousei_nyusatu/youkou.html"
        kousei_koukai = "/j/policy/agenda/meeting/kousei_nyusatu/koukai.html"
        for url in (REAL_KOUSEI, kousei_youkou, kousei_koukai, REAL_RINRI):
            body = (REAL_RAW / url.lstrip("/")).read_bytes()
            dst = ingest_pages.local_path(url, raw_dir)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(body)
            rows[url] = {
                "url": url, "status": "200", "last_modified": "2026-10-28T00:00:00+00:00",
                "fetched_at": "2026-11-19T05:47:00+00:00", "bytes": str(len(body)),
                "sha256": hashlib.sha256(body).hexdigest(),
                "content_sha256": ingest_pages.content_hash(body), "missing_count": "",
            }
        ingest_pages.write_manifest(rows, pages_manifest)
        fetch_files.write_manifest({}, files_manifest)
        build_db.write_csv(texts_csv, ["url", "text", "pages", "chars", "sha256", "note"], [])

        built = build_db.build(data_dir / "councils.csv", pages_manifest, raw_dir, texts_csv, repo=tmp)
        update._write_built_tables(built, data_dir)
        events.ensure_csv_header(data_dir / "events.csv", events.EVENT_FIELDS)
        events.ensure_csv_header(data_dir / "members_history.csv", events.MEMBER_HISTORY_FIELDS)
        return data_dir, raw_dir, pages_manifest, files_manifest

    def test_kousei_nyusatu_member_addition_and_idempotent_second_run(self):
        """Phase 1 完了条件のシナリオ: 委員1名追加＋名簿時点変更→2イベント＋履歴退避→再実行は変更なし。"""
        import re
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            data_dir, raw_dir, pages_manifest, _ = self._build_mini_repo(tmp)
            orig_kousei = ingest_pages.local_path(REAL_KOUSEI, raw_dir).read_text(encoding="utf-8")

            # 保存済み HTML の名簿時点を将来日付へ変更し、委員1名を追加
            updated_html, n_sub = re.subn(r"［令和[0-9０-９]+年[0-9０-９]+月[0-9０-９]+日］", "［令和9年1月15日］", orig_kousei, count=1)
            self.assertEqual(n_sub, 1)
            anchor = "<td>東京経済大学経済学部　教授</td>"
            self.assertIn(anchor, updated_html)
            pos = updated_html.index("</tr>", updated_html.index(anchor)) + len("</tr>")
            updated_html = (
                updated_html[:pos]
                + "\r\n\t\t\t\t\t<tr>\r\n\t\t\t\t\t\t<td>　テスト 太郎</td>\r\n\t\t\t\t\t\t<td>テスト法律事務所　弁護士</td>\r\n\t\t\t\t\t</tr>"
                + updated_html[pos:]
            )
            updated_bytes = updated_html.encode("utf-8")

            def fetcher_run1(url, if_modified_since=""):
                if url == REAL_KOUSEI:
                    return check_updates.FetchResponse(
                        status=200, body=updated_bytes,
                        last_modified="2027-01-15T02:54:00+00:00", fetched_at="2027-01-16T01:00:00+00:00",
                    )
                return check_updates.FetchResponse(status=304, fetched_at="2027-01-16T01:00:02+00:00")

            summary_md = tmp / "summary.md"
            out1 = update.run_update(
                repo=tmp, skip_pdf=True, interval=0, summary_out=summary_md, page_fetcher=fetcher_run1,
            )
            self.assertTrue(out1.ok, out1.error)
            self.assertEqual(out1.changed_pages, [REAL_KOUSEI])
            ev_types = sorted(e["event_type"] for e in out1.events)
            self.assertEqual(ev_types, ["member_added", "roster_as_of_changed"])
            self.assertGreater(out1.archived_members, 0)
            self.assertTrue(summary_md.exists())

            # members.csv に追加委員が入り、as_of が 2027-01-15 に変わっている
            with (data_dir / "members.csv").open(encoding="utf-8") as f:
                kousei_m = [r for r in csv.DictReader(f) if r["council_id"] == "kousei_nyusatu"]
            self.assertTrue(any(r["name"] == "テスト 太郎" for r in kousei_m))
            self.assertTrue(all(r["as_of"] == "2027-01-15" for r in kousei_m))

            # 続けてもう一度実行（304）すると変更なし
            def fetcher_run2(url, if_modified_since=""):
                return check_updates.FetchResponse(status=304, fetched_at="2027-01-16T02:00:00+00:00")

            out2 = update.run_update(repo=tmp, skip_pdf=True, interval=0, page_fetcher=fetcher_run2)
            self.assertTrue(out2.ok)
            self.assertEqual(out2.changed_pages, [])
            self.assertEqual(out2.events, [])

    def test_regression_check_blocks_unexplained_disappearance(self):
        before = events.Snapshot(
            councils=[{"council_id": "rinri", "section": "審議会", "name": "倫理審査会",
                       "link": REAL_RINRI, "note": "", "first_seen": ""}],
            meetings=[{"meeting_id": "rinri#105", "council_id": "rinri", "body": "",
                       "number": "105", "date": "2026-01-01", "source": REAL_RINRI}],
        )
        after = events.Snapshot(councils=before.councils, meetings=[])
        # rinri/index.html が変更されていないのに会議が消えたら失敗
        rep_fail = events.check_regressions(before, after, changed_pages={REAL_KOUSEI})
        self.assertFalse(rep_fail.ok)
        self.assertEqual(len(rep_fail.unexplained), 1)
        # rinri/index.html が変更ページに含まれていれば説明がつく
        rep_ok = events.check_regressions(before, after, changed_pages={REAL_RINRI})
        self.assertTrue(rep_ok.ok)


class WatchdogAndCheckAccessTest(unittest.TestCase):
    def test_find_stale_rss_pages(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rss = tmp / "rss_items.csv"
            pages_csv = tmp / "pages.csv"
            build_db.write_csv(
                rss,
                ["feed", "pub_date", "title", "link", "guid", "category", "description", "content",
                 "council_related", "first_seen"],
                [
                    # 4 日前に告知されたが、最終取得が 5 日前 → 取り込み漏れ
                    {"feed": "update", "pub_date": "2026-11-17T18:00:00+09:00", "title": "更新A",
                     "link": REAL_KOUSEI, "guid": "", "category": "", "description": "", "content": "",
                     "council_related": "1", "first_seen": "2026-11-17T10:00:00+00:00"},
                    # 1 日前に告知 → 3 日未満なので対象外
                    {"feed": "update", "pub_date": "2026-11-20T18:00:00+09:00", "title": "更新B",
                     "link": REAL_RINRI, "guid": "", "category": "", "description": "", "content": "",
                     "council_related": "1", "first_seen": "2026-11-20T10:00:00+00:00"},
                ],
            )
            ingest_pages.write_manifest({
                REAL_KOUSEI: {"url": REAL_KOUSEI, "status": "200", "last_modified": "",
                              "fetched_at": "2026-11-16T00:00:00+00:00", "bytes": "10", "sha256": "a",
                              "content_sha256": "a", "missing_count": ""},
                REAL_RINRI: {"url": REAL_RINRI, "status": "200", "last_modified": "",
                             "fetched_at": "2026-11-16T00:00:00+00:00", "bytes": "10", "sha256": "b",
                             "content_sha256": "b", "missing_count": ""},
            }, pages_csv)
            now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
            stale = events.find_stale_rss_pages(rss, pages_csv, now=now, grace_days=3)
            self.assertEqual([s["link"] for s in stale], [REAL_KOUSEI])

    def test_check_access_skips_conditional_html_when_blocked(self):
        calls = []

        class FakeResp:
            def __init__(self, status, body, headers):
                self.status = status
                self._body = body
                self.headers = headers

            def read(self, n=-1):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_opener(req, timeout=30):
            calls.append(req.full_url)
            if req.full_url.endswith(".html"):
                hdrs = Message()
                hdrs["cf-mitigated"] = "challenge"
                raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", hdrs, io.BytesIO(b""))
            hdrs = Message()
            hdrs["Content-Type"] = "application/pdf" if req.full_url.endswith(".pdf") else "application/xml"
            return FakeResp(200, b"%PDF-1.4" if req.full_url.endswith(".pdf") else b"<rss/>", hdrs)

        results = check_access.run_probes(interval=0, opener=fake_opener)
        self.assertEqual([r.kind for r in results], ["html", "html_conditional", "pdf", "rss"])
        self.assertEqual(results[0].status, "403")
        self.assertEqual(results[0].cf_mitigated, "challenge")
        self.assertTrue(results[1].skipped)
        self.assertEqual(results[2].status, "200")
        self.assertEqual(results[3].status, "200")
        # HTML が 403 だったので html_conditional はリクエストしていない（合計 3 リクエスト）
        self.assertEqual(len(calls), 3)
        md = check_access.format_markdown(results)
        self.assertIn("`403`", md)
        self.assertIn("`skipped`", md)


if __name__ == "__main__":
    unittest.main()
