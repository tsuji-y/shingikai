# 防衛省（`mod`）固有の仕様・検証記録

本ドキュメントは、全 16 省庁共通の [`README.md`](../../README.md) から防衛省（`www.mod.go.jp`）固有の収録範囲・アクセス実測・抜き取り検証の詳細を整理したものである。

## 1. 収録範囲（2026-11-21 時点）

防衛省の審議会等（[一覧ページ](https://www.mod.go.jp/j/policy/agenda/meeting/index.html)）およびサイト内の全 123 ページ、防衛省サイト上の PDF 421 件（3,778 ページ、約 225 万字）を収録。

| 表 | 件数 | 内容 |
|---|---:|---|
| `data/mod/councils.csv` | 67 | 会議体の一覧（うち 1 件 `cas-jieikan` は内閣官房サイト `www.cas.go.jp` 上の会議体） |
| `data/mod/council_profiles.csv` | 164 | 会議体の概要（主管省庁・根拠法令・設置年月日・所掌事務など） |
| `data/mod/members.csv` | 253 | 委員名簿（33 会議体） |
| `data/mod/meetings.csv` | 933 | 各回の会議（53 会議体）。開催日が分かるのは 667 件 |
| `data/mod/attendance.csv` | 2,307 | 各回の出席者、1 人 1 行（16 会議体の 164 会議） |
| `data/mod/persons.csv` | 257 | 委員・有識者の人物表 |
| `data/mod/documents.csv` | 2,053 | 資料・議事録などへのリンク（うち 1,602 件が会議に結び付く） |
| `data/mod/texts.csv`、`data/mod/text/` | 421 | PDF から抽出した本文 |
| `data/mod/rss_items.csv` | 425+ | RSS（`update.xml` / `news.xml`）の更新ログ |

- **PDF 原本アーカイブ**: ベースライン 421 件の PDF 原本（318.6 MB）は GitHub Release [`pdfs-2026-baseline`](https://github.com/tsuji-y/shingikai/releases/tag/pdfs-2026-baseline) に一括保管し、`raw/mod/files.csv` の `release_url` 列に記録済み。
- **未収録の範囲**: 国立国会図書館 WARP（`warp.da.ndl.go.jp`）にのみ存在する過去資料（PDF 1,297 件、HTML 315 件）の本文、および画像のみの PDF（4 件）の pdfminer 抽出本文（Gemini OCR で補完対象）。

## 2. 読み取りの限界と注意点

- 933 会議のうち、回次と開催日の両方が分かるのは 451 件、回次のみが 266 件、開催日のみが 216 件。
- 同じ回次で開催日が違う行は別会議（`meeting_id` の末尾に `@開催日`）として残す（防衛省改革検討委員会 `kaikaku` の回次振り直しや、`kyumei` 第 6 回の重複掲載など）。
- 資料に書かれた曜日が日付と合わないものが 4 件ある（`rinri#93`、`jinji/再就職等監視分科会#74`、`jinji/職員処遇問題部会#41`、`keiyaku_seido#39`）。`build_db.py` の実行時に警告として表示される。

## 3. 抜き取り検証（2026-11-19 実施）

出席者を読み取った 159 会議から、会議体ごとに最低 1 件を含む 30 会議を無作為抽出し、別エージェントによるブラインド照合を 2 回実施した。

| 回 | 対象 | 誤りのあった会議 | 内容 |
|---|---|---|---|
| 1 回目 | 30 会議 | 11 | 場所の欠落 5、出席者の区切り・区分・姓の誤り 6。開催日・時刻の誤りは 0 |
| 2 回目（修正後） | 同じ 30 会議＋別抽出 30 会議（計 53 会議） | 3 | 1 回目の誤りはすべて解消。新たに出席者 2 人の抜け 1 会議、「他」の消し残り 2 会議（すべて修正済み） |
