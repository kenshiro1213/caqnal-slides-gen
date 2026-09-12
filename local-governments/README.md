# local-governments/ フォルダ規約

自治体ごとに1フォルダ（日本語名）。CLI では `--client 八戸市` のようにフォルダ名で指定する。

```
<自治体名>/
├── client.yaml        自治体名・年度・データパス・尺度・属性・組織・指標定義（git管理）
├── questions.yaml     当年度 設問マスタ。CSVヘッダー verbatim を含む（git管理）
├── questions_R7.yaml  前年度 設問マスタ（経年比較する場合）
├── yoy_map.yaml       経年対応表（前年度設問 → 当年度設問、比較可/参考比較/比較不可）
├── profile/           `caqnal profile` の出力。列一覧・値分布・欠損。PIIなし（git管理）
├── RENAME_MEMO.md     受領ファイルのリネーム記録
├── data/              受領した生データ ← git 管理外
└── output/            生成物（intermediate/, charts/, pptx, pdf, xlsx） ← git 管理外
```

## data/ の命名規則
`<年度>_<種別>[_<版・日付>][_PII].<ext>`
- 年度: `R7`, `R8` …（令和）
- 種別: `回答` / `設問` / `対象者リスト` / `組織マスタ`
- 個人情報を含むファイルは `_PII` を付ける（`.gitignore` で二重に除外される。パイプラインは読まない）

## 新しい自治体を追加する手順
`docs/自治体オンボーディング手順.md` を参照。
