# 佐賀市_テスト版（架空データ）

佐賀市との打合せ用に、**架空の回答データ**でレポートの完成形を示すためのフォルダ。実データは一切含まない。

- `questions.yaml`: 佐賀市 R8 設問マスタ 62 問（`../佐賀市/questions.yaml` と同一）＋ 架空の属性・所属・自由記述設問 Q63〜Q69
- `make_synthetic.py`: `data/R8_回答.csv` を生成（seed 固定。部局差・カテゴリ差・役職差・自由記述を作り込んだ架空データ）
- `client.yaml`: 標準デッキ構成（全庁・部局別・1pager・手法解説）。部局名は架空

```bash
uv run python local-governments/佐賀市_テスト版/make_synthetic.py
uv run caqnal all --client 佐賀市_テスト版 --pdf
```
実データ受領後は `../佐賀市/` 側で作業する。このフォルダは打合せ後に削除してよい。
