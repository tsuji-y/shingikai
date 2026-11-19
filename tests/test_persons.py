import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mod"))
import minutes  # noqa: E402
import pages  # noqa: E402
import persons  # noqa: E402


def att(meeting, council, group, name_raw):
    surname, role = minutes.split_name(name_raw)
    return {"meeting_id": meeting, "council_id": council, "group": group, "name_raw": name_raw,
            "surname": surname, "role": role}


def member(council, name, title=""):
    return {"council_id": council, "name": name, "role": "", "title": title}


class NamePartsTest(unittest.TestCase):
    def test_full_names_and_affiliations(self):
        self.assertEqual(persons.name_parts("青木 節子(慶應義塾大学大学院教授)"), ("青木 節子", "慶應義塾大学大学院教授"))
        self.assertEqual(persons.name_parts("山川 宏 (国立研究開発法人 宇宙航空研究開発機構(JAXA)理事長)[座長]"),
                         ("山川 宏", "国立研究開発法人 宇宙航空研究開発機構(JAXA)理事長"))
        self.assertEqual(persons.name_parts("榊原定征(座長)"), ("榊原定征", ""))
        self.assertEqual(persons.name_parts("山谷 清志氏(同志社大学 名誉教授)"), ("山谷 清志", "同志社大学 名誉教授"))

    def test_surname_only_and_non_names(self):
        self.assertEqual(persons.name_parts("太田会長"), ("", ""))
        self.assertEqual(persons.name_parts("国立大学法人大阪大学工学部教授"), ("", ""))
        self.assertEqual(persons.name_parts("ラテジックアドバイザー"), ("", ""))
        self.assertEqual(persons.name_parts("半澤政策参与"), ("", ""))

    def test_variant_characters_share_a_key(self):
        self.assertEqual(persons.key("髙橋　秀雄"), persons.key("高橋 秀雄"))
        self.assertNotEqual(persons.key("豊嶋 守生"), persons.key("豊島 守生"))


class LinkTest(unittest.TestCase):
    def test_linking(self):
        members = [member("a", "太田 達也", "教授"), member("a", "佐藤 達夫"), member("a", "佐藤 丙午"),
                   member("b", "遠藤 典子", "早稲田大学教授")]
        rows = [att("a#1", "a", "委員", "太田会長"),          # 名簿に同じ姓が 1 人
                att("a#1", "a", "委員", "佐藤委員"),          # 名簿に同じ姓が 2 人
                att("a#1", "a", "委員", "能勢委員"),          # 名簿にいない
                att("a#2", "a", "委員", "能勢委員"),
                att("a#1", "a", "防衛省", "林服務管理官"),    # 省側
                att("c#1", "c", "有識者", "遠藤典子"),        # 別の会議体で、空白なしの氏名
                att("c#1", "c", "有識者", "遠藤委員")]        # 同じ会議体に氏名の分かる遠藤が 1 人
        people = persons.link(members, rows, {"a#1": "2024-01-10", "a#2": "2024-03-05", "c#1": "2025-02-01"},
                              {"a": 0, "b": 1, "c": 2})
        self.assertEqual([(r["person_id"], r["person_basis"]) for r in rows],
                         [("太田達也", "surname"), ("", "ambiguous"), ("a:能勢", "surname_only"),
                          ("a:能勢", "surname_only"), ("", ""), ("遠藤典子", "full_name"), ("遠藤典子", "surname")])
        by_id = {p["person_id"]: p for p in people}
        self.assertEqual((by_id["遠藤典子"]["name"], by_id["遠藤典子"]["councils"], by_id["遠藤典子"]["roster_councils"],
                          by_id["遠藤典子"]["n_meetings"]), ("遠藤 典子", "b c", "b", 1))
        self.assertEqual((by_id["a:能勢"]["basis"], by_id["a:能勢"]["n_meetings"], by_id["a:能勢"]["first_date"],
                          by_id["a:能勢"]["last_date"]), ("surname_only", 2, "2024-01-10", "2024-03-05"))
        self.assertEqual(members[0]["person_id"], "太田達也")


class AffiliationLinesTest(unittest.TestCase):
    def test_affiliation_on_the_following_lines(self):
        raw = ("【部外有識者】\n岩﨑  洋一\n元海上自衛隊補給本部副本部長\n岡  正義\n国立研究開発法人海上技\n術安全研究所 PT\nリーダー\n"
               "金子  博文（座長代理）  一般財団法人日本海事協会スト\nラテジックアドバイザー\n"
               "【防衛省】\n嶺  康晴\n防衛装備庁プロジェクト管理部長\n事務次官")
        self.assertEqual(minutes.split_attendees(raw),
                         [("部外有識者", "岩﨑 洋一(元海上自衛隊補給本部副本部長)"),
                          ("部外有識者", "岡 正義(国立研究開発法人海上技術安全研究所 PTリーダー)"),
                          ("部外有識者", "金子 博文(座長代理) 一般財団法人日本海事協会ストラテジックアドバイザー"),
                          ("防衛省", "嶺 康晴(防衛装備庁プロジェクト管理部長)"), ("防衛省", "事務次官")])

    def test_ruby_text_is_skipped(self):
        blocks = pages.parse("<table><tr><td><ruby>三島<rt>みしま</rt></ruby> <ruby>茂徳<rp>(</rp><rt>しげのり</rt><rp>)</rp></ruby></td></tr></table>",
                             "/j/policy/agenda/meeting/x/index.html")
        self.assertEqual(blocks[0].cells[0].text, "三島 茂徳")


if __name__ == "__main__":
    unittest.main()
