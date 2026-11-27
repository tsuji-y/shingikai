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

    def test_texts_bundle_archives_cleans_and_build_db_uses_parsed_cache(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "agencies.json").write_text(
                (REPO / "agencies.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            ag = agencies.get_agency("cfa", repo=repo)

            index_html = (
                "<html><body><main><h1>審議会一覧</h1>"
                "<ul><li><a href='/councils/test'>テスト会議</a></li></ul>"
                "</main></body></html>"
            ).encode("utf-8")
            council_html = (
                "<html><body><main><h1>テスト会議</h1>"
                "<ul>"
                "<li><a href='/assets/contents/roster.pdf'>構成員名簿</a></li>"
                "<li><a href='/assets/contents/mtg01_minutes.pdf'>第1回 議事要旨</a></li>"
                "</ul></main></body></html>"
            ).encode("utf-8")

            pages_map = {
                "/councils/meetings": index_html,
                "/councils/shingikai": "<html><body><main><h1>審議会</h1></main></body></html>".encode("utf-8"),
                "/councils/test": council_html,
            }

            def fake_page_fetcher(url, if_modified_since=""):
                if url not in pages_map:
                    return check_updates.FetchResponse(status=404, error="Not Found")
                return check_updates.FetchResponse(
                    status=200, body=pages_map[url],
                    last_modified="2026-05-01T00:00:00+00:00", fetched_at="2026-11-27T00:00:00+00:00",
                )

            out = update.run_update(
                agency_id="cfa",
                repo=repo,
                skip_pdf=True,
                interval=0,
                page_fetcher=fake_page_fetcher,
            )
            self.assertTrue(out.ok, out.error)

            # 抽出テキストをローカルに配置して texts.csv を作成
            roster_url = "https://www.cfa.go.jp/assets/contents/roster.pdf"
            minutes_url = "https://www.cfa.go.jp/assets/contents/mtg01_minutes.pdf"
            roster_txt = ag.data_dir / "text" / "assets" / "contents" / "roster.txt"
            minutes_txt = ag.data_dir / "text" / "assets" / "contents" / "mtg01_minutes.txt"
            roster_txt.parent.mkdir(parents=True, exist_ok=True)
            roster_txt.write_text(
                "テスト会議 構成員名簿\n令和8年4月1日現在\n"
                "◎山田 太郎 〇〇大学教授\n"
                "鈴木 花子 △△研究所研究員\n"
                "佐藤 次郎 □□市長\n",
                encoding="utf-8",
            )
            minutes_txt.write_text(
                "第1回 テスト会議 議事要旨\n"
                "日時：令和8年5月1日（金）10:00～12:00\n"
                "場所：こども家庭庁会議室\n"
                "出席者：山田 太郎（座長）、鈴木 花子、佐藤 次郎\n"
                "議題：基本方針について\n",
                encoding="utf-8",
            )
            import extract_text
            extract_text.write_csv(ag.data_dir / "texts.csv", [
                {
                    "url": roster_url,
                    "text": roster_txt.relative_to(repo).as_posix(),
                    "pages": "1",
                    "chars": "60",
                    "sha256": "aaa111",
                    "note": "",
                    "release_url": "",
                },
                {
                    "url": minutes_url,
                    "text": minutes_txt.relative_to(repo).as_posix(),
                    "pages": "1",
                    "chars": "90",
                    "sha256": "bbb222",
                    "note": "",
                    "release_url": "",
                },
            ])

            # 1) ローカル .txt ありで build → parsed_texts.json が生成される
            b1 = build_db.build(
                councils_path=ag.councils_csv,
                manifest=ag.pages_manifest,
                root=ag.raw_pages_dir,
                texts=ag.data_dir / "texts.csv",
                repo=repo,
                base_url=ag.base_url,
                hosts=tuple(ag.hosts),
            )
            self.assertGreaterEqual(len(b1.members), 3)
            self.assertEqual(len(b1.attendance), 3)
            self.assertEqual(b1.meetings[0]["date"], "2026-05-01")
            self.assertTrue((ag.data_dir / "parsed_texts.json").exists())

            # 2) upload_texts_bundle で Release 退避＆ローカル .txt 削除
            uploaded = []

            def fake_runner(cmd, **kwargs):
                if cmd[:3] == ["gh", "release", "view"]:
                    return subprocess.CompletedProcess(cmd, 0)
                if cmd[:3] == ["gh", "release", "upload"]:
                    with tarfile.open(cmd[4], "r:gz") as tf:
                        uploaded.extend(tf.getnames())
                return subprocess.CompletedProcess(cmd, 0)

            arch = archive_pdfs.upload_texts_bundle(
                texts_csv=ag.data_dir / "texts.csv",
                text_dir=ag.data_dir / "text",
                repo=repo,
                tag="cfa-texts-2026-backfill",
                agency_name=ag.name,
                repo_slug="tsuji-y/shingikai",
                chunk_id="c001",
                cleanup_local=True,
                runner=fake_runner,
            )
            self.assertEqual(len(arch), 2)
            self.assertEqual(sorted(uploaded), ["assets/contents/mtg01_minutes.txt", "assets/contents/roster.txt"])
            self.assertFalse(roster_txt.exists())
            self.assertFalse(minutes_txt.exists())

            # 3) ローカル .txt 削除後も parsed_texts.json から完全同一の表が再構築されること
            b2 = build_db.build(
                councils_path=ag.councils_csv,
                manifest=ag.pages_manifest,
                root=ag.raw_pages_dir,
                texts=ag.data_dir / "texts.csv",
                repo=repo,
                base_url=ag.base_url,
                hosts=tuple(ag.hosts),
            )
            self.assertEqual(b2.members, b1.members)
            self.assertEqual(b2.attendance, b1.attendance)
            self.assertEqual(b2.meetings, b1.meetings)

            # 4) catalog.csv / catalog.jsonl / data/all/index.json に text_release_url が反映されること
            build_db.write_csv(ag.data_dir / "meetings.csv", build_db.MEETING_FIELDS, b2.meetings)
            build_db.write_csv(ag.data_dir / "documents.csv", build_db.DOCUMENT_FIELDS, b2.documents)
            cat_rows = build_index.build_agency_catalog(ag, write=True)
            by_url = {r["url"]: r for r in cat_rows}
            self.assertIn("cfa-texts-2026-backfill", by_url[minutes_url]["text_release_url"])
            self.assertIn("cfa-texts-2026-backfill", by_url[roster_url]["text_release_url"])
            idx = build_index.build_all_index(repo=repo, write=True)
            self.assertEqual(idx["totals"]["texts_archived"], 2)

    def test_request_url_encodes_spaces_and_fetch_catches_invalid_url(self):
        import http.client

        raw = "https://www.cas.go.jp/jp/seisakukaigi/sosikihanzai/kettei/20170127_best practice.pdf"
        enc = fetch_files.request_url(raw)
        self.assertEqual(
            enc,
            "https://www.cas.go.jp/jp/seisakukaigi/sosikihanzai/kettei/20170127_best%20practice.pdf",
        )
        # すでに %20 エンコード済みの URL を二重エンコードしないこと
        self.assertEqual(fetch_files.request_url(enc), enc)

        seen_urls = []

        def fake_opener(req, timeout=0):
            seen_urls.append(req.full_url)
            raise http.client.InvalidURL("bad control char")

        row, body = fetch_files.fetch(raw, retry_backoff=(0.01, 0.01), opener=fake_opener)
        self.assertEqual(seen_urls, [enc])  # InvalidURL はリトライせず 1 回で終了
        self.assertEqual(row["url"], raw)   # 台帳キーは元 URL を維持
        self.assertEqual(row["status"], "error")
        self.assertIsNone(body)

    def test_overnight_manifest_and_cache_union_merge(self):
        import run_overnight_backfill

        ours_csv = (
            "url,status,content_type,last_modified,fetched_at,bytes,sha256,note,release_url\n"
            "https://example.go.jp/a.pdf,200,application/pdf,,2026-11-27T04:00:00+00:00,100,aaa,,https://gh/a.pdf\n"
            "https://example.go.jp/b.pdf,200,application/pdf,,2026-11-27T04:00:00+00:00,100,bbb,,\n"
        )
        theirs_csv = (
            "url,status,content_type,last_modified,fetched_at,bytes,sha256,note,release_url\n"
            "https://example.go.jp/b.pdf,200,application/pdf,,2026-11-27T05:00:00+00:00,100,bbb,,https://gh/b.tar.gz#b.pdf\n"
            "https://example.go.jp/c.pdf,200,application/pdf,,2026-11-27T05:00:00+00:00,200,ccc,,https://gh/c.tar.gz#c.pdf\n"
        )
        merged = run_overnight_backfill.merge_manifest_csv(
            ours_csv, theirs_csv, key_cols=("url",), fieldnames=fetch_files.FIELDS,
        )
        rows = {r["url"]: r for r in csv.DictReader(merged.splitlines())}
        self.assertEqual(set(rows), {
            "https://example.go.jp/a.pdf",
            "https://example.go.jp/b.pdf",
            "https://example.go.jp/c.pdf",
        })
        self.assertEqual(rows["https://example.go.jp/a.pdf"]["release_url"], "https://gh/a.pdf")
        self.assertEqual(rows["https://example.go.jp/b.pdf"]["release_url"], "https://gh/b.tar.gz#b.pdf")
        self.assertEqual(rows["https://example.go.jp/c.pdf"]["release_url"], "https://gh/c.tar.gz#c.pdf")

        cache_m = json.loads(run_overnight_backfill.merge_parsed_cache(
            json.dumps({"minutes": {"k1": {"sha256": "1"}}, "rosters": {}}),
            json.dumps({"minutes": {"k2": {"sha256": "2"}}, "rosters": {"r1": {"sha256": "3"}}}),
        ))
        self.assertEqual(set(cache_m["minutes"]), {"k1", "k2"})
        self.assertEqual(set(cache_m["rosters"]), {"r1"})

    def test_archive_gh_upload_retries_transient_failure(self):
        attempts = []
        sleeps = []

        def flaky_runner(cmd, **kwargs):
            attempts.append(cmd[:4])
            if len(attempts) < 3:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0)

        res = archive_pdfs._run_gh_with_retry(
            ["gh", "release", "upload", "cas-pdfs-2026-backfill", "x.tar.gz"],
            runner=flaky_runner,
            retry_backoff=(1.0, 2.0, 4.0),
            sleep_fn=sleeps.append,
        )
        self.assertEqual(res.returncode, 0)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_excluded_urls_skipped_in_subpages_and_files(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            cfg = json.loads((REPO / "agencies.json").read_text(encoding="utf-8"))
            self.assertIn(
                "https://www.maff.go.jp/j/council/seisaku/eisei/bukai_64/bukai_64.html",
                cfg["maff"]["excluded_urls"],
            )
            (repo / "agencies.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
            ag = agencies.get_agency("maff", repo=repo)
            ag.data_dir.mkdir(parents=True, exist_ok=True)
            (ag.data_dir / "documents.csv").write_text(
                "council_id,meeting_id,section,title,doc_type,url,host,format,text,pages,chars,source,doc_key\n"
                "eisei,eisei#64,,部会64,資料,https://www.maff.go.jp/j/council/seisaku/eisei/bukai_64/bukai_64.html,www.maff.go.jp,html,,,,/j/council/index.html,k1\n"
                "eisei,eisei#65,,部会65,資料,https://www.maff.go.jp/j/council/seisaku/eisei/bukai_65/bukai_65.html,www.maff.go.jp,html,,,,/j/council/index.html,k2\n"
                "eisei,eisei#64,,PDF1,資料,https://www.maff.go.jp/j/council/a.pdf,www.maff.go.jp,pdf,,,,/j/council/index.html,k3\n",
                encoding="utf-8",
            )
            sub_targets = fetch_subpages.collect_targets(ag, include_discovered=False)
            self.assertEqual(
                [t.url for t in sub_targets],
                ["https://www.maff.go.jp/j/council/seisaku/eisei/bukai_65/bukai_65.html"],
            )
            pdf_targets = fetch_files.targets(
                ag.data_dir / "documents.csv",
                hosts=list(ag.hosts),
                excluded_urls={"https://www.maff.go.jp/j/council/a.pdf"},
            )
            self.assertEqual(pdf_targets, [])


if __name__ == "__main__":
    unittest.main()
