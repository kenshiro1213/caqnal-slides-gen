"""Key Driver 分析（仕様書 §4-4）。

- Pearson 相関（回答者スコア、ペア n ≥ min_n）
- 標準化重回帰（statsmodels OLS、n ≥ 説明変数数 × ols_min_ratio のときのみ）と VIF
- impact = 相関順位と β 順位のパーセンタイル平均。高影響 = 上位 25%
- 現状スコア高低 = ドライバー設問平均の中央値で分割 → ゾーン A/B/C/D
- 部局別は n ≥ dept_min_n のとき相関のみ
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from ..config import ClientConfig
from .aggregate import Scope
from .scoring import Scored

ZONE_LABELS = {"A": "最優先改善", "B": "重要な強み", "C": "二次課題", "D": "維持領域"}


def _driver_ids(cfg: ClientConfig) -> list[str]:
    ex = set(cfg.raw.get("key_driver", {}).get("exclude", []))
    return [q["id"] for q in cfg.qs(("driver",)) if q["id"] not in ex]


def _ols(X: pd.DataFrame, y: pd.Series) -> tuple[pd.Series, pd.Series, dict]:
    import statsmodels.api as sm
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    Z = (X - X.mean()) / X.std(ddof=1)
    zy = (y - y.mean()) / y.std(ddof=1)
    Z = Z.loc[:, Z.std(ddof=1) > 0]
    model = sm.OLS(zy.values, sm.add_constant(Z.values)).fit()
    beta = pd.Series(model.params[1:], index=Z.columns)
    vif = pd.Series([variance_inflation_factor(Z.values, i) for i in range(Z.shape[1])], index=Z.columns)
    meta = {"n": int(model.nobs), "r2": float(model.rsquared), "adj_r2": float(model.rsquared_adj),
            "k": int(Z.shape[1])}
    return beta, vif, meta


def _zone(high_impact: bool, low_score: bool) -> str:
    if high_impact:
        return "A" if low_score else "B"
    return "C" if low_score else "D"


def key_drivers(cfg: ClientConfig, cur: Scored, scopes: list[Scope]) -> tuple[pd.DataFrame, list[dict]]:
    kd = cfg.analysis["key_driver"]
    target_key = f"指標:{cfg.raw['key_driver']['target']}"
    ids = [i for i in _driver_ids(cfg) if f"設問:{i}" in cur.R.columns]
    top_pct = cfg.analysis["judgement"]["high_impact_top_pct"]
    rows: list[dict] = []
    metas: list[dict] = []

    for sc in scopes:
        if sc.scope_type not in ("全庁", cfg.org_level):
            continue
        R = cur.R.reindex(sc.ids)
        y = R[target_key]
        n_scope = int(y.notna().sum())
        is_all = sc.scope_type == "全庁"
        if not is_all and n_scope < kd["dept_min_n"]:
            continue
        X = R[[f"設問:{i}" for i in ids]]
        X.columns = ids
        rec: list[dict] = []
        for i in ids:
            pair = pd.concat([X[i], y], axis=1).dropna()
            if len(pair) < kd["min_n"] or pair[i].std(ddof=1) == 0:
                r, p = np.nan, np.nan
            else:
                r, p = stats.pearsonr(pair[i].values, pair.iloc[:, 1].values)
                r, p = float(r), float(p)
            rec.append({"scope_type": sc.scope_type, "scope": sc.scope, "question_id": i, "n": int(len(pair)),
                        "r": r, "p_r": p, "score": float(X[i].mean()) if X[i].notna().any() else np.nan})
        d = pd.DataFrame(rec)
        beta = vif = None
        if is_all and "ols" in kd["method"]:
            full = pd.concat([X, y], axis=1).dropna()
            if len(full) >= kd["ols_min_ratio"] * len(ids):
                beta, vif, meta = _ols(full[ids], full.iloc[:, -1])
                meta.update(scope_type=sc.scope_type, scope=sc.scope)
                metas.append(meta)
        d["beta"] = d["question_id"].map(beta) if beta is not None else np.nan
        d["vif"] = d["question_id"].map(vif) if vif is not None else np.nan
        d["rank_r"] = d["r"].rank(ascending=False, method="min")
        if beta is not None:
            d["rank_beta"] = d["beta"].rank(ascending=False, method="min")
            d["impact"] = (d["r"].rank(pct=True) + d["beta"].rank(pct=True)) / 2
        else:
            d["rank_beta"] = np.nan
            d["impact"] = d["r"].rank(pct=True)
        d["rank_impact"] = d["impact"].rank(ascending=False, method="min")
        d["high_impact"] = d["impact"] >= d["impact"].quantile(1 - top_pct / 100)
        split = d["score"].median() if kd["score_split"] == "median" else d["score"].mean()
        d["low_score"] = d["score"] < split
        d["zone"] = [_zone(h, l) for h, l in zip(d["high_impact"], d["low_score"])]
        d["zone_label"] = d["zone"].map(ZONE_LABELS)
        d["score_split"] = split
        d["n_target"] = n_scope
        d["_k"] = d["question_id"].map(lambda x: (len(x), x))
        d = d.sort_values(["impact", "_k"], ascending=[False, True], kind="stable").drop(columns="_k")
        rows.append(d)
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return out, metas
