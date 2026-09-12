"""テスト用の合成回答データ（八戸市スキーマ）。seed 固定・小規模。git には入れない（data/ は ignore）。

questions*.yaml の header を CSV ヘッダーにそのまま使い、丸数字付きラベルで回答を作る。
監査委員事務局を n=2 にして匿名性抑制のテストケースにする。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).parent
CLIENT = HERE / "テスト市"

LIKERT = ["①そう思う", "②ややそう思う", "③どちらともいえない", "④あまりそう思わない", "⑤そう思わない"]
ATTR = {
    "Q1": ["①男性", "②女性", "③答えない"],
    "Q2": ["①一般職", "②管理職（課長級以上）"],
    "Q3": ["①10・20代", "②30代", "③40代", "④50代", "⑤60代以上"],
    "Q4": ["①3年未満", "②3年以上10年未満", "③10年以上20年未満", "④20年以上"],
}
ORGS = yaml.safe_load(open(CLIENT / "client.yaml", encoding="utf-8"))["organization"]["units"]


def _make(questions: list[dict], n: int, seed: int, q6_labels: list[str]) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    cols: dict[str, list] = {}
    base = rng.normal(0, 0.6, n)
    org_idx = rng.integers(0, len(ORGS) - 3, n)          # 監査委員事務局(-3), 議会(-2), 広域(-1) は後で調整
    org = [ORGS[i] for i in org_idx]
    org[:2] = [ORGS[-3]] * 2                              # 監査委員事務局 n=2
    org[2:8] = [ORGS[-2]] * 6
    org[8:15] = [ORGS[-1]] * 7
    role = rng.choice(ATTR["Q2"], n, p=[0.88, 0.12])
    for q in questions:
        qid, r = q["id"], q.get("role")
        if r == "attribute":
            if qid == "Q5":
                cols[q["header"]] = org
            elif qid == "Q2":
                cols[q["header"]] = list(role)
            elif qid == "Q6":
                cols[q["header"]] = list(rng.choice(q6_labels, n))
            else:
                cols[q["header"]] = list(rng.choice(ATTR[qid], n))
        elif r in ("outcome", "driver"):
            shift = {"Q43": -0.8, "Q49": -1.0, "Q52": -0.9}.get(qid, 0.0)
            z = base + rng.normal(0, 0.8, n) + shift + (0.4 if r == "outcome" else 0)
            idx = np.digitize(z, [-1.2, -0.4, 0.4, 1.2])   # 0..4 → ①..⑤ は逆順なので反転
            vals = [LIKERT[4 - i] for i in idx]
            if q.get("condition"):
                vals = [v if ro == "②管理職（課長級以上）" else "" for v, ro in zip(vals, role)]
            cols[q["header"]] = vals
        else:
            texts = ["人手不足で残業が多い。業務量の見直しをお願いしたい。", "上司との1on1が有意義。", "", "", "", "", "",
                     "評価制度の基準が不明確で納得感がない。", "研修の機会を増やしてほしい。", ""]
            cols[q["header"]] = list(rng.choice(texts, n))
    return pd.DataFrame(cols)


def build(out_dir: Path | None = None) -> tuple[Path, Path]:
    out_dir = out_dir or (CLIENT / "data")
    out_dir.mkdir(parents=True, exist_ok=True)
    q8 = yaml.safe_load(open(CLIENT / "questions.yaml", encoding="utf-8"))
    q7 = yaml.safe_load(open(CLIENT / "questions_R7.yaml", encoding="utf-8"))
    p8 = out_dir / "R8_回答.csv"
    p7 = out_dir / "R7_回答.csv"
    _make(q8, 320, 20260911, ["①民間経験あり", "②他自治体等（国・都道府県・市区町村）経験あり", "③ ①②両方あり", "④なし"]) \
        .to_csv(p8, index=False, encoding="utf-8-sig")
    _make(q7, 300, 20250911, ["①民間経験あり", "②他自治体等（国・都道府県・市区町村）経験あり", "③なし"]) \
        .to_csv(p7, index=False, encoding="utf-8-sig")
    return p8, p7


if __name__ == "__main__":
    print(build())
