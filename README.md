# shingikai

日本の主要 16 省庁（防衛省・デジタル庁・こども家庭庁・内閣官房・内閣府・農林水産省・財務省・法務省・環境省・金融庁・消費者庁・文部科学省・国土交通省・警察庁・個人情報保護委員会・厚生労働省）の審議会・有識者会議・検討会等について、会議体・各回のメタ情報・参加者・資料・議事録を統一スキーマで収録・日次追跡するデータベース。

## 現状（16 省庁収録状況）

2026-11-22 時点で、クラウド環境（GitHub Actions 標準ランナー）から安定取得可能な **全 16 省庁**（区分 A 14 省庁・区分 B 2 省庁）の会議体トップページを網羅収録し、計 **1,635 会議体・15,891 会議・41,659 資料・委員名簿 698 行・人物 683 人（うち複数省庁兼任 16 人）**を収録している（最新の省庁別集計は `data/all/agencies.csv`、省庁横断の委員兼任表は `data/all/persons_cross.csv`、防衛省固有の詳細記録は [`docs/agencies/mod.md`](docs/agencies/mod.md) を参照）。

| 省庁 ID | 省庁名 | 区分 | 会議体 (`councils`) | 会議 (`meetings`) | 資料 (`documents`) | 委員 (`members`) | 人物 (`persons`) | 保存 HTML (`pages`) | 抽出 PDF (`pdfs`) |
|---|---|:---:|---:|---:|---:|---:|---:|---:|---:|
| `mod` | 防衛省 | A | 67 | 933 | 2,053 | 253 | 257 | 123 | 421 |
| `digital` | デジタル庁 | A | 69 | 390 | 769 | 67 | 48 | 100 | 17 |
| `cfa` | こども家庭庁 | A | 92 | 627 | 843 | 132 | 113 | 95 | 14 |
| `cas` | 内閣官房 | A | 283 | 2,514 | 7,696 | 0 | 22 | 222 | 23 |
| `cao` | 内閣府 | A | 104 | 376 | 1,031 | 7 | 9 | 95 | 18 |
| `maff` | 農林水産省 | A | 55 | 1,110 | 3,070 | 0 | 25 | 46 | 8 |
| `mof` | 財務省 | A | 48 | 302 | 946 | 63 | 47 | 95 | 17 |
| `moj` | 法務省 | A | 25 | 238 | 1,678 | 11 | 37 | 56 | 18 |
| `env` | 環境省 | A | 138 | 2,631 | 4,570 | 72 | 51 | 139 | 13 |
| `fsa` | 金融庁 | A | 88 | 54 | 460 | 54 | 53 | 95 | 18 |
| `caa` | 消費者庁 | A | 21 | 95 | 521 | 19 | 19 | 52 | 18 |
| `mext` | 文部科学省 | A | 18 | 62 | 330 | 8 | 8 | 95 | 3 |
| `mlit` | 国土交通省 | A | 293 | 26 | 1,297 | 0 | 0 | 294 | 18 |
| `npa` | 警察庁 | A | 2 | 27 | 808 | 0 | 0 | 2 | 8 |
| `ppc` | 個人情報保護委員会 | B | 3 | 12 | 16 | 12 | 12 | 4 | 1 |
| `mhlw` | 厚生労働省 | B | 329 | 6,494 | 15,571 | 0 | 0 | 330 | 18 |
| **合計** | **16 省庁** | — | **1,635** | **15,891** | **41,659** | **698** | **683** | **1,843** | **633** |

- **区分 A（14 省庁）**: `Last-Modified` / `If-Modified-Since` による条件付き取得（`304 Not Modified`）と `content_sha256` の併用で追跡。
- **区分 B（2 省庁: `ppc`, `mhlw`）**: 動的 CMS のため `Last-Modified` を返さないが、`content_sha256`（本文ハッシュ比較）により同一パイプラインで変更検知・追跡。
- **区分 C（クラウド IP 遮断のため対象外とした 4 省庁）**: 経済産業省（403 CloudFront）、外務省（403 Akamai）、公正取引委員会（403 Akamai）、総務省（502 クラウド IP 拒否）。詳細と拡張工程表は [`docs/multi_ministry_plan.md`](docs/multi_ministry_plan.md) を参照。

### ディレクトリ構成と主要ファイル

省庁ごとのデータは `data/<agency>/` と `raw/<agency>/` に同じ表構成で格納し、省庁横断の集計表を `data/all/` に出力する。

| パス | 内容 |
|---|---|
| `agencies.json` | 全 16 省庁の URL・プレフィックス・取得階層・RSS 等の設定定義 |
| `data/all/agencies.csv` | 全 16 省庁の収録件数・最終取得日時サマリ（PDF カバレッジ・下位 HTML・テキスト数含む） |
| `data/all/index.json` | 全 16 省庁の統一索引マニフェスト（各省庁の `catalog.csv` / `catalog.jsonl`・Release タグ・カバレッジ集計） |
| `data/all/persons_cross.csv` | 省庁横断の委員・有識者名寄せ表（複数省庁の審議会を兼任する有識者を一覧化） |
| `data/all/recent_events.csv` | 全省庁の直近更新イベント一覧 |
| `data/<agency>/catalog.csv`、`catalog.jsonl` | 会議体・会議・資料・本文テキスト・Release URL を 1 つに結合した省庁別統一検索索引 |
| `data/<agency>/councils.csv` | 会議体の一覧 |
| `data/<agency>/council_profiles.csv` | 会議体の概要（根拠法令・所掌事務・設置趣旨など） |
| `data/<agency>/members.csv` | 委員名簿（HTML の名簿表および名簿 PDF から抽出） |
| `data/<agency>/meetings.csv` | 各回の会議（回次・開催日・時刻・場所・議題・出席者） |
| `data/<agency>/attendance.csv` | 各回の出席者（1 人 1 行） |
| `data/<agency>/persons.csv` | 省庁内の委員・有識者の人物表 |
| `data/<agency>/documents.csv` | 資料・議事録・名簿等へのリンク（WARP 移動耐性用の `doc_key` 付き） |
| `data/<agency>/texts.csv`、`data/<agency>/parsed_texts.json` | PDF・下位 HTML から抽出した本文テキストの台帳（`release_url` 付き）と構造化解析キャッシュ。`data/mod/text/` は Git 管理、他 15 省庁の `.txt` 本体は GitHub Releases（`<agency>-texts-*`）へ圧縮保管 |
| `data/<agency>/events.csv` | 表の差分から機械的に記録した更新イベント（追記のみ） |
| `data/<agency>/members_history.csv` | 委員名簿が変わる前のスナップショット（時点付き） |
| `raw/<agency>/pages.csv`、`raw/<agency>/pages/` | 取得した HTML の台帳（`content_sha256` 含む）と生バイト列 |
| `raw/<agency>/files.csv` | 取得した PDF の台帳（SHA-256 と GitHub Releases の `release_url` を記録。PDF 本体は Git 外） |
| `raw/<agency>/subpages.csv`、`subpage_links.csv` | 取得した下位 HTML の台帳（GitHub Releases の `release_url` 付き）と下位 HTML 内で発見した PDF リンク一覧（生 HTML 本体は Git 外） |
| `mod/agencies.py` | `agencies.json` の読み込みと汎用会議体一覧抽出 |
| `mod/cross_agency.py` | 全省庁の表から `data/all/` の横断表および `index.json` を生成する |
| `mod/build_index.py` | 省庁別の統一索引（`catalog.csv` / `catalog.jsonl`）および `data/all/index.json` を生成する |
| `mod/check_access.py` | HTML（通常・条件付き）・PDF・RSS の疎通を確認する（`--agency <id>` 対応） |
| `mod/check_updates.py` | 条件付き取得と `content_sha256` で変更ページ・新規ページ・消失を検知して保存する（`--agency <id>` 対応） |
| `mod/events.py` | 表の差分から `events.csv` を生成し、名簿履歴退避・回帰検査・RSS 取り込み漏れ検査を行う |
| `mod/update.py` | 更新チェック→表再構築→下位 HTML 取得→PDF 取得・抽出→回帰検査・イベント・索引更新を 1 コマンドで実行する（`--agency <id>` 対応） |
| `mod/fetch_subpages.py` | `documents.csv` 上の同一省庁下位 HTML を取得し、本文テキスト抽出（`data/<agency>/text/`）と内包 PDF リンク発見を行う |
| `mod/fetch_files.py` | `documents.csv` 上の PDF（議事録・配布資料すべて）を取得・差し替え照合する（`--agency <id>`、`--since-year`/`--until-year` 対応） |
| `mod/archive_pdfs.py` | 取得済み PDF・下位 HTML 原本および抽出テキスト（`mod` 以外）を GitHub Releases（月別 / 800MB シャード tar.gz）へ保管し、`release_url` を記録する |
| `mod/backfill.py` | 過去分の下位 HTML・議事録 PDF・配布資料 PDF をチャンク分割・中断再開可能で一括バックフィルする |
| `mod/extract_text.py` | PDF から本文を抽出する（`--agency <id>` 対応） |
| `mod/pages.py`、`mod/minutes.py`、`mod/persons.py`、`mod/build_db.py` | HTML・PDF 議事要旨・名簿 PDF の構造化と人物名寄せ（`--agency <id>` 対応） |
| `mod/llm/` | Gemini による補完（画像 PDF 文字起こし・出席者補完・資料要約・変更検討メモ・30 件評価） |
| `tests/` | `python3 -m unittest discover -s tests`（84 テスト） |

`mod/` ディレクトリ名は最初に構築した防衛省（Ministry of Defense）に由来し、全 16 省庁共通のパイプラインとして動作する（`--agency` 省略時の既定値は `mod`）。基本のコードは Python 標準ライブラリのみで、PDF の本文抽出（`extract_text.py`）に `requirements.txt`（pdfminer.six）、Gemini 処理（`mod/llm/`）に `requirements-llm.txt`（google-genai）を使う。

## アクセス制約と巡回ルール

すべての省庁サイトに対して以下の共通ルールを守る。
- 1 ドメインあたり直列・**2 秒以上**の間隔（`--interval 2.0`）をあけ、自己申告の `User-Agent`（`rss-poller/1.0` / `pdf-fetcher/1.0`）を送信する。
- `403`・`429` または `cf-mitigated` が返ったらリトライせず即座に停止する。ボット検証の突破・ステルス化・UA 偽装・URL 推測は一切行わない。
- `robots.txt` および国立国会図書館インターネット資料収集保存事業（WARP）の規約を遵守する。

### www.mod.go.jp の実測結果

2026-11-19 と 2026-11-21 に実測した結果。

| 対象 | 米国データセンター（11/19） | 国内 PC・Python 既定 UA（11/21） | 国内 PC / GitHub Actions・自前 UA `rss-poller/1.0`（11/21） |
|---|---|---|---|
| HTML（通常取得） | 403（`cf-mitigated: challenge`） | 403（`cf-mitigated: challenge`） | 200（本文は保存版と一致） |
| HTML（`If-Modified-Since`） | 未確認 | 未確認 | 304（本文なし） |
| PDF | 200 | 200 | 200（`Last-Modified` あり） |
| `/j/rss/update.xml`、`/j/rss/news.xml` | 200（自前 UA） | 403 | 200 |
| `robots.txt` | 200（`Disallow` は `/a/` と `/sp/j/` のみ） | 200 | 200 |

- HTML には Cloudflare が毎回異なる `cdn-cgi/challenge-platform` スクリプトを差し込むページがあるため、変更検知にはこの `<script>…</script>` を除いた `content_sha256` を使う。
- 各省庁サイトのコンテンツは「公共データ利用規約（第1.0版）」（PDL1.0）に準拠して利用できる。

### WARP

防衛省などのページは、古い回の資料や一覧を国立国会図書館のアーカイブ（WARP、`warp.da.ndl.go.jp`）へのリンクにしている。WARP の `robots.txt` は保存本体パス（`/20` で始まるパス等）を全ロボットに対して禁じているため、WARP 上の本体はスクリプトで取得しない。なお、取得済みの資料が後から WARP に移動しても本文・出席者が切れないよう、`documents.csv` に元のホスト＋パスを表す `doc_key` 列を持ち、`build_db.py` は `doc_key` で本文と結び付けている。

## ページの取得と定期更新

### 過去分の一括バックフィル（ローカル Mac 向け・チャンク再開対応）

議事録・配布資料 PDF・下位 HTML（本文テキスト＋内包 PDF）を 1 ドメイン 1 プロセス・2 秒間隔直列で安全にバックフィルする。原本は 800 MB 単位の `.tar.gz` シャードで GitHub Releases へ自動退避し、`--cleanup-local` でローカル原本を削除してディスク消費を抑える。

```bash
python3 mod/backfill.py --status-only                                                                  # 全 16 省庁の進捗一覧
python3 mod/backfill.py --wave wave1 --parallel 4 --chunk-html 100 --chunk-pdf 100 --archive --cleanup-local  # 中小型 9 機関を一括実行
python3 mod/backfill.py --wave wave2 --parallel 4 --chunk-html 100 --chunk-pdf 100 --archive --cleanup-local  # digital/mlit/maff/cas
python3 mod/backfill.py --agency env --since-year 2024 --until-year 2026 --archive --cleanup-local     # 環境省（年代分割）
python3 mod/backfill.py --agency mhlw --since-year 2024 --until-year 2026 --archive --cleanup-local    # 厚労省（年代分割）
```

### 日次更新（全 16 省庁 Matrix・配布資料 PDF 含む）

`.github/workflows/update.yml` は `plan` → `update-agency`（省庁別 Matrix、全 16 省庁並列・`fail-fast: false`）→ `finalize`（単一 commit・横断集計・通知 Issue・失敗時 `exit 1`）の 3 ジョブ構成で動作し、全 16 省庁で議事録・議事要旨・名簿に加え配布資料 PDF と下位 HTML も収集し、月別 Release（`<agency>-pdfs-YYYY-MM` / `<agency>-html-YYYY-MM` / `<agency>-texts-YYYY-MM`）へ原本・テキストを保管する（`mod` のテキストは引き続き Git に保持）。

```bash
python3 mod/update.py --agency mod --dry-run                 # 防衛省の変更確認のみ（--agency 省略時は mod）
python3 mod/update.py --agency digital --fetch-subpages      # デジタル庁の更新チェック→下位HTML→全PDF→回帰検査→索引更新
python3 mod/update.py --agency mod --check-pdf-updates       # 週次：取得済み PDF の差し替え照合も実施
python3 mod/build_index.py --agency all                      # 全 16 省庁の catalog.csv / catalog.jsonl と data/all/index.json を再生成
python3 mod/cross_agency.py                                  # 全 16 省庁の横断表（data/all/）と index.json を更新
```

- 403・429 または `cf-mitigated` が返った場合は即停止し、終了コード 1（未確認）で終了する。リトライしない。
- 回帰の検査（変更のないページから行が消えていないかの確認）に失敗した場合は、表を元に戻して終了コード 2 で終了する。
- 疎通のみ確認したい場合は `python3 mod/check_access.py --agency <id>` を使う。

### ブラウザ経由での代替取得（Python で 403 になる場合）

1. ブラウザで対象ページを開き、`mod/fetch_in_browser.js` を実行する。サイト内のパスを 1 件ずつ、2 秒強の間隔で取得し、403・429 が返ったら止まる。
2. 保存されたページ束を展開し、表を再構築する。

```bash
python3 mod/ingest_pages.py mod_pages.json
python3 mod/update.py --skip-fetch --changed-page /j/policy/agenda/meeting/<対象ページ>
```

## PDF の取得と本文の抽出

`documents.csv` に載っている各省庁サイト上の PDF を取得し、本文をテキストにする。

```bash
python3 mod/fetch_files.py --agency mod                   # 台帳に未収録の PDF だけ取得（raw/<agency>/files/ に保存）
python3 mod/fetch_files.py --agency digital --types 議事録,議事要旨,名簿
python3 mod/fetch_files.py --agency mod --refetch-missing # 台帳にあるが手元にない原本を取り直す場合
python3 mod/fetch_files.py --agency mod --check-replaced  # 取得済み PDF の差し替えを条件付き取得で照合
python3 mod/extract_text.py --agency mod                  # data/<agency>/text/ にテキストを保存
python3 mod/build_db.py --agency mod                      # 議事要旨・議事録・名簿 PDF から開催日時・出席者・委員名簿を補う
```

- `fetch_files.py` は 2 秒間隔で 1 件ずつ取得し、403・429 が返ったら止まる。既定では台帳（`raw/<agency>/files.csv`）に SHA-256 が記録されていれば取得済みとして飛ばす。PDF の本体（`raw/*/files/`）は Git に入れず、GitHub Releases に保管する。
- 新規・差し替え PDF は日次ワークフロー（`.github/workflows/update.yml`）から `mod/archive_pdfs.py --agency <id>` で年別 Release（`pdfs-YYYY` または `<agency>-pdfs-YYYY`）へ自動添付し、`raw/<agency>/files.csv` の `release_url` に記録する。防衛省のベースライン原本 421 件は Release `pdfs-2026-baseline` に一括保管済み。
- `extract_text.py` は pdfminer.six を使う（`pip install -r requirements.txt`）。

## Gemini による補完と要約（Phase 3）

既存の読み取り処理を置き換えずに、不足箇所（画像 PDF の文字起こし・出席者未抽出の議事要旨・資料の要約・日次の変更検討メモ）だけを Gemini（既定モデル: `gemini-2.5-flash`）で補う。API キーは環境変数（GitHub Actions では Secrets の `GEMINI_API_KEY`）から読み込む。

```bash
pip install -r requirements-llm.txt
python3 -m mod.llm.run --task evaluate --out checks/llm_eval_30.md             # 30 会議の精度評価
python3 -m mod.llm.run --task all --council rinri --backfill --max-llm-calls 5 # 1 会議体の少数確認
python3 -m mod.llm.run --task all --backfill --max-llm-calls 150               # 既存資料の要約・補完（バッチ実行可）
```

- GitHub Actions からは `.github/workflows/llm_backfill.yml`（手動実行）で 30 件評価や既存 421 件のさかのぼり要約・補完を実行できる。日次の `update.yml` でも、`GEMINI_API_KEY` が設定されていれば本体更新とは別のステップ・別の commit として新規分のみ自動処理する。
- 出席者・開催日の抽出結果は「氏名が空白を除いた原文にそのまま存在するか」「日付と曜日が整合するか」を自動検証し、検証を通った行（`validation_status=accepted`）だけを本体表へ反映する。通らない出力は `rejected` として `llm_extractions.csv` に記録して捨てる。
- **注意（機械生成）**: `data/mod/summaries.csv` の要約および通知 Issue に投稿される変更の検討メモは Gemini による機械生成であり、誤りや省略を含みうる。引用や分析には必ず元の資料（`url` / `text`）を確認すること。

## 会議体・会議・資料・委員の表

どの表も `council_id` で会議体と結び付く。`source` は、その行を読み取ったページのサイト内パス。

`councils.csv`（行の順序は一覧ページの掲載順）：

| 列 | 内容 |
|---|---|
| `council_id` | 会議体の ID。ページのディレクトリ名（`cho-shin`、`kondankai/atsugi` など）。内閣官房のサイトにある 1 件は `cas-jieikan` |
| `section` | 一覧ページ上の区分（関係閣僚会議／審議会・有識者会議等／委員会・検討会等／アーカイブ） |
| `name` | 会議体名。リンクの文言そのまま |
| `link` | サイト内の相対パス。外部サイトの場合は URL |
| `note` | 一覧ページに添えられた注記 |
| `first_seen` | 初めて取得した日時（UTC） |

`council_profiles.csv`：各会議体の「概要」の表を、`key`（主管省庁及び庶務担当部局、根拠法令、設置年月日、所掌事務など）と `value` の縦持ちにしたもの。

`members.csv`：

| 列 | 内容 |
|---|---|
| `body` | 部会・分科会のページに載っていた名簿の場合、その名称 |
| `group` | 名簿の直前の見出し（「委員」など） |
| `as_of`、`as_of_raw` | 名簿の時点（ISO 形式と元の表記） |
| `name`、`kana` | 氏名と、名簿に読みが添えられている場合はその読み |
| `mark`、`role` | 氏名に付いた記号（◎、○）と、凡例から読み取った役職（会長、座長代理など） |
| `title`、`note` | 職名等、備考 |
| `person_id` | 人物表（`persons.csv`）の ID |

名簿は掲載時点の 1 時点分だけで、過去の委員は入っていない。

`meetings.csv`：

| 列 | 内容 |
|---|---|
| `meeting_id` | `会議体[/部会]#回次`。回次が分からない会議は `@開催日` |
| `body` | 部会・分科会の名称。同じページの中で回次を別々に数えている場合は、その見出し（「総会」「部会」「平成２４年度」など） |
| `number`、`label` | 回次と、元の表記（「第201回定例」など） |
| `date`、`date_raw` | 開催日（ISO 形式）と元の表記 |
| `time` | 開催時刻（`14:00-16:00`）。議事要旨・議事録の冒頭から |
| `agenda` | 議題。複数あるときは ` / ` で区切る |
| `place`、`attendees` | 場所と、出席者の記載そのまま。議事要旨・議事録の冒頭から。1 人 1 行にしたものは `attendance.csv` |
| `page` | その回だけを載せたページがある場合、その URL |
| `n_documents` | 結び付いた資料の件数 |
| `found_by` | 読み取りの手掛かり。`table`（開催実績の表の行）、`heading`（「第N回…」の見出し）、`link`（リンクの文言）、`page`（その回だけのページ）、`pdf`（議事要旨・議事録の本文） |

ページに書かれた値を優先し、PDF の本文は空いている項目を補うのに使う。両者の開催日や回次が食い違う場合は、`build_db.py` の実行時に警告を出す。

`attendance.csv`（各回の出席者、1 人 1 行）：

| 列 | 内容 |
|---|---|
| `group` | 資料に書かれた区分（委員、分科会委員、防衛省、防衛省側、再就職等監察官など）。区分の中に内訳がある場合は `防衛省側/内部部局`、`オブザーバー/文部科学省` のように `/` でつなぐ |
| `seq` | その回の中での記載順 |
| `name_raw` | 記載そのまま（「太田会長」「青木 節子(慶應義塾大学大学院教授)」「林服務管理官」）。所属が次の行に書かれている名簿は「氏名(所属)」にまとめている |
| `surname`、`role` | 姓と会議体での役職。「太田会長」→ 太田／会長、「齋野委員（座長）」→ 齋野／座長。省側の出席者は職名との境目が決められないので空。「榊原定征（座長）」のように姓と名の間に空白がない氏名は、姓が空 |
| `person_id`、`person_basis` | 人物表の ID と、結び付けの根拠（後述）。省側の出席者は空 |
| `source` | 読み取った資料の URL |

`documents.csv`：

| 列 | 内容 |
|---|---|
| `meeting_id` | 結び付く会議。報告書や規程など、特定の回のものでなければ空 |
| `section` | 表の列見出し、または直前の見出し |
| `title` | リンクの文言 |
| `doc_type` | 文言から機械的に付けた種別（議事録、議事要旨、議事次第、資料、報告書、規程、開催案内など） |
| `url`、`host`、`format` | リンク先。`host` は `www.mod.go.jp` か `warp.da.ndl.go.jp` がほとんど |
| `text`、`pages`、`chars` | 本文を抽出済みの PDF について、テキストファイルのパス、ページ数、文字数。`chars` が 0 に近いものは画像の PDF |
| `doc_key` | 資料の同定キー（`www.mod.go.jp/...`）。WARP への移動後も元のホスト・パスを保つ |

会議に結び付かないサイト内ページ・外部サイトへのリンクは、案内とみなして載せていない。

### 読み取りの限界

- 933 件のうち、回次と開催日の両方が分かるのは 451 件。回次だけが 266 件、開催日だけが 216 件ある。一覧に片方しか書かれておらず、もう片方が書かれた資料が WARP にしかない会議が多い。
- 回次だけの行と開催日だけの行が同じ会議を指していても、資料から開催日が分からない限り突き合わせていない。防衛調達適正化会議（`cho-teki`）、防衛施設中央審議会（`shi-chu`）などは実際より多く数えている。
- 同じ回次で開催日が違う行は、まとめずに別の会議として残している（`meeting_id` の末尾に `@開催日` が付く）。回次を振り直している会議体（防衛省改革検討委員会 `kaikaku`）のほか、掲載側の誤記とみられるもの（`kyumei` の第６回が 2 行）がある。`build_db.py` の実行時に警告として表示される。
- 出席者は、防衛省サイトに議事要旨・議事録の PDF が残っている回に限られる（最も古いもので令和３年４月。会議体によってはここ 1〜2 年分だけ）。議事要旨・議事録 185 件のうち、出席者を読み取れたのは 165 件。公正入札調査会議は表の体裁で文字の並びが崩れ、政策評価に関する有識者会議の逐語の議事録などは出席者の一覧がない。
- 出席者の区分や名前の区切りは、資料の書き方から機械的に決めている。省側の出席者は職名だけの記載も多い。抜き取りで確かめた結果は「抜き取り検証」の節を参照。
- 資料に書かれた曜日が日付と合わないものが 4 件ある（`rinri#93`、`jinji/再就職等監視分科会#74`、`jinji/職員処遇問題部会#41`、`keiyaku_seido#39`）。開催日は資料の日付どおりに入れているが、日付と曜日のどちらが正しいかは確認できていない。`build_db.py` の実行時に警告として表示される。
- `doc_type` は文言だけで決めているので、目安にとどまる。

## 抜き取り検証

議事要旨・議事録から読み取った開催日時・場所・出席者について、2026-11-19 に抜き取りで確かめた。

```bash
python3 mod/make_check_sheet.py                                               # checks/attendance_sample.md
python3 mod/make_check_sheet.py --seed 2 --out checks/attendance_sample_2.md
```

出席者を読み取った 159 会議から、会議体ごとに最低 1 件を含む 30 会議を無作為に選び、読み取った値と、読み取り元の本文（PDF から抽出したテキスト）の冒頭を並べた確認表を作る。突き合わせは、読み取り処理を書いたのとは別の AI エージェントに、処理の中身を知らせずに行わせた。

| 回 | 対象 | 誤りのあった会議 | 内容 |
|---|---|---|---|
| 1 回目 | 30 会議 | 11 | 場所の欠落 5、出席者の区切り・区分・姓の誤り 6。開催日・時刻の誤りは 0 |
| 2 回目（修正後） | 同じ 30 会議と、別に選んだ 30 会議（重複 7、計 53 会議） | 3 | 1 回目の誤りはすべて解消。新たに、出席者 2 人の抜け 1 会議、「他」の消し残り 2 会議 |

2 回目で見つかった 3 会議も修正し、その 3 会議以外の出席者表が変わっていないことを確かめた。修正後にあらためて抜き取りはしていない。2 回目のうち初めて見る 23 会議に限ると、誤りは 2 会議（明確なもの 1、軽微なもの 1）だった。

確かめたのは「抽出したテキスト → 表」の読み取りで、「PDF → テキスト」の抽出が正しいかは含まれていない。PDF と見比べる場合は、確認表の各節にある原資料のリンクとチェック欄を使う。

## 人物表

`persons.csv` は、委員名簿（`members.csv`）と出席記録（`attendance.csv`）に出てくる委員・有識者を、人物ごとに 1 行にまとめたもの。省側の出席者は「林服務管理官」のように姓と職名の境目が決められず、職名だけの記載も多いので、人物としては扱っていない。

| 列 | 内容 |
|---|---|
| `person_id` | 氏名の分かる人物は、空白を除いた氏名（異体字は照合用にそろえる。「髙橋 秀雄」→ `高橋秀雄`）。姓しか分からない人物は `会議体:姓`（`rinri:能勢`） |
| `name`、`surname` | 氏名（元の字のまま）と姓。姓と名の境目が分からない場合、`surname` は空 |
| `basis` | `full_name`（氏名が分かる）または `surname_only`（姓だけ） |
| `councils` | 名簿か出席記録に出てくる会議体 |
| `roster_councils` | 現在の名簿に載っている会議体 |
| `roles`、`affiliations` | 記載のあった役職と、所属・職名（名簿と議事要旨の記載をそのまま並べたもの） |
| `n_meetings`、`first_date`、`last_date` | 出席記録のある会議の数と、その最初・最後の開催日 |

`attendance.csv` の `person_basis` は、その行を人物に結び付けた根拠を示す。

| 値 | 内容 |
|---|---|
| `full_name` | 資料に氏名が書かれている。氏名が同じなら、会議体が違っても同じ人物とみなす |
| `surname` | 「太田会長」のように姓だけの記載。同じ会議体に、その姓で氏名の分かる人物が 1 人だけいる |
| `surname_only` | 姓だけの記載で、同じ会議体に氏名の分かる人物がいない。会議体ごとに仮の人物（`会議体:姓`）を置く |
| `ambiguous` | 姓だけの記載で、同じ会議体にその姓の人物が複数いる。結び付けない（現時点で該当なし） |

同姓同名の別人は区別できない。また、氏名の手掛かりは主に現在の名簿なので、`surname` で結び付けた過去の回が、実は同じ姓の前任者だったという取り違えは起こりうる。`surname_only` の 14 人は、名簿にいない過去の委員とみられる。

## RSS

- `update.xml`：サイト全体の更新情報。件名は「セクション｜…を更新」。審議会ページの更新は `/j/policy/agenda/meeting/` 配下へのリンクとして載る。
- `news.xml`：お知らせ（報道資料）。分科会の開催告知などが載る。
- どちらも直近約 2.5 か月分しか保持しない。本文・概要はなく、分かるのは件名・リンク・日時だけ。

```bash
python3 mod/poll_rss.py
```

`rss_items.csv` の列：

| 列 | 内容 |
|---|---|
| `feed` | `update` または `news` |
| `pub_date` | 掲載日時（ISO 8601、JST） |
| `title`、`link`、`guid`、`category`、`description`、`content` | フィードの値そのまま。`link` はサイト内の相対パス |
| `council_related` | 審議会ページ配下へのリンク、または件名に会議体らしい語を含む場合に `1`。件名による判定は目安 |
| `first_seen` | 初めて取得した日時（UTC） |

同じページが複数回更新された場合は、掲載日時ごとに別の行になる。

## 出典と利用条件

収録しているのは、各省庁ウェブサイト（防衛省・デジタル庁・こども家庭庁・内閣官房・内閣府・農林水産省・財務省・法務省・環境省・金融庁・消費者庁・文部科学省・国土交通省・警察庁・個人情報保護委員会・厚生労働省。各省庁のベース URL は `agencies.json` および `data/all/agencies.csv`）で公開されている情報と、それを加工したものである。各省庁サイトのコンテンツは「公共データ利用規約（第1.0版）」（PDL1.0）または政府標準利用規約（第2.0版）に準拠して利用できる。

- `raw/<agency>/pages/`（取得した HTML）：出典：各省庁ウェブサイト（各ページの URL は `raw/<agency>/pages.csv`）、[PDL1.0](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)（2026年11月19日〜21日に利用）
- `data/<agency>/text/`（PDF の本文）：各省庁ウェブサイトの PDF（各ファイルの URL は `data/<agency>/texts.csv`）から、本文を機械的に抽出して作成
- `data/<agency>/` および `data/all/` の各表：各省庁ウェブサイト（各行の `source`・`url` 列に元のページ・資料）を加工して作成

抽出と加工は本リポジトリの作成者が行ったもので、各省庁が作成した情報そのものではない。機械的な読み取りによる誤りを含みうるので、引用や分析に使う際は元の資料を確認すること。
