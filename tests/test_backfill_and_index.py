import csv
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mod"))
import agencies  # noqa: E402
import archive_pdfs  # noqa: E402
import build_db  # noqa: E402
import build_index  # noqa: E402
import check_updates  # noqa: E402
import cross_agency  # noqa: E402
import fetch_files  # noqa: E402
import fetch_subpages  # noqa: E402
import update  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class SubpagesAndCatalogTest(unittest.TestCase):
    def _make_mini_repo(self, td: str) -> Path:
        repo = Path(td)
        (repo / "agencies.json").write_text(
            (REPO / "agencies.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return repo

    def test_subpages_fetch_extracts_text_and_discovers_handout_pdfs(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._make_mini_repo(td)
            ag = agencies.get_agency("mhlw", repo=repo)

            # 親ページ（会議体トップ）には下位 HTML（/stf/newpage_99999.html と 2019年の過去ページ）へのリンクがある
            index_html = (
                "<html><body><main><h1>審議会一覧</h1>"
                "<ul><li><a href='/stf/shingi/shingi-test_001.html'>テスト審議会</a></li></ul>"
                "</main></body></html>"
            ).encode("utf-8")
            council_html = (
                "<html><body><main><h1>テスト審議会</h1>"
                "<table><tr><th>開催回</th><th>年月日</th><th>資料</th></tr>"
                "<tr><td>第2回</td><td>令和8年5月10日</td>"
                "<td><a href='/stf/newpage_2026.html'>第2回議事録・資料</a></td></tr>"
                "<tr><td>第1回</td><td>平成31年2月1日</td>"
                "<td><a href='/stf/newpage_2019.html'>第1回議事録</a></td></tr>"
                "</table></main></body></html>"
            ).encode("utf-8")
            sub_2026_html = (
                "<html><body><div id='header'><a href='/top.html'>メニュー</a></div>"
                "<main><h1>第2回テスト審議会 議事録</h1>"
                "<p>日時：令和8年5月10日（日）14:00～16:00</p>"
                "<p>出席者：山田 太郎（座長）、鈴木 花子</p>"
                "<p>議事内容：医療データ利活用について議論した。</p>"
                "<h2>配付資料</h2><ul>"
                "<li><a href='/content/000100.pdf'>資料1 医療データ概要</a></li>"
                "<li><a href='https://warp.da.ndl.go.jp/info:ndljp/pid/1/www.mhlw.go.jp/old.pdf'>WARP退避資料</a></li>"
                "</ul></main></body></html>"
            ).encode("utf-8")
            sub_2019_html = (
                "<html><body><main><h1>第1回テスト審議会 議事録</h1>"
                "<p>日時：平成31年2月1日</p></main></body></html>"
            ).encode("utf-8")

            pages_map = {
                "/stf/shingi/indexshingi.html": index_html,
                "/stf/shingi/other-kami.html": "<html><body><main><h1>その他</h1></main></body></html>".encode("utf-8"),
                "/stf/shingi/shingi-test_001.html": council_html,
            }
            sub_map = {
                "https://www.mhlw.go.jp/stf/newpage_2026.html": sub_2026_html,
                "https://www.mhlw.go.jp/stf/newpage_2019.html": sub_2019_html,
            }

            def fake_page_fetcher(url, if_modified_since=""):
                if url not in pages_map:
                    return check_updates.FetchResponse(status=404, error="Not Found")
                if if_modified_since:
                    return check_updates.FetchResponse(status=304, fetched_at="2026-11-25T00:00:00+00:00")
                return check_updates.FetchResponse(
                    status=200, body=pages_map[url],
                    last_modified="2026-05-10T00:00:00+00:00", fetched_at="2026-11-25T00:00:00+00:00",
                )

            def fake_sub_fetcher(url, if_modified_since=""):
                if url not in sub_map:
                    return check_updates.FetchResponse(status=404, error="Not Found")
                return check_updates.FetchResponse(
                    status=200, body=sub_map[url],
                    last_modified="2026-05-10T00:00:00+00:00", fetched_at="2026-11-25T00:00:00+00:00",
                )

            def fake_pdf_fetcher(url, max_bytes=0, *, if_modified_since=""):
                body = b"%PDF-1.4 handout content"
                return {
                    "url": url, "status": "200", "content_type": "application/pdf",
                    "last_modified": "2026-05-10T00:00:00+00:00",
                    "fetched_at": "2026-11-25T00:00:00+00:00",
                    "bytes": str(len(body)),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "note": "",
                }, body

            # 1) 初回: トップページ取得＋下位 HTML 取得＋内包配布資料 PDF 取得まで一貫実行（pdf_types=None で配布資料も含む）
            out = update.run_update(
                agency_id="mhlw",
                repo=repo,
                pdf_types=None,
                fetch_subpages_enabled=True,
                max_subpages=10,
                interval=0,
                page_fetcher=fake_page_fetcher,
                subpage_fetcher=fake_sub_fetcher,
                pdf_fetcher=fake_pdf_fetcher,
                text_extractor=lambda *a: 1,
            )
            self.assertTrue(out.ok, out.error)
            self.assertEqual(len(out.saved_subpages), 2)
            # 内包配布資料 PDF（/content/000100.pdf）が発見・取得され、WARP は除外されること
            self.assertEqual(out.saved_pdfs, ["https://www.mhlw.go.jp/content/000100.pdf"])

            # 下位 HTML の本文テキストがナビ除外で抽出され data/mhlw/text/ に保存されていること
            txt_path = repo / "data" / "mhlw" / "text" / "stf" / "newpage_2026.txt"
            self.assertTrue(txt_path.exists())
            txt_content = txt_path.read_text(encoding="utf-8")
            self.assertIn("医療データ利活用", txt_content)
            self.assertNotIn("メニュー", txt_content)

            # 統一検索索引（catalog.csv / catalog.jsonl）と横断 index.json が生成されること
            catalog_csv = ag.data_dir / "catalog.csv"
            catalog_jsonl = ag.data_dir / "catalog.jsonl"
            self.assertTrue(catalog_csv.exists())
            self.assertTrue(catalog_jsonl.exists())
            cat_rows = [json.loads(line) for line in catalog_jsonl.read_text(encoding="utf-8").splitlines() if line]
            urls = {r["url"]: r for r in cat_rows}
            self.assertIn("https://www.mhlw.go.jp/content/000100.pdf", urls)
            self.assertEqual(urls["https://www.mhlw.go.jp/content/000100.pdf"]["fetched"], "true")
            self.assertEqual(urls["https://www.mhlw.go.jp/stf/newpage_2026.html"]["text_path"], "data/mhlw/text/stf/newpage_2026.txt")

            # 横断集計で data/all/index.json も生成されること
            cross_agency.build_cross_tables(repo=repo, write=True)
            idx_json = json.loads((repo / "data" / "all" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(idx_json["totals"]["pdfs_fetched"], 1)
            self.assertEqual(idx_json["totals"]["subpages_fetched"], 2)

            # 2) 年代フィルタの検証（since_year=2024 は 2026 年分だけ返す）
            recent_targets = fetch_subpages.collect_targets(ag, since_year=2024, include_discovered=False)
            self.assertEqual([t.url for t in recent_targets], ["https://www.mhlw.go.jp/stf/newpage_2026.html"])
            old_targets = fetch_subpages.collect_targets(ag, until_year=2020, include_discovered=False)
            self.assertEqual([t.url for t in old_targets], ["https://www.mhlw.go.jp/stf/newpage_2019.html"])

            # 3) 2 回目実行は変更なしになること
            out2 = update.run_update(
                agency_id="mhlw",
                repo=repo,
                pdf_types=None,
                fetch_subpages_enabled=True,
                max_subpages=10,
                interval=0,
                page_fetcher=fake_page_fetcher,
                subpage_fetcher=fake_sub_fetcher,
                pdf_fetcher=fake_pdf_fetcher,
                text_extractor=lambda *a: 0,
            )
            self.assertTrue(out2.ok, out2.error)
            self.assertEqual(out2.saved_subpages, [])
            self.assertEqual(out2.saved_pdfs, [])


class ShardedArchivingTest(unittest.TestCase):
    def test_pdf_bundle_shards_and_cleans_local_files(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            files_dir = tmp / "files"
            manifest = tmp / "files.csv"
            rows = {}
            for i in range(3):
                url = f"https://www.cas.go.jp/jp/seisaku/test/doc{i}.pdf"
                p = fetch_files.local_path(url, files_dir)
                p.parent.mkdir(parents=True, exist_ok=True)
                body = b"%PDF-1.4 " + (b"X" * 100)
                p.write_bytes(body)
                rows[url] = {
                    "url": url, "status": "200", "content_type": "application/pdf",
                    "last_modified": "", "fetched_at": "", "bytes": str(len(body)),
                    "sha256": hashlib.sha256(body).hexdigest(), "note": "", "release_url": "",
                }
            fetch_files.write_manifest(rows, manifest)

            uploaded_tars = []

            def fake_runner(cmd, **kwargs):
                if cmd[:3] == ["gh", "release", "view"]:
                    return subprocess.CompletedProcess(cmd, 0)
                if cmd[:3] == ["gh", "release", "upload"]:
                    tar_path = Path(cmd[4])
                    with tarfile.open(tar_path, "r:gz") as tf:
                        uploaded_tars.append((tar_path.name, tf.getnames()))
                return subprocess.CompletedProcess(cmd, 0)

            # 1 シャード 150 バイト上限で 3 件 → 3 シャードに分割されること
            res = archive_pdfs.upload_baseline_bundle(
                manifest=manifest,
                files_dir=files_dir,
                tag="cas-pdfs-2026-backfill",
                agency_name="内閣官房",
                repo_slug="tsuji-y/shingikai",
                max_shard_bytes=150,
                chunk_id="c001",
                cleanup_local=True,
                runner=fake_runner,
            )
            self.assertEqual(len(res), 3)
            self.assertEqual(len(uploaded_tars), 3)
            self.assertTrue(all("-c001-p00" in name for name, _ in uploaded_tars))

            # cleanup_local=True でローカル原本が削除され、台帳に release_url が記録されていること
            saved = fetch_files.read_manifest(manifest)
            for url in rows:
                self.assertIn("cas-pdfs-2026-backfill", saved[url]["release_url"])
                self.assertFalse(fetch_files.local_path(url, files_dir).exists())

    def test_subpages_bundle_archives_and_cleans_local_html(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sub_dir = tmp / "subpages"
            manifest = tmp / "subpages.csv"
            url = "https://www.digital.go.jp/councils/ai/mtg-01"
            p = fetch_subpages.local_path(url, sub_dir)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"<html><body>meeting 1</body></html>")
            fetch_subpages.write_manifest({
                url: {
                    "url": url, "council_id": "ai", "meeting_id": "ai#1", "meeting_date": "2026-05-01",
                    "doc_type": "議事録", "status": "200", "last_modified": "", "fetched_at": "",
                    "bytes": "35", "sha256": "abc123", "text": "data/digital/text/councils/ai/mtg-01.txt",
                    "discovered_pdfs": "0", "note": "", "release_url": "",
                }
            }, manifest)

            seen_members = []

            def fake_runner(cmd, **kwargs):
                if cmd[:3] == ["gh", "release", "view"]:
                    return subprocess.CompletedProcess(cmd, 0)
                if cmd[:3] == ["gh", "release", "upload"]:
                    with tarfile.open(cmd[4], "r:gz") as tf:
                        seen_members.extend(tf.getnames())
                return subprocess.CompletedProcess(cmd, 0)

            res = archive_pdfs.upload_subpages_bundle(
                manifest=manifest,
                subpages_dir=sub_dir,
                tag="digital-html-2026-11",
                agency_name="デジタル庁",
                repo_slug="tsuji-y/shingikai",
                chunk_id="c001",
                cleanup_local=True,
                runner=fake_runner,
            )
            self.assertEqual(len(res), 1)
            self.assertEqual(seen_members, ["councils/ai/mtg-01/index.html"])
            self.assertFalse(p.exists())
            updated = fetch_subpages.read_manifest(manifest)
            self.assertIn("digital-html-2026-11", updated[url]["release_url"])

    def test_double_slash_pdf_url_resolves_cleanly(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = fetch_files.local_path("https://www.ppc.go.jp/files/pdf//250919_gijiroku.pdf", root)
            self.assertEqual(p, root / "files" / "pdf" / "250919_gijiroku.pdf")

    def test_subpage_dotdot_url_resolves_safely_inside_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            url = "https://www.mhlw.go.jp/shingi/../shingi/2009/10/s1019-3.html"
            self.assertEqual(
                fetch_subpages.local_path(url, root),
                root / "shingi" / "2009" / "10" / "s1019-3.html",
            )
            self.assertEqual(
                fetch_subpages.html_text_path(url, root),
                root / "shingi" / "2009" / "10" / "s1019-3.txt",
            )


if __name__ == "__main__":
    unittest.main()
