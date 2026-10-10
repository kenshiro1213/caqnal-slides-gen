"""models 工程: 重回帰・未学習データでの検証・決定木と採否候補（仕様 v2 §9, §10）。

入力: aggregate の respondent_scores（private）、normalize の respondents / org。
目的指標ごとに 3 モデルを作る: カテゴリ重回帰 / 設問重回帰 / 決定木（設問の回答値で分岐）。

検証
- 予測を当てはめたい単位（models.cv.group_level の組織）で分割する群交差検証
- 前処理（標準化）は学習側だけで決める。比較基準は「学習側の平均だけで予測」
- 検証 R² は全ての検証側予測を結合して計算する。MAE は 100 点換算の点

採否は候補（採用 / 参考 / 保留 / 推定不能）と理由を出す。最終判断は担当者（models.decisions で上書き）。
係数は他の項目を同時に入れた条件での関連であり、因果効果や改善幅ではない。

出力（run/private/models/）
  model_fit.csv     objective_id, model, n, k, rank, df_resid, r2, adj_r2, cv_r2, cv_mae, base_mae, folds,
                    cv_groups, vif_max, root_stability, candidate, reasons, decision
  ols_coefs.csv     objective_id, model, term, beta, ci_low, ci_high, p, vif
  tree_nodes.csv    objective_id, node, parent, depth, side, feature, threshold, n, mean, pos, is_leaf, below_min_n
  tree_stability.csv objective_id, feature, root_freq
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import ClientConfig
from .describe import categories, driver_ids, objectives

CANDIDATES = ("採用", "参考", "保留", "推定不能")
MODEL_NAMES = {"category": "カテゴリ重回帰", "question": "設問重回帰", "tree": "決定木"}


@dataclass
class Models:
    tables: dict[str, pd.DataFrame]
    log: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def _settings(cfg: ClientConfig) -> dict:
    d = cfg.analysis.get("models_v2", {})
    m = cfg.models
    return {
        "group_level": (m.get("cv") or {}).get("group_level"),
        "folds": (m.get("cv") or {}).get("folds"),
        "interval": (m.get("ols") or {}).get("interval") or "HC3",
        "max_depth": (m.get("tree") or {}).get("max_depth"),
        "min_leaf": (m.get("tree") or {}).get("min_leaf"),
        "boot_n": (m.get("bootstrap") or {}).get("n"),
        "seed": (m.get("bootstrap") or {}).get("seed", 0) or 0,
        "vif_reference": d.get("vif_reference", 10),
        "stability_reference": d.get("stability_reference", 0.8),
        "decisions": m.get("decisions") or {},
    }


def _groups(cfg: ClientConfig, respondents: pd.DataFrame, org: pd.DataFrame, level: str | None) -> pd.Series:
    """回答者 → 検証の分割単位（group_level の組織。配下の組織の職員はその祖先へ寄せる）。"""
    parent = dict(zip(org["unit_id"], org["parent_id"]))
    lvl = dict(zip(org["unit_id"], org["level"]))

    def up(u):
        seen = set()
        while u is not None and u not in seen:
            if lvl.get(u) == level:
                return u
            seen.add(u)
            u = parent.get(u)
        return None

    return pd.Series([up(u) for u in respondents["unit_id"]], index=respondents["respondent_id"])


def _to100(y: pd.Series, lo: float, hi: float) -> pd.Series:
    return (y - lo) / (hi - lo) * 100


# ---------------------------------------------------------------- 重回帰
def _ols_fit(X: pd.DataFrame, y: pd.Series, interval: str, groups: pd.Series | None):
    import statsmodels.api as sm

    Z = (X - X.mean()) / X.std(ddof=1)
    zy = (y - y.mean()) / y.std(ddof=1)
    design = sm.add_constant(Z.values, has_constant="add")
    model = sm.OLS(zy.values, design)
    if interval == "cluster" and groups is not None and groups.notna().all():
        codes = pd.factorize(groups)[0]
        return model.fit(cov_type="cluster", cov_kwds={"groups": codes})
    return model.fit(cov_type=interval)


def _vif(X: pd.DataFrame) -> pd.Series:
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    Z = ((X - X.mean()) / X.std(ddof=1)).values
    return pd.Series([variance_inflation_factor(Z, i) for i in range(Z.shape[1])], index=X.columns)


def _cv_predict(X: pd.DataFrame, y: pd.Series, groups: pd.Series | None, folds: int, fit_predict) -> dict:
    """群交差検証。fit_predict(Xtr, ytr, Xte) → 予測。基準は学習側の平均。"""
    from sklearn.model_selection import GroupKFold, KFold

    if groups is not None:
        n_groups = groups.nunique()
        k = min(folds, n_groups)
        splitter = GroupKFold(n_splits=k).split(X, y, groups)
    else:
        n_groups, k = None, folds
        splitter = KFold(n_splits=k, shuffle=True, random_state=0).split(X, y)
    pred = pd.Series(np.nan, index=y.index)
    base = pd.Series(np.nan, index=y.index)
    for tr, te in splitter:
        Xtr, ytr, Xte = X.iloc[tr], y.iloc[tr], X.iloc[te]
        pred.iloc[te] = fit_predict(Xtr, ytr, Xte)
        base.iloc[te] = float(ytr.mean())
    sst = float(((y - y.mean()) ** 2).sum())
    return {"cv_r2": 1 - float(((y - pred) ** 2).sum()) / sst if sst else None,
            "cv_mae": float((y - pred).abs().mean()), "base_mae": float((y - base).abs().mean()),
            "folds": k, "cv_groups": n_groups}


def _ols_fit_predict(Xtr: pd.DataFrame, ytr: pd.Series, Xte: pd.DataFrame) -> np.ndarray:
    """学習側だけで標準化して推定し、検証側に同じ変換を当てて予測する。"""
    mu, sd = Xtr.mean(), Xtr.std(ddof=1).replace(0, 1)
    A = np.column_stack([np.ones(len(Xtr)), ((Xtr - mu) / sd).values])
    coef, *_ = np.linalg.lstsq(A, ytr.values, rcond=None)
    return np.column_stack([np.ones(len(Xte)), ((Xte - mu) / sd).values]) @ coef


def _ols_model(X: pd.DataFrame, y: pd.Series, groups: pd.Series | None, s: dict) -> tuple[dict, pd.DataFrame]:
    n, k = X.shape
    A = np.column_stack([np.ones(n), X.values])
    rank = int(np.linalg.matrix_rank(A))
    fit = {"n": n, "k": k, "rank": rank, "df_resid": n - rank}
    const = [c for c in X.columns if X[c].std(ddof=1) == 0]
    if rank < k + 1 or n - rank <= 0 or const:
        fit["reasons"] = ["rank または自由度が不足"] + ([f"変動のない項目 {const}"] if const else [])
        return fit, pd.DataFrame()
    res = _ols_fit(X, y, s["interval"], groups)
    ci = res.conf_int()
    vif = _vif(X)
    coefs = pd.DataFrame({"term": X.columns, "beta": res.params[1:], "ci_low": ci[1:, 0], "ci_high": ci[1:, 1],
                          "p": res.pvalues[1:], "vif": vif.values})
    fit.update({"r2": float(res.rsquared), "adj_r2": float(res.rsquared_adj), "vif_max": float(vif.max())})
    fit.update(_cv_predict(X, y, groups, s["folds"], _ols_fit_predict))
    return fit, coefs


# ---------------------------------------------------------------- 決定木
def _tree(X: pd.DataFrame, y: pd.Series, pos: pd.Series, s: dict):
    from sklearn.tree import DecisionTreeRegressor

    return DecisionTreeRegressor(max_depth=s["max_depth"], min_samples_leaf=s["min_leaf"], random_state=0).fit(X, y)


def _tree_nodes(model, X: pd.DataFrame, y: pd.Series, pos: pd.Series, min_n: int | None) -> pd.DataFrame:
    t = model.tree_
    leaf = model.apply(X)
    path = model.decision_path(X)
    rows = []
    parent = {}
    for i in range(t.node_count):
        for side, c in (("le", t.children_left[i]), ("gt", t.children_right[i])):
            if c != -1:
                parent[c] = (i, side)
    depth = {0: 0}
    for i in range(t.node_count):
        if i in parent:
            depth[i] = depth[parent[i][0]] + 1
        mask = path[:, i].toarray().ravel().astype(bool)
        is_leaf = t.children_left[i] == -1
        n = int(mask.sum())
        rows.append({"node": i, "parent": parent.get(i, (None,))[0], "depth": depth[i],
                     "side": parent.get(i, (None, None))[1],
                     "feature": None if is_leaf else X.columns[t.feature[i]],
                     "threshold": None if is_leaf else float(t.threshold[i]),
                     "n": n, "mean": float(y[mask].mean()), "pos": float(pos[mask].mean()) if n else None,
                     "is_leaf": bool(is_leaf), "below_min_n": bool(min_n is not None and n < min_n)})
    assert (np.bincount(leaf, minlength=t.node_count) > 0).sum() == sum(r["is_leaf"] for r in rows)
    return pd.DataFrame(rows)


def _root_stability(X: pd.DataFrame, y: pd.Series, groups: pd.Series | None, s: dict) -> pd.Series:
    """群（無ければ回答者）を復元抽出し、根元の分岐に選ばれる項目の頻度を数える。"""
    from sklearn.tree import DecisionTreeRegressor

    rng = np.random.default_rng(s["seed"])
    counts: dict[str, int] = {}
    units = groups.dropna().unique() if groups is not None else None
    for _ in range(s["boot_n"]):
        if units is not None:
            pick = rng.choice(units, size=len(units), replace=True)
            idx = np.concatenate([np.flatnonzero(groups.values == g) for g in pick])
        else:
            idx = rng.integers(0, len(y), len(y))
        m = DecisionTreeRegressor(max_depth=1, min_samples_leaf=s["min_leaf"], random_state=0)
        m.fit(X.iloc[idx], y.iloc[idx])
        if m.tree_.node_count > 1:
            f = X.columns[m.tree_.feature[0]]
            counts[f] = counts.get(f, 0) + 1
    return (pd.Series(counts, dtype=float) / s["boot_n"]).sort_values(ascending=False)


# ---------------------------------------------------------------- 採否候補
def _candidate(fit: dict, s: dict) -> tuple[str, list[str]]:
    reasons = list(fit.get("reasons", []))
    if fit.get("r2") is None:
        return "推定不能", reasons or ["推定できない"]
    if fit.get("cv_r2") is None or fit["cv_mae"] >= fit["base_mae"]:
        return "保留", reasons + ["未学習データで平均だけの予測より誤差が小さくならない（学習でだけ良好の可能性）"]
    if fit["cv_r2"] <= 0:
        return "保留", reasons + ["検証 R² が 0 以下"]
    if fit.get("vif_max") is not None and fit["vif_max"] > s["vif_reference"]:
        reasons.append(f"VIF 最大 {fit['vif_max']:.1f}（共線性が強く個々の係数は不安定）")
        return "参考", reasons
    if fit.get("root_stability") is not None and fit["root_stability"] < s["stability_reference"]:
        reasons.append(f"再抽出で根元の項目が変わる（最頻 {fit['root_stability']:.0%}）")
        return "参考", reasons
    if fit.get("cv_groups") is not None and fit["cv_groups"] < fit.get("folds", 0):
        reasons.append("検証の群数が分割数より少ない")
        return "参考", reasons
    return "採用", reasons


# ---------------------------------------------------------------- 本体
def fit_models(cfg: ClientConfig, rs: pd.DataFrame, respondents: pd.DataFrame, org: pd.DataFrame) -> Models:
    out = Models(tables={})
    s = _settings(cfg)
    for key in ("folds", "max_depth", "min_leaf"):
        if s[key] is None:
            raise ValueError(f"models の {key} が未確定のため、モデルを推定しません（記述統計はそのまま使えます）")
    rs = rs.set_index("respondent_id")
    groups_all = None
    if s["group_level"]:
        groups_all = _groups(cfg, respondents, org, s["group_level"]).reindex(rs.index)
    else:
        out.unresolved.append("models.cv.group_level 未確定のため、回答者単位の分割で検証（未観測の組織への当てはまりは未検証）")
    min_n = cfg.publication.get("min_n")

    fits, coefs, nodes, stab = [], [], [], []
    for o in objectives(cfg):
        ykey = f"目的指標:{o['id']}"
        if ykey not in rs.columns:
            continue
        q = cfg.q(o["question"])
        sc = cfg.scale(q.get("scale") or "agree5")
        lo, hi = float(sc.get("min", 1)), float(sc.get("max", 5))
        pos_vals = set(map(float, sc.get("positive", [])))
        conditional = [i for i in driver_ids(cfg) if cfg.q(i).get("condition")]
        specs = {
            "category": [f"カテゴリ:{c}" for c, _ in categories(cfg)],
            "question": [f"設問:{i}" for i in driver_ids(cfg) if i not in conditional],
        }
        specs["tree"] = specs["question"]
        for model, cols in specs.items():
            cols = [c for c in cols if c in rs.columns]
            data = rs[cols + [ykey]].dropna()
            X = data[cols].rename(columns=lambda c: c.split(":", 1)[1])
            y = _to100(data[ykey], lo, hi)
            groups = groups_all.reindex(data.index) if groups_all is not None else None
            if groups is not None and groups.isna().any():
                keep = groups.notna()
                out.warnings.append(f"{o['id']} {MODEL_NAMES[model]}: 検証単位が決まらない {int((~keep).sum())} 名をモデルから除外")
                X, y, groups, data = X[keep], y[keep], groups[keep], data[keep]
            row = {"objective_id": o["id"], "model": model}
            if model == "tree":
                if len(y) < 2 * s["min_leaf"]:
                    fit = {"n": len(y), "k": X.shape[1], "reasons": ["人数が葉の最低人数の 2 倍に満たない"]}
                else:
                    pos = data[ykey].isin(pos_vals).astype(float)
                    m = _tree(X, y, pos, s)
                    nd = _tree_nodes(m, X, y, pos, min_n)
                    nodes.append(nd.assign(objective_id=o["id"]))
                    pred_r2 = m.score(X, y)
                    fit = {"n": len(y), "k": X.shape[1], "r2": float(pred_r2)}
                    fit.update(_cv_predict(X, y, groups, s["folds"],
                                           lambda a, b, c: _tree(a, b, None, s).predict(c)))
                    if s["boot_n"]:
                        st = _root_stability(X, y, groups, s)
                        stab.append(pd.DataFrame({"objective_id": o["id"], "feature": st.index, "root_freq": st.values}))
                        fit["root_stability"] = float(st.iloc[0]) if len(st) else 0.0
                    if nd["below_min_n"].any():
                        fit.setdefault("reasons", []).append("公開最低人数に満たない葉がある（非表示か集約）")
            else:
                fit, cf = _ols_model(X, y, groups, s)
                if not cf.empty:
                    coefs.append(cf.assign(objective_id=o["id"], model=model))
            cand, reasons = _candidate(fit, s)
            decision = (s["decisions"].get(o["id"]) or {}).get(model)
            row.update({k: v for k, v in fit.items() if k != "reasons"})
            row.update({"candidate": cand, "reasons": " / ".join(reasons), "decision": decision})
            fits.append(row)
    conditional = [i for i in driver_ids(cfg) if cfg.q(i).get("condition")]
    if conditional:
        out.log["excluded_conditional"] = conditional
        out.warnings.append(f"条件付き設問 {conditional} は回答者が限られるため設問モデル・決定木の説明変数から除外")
    if not s["boot_n"]:
        out.unresolved.append("models.bootstrap.n 未確定のため、決定木の再抽出による安定性を確認していない")
    cols_fit = ["objective_id", "model", "n", "k", "rank", "df_resid", "r2", "adj_r2", "cv_r2", "cv_mae", "base_mae",
                "folds", "cv_groups", "vif_max", "root_stability", "candidate", "reasons", "decision"]
    out.tables["model_fit"] = pd.DataFrame(fits).reindex(columns=cols_fit)
    out.tables["ols_coefs"] = (pd.concat(coefs, ignore_index=True) if coefs else pd.DataFrame())
    if not out.tables["ols_coefs"].empty:
        out.tables["ols_coefs"] = out.tables["ols_coefs"][["objective_id", "model", "term", "beta", "ci_low", "ci_high",
                                                           "p", "vif"]]
    out.tables["tree_nodes"] = pd.concat(nodes, ignore_index=True) if nodes else pd.DataFrame()
    out.tables["tree_stability"] = pd.concat(stab, ignore_index=True) if stab else pd.DataFrame()
    out.log["settings"] = {k: v for k, v in s.items() if k != "decisions"}
    out.log["interval"] = f"{s['interval']}（標準化係数 β の 95% 区間）"
    return out


def write_models(res: Models, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, df in res.tables.items():
        p = out_dir / f"{name}.csv"
        df.to_csv(p, index=False, encoding="utf-8", lineterminator="\n")
        paths.append(p)
    p = out_dir / "models_log.json"
    p.write_text(json.dumps({"log": res.log, "warnings": res.warnings, "unresolved": res.unresolved},
                            ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return paths + [p]
