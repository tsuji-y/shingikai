# shingikai

防衛省の審議会等（[一覧ページ](https://www.mod.go.jp/j/policy/agenda/meeting/index.html)）について、会議体・各回のメタ情報・参加者・資料・議事録をまとめるデータベース。構築中。

## 現状

実装済みは RSS の取り込みだけ。会議体の一覧と各回のデータは未取得。

| パス | 内容 |
|---|---|
| `mod/poll_rss.py` | 防衛省 RSS 2 本を取得し、未収録の item を追記する |
| `data/mod/rss_items.csv` | RSS の更新ログ（2026-09-01 以降） |
| `tests/` | `python3 -m unittest discover -s tests` |

`mod` は Ministry of Defense の略。コードは Python 標準ライブラリのみ。

## www.mod.go.jp のアクセス制約

2026-11-19 にクラウド環境（米国のデータセンター経由）から実測した結果。

| 対象 | 結果 |
|---|---|
| HTML・ディレクトリ URL・`sitemap.xml` | 403。Cloudflare のボット検証（`cf-mitigated: challenge`） |
| PDF | 200（`/j/press/news/2026/11/11b.pdf` で確認） |
| `/j/rss/update.xml`、`/j/rss/news.xml` | 200。ただし Python 既定の User-Agent では 403 |
| `robots.txt` | 200。`Disallow` は `/a/` と `/sp/j/` のみ |

ボット検証は運営側が自動アクセスを弾くための仕組みなので、ステルス化などで突破しない。検証が出ない経路で取得する。PDF の URL を推測して総当たりすることもしない。

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
