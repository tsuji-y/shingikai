import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mod"))
import poll_rss  # noqa: E402

FEED = """﻿<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>t</title>
<item><title>防衛政策｜審議会・検討会等（防衛調達審議会）を更新</title><link>/j/policy/agenda/meeting/cho-shin/index.html</link><guid isPermaLink="true">/j/policy/agenda/meeting/cho-shin/index.html</guid><category>行政</category><pubDate>Wed, 30 Sep 2026 18:05:00 +0900</pubDate><description></description><content:encoded></content:encoded></item>
<item><title>予算・調達｜内部部局を更新</title><link>/j/budget/index.html</link><guid>/j/budget/index.html</guid><category>行政</category><pubDate>Wed, 30 Sep 2026 18:05:00 +0900</pubDate><description></description><content:encoded></content:encoded></item>
<item><title>防衛人事審議会公正審査分科会の開催について</title><link>/j/press/news/2026/09/24a.html</link><guid>/j/press/news/2026/09/24a.html</guid><category>行政</category><pubDate>Thu, 24 Sep 2026 17:00:00 +0900</pubDate><description></description><content:encoded></content:encoded></item>
</channel></rss>""".encode("utf-8")


class PollRssTest(unittest.TestCase):
    def test_parse_fields_and_council_flag(self):
        rows = poll_rss.parse("update", FEED, "2026-11-19T00:00:00+00:00")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["pub_date"], "2026-09-30T18:05:00+09:00")
        self.assertEqual(rows[0]["link"], "/j/policy/agenda/meeting/cho-shin/index.html")
        self.assertEqual([r["council_related"] for r in rows], ["1", "0", "1"])

    def test_title_heuristic_excludes_kondan_kaishoku(self):
        self.assertFalse(poll_rss.is_council_related(
            "update", "防衛省について｜副大臣の動静（国防大臣との懇談会食）を更新", "/j/profile/minister/x.html"))
        self.assertTrue(poll_rss.is_council_related("news", "○○に関する懇談会の開催について", "/j/press/news/a.html"))

    def test_merge_is_idempotent_and_keeps_first_seen(self):
        first = poll_rss.parse("update", FEED, "2026-11-19T00:00:00+00:00")
        merged, new = poll_rss.merge([], first)
        self.assertEqual(len(new), 3)
        again = poll_rss.parse("update", FEED, "2026-11-20T00:00:00+00:00")
        merged2, new2 = poll_rss.merge(merged, again)
        self.assertEqual(new2, [])
        self.assertEqual({r["first_seen"] for r in merged2}, {"2026-11-19T00:00:00+00:00"})

    def test_same_page_updated_twice_is_two_rows(self):
        first = poll_rss.parse("update", FEED, "x")
        later = FEED.replace(b"Wed, 30 Sep 2026", b"Wed, 28 Oct 2026")
        merged, new = poll_rss.merge(first, poll_rss.parse("update", later, "y"))
        self.assertEqual(len(new), 2)
        self.assertEqual(len(merged), 5)

    def test_challenge_page_is_rejected(self):
        with self.assertRaises(poll_rss.FeedError):
            poll_rss.parse("update", b"<!DOCTYPE html><html><title>Just a moment...</title></html>", "x")

    def test_empty_feed_is_rejected(self):
        with self.assertRaises(poll_rss.FeedError):
            poll_rss.parse("update", b'<rss version="2.0"><channel></channel></rss>', "x")


if __name__ == "__main__":
    unittest.main()
