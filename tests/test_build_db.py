import base64
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mod"))
import build_db  # noqa: E402
import ingest_pages  # noqa: E402
import pages  # noqa: E402

ROOT = "/j/policy/agenda/meeting/"
INDEX = ROOT + "x/index.html"
COUNCILS = [{"council_id": "x", "section": "審議会・有識者会議等", "name": "テスト審議会",
             "link": INDEX, "note": "", "first_seen": ""}]


def run(page_list, known=()):
    """[(url, html), ...] を取り込んだ Builder を返す。"""
    councils = build_db.Councils(COUNCILS)
    b = build_db.Builder(councils, set(known) | {u for u, _ in page_list})
    for url, html in page_list:
        blocks = pages.parse(html, url)
        b.page(url, blocks, split=build_db.separate_series(councils, b.known_pages, url, blocks))
    build_db.finish(b)
    return b


def page(body):
    return f"<html><body><header><a href='/'>top</a></header><h1>テスト審議会</h1>{body}<footer><a href='/x'>f</a></footer></body></html>"


class DateAndNumberTest(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(build_db.parse_date("令和８年３月３１日")[0], "2026-03-31")
        self.assertEqual(build_db.parse_date("平成元年４月１日")[0], "1989-04-01")
        self.assertEqual(build_db.parse_date("R8.8.26")[0], "2026-08-26")
        self.assertEqual(build_db.parse_date("H12.4.12")[0], "2000-04-12")
        self.assertEqual(build_db.parse_date("2026年9月30日更新")[0], "2026-09-30")
        self.assertEqual(build_db.parse_date("令和８年２月３０日")[0], "")
        self.assertEqual(build_db.parse_date("第３回会合")[0], "")

    def test_short_date_needs_matching_url(self):
        text = "議事要旨（第89回 08.9.3）"
        self.assertEqual(build_db.parse_date(text)[0], "")
        self.assertEqual(build_db.parse_date(text, "https://www.mod.go.jp/a/giji_20260903.pdf")[0], "2026-09-03")
        self.assertEqual(build_db.parse_date(text, "https://www.mod.go.jp/a/giji_20260904.pdf")[0], "")

    def test_numbers(self):
        self.assertEqual(build_db.parse_number("第２０１回定例"), 201)
        self.assertEqual(build_db.parse_number("第二回出席"), 2)
        self.assertIsNone(build_db.parse_number("第１００回～第５１回"))
        self.assertIsNone(build_db.parse_number("第３回報告（平成２４年９月２６日）"))
        self.assertIsNone(build_db.parse_number("会長の互選等について"))


class PagesTest(unittest.TestCase):
    def test_blocks_skip_navigation_and_resolve_links(self):
        html = page("<h2>議事録等</h2><p><a href='pdf/a.pdf'>議事録 <span>別ウィンドウで開く</span></a></p>"
                    "<table><tr><th>開催回</th><th>年月日</th></tr>"
                    "<tr><td>第1回</td><td>令和8年1月5日<br>（持ち回り）</td></tr></table>")
        blocks = pages.parse(html, INDEX)
        self.assertEqual([b.kind for b in blocks], ["heading", "heading", "text", "row", "row"])
        self.assertEqual(blocks[2].links[0].text, "議事録")
        self.assertEqual(blocks[2].links[0].url, "https://www.mod.go.jp" + ROOT + "x/pdf/a.pdf")
        self.assertEqual(blocks[2].path, ("議事録等",))
        self.assertTrue(all(c.header for c in blocks[3].cells))
        self.assertEqual(blocks[4].cells[1].text, "令和8年1月5日\n（持ち回り）")


class BuildTest(unittest.TestCase):
    def test_meeting_table_with_documents(self):
        b = run([(INDEX, page(
            "<h2>議事録等</h2><table><tr><th>開催回</th><th>年月日</th><th>議題</th><th>議事録等</th></tr>"
            "<tr><td>第2回</td><td>令和8年3月31日</td><td><ul><li>議題A</li><li>議題B</li></ul></td>"
            "<td><a href='pdf/2_1.pdf'>会議資料</a><a href='pdf/2_2.pdf'>議事要旨</a></td></tr>"
            "<tr><td>第1回</td><td>R7.12.17</td><td>議題C</td><td><a href='pdf/1.pdf'>議事録</a></td></tr></table>"))])
        self.assertEqual([(m["meeting_id"], m["date"], m["agenda"], m["n_documents"]) for m in b.meetings],
                         [("x#1", "2025-12-17", "議題C", 1), ("x#2", "2026-03-31", "議題A / 議題B", 2)])
        self.assertEqual([(d["meeting_id"], d["doc_type"], d["section"]) for d in b.documents],
                         [("x#2", "資料", "議事録等"), ("x#2", "議事要旨", "議事録等"), ("x#1", "議事録", "議事録等")])

    def test_link_text_and_table_refer_to_the_same_meeting(self):
        b = run([(INDEX, page(
            "<table><tr><th>区分</th><th>年月日</th><th>議題</th></tr>"
            "<tr><td>第198回定例</td><td>R7.12.17</td><td>調査審議</td></tr></table>"
            "<h2>議事録等</h2><ul><li><a href='giji/pdf/198.pdf'>第198回テスト審議会議事要旨</a></li>"
            "<li><a href='giji/pdf/197.pdf'>第197回テスト審議会議事要旨</a></li>"
            "<li><a href='giji_001-100.html'>第100回～第1回</a></li></ul>"))])
        self.assertEqual([(m["meeting_id"], m["date"], m["found_by"]) for m in b.meetings],
                         [("x#197", "", "link"), ("x#198", "2025-12-17", "table+link")])
        self.assertEqual(len(b.documents), 2)  # 範囲のリンクはサイト内の案内なので載せない

    def test_series_under_different_headings_are_kept_apart(self):
        table = ("<table><tr><th>開催回</th><th>年月日</th></tr>"
                 "<tr><td>第1回</td><td>{}</td></tr></table>")
        b = run([(INDEX, page("<h2>議事録等</h2><h3>総会</h3>" + table.format("令和6年2月19日")
                              + "<h3>部会</h3>" + table.format("令和6年8月30日")))])
        self.assertEqual([(m["meeting_id"], m["date"]) for m in b.meetings],
                         [("x/総会#1", "2024-02-19"), ("x/部会#1", "2024-08-30")])
        self.assertEqual(b.warnings, [])

    def test_same_number_with_different_dates_is_not_merged(self):
        b = run([(INDEX, page(
            "<table><tr><th>開催回</th><th>年月日</th></tr>"
            "<tr><td>第6回</td><td>平成28年6月8日</td></tr>"
            "<tr><td>第6回</td><td>平成27年4月22日</td></tr></table>"))])
        self.assertEqual([m["meeting_id"] for m in b.meetings], ["x#6@2015-04-22", "x#6"])
        self.assertEqual(len(b.warnings), 1)

    def test_rows_without_label_continue_the_previous_meeting(self):
        b = run([(INDEX, page(
            "<table><tr><th>第14回</th><td>H22.5.25</td><td><a href='a.pdf'>議事次第</a></td></tr>"
            "<tr><td><a href='b.pdf'>別の懇談会（第７回）の資料</a></td></tr></table>"))])
        self.assertEqual([m["meeting_id"] for m in b.meetings], ["x#14"])
        self.assertEqual([d["meeting_id"] for d in b.documents], ["x#14", "x#14"])

    def test_members_with_legend_and_as_of(self):
        b = run([(INDEX, page(
            "<h2>概要</h2><table><tr><th>根拠法令</th><td>防衛省組織令</td></tr>"
            "<tr><th>第１回会合日</th><td>平成２２年６月９日</td></tr></table>"
            "<h3>委員</h3><p>［令和８年４月１日現在］</p>"
            "<table><tr><th>氏名</th><th>職名等</th></tr>"
            "<tr><td>◎山田 太郎</td><td>大学教授</td></tr><tr><td>〇鈴木　花子</td><td>弁護士</td></tr>"
            "<tr><td>佐藤 次郎</td><td>公認会計士</td></tr></table><p>◎は会長を、◯は会長代理を示す。</p>"))])
        self.assertEqual([(r["name"], r["mark"], r["role"], r["as_of"]) for r in b.members],
                         [("山田 太郎", "◎", "会長", "2026-04-01"), ("鈴木 花子", "○", "会長代理", "2026-04-01"),
                          ("佐藤 次郎", "", "", "2026-04-01")])
        self.assertEqual([(r["key"], r["value"]) for r in b.profiles],
                         [("根拠法令", "防衛省組織令"), ("第１回会合日", "平成２２年６月９日")])
        self.assertEqual(b.meetings, [])

    def test_meeting_page_is_merged_with_the_index_row(self):
        detail = ROOT + "x/20260831.html"
        b = run([(INDEX, page(
            "<table><tr><th>開催回</th><th>年月日</th><th>議題</th></tr>"
            "<tr><td><a href='20260831.html'>第10回</a></td><td>令和8年8月31日</td><td>課題</td></tr></table>")),
            (detail, "<html><body><h1>第１０回 テスト審議会</h1><h2>年月日</h2><p>令和８年８月３１日</p>"
                     "<h2>３ 出席者</h2><p>山田会長、鈴木委員</p>"
                     "<h2>関連資料</h2><p><a href='pdf/20260831b.pdf'>課題について</a></p></body></html>")])
        self.assertEqual(len(b.meetings), 1)
        m = b.meetings[0]
        self.assertEqual((m["meeting_id"], m["date"], m["found_by"], m["attendees"]),
                         ("x#10", "2026-08-31", "table+page", "山田会長、鈴木委員"))
        self.assertEqual(m["page"], "https://www.mod.go.jp" + detail)
        self.assertEqual([(d["meeting_id"], d["section"]) for d in b.documents], [("x#10", "関連資料")])

    def test_date_before_links_applies_to_the_whole_line(self):
        b = run([(ROOT + "x/giji_bunkakai.html",
                  "<html><body><h1>公開分科会</h1><h2>平成２７年</h2><ul>"
                  "<li>（平成27年9月15日）<a href='a.pdf'>分科会議事録</a>｜<a href='b.pdf'>議決書</a></li>"
                  "</ul></body></html>")])
        self.assertEqual([(m["meeting_id"], m["body"]) for m in b.meetings],
                         [("x/公開分科会@2015-09-15", "公開分科会")])
        self.assertEqual([d["meeting_id"] for d in b.documents], ["x/公開分科会@2015-09-15"] * 2)


class IngestTest(unittest.TestCase):
    def test_ingest_saves_bytes_and_skips_errors(self):
        body = "<html>日本語\r\n</html>".encode("utf-8")
        records = [{"url": ROOT + "x/index.html", "status": 200, "last_modified": "Wed, 30 Sep 2026 01:41:18 GMT",
                    "fetched_at": "2026-11-19T05:46:10.084Z", "body_b64": base64.b64encode(body).decode()},
                   {"url": ROOT + "x/gone.html", "status": 404, "body_b64": ""}]
        with tempfile.TemporaryDirectory() as tmp:
            rows = {}
            saved, skipped = ingest_pages.ingest(records, rows, Path(tmp))
            self.assertEqual((saved, len(skipped)), (1, 1))
            self.assertEqual(ingest_pages.local_path(ROOT + "x/index.html", Path(tmp)).read_bytes(), body)
            self.assertEqual(rows[ROOT + "x/index.html"]["last_modified"], "2026-09-30T01:41:18+00:00")

    def test_paths_outside_the_root_are_rejected(self):
        with self.assertRaises(ValueError):
            ingest_pages.local_path("/j/../../etc/passwd")
        with self.assertRaises(ValueError):
            ingest_pages.local_path("j/policy/index.html")


if __name__ == "__main__":
    unittest.main()
