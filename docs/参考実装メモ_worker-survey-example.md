# 参考実装メモ: `/Users/kenshiro.takimoto/Dev/me/worker-survey-example`

従業員サーベイ Excel → 解析 → PPTX/PDF を再現性重視で生成するサンプル（Python 3.12 / uv / pandas / matplotlib / python-pptx / PyYAML、約3,150行）。
本プロジェクトはこの構造を土台にしている。参考リポジトリを再度読む必要が出たときのための索引。

## 構造（転用した点）
- **3ステージ file-coupled**: `data/input/*.xlsx` → `output/intermediate/*.csv + summary.json` → `output/report.pptx`。render は中間ファイルだけを読む
- **`report.yaml` のスライドリスト + `REGISTRY` dict**: `for spec in report["slides"]: REGISTRY[spec["type"]](slide, ctx, spec, page_no)`。org パラメータを変えたブロックを追加するだけで組織別セクションを量産
- **`charts.render_table()`**（`render/charts.py:252`）: matplotlib の Rectangle + text だけで密な日本語表を PNG 化。列 `{id,title,width,kind(text|num|sig|bar3),align,group}`、行 `{style,cells}`。sig セルは緑/赤地、bar3 は100%積み上げ。本文表の9割はこれで済む
- **`normalize_pptx()`**（`render/pptx_build.py:56`）: zip エントリをソートし直し、`docProps/core.xml` の created/modified と ZipInfo の date_time を 2020-01-01 に固定 → PPTX がバイト一致
- **`set_run_font()`**（`render/pptx_helpers.py:26-32`）: `run.font.name` に加えて `rPr` に `<a:ea typeface=...>` を注入。これが無いと日本語がテーマフォントに戻る
- **`content_header()`** が本文開始 y を返し、各 builder は相対配置。`place_image()` は PIL で PNG 寸法を読み DPI で inch 換算、縮小のみ
- **`_save()`** で `metadata={"Software": None}`（PNG から matplotlib バージョン除去）
- **`cmd_verify`**: analyze+render を temp dir で2回実行し SHA-256 を比較
- 有意差: `significance.two_proportion_p / one_proportion_p / sig_flag`
- 匿名性抑制 `_suppress(row)`: n<min_group_size で pct/delta/diff を NaN、sig を 0

## 参考実装の問題点（本プロジェクトで直した点）
| 問題 | 対応 |
|---|---|
| `CONFIG_DIR` / `DATA_DIR` がハードコード、`--config-dir` なし | `--client <自治体名>` でクライアントルートを解決 |
| `engagement` / `enablement` のIDがコードに直書き | `client.yaml: indicators / segmentation / positioning / key_driver` から導出 |
| 属性キー tenure/age/gender/job_type と配置座標が直書き | `client.yaml: attributes` の順に自動配置 |
| 4類型ラベル（活躍社員…）とベンチマークキーが色辞書のキー | ラベルは config、色は quadrant キー（high_high…）で持つ |
| `Presentation()` 既定テンプレート、blank layout のみ | `カクナルスライド.pptx` を開き Master 3 の日本語レイアウトを使う |
| `.gitignore` が生データを除外していない（サンプルなので意図的） | `local-governments/*/data/` を除外 |
| 単一年度の literal `sig_2024` | 前年度は `data.previous[0]` から解決 |
| `ctx.cat_row().iloc[0]` の空チェックなし | 欠損時は `None` を返し「－」表示 |

## 参照したいときの主要ファイル
- `src/survey_report/render/charts.py`（render_table, wrap_cjk, outcome_scatter, segmentation_quadrants）
- `src/survey_report/render/pptx_helpers.py`（set_run_font, add_table, style_cell, content_header）
- `src/survey_report/render/pptx_build.py`（build_pptx, normalize_pptx）
- `src/survey_report/analysis/aggregate.py`（_org_table, _suppress, attribute_category_table）
- `config/survey.yaml` / `analysis.yaml` / `report.yaml`
