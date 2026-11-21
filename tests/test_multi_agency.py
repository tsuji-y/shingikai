import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mod"))
import agencies  # noqa: E402
import build_db  # noqa: E402
import check_updates  # noqa: E402
import fetch_files  # noqa: E402
import ingest_pages  # noqa: E402
import pages  # noqa: E402
import update  # noqa: E402


class MultiAgencyConfigAndParsingTest(unittest.TestCase):
    def test_all_16_agencies_load_cleanly(self):
        cfgs = agencies.load_agencies()
        expected = {
            "mod", "digital", "cfa", "cas", "cao", "maff",
            "mof", "moj", "env", "fsa", "caa",
            "mext", "mlit", "npa", "ppc", "mhlw",
        }
        self.assertEqual(set(cfgs.keys()), expected)
        for aid, ag in cfgs.items():
            self.assertEqual(ag.agency_id, aid)
            self.assertTrue(ag.base_url.startswith("https://"))
            self.assertTrue(ag.index_urls)
            self.assertTrue(ag.page_prefixes)

    def test_extensionless_and_html_local_path_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p_extless = ingest_pages.local_path("/councils/ai-advisory-board", root)
            self.assertEqual(p_extless, root / "councils" / "ai-advisory-board" / "index.html")
            p_child = ingest_pages.local_path("/councils/ai-advisory-board/6e055ef3-uuid", root)
            self.assertEqual(p_child, root / "councils" / "ai-advisory-board" / "6e055ef3-uuid" / "index.html")
            p_html = ingest_pages.local_path("/j/policy/agenda/meeting/index.html", root)
            self.assertEqual(p_html, root / "j" / "policy" / "agenda" / "meeting" / "index.html")

            p_extless.parent.mkdir(parents=True, exist_ok=True)
            p_extless.write_text("<html><body><h1>AI</h1></body></html>", encoding="utf-8")
            self.assertEqual(pages.resolve_local_path("/councils/ai-advisory-board", root), p_extless)

    def test_div_nav_skipping_and_body_id_header_exemption(self):
        # cas.go.jp のように <body id="header"> が付いていても本文はスキップせず、
        # <div class="gnav"> や <div id="footer"> はスキップすること
        html = (
            '\ufeff<html><body id="header">'
            '<div class="gnav"><ul><li><a href="/top.html">トップ</a></li></ul></div>'
            '<div id="contents"><h1>人工知能戦略専門調査会</h1>'
            '<p><a href="/jp/seisaku/ai/index.html">第1回会合</a></p></div>'
            '<div id="footer"><p><a href="/privacy.html">プライバシー</a></p></div>'
            '</body></html>'
        )
        blocks = pages.parse(html, "/jp/seisaku/index.html", base_url="https://www.cas.go.jp")
        texts = [b.text for b in blocks]
        self.assertIn("人工知能戦略専門調査会", texts)
        self.assertIn("第1回会合", texts)
        self.assertNotIn("トップ", texts)
        self.assertNotIn("プライバシー", texts)

    def test_bootstrap_extensionless_agency_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "agencies.json").write_text(
                (Path(__file__).resolve().parent.parent / "agencies.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            index_html = (
                '<html><body><main><h1>会議等</h1>'
                '<h2>有識者会議</h2>'
                '<ul><li><a href="/councils/ai-advisory-board">AIアドバイザリーボード</a></li></ul>'
                '</main></body></html>'
            ).encode("utf-8")
            council_html = (
                '<html><body><main><h1>AIアドバイザリーボード</h1>'
                '<h2>開催状況</h2>'
                '<ul><li><a href="/councils/ai-advisory-board/mtg-01">第1回 AIアドバイザリーボード（2026年5月10日）</a></li></ul>'
                '<h2>構成員名簿</h2>'
                '<p>令和8年5月1日現在</p>'
                '<table><tr><th>氏名</th><th>役職</th></tr>'
                '<tr><td>山田 太郎</td><td>東京大学教授</td></tr></table>'
                '</main></body></html>'
            ).encode("utf-8")
            mtg_html = (
                '<html><body><main><h1>第1回 AIアドバイザリーボード</h1>'
                '<h2>概要</h2>'
                '<ul><li>開催日時：2026年5月10日（日曜）10時00分から12時00分まで</li>'
                '<li>開催場所：オンライン開催</li></ul>'
                '<h2>資料</h2>'
                '<ul>'
                '<li><a href="/assets/contents/node/basic_page/gijiyoushi01.pdf">議事要旨（PDF）</a></li>'
                '<li><a href="/assets/contents/node/basic_page/shiryo01.pdf">資料1 説明資料（PDF）</a></li>'
                '</ul>'
                '</main></body></html>'
            ).encode("utf-8")

            web_pages = {
                "/councils": index_html,
                "/councils/ai-advisory-board": council_html,
                "/councils/ai-advisory-board/mtg-01": mtg_html,
            }

            def fake_page_fetcher(url, if_modified_since=""):
                if url not in web_pages:
                    return check_updates.FetchResponse(status=404, error="Not Found")
                if if_modified_since:
                    return check_updates.FetchResponse(status=304, fetched_at="2026-11-21T00:00:00+00:00")
                return check_updates.FetchResponse(
                    status=200,
                    body=web_pages[url],
                    last_modified="2026-05-10T00:00:00+00:00",
                    fetched_at="2026-11-21T00:00:00+00:00",
                )

            def fake_pdf_fetcher(url, max_bytes=0, *, if_modified_since=""):
                body = b"%PDF-1.4 dummy"
                return {
                    "url": url,
                    "status": "200",
                    "content_type": "application/pdf",
                    "last_modified": "2026-05-10T00:00:00+00:00",
                    "fetched_at": "2026-11-21T00:00:00+00:00",
                    "bytes": str(len(body)),
                    "sha256": "deadbeef" * 8,
                    "note": "",
                }, body

            out1 = update.run_update(
                agency_id="digital",
                repo=repo,
                pdf_types={"議事要旨", "議事録", "名簿"},
                interval=0,
                page_fetcher=fake_page_fetcher,
                pdf_fetcher=fake_pdf_fetcher,
                text_extractor=lambda *a: 1,
            )
            self.assertTrue(out1.ok, out1.error)
            self.assertEqual(len(out1.new_pages), 3)
            # PDF は議事要旨のみ取得され、一般資料はスキップされること
            self.assertEqual(
                out1.saved_pdfs,
                ["https://www.digital.go.jp/assets/contents/node/basic_page/gijiyoushi01.pdf"],
            )

            data_dir = repo / "data" / "digital"
            councils = build_db.load_councils(data_dir / "councils.csv")
            self.assertEqual(len(councils), 1)
            self.assertEqual(councils[0]["council_id"], "ai-advisory-board")

            # 2 回目の実行では変更なしになること
            out2 = update.run_update(
                agency_id="digital",
                repo=repo,
                pdf_types={"議事要旨", "議事録", "名簿"},
                interval=0,
                page_fetcher=fake_page_fetcher,
                pdf_fetcher=fake_pdf_fetcher,
                text_extractor=lambda *a: 0,
            )
            self.assertTrue(out2.ok, out2.error)
            self.assertEqual(out2.changed_pages, [])
            self.assertEqual(out2.new_pages, [])
            self.assertEqual(out2.saved_pdfs, [])
            self.assertEqual(out2.events, [])

            # 省庁横断テーブルの生成テスト
            import cross_agency

            ag_rows, p_rows, ev_rows = cross_agency.build_cross_tables(repo=repo, write=True)
            self.assertEqual(len(ag_rows), 16)
            dig_row = next(r for r in ag_rows if r["agency_id"] == "digital")
            self.assertEqual(dig_row["councils"], "1")
            self.assertEqual(dig_row["members"], "1")
            self.assertEqual(len(p_rows), 1)
            self.assertEqual(p_rows[0]["name"], "山田 太郎")
            self.assertTrue((repo / "data" / "all" / "agencies.csv").exists())
            self.assertTrue((repo / "data" / "all" / "persons_cross.csv").exists())
            self.assertTrue((repo / "data" / "all" / "recent_events.csv").exists())


if __name__ == "__main__":
    unittest.main()
