import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mod"))
import build_db  # noqa: E402
import minutes  # noqa: E402
import pages  # noqa: E402

NUMBERED = """第１０５回自衛隊員倫理審査会議事録
１  日  時
    令和６年１２月１１日（水）１４時００分～１６時００分
２  場  所
    防衛省 E１棟８階  会議室 C
３  出席者
    （委  員）  太田会長、能勢委員、山宮委員、諏訪委員
    （防衛省）  林服務管理官
４  議  事
(１)  開会の辞
○  太田会長  只今より開催させていただきます。
"""

NESTED = """第５９回再就職等監視分科会  議事要旨
１  会議の日時及び場所
（１）日時
      令和５年１月２７日（金）１０００～１２００
（２）場所
      防衛省Ｅ１棟６階  再就職等監視室
２  出席者
    （分科会委員）
根本分科会長、峰委員、永野委員、森永委員
    （再就職等監察官）
      宮田監察官、一條監察官、鈴木監察官、中村監察官、西畠監察官、藤井監察
官、山中監察官
    （防衛省側）
      渡邊再就職等監視室長ほか
３  議題
（１）第５８回分科会議事録及び議事要旨の確認について
（２）管理職隊員以外の隊員の予備的調査
等の調査状況について
４  審議の概要及び結果
（１）確認された。
"""

INLINE = """第６回処遇・給与部会  議事録
日時：令和７年７月２８日（月）  １３３０〜１５００
場所：防衛省会議室
出席者：（部会委員）井上部会長、浦岡委員、金野委員、
中山委員
        （防衛省）青木人事教育局長、尾山空幕人事教育部長  他
井上部会長の開会の辞に引き続き、防衛省より人事異動者の紹介及び説明を行い、その後、意
見交換。
委員：給付水準の引上げ等については、承知した。
"""


class HeaderTest(unittest.TestCase):
    def test_numbered_sections(self):
        h = minutes.parse_header(NUMBERED)
        self.assertEqual(build_db.parse_date(h["date_raw"])[0], "2024-12-11")
        self.assertEqual(h["time"], "14:00-16:00")
        self.assertEqual(h["place"], "防衛省 E1棟8階 会議室 C")
        self.assertEqual(minutes.split_attendees(h["attendees"]),
                         [("委員", "太田会長"), ("委員", "能勢委員"), ("委員", "山宮委員"), ("委員", "諏訪委員"),
                          ("防衛省", "林服務管理官")])
        self.assertEqual(h["agenda"], "")  # 「議事」は本文の始まり

    def test_nested_numbering_and_wrapped_names(self):
        h = minutes.parse_header(NESTED)
        self.assertEqual(build_db.parse_date(h["date_raw"])[0], "2023-01-27")
        self.assertEqual(h["time"], "10:00-12:00")
        self.assertEqual(h["place"], "防衛省E1棟6階 再就職等監視室")
        names = minutes.split_attendees(h["attendees"])
        self.assertEqual([n for g, n in names if g == "再就職等監察官"][-2:], ["藤井監察官", "山中監察官"])
        self.assertEqual(names[-1], ("防衛省側", "渡邊再就職等監視室長"))
        self.assertEqual(h["agenda"], "(1)第58回分科会議事録及び議事要旨の確認について / "
                                      "(2)管理職隊員以外の隊員の予備的調査等の調査状況について")

    def test_inline_fields_stop_before_the_body(self):
        h = minutes.parse_header(INLINE)
        self.assertEqual(build_db.parse_date(h["date_raw"])[0], "2025-07-28")
        self.assertEqual(h["time"], "13:30-15:00")
        self.assertEqual(h["place"], "防衛省会議室")
        self.assertEqual(minutes.split_attendees(h["attendees"]),
                         [("部会委員", "井上部会長"), ("部会委員", "浦岡委員"), ("部会委員", "金野委員"),
                          ("部会委員", "中山委員"), ("防衛省", "青木人事教育局長"), ("防衛省", "尾山空幕人事教育部長")])

    def test_date_line_under_the_title(self):
        h = minutes.parse_header("第５７回  防衛省\n政策評価に関する有識者会議\n令和８年８月１３日\n午後１時３０分  開会\n○山谷座長  それでは。\n")
        self.assertEqual(build_db.parse_date(h["date_raw"])[0], "2026-08-13")
        self.assertEqual(h["attendees"], "")

    def test_only_the_first_page_is_read(self):
        self.assertEqual(minutes.parse_header("表紙\n\f１ 日時\n令和８年１月５日\n")["date_raw"], "")


class NamesTest(unittest.TestCase):
    def test_names_separated_by_spaces(self):
        raw = "（委  員）\n田内会長  柴山会長代理  石田委員\n（防衛省）\n滝澤長官官房審議官、小杉装備政策部\n長、秋田監察監査・評価官（事務局）\n金山統幕後方計画部長"
        self.assertEqual(minutes.split_attendees(raw),
                         [("委員", "田内会長"), ("委員", "柴山会長代理"), ("委員", "石田委員"),
                          ("防衛省", "滝澤長官官房審議官"), ("防衛省", "小杉装備政策部長"),
                          ("防衛省", "秋田監察監査・評価官(事務局)"), ("防衛省", "金山統幕後方計画部長")])

    def test_one_full_name_per_line(self):
        raw = "（委  員）\n青木  節子（慶應義塾大学大学院教授）\n中西  寛（京都大学大学院教授）"
        self.assertEqual(minutes.split_attendees(raw),
                         [("委員", "青木 節子(慶應義塾大学大学院教授)"), ("委員", "中西 寛(京都大学大学院教授)")])

    def test_bulleted_groups_and_roles_as_labels(self):
        text = ("第５３回テスト有識者会議議事概要\n１．会議概要\n日  時：令和７年８月５日（火）１０００－１１２７\n"
                "開催要領：オンライン会議\n参加者：\n・有識者\n        座長：山谷  清志氏（同志社大学  名誉教授）\n"
                "        委員：佐藤  達夫氏（取締役会長）\n              南島  和久氏（龍谷大学教授）\n"
                "・防衛省（主要な参加者）\n  大臣官房企画評価課長\n  事業監理官（次期戦闘機担当）付技術計画調\n整官\n"
                "    議  題：令和７年度政策評価書（案）\n２．有識者の主なコメント等\n")
        h = minutes.parse_header(text)
        self.assertEqual((h["time"], h["place"], h["agenda"]), ("10:00-11:27", "オンライン会議", "令和7年度政策評価書(案)"))
        self.assertEqual(minutes.split_attendees(h["attendees"]),
                         [("座長", "山谷 清志氏(同志社大学 名誉教授)"), ("委員", "佐藤 達夫氏(取締役会長)"),
                          ("委員", "南島 和久氏(龍谷大学教授)"), ("防衛省(主要な参加者)", "大臣官房企画評価課長"),
                          ("防衛省(主要な参加者)", "事業監理官(次期戦闘機担当)付技術計画調整官")])
        self.assertEqual(minutes.split_name("山谷 清志氏(同志社大学 名誉教授)"), ("山谷", ""))
        self.assertEqual(minutes.split_name("藤井 聡 (京都大学大学院工学研究科教授)[会長]"), ("藤井", "会長"))

    def test_split_name(self):
        self.assertEqual(minutes.split_name("太田会長"), ("太田", "会長"))
        self.assertEqual(minutes.split_name("柴山会長代理"), ("柴山", "会長代理"))
        self.assertEqual(minutes.split_name("根本分科会長"), ("根本", "分科会長"))
        self.assertEqual(minutes.split_name("青木 節子(慶應義塾大学大学院教授)"), ("青木", ""))
        self.assertEqual(minutes.split_name("林服務管理官"), ("", ""))
        self.assertEqual(minutes.split_name("防衛装備庁 滝澤長官官房審議官"), ("", ""))

    def test_time(self):
        self.assertEqual(minutes.parse_time("令和８年７月８日（水）  ０９：４０～１５：００"), "09:40-15:00")
        self.assertEqual(minutes.parse_time("令和５年２月７日（火）"), "")


class ApplyMinutesTest(unittest.TestCase):
    ROOT = "/j/policy/agenda/meeting/"
    COUNCILS = [{"council_id": "x", "section": "", "name": "テスト審議会", "link": ROOT + "x/index.html",
                 "note": "", "first_seen": ""}]
    HTML = ("<html><body><h1>テスト審議会</h1><h3>委員</h3><table><tr><th>氏名</th><th>職名等</th></tr>"
            "<tr><td>◎太田 達也</td><td>教授</td></tr></table>"
            "<ul><li><a href='pdf/giji_105.pdf'>第105回テスト審議会議事録</a></li>"
            "<li><a href='pdf/giji_104.pdf'>第104回テスト審議会議事録</a></li></ul></body></html>")

    def build(self, texts, html=None):
        """texts は {ファイル名: 本文}。本文のある PDF として扱う。"""
        url = self.ROOT + "x/index.html"
        b = build_db.Builder(build_db.Councils(self.COUNCILS), {url})
        b.page(url, pages.parse(html or self.HTML, url))
        build_db.finish(b)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            rows = []
            for name, body in texts.items():
                (repo / name).write_text(body, encoding="utf-8")
                rows.append({"url": f"https://www.mod.go.jp{self.ROOT}x/pdf/{name.replace('.txt', '.pdf')}", "text": name})
            with (repo / "texts.csv").open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["url", "text"])
                w.writeheader()
                w.writerows(rows)
            build_db.apply_minutes(b, repo / "texts.csv", repo)
        return b

    def test_date_and_attendance_are_filled_from_the_text(self):
        b = self.build({"giji_105.txt": NUMBERED})
        m = {m["meeting_id"]: m for m in b.meetings}
        self.assertEqual((m["x#105"]["date"], m["x#105"]["time"], m["x#105"]["found_by"]),
                         ("2024-12-11", "14:00-16:00", "link+pdf"))
        self.assertEqual((m["x#104"]["date"], m["x#104"]["found_by"]), ("", "link"))
        first = b.attendance[0]
        self.assertEqual((first["meeting_id"], first["group"], first["name_raw"], first["surname"], first["role"],
                          first["roster_match"]), ("x#105", "委員", "太田会長", "太田", "会長", "太田 達也"))
        self.assertEqual([a["roster_match"] for a in b.attendance[1:]], ["", "", "", ""])

    def test_date_only_row_is_merged_once_the_text_gives_the_date(self):
        html = ("<html><body><h1>テスト審議会</h1><table><tr><th>年月日</th><th>議題</th></tr>"
                "<tr><td>R6.12.11</td><td>会長の互選等について</td></tr>"
                "<tr><td>R3.4.15</td><td>運営規則について</td></tr></table>"
                "<p><a href='pdf/giji_105.pdf'>第105回テスト審議会議事録</a></p></body></html>")
        before = self.build({}, html)
        self.assertEqual([m["meeting_id"] for m in before.meetings], ["x#105", "x@2021-04-15", "x@2024-12-11"])
        b = self.build({"giji_105.txt": NUMBERED}, html)
        self.assertEqual([(m["meeting_id"], m["date"], m["agenda"], m["n_documents"], m["found_by"]) for m in b.meetings],
                         [("x#105", "2024-12-11", "会長の互選等について", 1, "table+link+pdf"), ("x@2021-04-15", "2021-04-15", "運営規則について", 0, "table")])
        self.assertEqual({a["meeting_id"] for a in b.attendance}, {"x#105"})

    def test_text_for_another_meeting_is_not_applied(self):
        b = self.build({"giji_104.txt": NUMBERED})  # 第104回のリンク先の表題が第105回
        self.assertEqual(b.attendance, [])
        self.assertEqual(len(b.warnings), 1)


if __name__ == "__main__":
    unittest.main()
