# caqnal-slides-gen

CAQNAL 自治体エンゲージメント調査の分析レポート（PPTX / PDF / Excel）を、自治体ごとの設定ファイル差し替えだけで量産する Python パイプライン。

```
回答 CSV + 設問マスタ xlsx  ──►  caqnal profile / init（ヘッダー・設問を YAML 化）
        │
        ▼
client.yaml / questions.yaml / yoy_map.yaml  ──►  caqnal analyze（集計・KD・4類型・判定 → intermediate/）
        │
        ▼
config/decks/*.yaml + カクナルスライド.pptx  ──►  caqnal render（全庁 / 部局別×N / 1pager / 手法解説（社内向け） / 詳細集計 Excel / PDF）
```

## セットアップ
```bash
uv sync
uv run caqnal --help
```
PDF 出力には LibreOffice（`brew install --cask libreoffice`）と Noto Sans JP フォントのインストールが必要。

## 使い方（八戸市の例）
```bash
uv run caqnal validate --client 八戸市        # 入力チェック
uv run caqnal analyze  --client 八戸市        # → local-governments/八戸市/output/intermediate/
uv run caqnal render   --client 八戸市 --pdf  # → output/*.pptx, 部局別/*.pptx, 分析手法解説_社内向け.pptx, *.xlsx, *.pdf
uv run caqnal all      --client 八戸市 --pdf  # 上記を一括
uv run caqnal verify   --client 八戸市        # 2回実行してバイト一致を確認
uv run caqnal render   --client 八戸市 --font meiryo --pdf   # Meiryo UI 版を output/font_meiryo/ に出力
uv run pytest                                 # 合成データでの回帰テスト
```
新しい自治体の追加は `docs/自治体オンボーディング手順.md`。

> **仕様 v2（自治体共通版 2026-10-08）へ移行中。** 現行仕様の要約は `docs/仕様書要約_自治体共通版v2.md`、サンプル（佐賀市）の頁構成は `docs/参考デッキ構成_佐賀市v2.md`、移行計画と進捗は `docs/PLAN.md`。上のコマンドは v1 実装のもの。v2 の工程は実装した分から使える: `caqnal run --client X`（run の状態）、`caqnal normalize --client X`（原資料 → `output/runs/<run_id>/private/normalized/`）、`caqnal report --client X`（読者別 PPTX → `output/runs/<run_id>/render/`。途中の aggregate / models / publish / compose も個別に実行できる）。

## ディレクトリ
| パス | 内容 |
|---|---|
| `src/caqnal_report/` | `cli` / `config` / `ingest`（取込・プロファイル・設問マスタ・経年・検証）/ `analysis`（スコア・集計・ランキング・4類型・KD・自由記述・判定・示唆）/ `render`（テーマ・図表・PPTX・Excel・PDF） |
| `config/` | `analysis_defaults.yaml`（仕様書 §9 閾値）/ `theme.yaml`（色・フォント・座標）/ `action_library.yaml` / `comment_themes.yaml` / `decks/`（スライド構成） |
| `local-governments/<自治体>/` | `client.yaml` `questions.yaml` `yoy_map.yaml` `profile/` `RENAME_MEMO.md`（git 管理）、`data/` `output/`（git 管理外） |
| `slides-master/` | カクナルスライド.pptx（テンプレート） |
| `spec/` | `自治体向け/`: 現行の引継ぎ手順書 v2.0（docx）と佐賀市のサンプル PPTX（全庁・所属長・人事）。直下の docx は旧仕様 v1 |
| `docs/` | 仕様書要約（v2 現行・v1 旧）・参考デッキ構成・スライドマスター仕様・データ形式メモ・参考実装メモ・計画・オンボーディング手順（原本を開かずに参照するための md） |
| `tests/` | 合成データ（八戸市スキーマ、seed 固定）による回帰テスト |

## 設計の要点
- 3 ステージはファイルでのみ結合（`data/` → `output/intermediate/` → `output/*.pptx`）。render は中間 CSV/JSON だけを読む
- スライド構成は `config/decks/*.yaml` のリスト。`type` は `render/slides.py`（レポート）と `render/slides_method.py`（手法解説）の REGISTRY。部局別は `scope` を置換して部局数分生成
- `手法解説` デッキは同じ中間データ・設定から「何をどう分析したか」を社内向けに解説する。閾値・n・モデル統計はレポートと必ず一致する
- 閾値・匿名性・対象設問は全て YAML（仕様書 §13 再現性要件）。解析時の設定は `settings_snapshot.yaml` に保存
- 尺度は自治体ごとに `client.yaml: scales` で定義（八戸市は ①=5 の逆順、佐賀市は 5=5 の正順）
- 経年比較は設問番号ではなく設問文で対応付け（`yoy_map.yaml`、比較可／参考比較／比較不可）
- PPTX は zip タイムスタンプを固定してバイト一致。PNG は matplotlib メタデータを除去
- フォントは Noto Sans JP 既定。`config/theme.yaml: font.preset: meiryo` または `render --font meiryo` で Meiryo UI に切替。Mac では Office 同梱の `meiryo.ttc` を図表に使い、PDF 変換（LibreOffice）に見せるには `~/Library/Fonts` にシンボリックリンクを置く

## 生データの扱い
`local-governments/*/data/`、`*/output/`、`*_PII*` は `.gitignore` で除外。自治体名・列名・設問文・値分布（`profile/`）のみを追跡する。
