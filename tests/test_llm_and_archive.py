import csv
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mod"))
import archive_pdfs  # noqa: E402
import build_db  # noqa: E402
import fetch_files  # noqa: E402
import ingest_pages  # noqa: E402
from mod.llm import config as llm_config  # noqa: E402
from mod.llm import evaluate as llm_eval  # noqa: E402
from mod.llm import tasks as llm_tasks  # noqa: E402
from mod.llm import validate as llm_validate  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class ArchivePdfsTest(unittest.TestCase):
    def test_asset_name_avoids_council_and_version_collisions(self):
        u1 = "https://www.mod.go.jp/j/policy/agenda/meeting/rinri/pdf/siryo01.pdf"
        u2 = "https://www.mod.go.jp/j/policy/agenda/meeting/jinji/pdf/siryo01.pdf"
        n1a = archive_pdfs.asset_name(u1, "aaaaaaaa11111111")
        n1b = archive_pdfs.asset_name(u1, "bbbbbbbb22222222")
        n2 = archive_pdfs.asset_name(u2, "aaaaaaaa11111111")
        self.assertEqual(len({n1a, n1b, n2}), 3)
        self.assertTrue(n1a.startswith("rinri__pdf__siryo01__aaaaaaaa"))

    def test_upload_incremental_and_replace_detection(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            files_dir = tmp / "files"
            manifest = tmp / "files.csv"
            url = "https://www.mod.go.jp/j/policy/agenda/meeting/rinri/pdf/giji_105.pdf"
            p = fetch_files.local_path(url, files_dir)
            p.parent.mkdir(parents=True, exist_ok=True)
            body = b"%PDF-1.7 sample"
            sha1 = hashlib.sha256(body).hexdigest()
            p.write_bytes(body)

            fetch_files.write_manifest({url: {
                "url": url, "status": "200", "content_type": "application/pdf",
                "last_modified": "2026-11-20T00:00:00+00:00", "fetched_at": "2026-11-21T00:00:00+00:00",
                "bytes": str(len(body)), "sha256": sha1, "note": "", "release_url": "",
            }}, manifest)

            cmds = []

            def fake_runner(cmd, **kwargs):
                cmds.append(cmd)
                # release view は初回 1（未作成）、2 回目以降は 0
                if cmd[:3] == ["gh", "release", "view"]:
                    rc = 1 if len([c for c in cmds if c[:3] == ["gh", "release", "view"]]) == 1 else 0
                    return subprocess.CompletedProcess(cmd, rc)
                return subprocess.CompletedProcess(cmd, 0)

            res1 = archive_pdfs.upload_incremental(
                manifest=manifest, files_dir=files_dir, tag="pdfs-2026", repo_slug="tsuji-y/shingikai",
                runner=fake_runner,
            )
            self.assertEqual(len(res1), 1)
            rows = fetch_files.read_manifest(manifest)
            self.assertIn("releases/download/pdfs-2026/rinri__pdf__giji_105__", rows[url]["release_url"])

            # 同じ sha256 なら 2 回目はアップロード不要
            res2 = archive_pdfs.upload_incremental(
                manifest=manifest, files_dir=files_dir, tag="pdfs-2026", repo_slug="tsuji-y/shingikai",
                runner=fake_runner,
            )
            self.assertEqual(res2, [])

            # PDF が差し替わって sha256 が変われば再アップロード対象になる
            rows[url]["sha256"] = "ffffffff00000000"
            fetch_files.write_manifest(rows, manifest)
            self.assertTrue(archive_pdfs.needs_upload(rows[url], files_dir))

    def test_upload_baseline_bundle_creates_valid_tarball(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            files_dir = tmp / "files"
            manifest = tmp / "files.csv"
            url = "https://www.mod.go.jp/j/policy/agenda/meeting/rinri/pdf/a.pdf"
            p = fetch_files.local_path(url, files_dir)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"%PDF-1.4 hello")
            fetch_files.write_manifest({url: {
                "url": url, "status": "200", "content_type": "application/pdf",
                "last_modified": "", "fetched_at": "", "bytes": "14", "sha256": "12345678abcd",
                "note": "", "release_url": "",
            }}, manifest)

            members_seen = []

            def fake_runner(cmd, **kwargs):
                if cmd[:3] == ["gh", "release", "view"]:
                    return subprocess.CompletedProcess(cmd, 0)
                if cmd[:3] == ["gh", "release", "upload"]:
                    with tarfile.open(cmd[4], "r:gz") as tf:
                        members_seen.extend(tf.getnames())
                return subprocess.CompletedProcess(cmd, 0)

            res = archive_pdfs.upload_baseline_bundle(
                manifest=manifest, files_dir=files_dir, tag="pdfs-2026-baseline",
                repo_slug="tsuji-y/shingikai", runner=fake_runner,
            )
            self.assertEqual(len(res), 1)
            self.assertEqual(members_seen, ["j/policy/agenda/meeting/rinri/pdf/a.pdf"])
            rows = fetch_files.read_manifest(manifest)
            self.assertIn("#j/policy/agenda/meeting/rinri/pdf/a.pdf", rows[url]["release_url"])


class LLMValidationTest(unittest.TestCase):
    SOURCE = (
        "第5回 テスト審議会 議事要旨\n"
        "日時：令和8年5月10日（日）14:00～16:00\n"
        "場所：防衛省A棟第1会議室\n"
        "出席者：山田 太郎（会長）、鈴木 花子、佐藤 一郎\n"
    )

    def test_valid_extraction_passes(self):
        payload = {
            "date": "2026-05-10",
            "date_raw": "令和8年5月10日（日）",
            "time": "14:00-16:00",
            "place": "防衛省A棟第1会議室",
            "agenda": "",
            "attendees": [
                {"group": "委員", "name_raw": "山田 太郎", "surname": "山田", "role": "会長"},
                {"group": "委員", "name_raw": "鈴木 花子", "surname": "鈴木", "role": ""},
            ],
        }
        res = llm_validate.validate_minutes_extraction(payload, self.SOURCE)
        self.assertTrue(res.ok, res.errors)
        self.assertEqual(len(res.cleaned["attendees"]), 2)

    def test_hallucinated_name_is_rejected(self):
        payload = {
            "date": "2026-05-10",
            "date_raw": "令和8年5月10日（日）",
            "time": "14:00-16:00",
            "place": "防衛省A棟第1会議室",
            "agenda": "",
            "attendees": [
                {"group": "委員", "name_raw": "山田 太郎", "surname": "山田", "role": "会長"},
                {"group": "委員", "name_raw": "架空 人物", "surname": "架空", "role": ""},
            ],
        }
        res = llm_validate.validate_minutes_extraction(payload, self.SOURCE)
        self.assertFalse(res.ok)
        self.assertTrue(any("架空 人物" in e for e in res.errors))

    def test_invalid_date_and_weekday_mismatch_are_rejected(self):
        bad_date = llm_validate.validate_minutes_extraction({"date": "2026-02-30", "attendees": []}, self.SOURCE)
        self.assertFalse(bad_date.ok)

        bad_weekday = llm_validate.validate_minutes_extraction(
            {"date": "2026-05-10", "date_raw": "令和8年5月10日（月）", "attendees": []},
            self.SOURCE.replace("（日）", "（月）"),
        )
        self.assertFalse(bad_weekday.ok)
        self.assertTrue(any("曜日不一致" in e for e in bad_weekday.errors))


class LLMTasksAndBuildIntegrationTest(unittest.TestCase):
    def _setup_mini_env(self, tmp: Path):
        data_dir = tmp / "data" / "mod"
        raw_pages = tmp / "raw" / "mod" / "pages"
        raw_files = tmp / "raw" / "mod" / "files"
        text_dir = data_dir / "text" / "j" / "policy" / "agenda" / "meeting" / "x" / "pdf"
        text_dir.mkdir(parents=True, exist_ok=True)

        # 既存パーサでは出席者を取れない崩れたレイアウトの議事要旨
        txt1 = text_dir / "giji_01.txt"
        txt1.write_text(
            "第1回テスト審議会 議事要旨\n"
            "令和8年5月10日（日）開催 於：防衛省会議室\n"
            "出席 田中 健一 委員 / 高橋 美咲 委員\n"
            "議事として予算案を審議した。\n",
            encoding="utf-8",
        )
        txt2 = text_dir / "giji_02.txt"
        txt2.write_text(
            "第2回テスト審議会 議事要旨\n"
            "令和8年6月14日（日）開催 於：防衛省会議室\n"
            "出席 伊藤 誠 委員\n"
            "議事として報告書案を審議した。\n",
            encoding="utf-8",
        )

        u1 = "https://www.mod.go.jp/j/policy/agenda/meeting/x/pdf/giji_01.pdf"
        u2 = "https://www.mod.go.jp/j/policy/agenda/meeting/x/pdf/giji_02.pdf"
        councils_csv = data_dir / "councils.csv"
        build_db.write_csv(councils_csv, build_db.COUNCIL_FIELDS, [{
            "council_id": "x", "section": "審議会", "name": "テスト審議会",
            "link": "/j/policy/agenda/meeting/x/index.html", "note": "", "first_seen": "2026-11-19T00:00:00+00:00",
        }])
        html = (
            "<html><body><h1>テスト審議会</h1>"
            "<table><tr><th>開催回</th><th>年月日</th><th>資料</th></tr>"
            f"<tr><td>第1回</td><td>令和8年5月10日</td><td><a href='{u1}'>議事要旨</a></td></tr>"
            f"<tr><td>第2回</td><td>令和8年6月14日</td><td><a href='{u2}'>議事要旨</a></td></tr>"
            "</table></body></html>"
        ).encode("utf-8")
        page_url = "/j/policy/agenda/meeting/x/index.html"
        p_file = ingest_pages.local_path(page_url, raw_pages)
        p_file.parent.mkdir(parents=True, exist_ok=True)
        p_file.write_bytes(html)
        pages_manifest = tmp / "raw" / "mod" / "pages.csv"
        ingest_pages.write_manifest({page_url: {
            "url": page_url, "status": "200", "last_modified": "", "fetched_at": "",
            "bytes": str(len(html)), "sha256": hashlib.sha256(html).hexdigest(),
            "content_sha256": ingest_pages.content_hash(html), "missing_count": "",
        }}, pages_manifest)

        texts_csv = data_dir / "texts.csv"
        build_db.write_csv(texts_csv, ["url", "text", "pages", "chars", "sha256", "note"], [
            {"url": u1, "text": txt1.relative_to(tmp).as_posix(), "pages": "1", "chars": "70", "sha256": "s1", "note": ""},
            {"url": u2, "text": txt2.relative_to(tmp).as_posix(), "pages": "1", "chars": "70", "sha256": "s2", "note": ""},
        ])
        b0 = build_db.build(councils_csv, pages_manifest, raw_pages, texts_csv, repo=tmp)
        build_db.write_csv(data_dir / "meetings.csv", build_db.MEETING_FIELDS, b0.meetings)
        build_db.write_csv(data_dir / "documents.csv", build_db.DOCUMENT_FIELDS, b0.documents)
        build_db.write_csv(data_dir / "attendance.csv", build_db.ATTENDANCE_FIELDS, b0.attendance)
        return data_dir, raw_pages, raw_files, pages_manifest, councils_csv, texts_csv, u1, u2

    def test_rejected_output_never_enters_tables_while_accepted_does(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            data_dir, raw_pages, _, pages_manifest, councils_csv, texts_csv, u1, u2 = self._setup_mini_env(tmp)
            ext_csv = data_dir / "llm_extractions.csv"

            class FakeClient:
                model = "gemini-2.5-flash"

                def generate_json(self, prompt, content):
                    if "第1回" in content:
                        # 検証に通る正しい抽出
                        return {
                            "date": "2026-05-10", "date_raw": "令和8年5月10日（日）",
                            "time": "", "place": "防衛省会議室", "agenda": "予算案",
                            "attendees": [
                                {"group": "委員", "name_raw": "田中 健一", "surname": "田中", "role": "委員"},
                                {"group": "委員", "name_raw": "高橋 美咲", "surname": "高橋", "role": "委員"},
                            ],
                        }
                    # 第2回は原文にない架空人物を混ぜる → 検証で却下されるべき
                    return {
                        "date": "2026-06-14", "date_raw": "令和8年6月14日（日）",
                        "time": "", "place": "防衛省会議室", "agenda": "",
                        "attendees": [
                            {"group": "委員", "name_raw": "存在しない 太郎", "surname": "存在しない", "role": ""},
                        ],
                    }

            budget = llm_tasks.CallBudget(limit=10)
            st = llm_tasks.extract_missing_attendance(
                FakeClient(), budget, repo=tmp, data_dir=data_dir,
                extractions_csv=ext_csv, backfill=True,
            )
            self.assertEqual((st.processed, st.accepted, st.rejected), (2, 1, 1))

            # build_db 再構築: 第1回(accepted)は出席者表に入り、第2回(rejected)は入らない
            b = build_db.build(councils_csv, pages_manifest, raw_pages, texts_csv, repo=tmp)
            att_mids = {a["meeting_id"] for a in b.attendance}
            self.assertIn("x#1", att_mids)
            self.assertNotIn("x#2", att_mids)
            self.assertTrue(all(a["name_raw"] != "存在しない 太郎" for a in b.attendance))
            m1 = next(m for m in b.meetings if m["meeting_id"] == "x#1")
            self.assertIn("llm", m1["found_by"].split("+"))
            # person_id は既存規則（persons.link）で決まっている
            self.assertEqual({a["person_id"] for a in b.attendance if a["meeting_id"] == "x#1"}, {"田中健一", "高橋美咲"})

            # 2 回目実行は SHA-256 キャッシュで 0 回呼び出し
            budget2 = llm_tasks.CallBudget(limit=10)
            st2 = llm_tasks.extract_missing_attendance(
                FakeClient(), budget2, repo=tmp, data_dir=data_dir,
                extractions_csv=ext_csv, backfill=True,
            )
            self.assertEqual(budget2.used, 0)
            self.assertEqual(st2.processed, 0)

    def test_summarize_dedup_budget_and_explicit_backfill_guard(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            data_dir, _, _, _, _, _, u1, u2 = self._setup_mini_env(tmp)
            sum_csv = data_dir / "summaries.csv"
            ext_csv = data_dir / "llm_extractions.csv"
            calls = []

            class FakeClient:
                model = "gemini-2.5-flash"

                def generate_json(self, prompt, content):
                    calls.append(content[:30])
                    return {"summary": "予算案および報告書案について審議した。", "key_points": ["論点1", "論点2"]}

            # backfill=False かつ target_urls=None なら既存文書の自動要約は走らない
            st0 = llm_tasks.summarize_documents(
                FakeClient(), llm_tasks.CallBudget(limit=10), repo=tmp, data_dir=data_dir,
                summaries_csv=sum_csv, extractions_csv=ext_csv, backfill=False,
            )
            self.assertEqual(st0.processed, 0)
            self.assertEqual(len(calls), 0)

            # --max-llm-calls 1 で 1 件だけ要約
            st1 = llm_tasks.summarize_documents(
                FakeClient(), llm_tasks.CallBudget(limit=1), repo=tmp, data_dir=data_dir,
                summaries_csv=sum_csv, extractions_csv=ext_csv, backfill=True,
            )
            self.assertEqual((st1.processed, st1.accepted), (1, 1))
            self.assertEqual(len(calls), 1)

            # 続きを実行すると残り 1 件だけ要約（既処理 1 件は SHA-256 でスキップ）
            st2 = llm_tasks.summarize_documents(
                FakeClient(), llm_tasks.CallBudget(limit=5), repo=tmp, data_dir=data_dir,
                summaries_csv=sum_csv, extractions_csv=ext_csv, backfill=True,
            )
            self.assertEqual((st2.processed, st2.accepted, st2.skipped), (1, 1, 1))
            self.assertEqual(len(calls), 2)

            # メタ列（model, prompt_version, generated_at, input_sha256）が全行にある
            with sum_csv.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            for r in rows:
                self.assertEqual(r["model"], "gemini-2.5-flash")
                self.assertEqual(r["prompt_version"], "summarize-v1")
                self.assertTrue(r["generated_at"])
                self.assertEqual(len(r["input_sha256"]), 64)

    def test_evaluate_runs_on_real_repo_sample_with_mock_client(self):
        class FakeEvalClient:
            model = "gemini-2.5-flash"

            def generate_json(self, prompt, content):
                return {"date": "", "date_raw": "", "time": "", "place": "", "agenda": "", "attendees": []}

        items = llm_eval.evaluate(FakeEvalClient(), n=30, seed=20261119)
        self.assertEqual(len(items), 30)
        report = llm_eval.format_report(items, model="gemini-2.5-flash", seed=20261119)
        self.assertIn("Phase 3 Gemini 抽出評価レポート", report)
        self.assertIn("自動検証通過", report)


if __name__ == "__main__":
    unittest.main()
