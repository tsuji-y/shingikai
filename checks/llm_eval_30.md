# Phase 3 Gemini 抽出評価レポート

- モデル: `gemini-2.5-flash` / プロンプト版: `extract-v1`
- サンプル: 20 会議（seed=20261119、`make_check_sheet.py` と同一抽出方式）

## 集計

| 指標 | 件数 | 割合 |
|---|---:|---:|
| 自動検証通過 | 0 / 20 | 0.0% |
| 開催日一致 | 0 / 20 | 0.0% |
| 時刻一致 | 0 / 20 | 0.0% |
| 場所一致 | 0 / 20 | 0.0% |
| 出席者完全一致 | 0 / 20 | 0.0% |
| API/解釈エラー | 20 / 20 | 100.0% |

## 各会議の比較

| # | meeting_id | 検証 | 日付 | 時刻 | 場所 | 出席者 (既存/LLM/一致) | 備考 |
|---:|---|---|---|---|---|---:|---|
| 1 | `rinri#95` | DIFF | DIFF | DIFF | DIFF | 5/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 2 | `rinri#103` | DIFF | DIFF | DIFF | DIFF | 5/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 3 | `shi-chu#10` | DIFF | DIFF | DIFF | DIFF | 11/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 4 | `jinji/再就職等監視分科会#63` | DIFF | DIFF | DIFF | DIFF | 13/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 5 | `jinji/処遇・給与部会#10` | DIFF | DIFF | DIFF | DIFF | 14/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 6 | `jinji/職員処遇問題部会#40` | DIFF | DIFF | DIFF | DIFF | 6/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 7 | `cho-shin#182` | DIFF | DIFF | DIFF | DIFF | 16/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 8 | `seisaku#43` | DIFF | DIFF | DIFF | DIFF | 12/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 9 | `keiyaku_seido#39` | DIFF | DIFF | DIFF | DIFF | 12/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 10 | `bouei_shisetsu#10` | DIFF | DIFF | DIFF | DIFF | 15/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 11 | `harassment#7` | DIFF | DIFF | DIFF | DIFF | 12/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 12 | `kiban#8` | DIFF | DIFF | DIFF | DIFF | 23/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 13 | `cap_build#3` | DIFF | DIFF | DIFF | DIFF | 6/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 14 | `blood-transfusion#1` | DIFF | DIFF | DIFF | DIFF | 14/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 15 | `drastic-reinforcement/総会#5` | DIFF | DIFF | DIFF | DIFF | 30/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 16 | `drastic-reinforcement/総会#6` | DIFF | DIFF | DIFF | DIFF | 26/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 17 | `sds#5` | DIFF | DIFF | DIFF | DIFF | 18/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 18 | `space_domain#1` | DIFF | DIFF | DIFF | DIFF | 20/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 19 | `dstb#9` | DIFF | DIFF | DIFF | DIFF | 21/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |
| 20 | `ts-ships#3` | DIFF | DIFF | DIFF | DIFF | 7/0/0 | Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message' |

## 食い違いの詳細（原文確認用）

### `rinri#95`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/rinri/pdf/giji_95.pdf>
- 既存: date=`` time=`` place=`` (5名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `rinri#103`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/rinri/pdf/giji_103.pdf>
- 既存: date=`` time=`` place=`` (5名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `shi-chu#10`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/shi-chu/pdf/giji_10.pdf>
- 既存: date=`` time=`` place=`` (11名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `jinji/再就職等監視分科会#63`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/jinji/pdf/giji_saishushoku_20230726.pdf>
- 既存: date=`` time=`` place=`` (13名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `jinji/処遇・給与部会#10`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/jinji/pdf/giji_shoguu_10_03.pdf>
- 既存: date=`` time=`` place=`` (14名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `jinji/職員処遇問題部会#40`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/jinji/pdf/giji_shokuin_40.pdf>
- 既存: date=`` time=`` place=`` (6名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `cho-shin#182`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/cho-shin/giji/pdf/182.pdf>
- 既存: date=`` time=`` place=`` (16名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `seisaku#43`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/seisaku/pdf/gijigaiyou43.pdf>
- 既存: date=`` time=`` place=`` (12名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `keiyaku_seido#39`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/keiyaku_seido/pdf/giji_39.pdf>
- 既存: date=`` time=`` place=`` (12名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `bouei_shisetsu#10`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/bouei_shisetsu/pdf/10_gijiyoshi.pdf>
- 既存: date=`` time=`` place=`` (15名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `harassment#7`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/harassment/pdf/gijiroku07.pdf>
- 既存: date=`` time=`` place=`` (12名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `kiban#8`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/kiban/pdf/20241105_05.pdf>
- 既存: date=`` time=`` place=`` (23名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `cap_build#3`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/cap_build/pdf/gijigaiyou_20250717.pdf>
- 既存: date=`` time=`` place=`` (6名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `blood-transfusion#1`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/blood-transfusion/pdf/gijiyoushi01.pdf>
- 既存: date=`` time=`` place=`` (14名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `drastic-reinforcement/総会#5`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/drastic-reinforcement/pdf/siryo05_03.pdf>
- 既存: date=`` time=`` place=`` (30名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `drastic-reinforcement/総会#6`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/drastic-reinforcement/pdf/siryo06_03.pdf>
- 既存: date=`` time=`` place=`` (26名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `sds#5`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/sds/pdf/giji_05.pdf>
- 既存: date=`` time=`` place=`` (18名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `space_domain#1`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/space_domain/pdf/siryo01_03.pdf>
- 既存: date=`` time=`` place=`` (20名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `dstb#9`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/dstb/pdf/siryo09_04.pdf>
- 既存: date=`` time=`` place=`` (21名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

### `ts-ships#3`

- 原資料: <https://www.mod.go.jp/j/policy/agenda/meeting/ts-ships/pdf/siryo03_01.pdf>
- 既存: date=`` time=`` place=`` (7名)
- LLM: date=`` time=`` place=`` (0名)
- 実行エラー: Gemini API 呼び出し失敗: ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features and improvements. We recommend you to use the Interactions API (https://aistudio.google.com/docs/get-started).', 'status': 'NOT_FOUND'}}

```text
```

