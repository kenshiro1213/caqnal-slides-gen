# CAQNAL スライド量産パイプライン 実装計画（v2: 自治体共通版対応）

## Context
`spec/自治体向け/` に新しい手順書 **CAQNAL_レポート作成引継ぎ手順書_自治体共通版.docx（v2.0, 2026-10-08）** と、佐賀市の完成サンプル PPTX 6 点（全庁本編 68 頁・全庁詳細付録 上下冊・総務部長/人事課長向け・人事担当向け）が届いた。現行実装は旧仕様（T01〜T15、4類型、Impact×Score ゾーン、ルールベース示唆、matplotlib 画像）に沿っている。新仕様では次の点が変わった。
- 工程が 8 段になる（validate→normalize→aggregate→models→publish→compose→render→verify）。run の状態と入力の指紋を持つ
- 成果物が読者別になる（全庁／部局長／課長／人事担当＋詳細付録）。所属長版は「同じ部局内の付録」と「記入シート 1 枚」
- 匿名性は二次秘匿まで行い、読者別の公開データを作って render はそれだけを読む
- 重回帰は未学習データで検証し、決定木を加え、モデルごとに採否の状態（採用/参考/保留/推定不能）を持つ
- グラフ・表は PowerPoint 上で**編集可能**にする（ネイティブのグラフ・表）。紺 #173650／ゴールド #CAA44A
- 自治体固有の処理（Q番号固定・人数・施策文）は共通コードに入れない。佐賀市は検証事例として扱う

ユーザーの決定: **段階的に全面対応**する。同梱と書かれた「実装仕様書・プロンプト10本・設定点検ツール・設定例」は**手元に無い**ため、手順書本文とサンプル PPTX から仕様を起こす。

## 方針
- 既存の 3 ステージ（ingest/analysis/render）を 8 工程へ組み替える。既存モジュールは活かせる範囲で再利用する（scoring・aggregate・drivers・pptx_helpers・theme・charts・deck の REGISTRY 方式）
- 工程間はファイルだけで受け渡す。`output/runs/<run_id>/{private,public/<reader>,render,qa}/`＋`run.json`（状態、各工程の入力・設定・コードの指紋、警告、未確定事項）
- 状態: `draft_unconfirmed → validated → computed → public_view_ready → draft_qa_pending → release_ready`。unknown／failed／blocked を passed として扱わない
- 旧仕様だけにある要素（4類型・Impact×Score ゾーン・重点部局スコア・ランキング）は既定デッキから外す。ランキングは設定で有効にしたときだけ出す。コードは当面残し、最終フェーズで整理する

## フェーズ

### P0. 仕様の md 化と既存 docs の更新（コード変更なし）
- `docs/仕様書要約_自治体共通版v2.md` を新規作成する。章ごとの要点、状態表、成果物×読者表、モデル採否表、佐賀市事例の値を載せる
- `docs/参考デッキ構成_佐賀市v2.md` を新規作成する。サンプル 6 デッキの章・頁構成を記録し、slide type の設計元にする
- `docs/PLAN.md` を本計画で更新する。`docs/仕様書要約_分析レポート制作.md` は「旧仕様（v1）」と明記する。`docs/自治体オンボーディング手順.md` と `README.md` を新工程に合わせる
- メモリ: `caqnal-project-decisions` に v2 移行（ルールベース示唆→人が確認する compose、画像→ネイティブ図表）を追記する

### P1. 案件設定と validate
- `client.yaml` のスキーマを拡張する（未確定は `null`）
  - `population`: 対象・対象外の規則、母数の基準、重複の採用規則
  - `org_dictionary`: unit_id／parent_id／名称／階層／表示順。別ファイル `org.yaml`
  - `attributes`: 可変長の dimension 一覧。基準日、年代・勤続の境界
  - `indicators`: 目的指標・カテゴリ指標・共通9問。`status: implemented|undefined|not_conducted|partial`
  - `scales`: 中立なし（4件法）と逆転に対応。逆転は 1 回だけ適用
  - `anonymity`: 最低 n を案件で必須にする（既定値を自動適用しない）
  - `readers`: 全庁／部局長(unit_ids)／課長(unit_ids)／人事。許可する組織・属性・自由記述・モデル参照
  - `models`: CV の分割単位（部局/課）、木の最大深さ・葉の最低人数、再抽出の回数
- `ingest/validate.py` を拡張する
  - null の数を数え、残っていれば `draft_unconfirmed` にする
  - 組織辞書: 親の存在・循環・孤立
  - 設問 ID の一意性、尺度の参照先、指標の構成設問の存在
  - 自治体名による分岐や固定 Q 番号が無いかの静的点検
- 対象: `config.py`（`ClientConfig` に org 辞書・readers・models のアクセサを追加）、`ingest/validate.py`、`cli.py`

### P2. normalize と run 管理
- 新規 `src/caqnal_report/run.py`: run_id の採番、`run.json` 読み書き、SHA256 指紋、前工程の成功と指紋一致の確認、依存出力の無効化
- 新規 `ingest/normalize.py`: 元 CSV/xlsx を標準テーブルへ変換して `private/` に CSV で書く
  - `respondents`（対象/対象外理由）、`responses` long、`questions`、`org`、`attributes`、`comments`
  - ID は文字列で照合する（ゼロ埋め・空白を正規化）。照合・除外の記録を残す
  - 既存の `ingest/loaders.py` の丸数字除去・匿名 ID 採番を流用する
- 対象者リストとの紐付け（佐賀市の ID 入力設問→名簿）は入力変換の設定で行う。名簿を読むときも PII 列は private 外へ出さない

### P3. aggregate
- 回答状況: 対象・対象外・有効・未照合・重複・欠損を段階別に。回答率は所属別・属性別で、元データと独立に照合する
- 記述統計: 全庁・部局・課・許可属性 × 設問・カテゴリ・目的指標。n・平均・点数・肯定/中立/否定。丸めは表示時だけ。`analysis/aggregate.py`・`scoring.py` を拡張する（課の階層、可変の属性）
- 相関: カテゴリ×目的指標、設問×目的指標。組合せごとの n とブートストラップ区間を出す

### P4. models
- `analysis/drivers.py` を `analysis/models/` に再構成する
  - `ols.py`: rank・残差自由度・VIF・区間（クラスタ単位を明記）。一意に推定できなければ `推定不能`
  - `validation.py`: グループ分割（部局/課）の交差検証。前処理・標準化は学習側だけで行う。検証 R²・MAE を平均だけの基準と比べる
  - `tree.py`: scikit-learn の決定木。深さと葉の最低人数を設定から取る。木の葉にも公開条件をかける。再抽出で根元項目の安定性を見る
  - `status.py`: 採否（採用/参考/保留/推定不能）と理由
- 依存に `scikit-learn` を追加する（`pyproject.toml`）

### P5. publish（読者別公開データ）
- 新規 `analysis/publish.py`
  - 一次秘匿（n<最低人数）
  - 二次秘匿: 親子の組織、属性の合計、重複する表から差し引きで推測できるセルを追加で隠す
  - 読者の範囲で切り出す。所属長は組織辞書から同じ部局内だけ。親が不明なら全庁へ広げずに停止する
  - 非表示の理由を記録する。非表示の値は公開ファイルに残さない（0 と非表示を区別する）
- 自由記述: 記述者数・実質意見数・テーマ（記述 ID＋原文の指紋）。公開は人が確認した要約だけ。人事限定の属性別分析は人事の公開データにだけ入れる

### P6. compose と render（読者別デッキ）
- compose: 新規 `local-governments/<自治体>/interpretation.yaml`（コンサルタントが書く）。中身は観測事実（根拠への参照と n）→背景仮説→大方針→重点施策（対象・内容・担当・権限）→試行と確認（時期・工数の仮定・指標・拡大/中止の条件）、方針資料の出典（名称・改定日・頁）。ルールベースでは「観測事実の下書き」だけを生成し、施策文は生成しない
- render は public データと interpretation だけを読む。編集可能にするため python-pptx のネイティブのグラフ・表へ移行する（`render/charts.py` の画像描画を `render/native_charts.py` に置き換える）。グラフの埋込 Excel に非表示セルを残さない
- theme: 紺 #173650・ゴールド #CAA44A を `config/theme.yaml` に追加し、見出しは紺に揃える
- デッキ設定 `config/decks/`（読者ごと。頁数は設問数・組織数・属性数から自動で分割）
  - `全庁.yaml`: 表紙・目次・見方・回答状況（属性数に応じて頁を分割）・サマリー2枚・4指標・カテゴリ・設問の強み/課題・部局比較（分割）・属性別・KD（カテゴリ/設問×目的指標）・点数×関連・重回帰・決定木・職員の声・大方針・施策・実行と指標・資料（確認事項・用語集）
  - `全庁_詳細付録.yaml`: 許可された所属 × カテゴリ・全設問詳細を繰り返す
  - `部局長.yaml` / `課長.yaml`: 自所属サマリー2枚・4指標・カテゴリ・全庁分析の使い方・職員の声・重点施策・**記入シート1枚**（課題/行動/担当/期限/確認指標/支援依頼）・役割分担・90日計画・同じ部局内の付録（所属ごとにカテゴリ・4指標＋設問詳細）
  - `人事.yaml`: 基本方針・重点設問・属性差・KD/モデル・人事施策・工数・実行体制・付録 A1〜A7（部局別・全設問・相関・属性・モデル詳細・人事限定の自由記述）
  - 指標が未実施/未定義なら、その頁は注意書きを付けて省略する。採用しないモデルは「解釈が難しい理由と代わりの読み方」の頁にする
- 既存の `1pager`・`部局別`・`手法解説` は、`手法解説` を v2 のモデル診断に合わせて残し、他は廃止または全庁デッキへ統合する

### P7. verify と受入
- 新規 `qa/`
  - 数値照合: private 集計 ⇄ public ⇄ PPT に入っている値（表セル・グラフのキャッシュ・埋込 Excel）
  - 漏えい検査: ノート・代替テキスト・非表示頁・埋込データに、非表示の値や他自治体の名称・ID が無いか
  - 表示検査: LibreOffice で PNG 化して全頁の画像を出し、文字切れ・重なりを検査
  - 再実行時のバイト一致（既存の `cmd_verify` を流用）
  - QA 記録（確認者・日付・最終指紋）が揃えば `release_ready`
- 受入（手順書 §18）: 設問・尺度・階層・属性が違う 2 案件以上で、共通コードを変えずに通ること
  - 八戸市（実データ・部局のみ・逆転あり・経年あり）
  - 佐賀市テスト版（合成・課の階層あり）
  - 新たに架空の「テスト町」（4件法・共通9問あり・小規模所属・組織改編）を `tests/fixtures/` に作る

## 主に変更・追加するファイル
- 変更: `src/caqnal_report/{cli.py,config.py}`、`ingest/validate.py`、`analysis/{aggregate,scoring,drivers,comments}.py`、`render/{deck,slides,pptx_helpers,theme,excel}.py`、`config/{analysis_defaults,theme}.yaml`、`config/decks/*`、`local-governments/佐賀市/client.yaml`、`pyproject.toml`、docs 一式
- 新規: `run.py`、`ingest/normalize.py`、`analysis/models/*`、`analysis/publish.py`、`render/native_charts.py`、`qa/*`、`local-governments/<自治体>/{org.yaml,interpretation.yaml}`、`tests/fixtures/テスト町/`
- CLI: `caqnal validate|normalize|aggregate|models|publish|compose|render|verify|all --client X [--run RUN_ID] [--reader 全庁|部局長:<unit>|課長:<unit>|人事]`

## 検証
- 各フェーズの終わりに `uv run pytest` を通す。追加するテスト:
  - 二次秘匿で差し引き推測できないこと
  - 範囲外の所属が出ないこと
  - rank 落ちで推定不能になること
  - CV で学習側だけ標準化していること
  - 4件法で中立が無いこと
- `uv run caqnal all --client 八戸市 --pdf`、`--client 佐賀市_テスト版` で全読者のデッキを生成し、`caqnal verify` で QA が通ること、2 回実行でバイト一致すること
- 生成した PPTX を PNG 化して数枚を目視する。グラフが PowerPoint で編集できることは手動確認として記録する（構造検証とは区別する）
- 佐賀市の実回答はまだ受領していないため、サンプル PPTX との値の一致は受領後に確認する

## 進め方
P0 → P1 → P2 の順にコミットを分けて進める。各フェーズの完了時に報告し、P6 のデッキ構成は P0 の構成 md を見てもらってから着手する。

## 進捗
| フェーズ | 状態 |
|---|---|
| P0 仕様の md 化・docs 更新 | 完了（2026-10-10） |
| P1 案件設定と validate | 未着手 |
| P2 normalize と run 管理 | 未着手 |
| P3 aggregate | 未着手 |
| P4 models | 未着手 |
| P5 publish | 未着手 |
| P6 compose と render | 未着手（デッキ構成は `docs/参考デッキ構成_佐賀市v2.md` を確認してから） |
| P7 verify と受入 | 未着手 |

参照: `docs/仕様書要約_自治体共通版v2.md`（現行仕様）、`docs/参考デッキ構成_佐賀市v2.md`（サンプルの頁構成）


---

## 付録: v1 計画（旧仕様、2026-09 実装済み）

### Context
- 目的: 自治体エンゲージメント調査（CAQNAL）の分析レポート（PPTX/PDF/Excel）を、自治体ごとの設定ファイル差し替えだけで量産できる Python パイプラインを作る
- 参考: `/Users/kenshiro.takimoto/Dev/me/worker-survey-example`（Python 3.12 / uv / pandas / matplotlib / python-pptx、3ステージ疎結合）
- 仕様: `CAQNAL_エンゲージメント調査_分析レポート制作.docx`（ページテンプレート T01〜T15、閾値§9、設定項目 Appendix A）
- 初回対象: 八戸市 令和8年度（R7 との経年比較あり）
- ユーザー決定事項:
  - フォルダ名は日本語のまま（typo `local-goverments` → `local-governments` のみ修正）。CLI は `--client 八戸市`
  - 初回で 全庁レポート＋部局別6P×部局＋Executive One Pager＋詳細集計Excel を一括生成
  - 示唆文はルールベースのみ。LLM 入力用 `insights.json`（仕様書7-1構造）は出力しておく
  - フォント: Noto Sans JP をデフォルト、`config/theme.yaml` で Meiryo UI に切替可能
- 制約:
  - 生の調査ファイル（CSV/xlsx/対象者リスト）は git 管理しない。自治体名・ヘッダー情報（列名・設問マスタ・分布プロファイル）は git 管理する
  - リネームは統一規則で行い、各自治体フォルダ直下に `RENAME_MEMO.md` を残す
  - 各入力ファイルの要約をトークン節約形式の md で保存し、以後は原本を開かずに参照できるようにする。プランニング資料も md で残す

### 調査で判明した重要事実（設計に直結）
| 事実 | 設計への反映 |
|---|---|
| 八戸市CSV: UTF-8 BOM、984行×61列、回答者ID列なし、PII列なし | `encoding: utf-8-sig`、行番号から匿名ID `R8-0001` を採番 |
| 尺度 `①そう思う…⑤そう思わない`（①が最良、丸数字付きラベル） | scale を独立定義し `label→score` 写像で `①→5 … ⑤→1`。肯定=5,4 / 中立=3 / 否定=2,1 |
| 属性値も丸数字付き（`①男性`）、Q6の「なし」コードが R7 ③→R8 ④に変化 | 丸数字プレフィクスを剥がしテキストで突合。コードは使わない |
| カテゴリは列名の `【…】` プレフィクス（先頭設問のみ）。`【人事施策について】` が2回出現 | xlsx 設問マスタからカテゴリを取り、CSVヘッダーは verbatim 保存のみ |
| R7→R8 で41問の設問番号がシフト、1問削除（R7 Q34）、10問追加、1問カテゴリ移動 | 経年キーは正規化した設問文。`yoy_map.yaml` で明示上書き（R7 Q34→R8 Q55 は「参考比較」） |
| Q43・Q52 は意味的逆転項目、Q56 は管理職限定だが一般職135名も回答 | `reverse: true` フラグ、`condition: {attribute: 役職, equals: 管理職}` で条件外回答を除外 |
| 監査委員事務局 n=2 | 匿名性 min_n=5 の抑制テストケース |
| 組織階層は部局のみ（課なし） | `organization.levels: [部局]`。課長向けシートは対象外（課データが来たら追加） |
| スライドマスター 10×5.625in（非標準）。Master 3 に日本語レイアウト（表紙/本文1/2分割①②③/3分割/中表紙/中表紙APPENDIX）。本文プレースホルダなし、タイトルバー高さ0.32in、右下ページ番号 | テンプレートを `Presentation(master)` で開き、サンプル5枚を削除して使う。本文は絶対座標配置（座標は theme.yaml に持つ） |
| テーマ色 `CQL配色`: dk1 353636 / dk2 808484 / lt2 CECFCF / accent1 CAA44A(金) / accent2 876A27 / accent3 EDDFC0 / accent4 DECB72 / accent5 2196F3(青) / accent6 E91E63(桃) | ポジティブ=青系、注意=桃系、中立=グレー、ブランド=金。ヒートマップ用連続色は青⇄桃の発散スケール |
| Meiryo UI / M PLUS 1p は Mac に未インストール。Noto Sans JP は `~/Library/Fonts` にあり | Noto を `assets/fonts/` にベンダリング（OFL）。theme.yaml `font.family` 切替 |
| 環境: Python 3.12.8、uv、pandas/openpyxl/python-pptx/matplotlib/PyYAML あり、`soffice` は `/opt/homebrew/bin/soffice` | 追加依存: scipy, statsmodels（Welch t / 重回帰 / VIF）, xlsxwriter |
| 佐賀市 対象者リスト CSV は文字化け済みだが メール・職員番号・生年月日が生きた PII | `data/` に `_PII` サフィックスで隔離、パイプラインでは読まない |

### 最終ディレクトリ構成
```
caqnal-slides-gen/
├── .gitignore  .python-version  pyproject.toml  uv.lock  README.md
├── docs/                                  ← トークン節約用資料（全て git 管理）
│   ├── PLAN.md                            本計画（この plan ファイルを転記・更新）
│   ├── 仕様書要約_分析レポート制作.md         docx の要約（章・テンプレID・閾値・Appendix A を表で）
│   ├── 参考実装メモ_worker-survey-example.md  転用した構造と直した点
│   ├── スライドマスター仕様.md               レイアウト名・座標・テーマ色・フォント
│   ├── データ形式メモ.md                    CSV encoding・尺度・落とし穴（自治体横断）
│   └── 自治体オンボーディング手順.md          新自治体を追加する手順（profile→init→analyze→render）
├── spec/CAQNAL_エンゲージメント調査_分析レポート制作.docx   （local-goverments/ から移動）
├── slides-master/カクナルスライド.pptx     （そのまま）
├── assets/fonts/NotoSansJP-{Regular,Bold}.ttf + OFL.txt
├── config/
│   ├── analysis_defaults.yaml             §9 閾値標準案・§9-1 重点部局加点・匿名性・KD設定
│   ├── action_library.yaml                §10-2 課題テーマ別 施策ライブラリ、§6-3-1 WS用の問い
│   ├── theme.yaml                         色・フォント（noto/meiryo プリセット）・スライド座標
│   └── decks/
│       ├── 全庁.yaml  部局別.yaml  1pager.yaml   スライド構成（type と param の列）
├── local-governments/
│   ├── README.md                          フォルダ規約・命名規則
│   ├── 八戸市/
│   │   ├── client.yaml                    自治体名・年度・データパス・尺度・属性・組織・指標定義
│   │   ├── questions.yaml                 R8 設問マスタ（CSVヘッダー verbatim を含む）
│   │   ├── questions_R7.yaml              R7 設問マスタ
│   │   ├── yoy_map.yaml                   経年対応表（自動生成＋手動上書き）
│   │   ├── profile/                       `profile` コマンド出力 md（列一覧・分布・欠損、PIIなし）
│   │   ├── RENAME_MEMO.md
│   │   ├── data/                          ← gitignore（R8_回答.csv, R7_回答.csv, R8_設問_v1.2_260623.xlsx）
│   │   └── output/                        ← gitignore（intermediate/, charts/, *.pptx, *.pdf, *.xlsx）
│   ├── 佐賀市/   client.yaml(下書き) questions.yaml(xlsxから生成) profile/ RENAME_MEMO.md data/
│   ├── 十和田市/ 同上
│   └── 板橋区/   同上
├── src/caqnal_report/
│   ├── cli.py            profile / init / validate / analyze / render / all / verify
│   ├── config.py         ClientConfig（client.yaml + questions + defaults を合成、パス解決）
│   ├── ingest/
│   │   ├── loaders.py    CSV読込(utf-8-sig)、丸数字除去、匿名ID採番、属性・組織の正規化
│   │   ├── profile.py    → profile/*.md（ヘッダー一覧・行列数・欠損・値分布）
│   │   ├── question_master.py  xlsx（八戸市型 / 佐賀市型 / 十和田・板橋型）→ questions.yaml 下書き
│   │   ├── yoy.py        設問文正規化マッチ → yoy_map.yaml 下書き（比較可/参考比較/比較不可）
│   │   └── validate.py   仕様書1-3 チェック（列対応・尺度・組織紐付け・匿名性設定）
│   ├── analysis/
│   │   ├── scoring.py    label→score、逆転、条件付き設問、肯定/中立/否定分類、long形式
│   │   ├── aggregate.py  scope(全庁/部局/属性値) × unit(設問/カテゴリ/軸/総合指標): n, mean, pos%, neu%, neg%, sd, 0-100, 全庁差, 前年差, 抑制
│   │   ├── ranking.py    高評価/低評価/改善/悪化/全庁比±（§4-2）
│   │   ├── significance.py  Welch t、二比率z、sig_flag
│   │   ├── drivers.py    Pearson相関 + 標準化重回帰(statsmodels OLS) + VIF → Impact×Score ゾーンA-D
│   │   ├── segmentation.py  4類型（活躍実感型/意欲先行型/環境充足型/要支援型）、しきい値=全庁平均
│   │   ├── comments.py   自由記述: 件数・文字数、キーワード辞書テーマ分類、部局別テーマ構成、簡易マスキング
│   │   ├── judgement.py  §9 強み/課題判定、§4-9 6観点シグナル→重点課題候補、§9-1 重点部局スコア
│   │   ├── insights.py   insights.json（7-1構造）＋ルールベース文（7-2の①事実②特徴③要因候補）
│   │   └── pipeline.py   run_analysis / write_intermediate / load_intermediate
│   └── render/
│       ├── theme.py      theme.yaml 読込、matplotlib フォント登録、色定数
│       ├── charts.py     横棒・ヒートマップ・バブル散布・100%積み上げ・Impact×Score・KPIカード・render_table
│       ├── pptx_helpers.py  テンプレート展開、レイアウト取得、タイトル/リード文/脚注/ページ番号、`<a:ea>` フォント注入
│       ├── slides.py     REGISTRY: cover, toc, section, T01…T15 各 builder（引数 scope で全庁/部局を切替）
│       ├── deck.py       decks/*.yaml → pptx、部局ループ、normalize_pptx（バイト一致）
│       ├── excel.py      詳細集計 xlsx（全設問×組織×属性、KD結果、テーマ分類、設定値スナップショット）
│       └── pdf.py        soffice 変換
└── tests/
    ├── fixtures/make_synthetic.py   八戸市スキーマの合成データ生成（seed固定、小規模、git管理OK）
    ├── test_scoring.py test_aggregate.py test_yoy.py test_suppression.py test_drivers.py
    └── test_render_smoke.py         合成データで全デッキ生成、枚数・確定性を確認
```

### リネーム計画（実施時に各フォルダへ RENAME_MEMO.md を書く）
命名規則: `data/<年度>_<種別>[_<版・日付>][_PII].<ext>`　年度=R7/R8…、種別 ∈ {回答, 設問, 対象者リスト, 組織マスタ}

| 元 | 先 |
|---|---|
| `local-goverments/` | `local-governments/` |
| `local-goverments/CAQNAL_エンゲージメント調査_分析レポート制作.docx` | `spec/CAQNAL_エンゲージメント調査_分析レポート制作.docx` |
| `local-goverments/index.md`（0バイト） | 削除し `local-governments/README.md` を新設 |
| `八戸市/【八戸市令和8年度】20260721最終survey_1784674364 2.csv` | `八戸市/data/R8_回答.csv` |
| `八戸市/【令和7年度】survey_1767567610 3.csv` | `八戸市/data/R7_回答.csv` |
| `八戸市/八戸市様_令和8年度エンゲージメント調査設問v1.2_260623.xlsx` | `八戸市/data/R8_設問_v1.2_260623.xlsx` |
| `佐賀市/佐賀市様_令和8年度エンゲージメント調査設問_260805 (5).xlsx` | `佐賀市/data/R8_設問_260805.xlsx` |
| `佐賀市/佐賀市様_対象リスト(メール送付用).csv` | `佐賀市/data/R8_対象者リスト_PII.csv`（読み込まない） |
| `十和田市/（設問未確定）和田市様_R8年度エンケ_ーシ_メント調査.xlsx` | `十和田市/data/R8_設問_未確定.xlsx` |
| `板橋区/（設問未確定）板橋区様_R8年度エンゲージメント調査 .xlsx` | `板橋区/data/R8_設問_未確定.xlsx` |

全て未追跡ファイルなので `mv` で移動（git 履歴なし）。`.gitignore` に `local-governments/*/data/`、`local-governments/*/output/`、`**/*_PII*` を入れてからコミット。

### 設定スキーマ（八戸市の値で作成し、他自治体はこれをコピーして埋める）
`client.yaml`
```yaml
client: {name: 八戸市, fiscal_year: R8, fiscal_year_label: 令和8年度, report_date: 2026-09, target_count: null}  # target_count 不明 → 回答率は「－」
data:
  responses: data/R8_回答.csv
  encoding: utf-8-sig
  respondent_id: {source: row_index, prefix: R8-}
  previous: [{fiscal_year: R7, responses: data/R7_回答.csv, questions: questions_R7.yaml, yoy_map: yoy_map.yaml}]
label_normalize: strip_circled_prefix          # ①②… と直後の空白を除去
scales:
  agree5:
    labels: {そう思う: 5, ややそう思う: 4, どちらともいえない: 3, あまりそう思わない: 2, そう思わない: 1}
    positive: [5, 4]; neutral: [3]; negative: [2, 1]; excluded: []   # わからない があれば excluded に
attributes:                                    # key=表示名。order=表示順
  性別:   {question: Q1, order: [男性, 女性, 答えない]}
  役職:   {question: Q2, order: [一般職, 管理職（課長級以上）]}
  年代:   {question: Q3, order: [10・20代, 30代, 40代, 50代, 60代以上]}
  勤続年数: {question: Q4, order: [3年未満, 3年以上10年未満, 10年以上20年未満, 20年以上]}
  入庁前経験: {question: Q6, order: [民間経験あり, 他自治体等（国・都道府県・市区町村）経験あり, ①②両方あり, なし]}
organization:
  levels: [部局]
  question: Q5
  units: [危機管理部, 総合政策部, 総務部, 財政部, 商工労働まちづくり部, 観光文化スポーツ部, 農林水産部, 福祉部, こども健康部, 市民環境部, 建設部, 都市整備部, 出納室, 交通部, 教育委員会, 監査委員事務局, 議会事務局, 広域市町村圏事務組合]  # xlsx の並び=表示順
indicators:
  overall_engagement: {name: 総合エンゲージメント, questions: [Q58, Q59, Q60]}
  働きがい:   {from_axis: 働きがい}
  働きやすさ: {from_axis: 働きやすさ}
segmentation: {x: overall_engagement, y: 働きやすさ, threshold: mean,
  labels: {high_high: 活躍実感型, high_low: 意欲先行型, low_high: 環境充足型, low_low: 要支援型}}
positioning: {x: 働きがい, y: 働きやすさ, bubble: n}
key_driver: {target: overall_engagement, exclude: [Q56], min_n: 30, dept_min_n: 30}
comments: {question: Q61, min_count: 5}
decks: [全庁, 部局別, 1pager]
```
`questions.yaml`（1設問1エントリ。`header` が CSV ヘッダー verbatim ＝「ヘッダー情報の保存」）
```yaml
- {id: Q7, column: 6, header: "【仕事内容について】Q7.仕事に意義や価値を感じられる", text: 仕事に意義や価値を感じられる,
   axis: 働きがい, category: 仕事内容について, role: driver, scale: agree5, reverse: false, condition: null}
- {id: Q43, ..., reverse: true}     # 業務量が周囲より多い → 逆転（CAQNAL確認事項）
- {id: Q52, ..., reverse: true}     # 評価制度を更新する必要 → 逆転（CAQNAL確認事項）
- {id: Q56, ..., condition: {attribute: 役職, equals: 管理職（課長級以上）}}
- {id: Q58, ..., role: outcome}; Q61 role: freetext; Q1-Q6 role: attribute
```
`yoy_map.yaml`: `[{current: Q55, previous: Q34, status: 参考比較, note: 文言変更}, ...]`。`status ∈ {比較可, 参考比較, 比較不可}`。自動生成分は `status: 比較可` で、削除・文言変更は下書き時に `要確認` を付ける。

`config/theme.yaml`: `font: {preset: noto}` と `presets: {noto: {family: Noto Sans JP, files: [...]}, meiryo: {family: Meiryo UI, files: []}}`、色（上記テーマ色をロール名で）、スライド寸法 `10 x 5.625 in`、`content_box: {top: 0.85, left: 0.45, right: 0.45, bottom: 0.55}`、レイアウト名マップ。

### パイプライン（3ステージ、ファイル結合）
1. `profile`  → `profile/<ファイル名>.md`（ヘッダー一覧・行列数・欠損・値分布 top20・自由記述は件数のみ）。新自治体受領時に最初に実行する。
2. `init`     → xlsx から `questions.yaml` 下書き、前年度があれば `yoy_map.yaml` 下書き。人が `reverse`/`role`/`condition` を確認して確定。
3. `validate` → 仕様書1-3（列対応1:1、尺度定義、組織紐付け、匿名性設定、経年対応の分類）。NG は具体的メッセージで停止。
4. `analyze`  → `output/intermediate/`：`long.csv`, `scores_by_scope.csv`（scope_type/scope/unit_type/unit/n/mean/pos/neu/neg/sd/norm/diff_all/diff_yoy/yoy_status/sig/suppressed）, `rankings.csv`, `drivers.csv`, `segments.csv`, `comments_themes.csv`, `judgement.csv`, `priority_orgs.csv`, `insights.json`, `summary.json`, `settings_snapshot.yaml`
5. `render`   → `output/八戸市_R8_全庁分析レポート.pptx`、`output/部局別/八戸市_R8_<部局>.pptx`（n<5 の部局は生成せず一覧に「非表示」）、`output/八戸市_R8_ExecutiveOnePager.pptx`、`output/八戸市_R8_詳細集計.xlsx`、`--pdf` で PDF
6. `verify`   → analyze+render を2回実行し SHA-256 比較（参考実装の `normalize_pptx` を移植）

### デッキ構成（`config/decks/全庁.yaml`、約45枚）
表紙 / 目次 / 0中表紙 / **T01** Executive One Page / 1中表紙 / 調査概要 / 回答者属性 / 結果の読み方（指標定義・非表示ルール） / 2中表紙 / **T02** 総合エンゲージメントKPI（3設問カード＋分布＋前年差） / **T03** カテゴリー結果（横棒＋前年差） / 軸別（働きがい×働きやすさ） / **T04** 高評価TOP10 / 低評価BOTTOM10 / **T05** 改善・悪化TOP10 / 当年×前年差4象限 / 平均×SD ばらつき / 3中表紙 / **T08** 4類型全庁構成比＋前年 / 部局別4類型100%積み上げ / 類型別ドライバー差 / 4中表紙 / **T09** 相関ランキング / 標準化β / Impact×Score（A〜Dゾーン） / 5中表紙 / **T06** 部局別総合一覧（当年・前年差・全庁差・n） / 部局×カテゴリ 全庁差ヒートマップ / 前年差ヒートマップ / **T07** 部局ポジショニング / 重点部局・好事例候補（§9-1加点表） / 6中表紙 / **T10** 属性×カテゴリ（年代・役職・勤続・性別・入庁前経験、2属性/枚） / 7中表紙 / **T11** 自由記述概況（件数・テーマ構成） / テーマ×部局 / 8中表紙 / **T14** 総括（強み・課題・主要因、ルールベース文） / 重点課題候補（§4-9 6観点シグナル表） / 9中表紙 / **T15** 施策方向性（課題テーマ→現場確認→施策候補、`action_library.yaml`） / APPENDIX中表紙 / 設問一覧 / 分析手法・注記

`部局別.yaml`（6P、仕様書6-2）: **T12** 部局サマリー / カテゴリ・設問（強みTOP3・課題TOP3） / 経年変化 / 4類型構成（課別ポジショニングは課データなしのため省略注記） / 部局Key Driver（n≥30、未満は全庁KDを参照表示） / アクション仮説（§10-2 テーマ表＋§6-3-1 WS用の問い）
`1pager.yaml`（仕様書6-1）: Overall / Strength3 / Challenge3 / Key Driver3 / Priority Org / Action3 を1枚に配置

各 builder は `(slide, ctx, spec, page_no)` シグネチャで REGISTRY 登録。`spec.scope` で全庁/部局を切替（参考実装の `org` パラメータ方式）。

### 参考実装から移植する関数（`/Users/kenshiro.takimoto/Dev/me/worker-survey-example/src/survey_report/`）
- `render/pptx_build.py: normalize_pptx()` → `render/deck.py`（そのまま）
- `render/pptx_helpers.py: set_run_font()`（`<a:ea>` 注入）, `add_table()/style_cell()` → `render/pptx_helpers.py`
- `render/charts.py: render_table()`, `wrap_cjk()`, `_save()`（metadata Software=None） → `render/charts.py`
- `analysis/significance.py` → `analysis/significance.py`（Welch t を追加）
- `cli.py: cmd_verify` の2回実行ハッシュ比較 → `cli.py`
- 直す点: ディレクトリのハードコード → `--client` でクライアントルートを解決／outcome ID・属性キー・年度の直書き → config 導出／`slide_layouts[6]` 新規作成 → テンプレート pptx 展開

### 前提（config にコメントとして明記。CAQNAL 確認事項）
- 総合エンゲージメント = Q58, Q59, Q60（仕様書3-2 標準の3指標は八戸市設問に無いため）
- 逆転項目 = Q43, Q52。Q56 は管理職回答のみ採用、Key Driver から除外
- 4類型・ポジショニングのしきい値 = 全庁平均。4類型軸 = 総合エンゲージメント × 働きやすさ軸
- 対象者数 不明 → `target_count: null` で回答率非表示
- 自由記述テーマ分類はキーワード辞書（`config/comment_themes.yaml`、§4-8-2 のテーマ一覧）による規則分類。LLM 分類は将来ステージ
- カテゴリ名は xlsx 準拠（`現在の成長について`）。CSV の `【成長実感について】` は header に verbatim 保持のみ

### 実装順序
1. リネーム・`.gitignore`・`pyproject.toml`（uv、deps: pandas numpy scipy statsmodels matplotlib python-pptx openpyxl xlsxwriter pyyaml、dev: pytest）・フォントベンダリング・`docs/*.md`・`RENAME_MEMO.md`・`local-governments/README.md`
2. `config.py` + `ingest/`（loaders, profile, question_master, yoy, validate）→ 八戸市の `client.yaml`/`questions.yaml`/`questions_R7.yaml`/`yoy_map.yaml`/`profile/` を生成し手で確定
3. `analysis/`（scoring → aggregate → ranking → significance → segmentation → drivers → comments → judgement → insights → pipeline）＋ tests（合成データ）
4. `render/`（theme → charts → pptx_helpers → slides の各 T → deck → excel → pdf）
5. `cli.py`、`verify`、八戸市で `all --pdf` 実行し PDF を目視確認、レイアウト微調整
6. 他3自治体: `init` で `questions.yaml` 下書き＋`profile/`＋`RENAME_MEMO.md`（データ未着なので analyze は不可）
7. `docs/自治体オンボーディング手順.md`、README、コミット（データ除外を `git status` で確認）

### 検証
- `uv run pytest`（合成データでスコア・抑制（n<5 → 全指標 NaN, 表示「－」）・経年マッチ・KD・レンダ smoke）
- `uv run caqnal validate --client 八戸市` が通る。R7 Q34 が「参考比較/要確認」で列挙される
- `uv run caqnal all --client 八戸市 --pdf` → 全庁 pptx/pdf、部局別 17本（監査委員事務局は非表示扱い）、1pager、詳細集計 xlsx が生成される
- 手計算照合: 全庁 Q58 の肯定率・平均、こども健康部の総合エンゲージメント、年代=30代 のカテゴリ平均 を pandas で独立計算し一致確認（仕様書11-4）
- PDF を Read ツールで数ページ画像確認（文字切れ・凡例・n数表示・ページ番号）
- `uv run caqnal verify --client 八戸市` でバイト一致
- `git status` で `data/`・`output/`・`_PII` が追跡されていないことを確認

### 実装状況（2026-09-11）
- 実装完了: リネーム・gitignore・docs / `caqnal profile|init|validate|analyze|render|all|verify` / 全庁47枚・部局別7枚×17・One Pager・分析手法解説（社内向け 22枚）・詳細集計 Excel / pytest 11件 / `verify` バイト一致
- 目次ページはテンプレ2枚目と同じレイアウト（マスター1 `TITLE_2`: 左に「目次」、右に章タイトル）を使用
- 八戸市で確認済みの出力: `local-governments/八戸市/output/`（git 管理外）
- 佐賀市: `client.yaml`（尺度=正順、総合=Q59-61、上司マネジメント満足度=Q62、属性・所属・自由記述なし）と自治体専用 `decks/全庁.yaml` まで作成。合成データで validate→analyze→render→PDF の疎通を確認済み（2026-09-13）。回答 CSV は未受領で、受領後に `init --responses` で column/header を埋めて実行する
- 十和田市・板橋区: `profile/` と `questions.yaml` 下書きまで。回答データ受領後に client.yaml を作成して analyze
- 今後の候補: LLM による示唆文生成（insights.json を入力）、課レベル対応（課長向け 2P シート）、Meiryo フォントでの PDF 検証（Windows）
