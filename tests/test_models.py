"""models 工程（仕様 v2 §9, §10）。"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from caqnal_report.analysis.describe import aggregate
from caqnal_report.analysis.models import _candidate, _groups, _ols_fit_predict, _ols_model, fit_models
from caqnal_report.ingest.normalize import normalize

S = {"folds": 5, "interval": "HC3", "vif_reference": 10, "stability_reference": 0.8}


@pytest.fixture(scope="module")
def data(cfg):
    c = copy.deepcopy(cfg)
    c.raw["models"] = {"cv": {"group_level": "部局", "folds": 5}, "tree": {"max_depth": 2, "min_leaf": 40},
                       "bootstrap": {"n": 20, "seed": 0}}
    c.raw["publication"] = {"min_n": 60}
    norm = normalize(c).tables
    rs = aggregate(c, norm).tables["respondent_scores"]
    return c, rs, norm


def test_fit_models_outputs(data):
    c, rs, norm = data
    res = fit_models(c, rs, norm["respondents"], norm["org"])
    fit = res.tables["model_fit"]
    assert set(fit["model"]) == {"category", "question", "tree"}
    assert fit["candidate"].isin(["採用", "参考", "保留", "推定不能"]).all()
    ols = fit[fit.model != "tree"]
    assert (ols["df_resid"] > 0).all() and (ols["cv_groups"] > 1).all()
    nodes = res.tables["tree_nodes"]
    leaves = nodes[nodes.is_leaf]
    for oid, g in leaves.groupby("objective_id"):           # 葉の人数の合計 = 木の人数
        assert g["n"].sum() == nodes[(nodes.objective_id == oid) & (nodes.node == 0)]["n"].iloc[0]
    assert (leaves["below_min_n"] == (leaves["n"] < 60)).all()


def test_rank_deficient_is_not_estimable():
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"a": rng.normal(size=50), "b": rng.normal(size=50)})
    X["c"] = X["a"] + X["b"]                                 # 完全な共線性
    y = pd.Series(rng.normal(size=50))
    fit, coefs = _ols_model(X, y, None, S)
    assert coefs.empty and fit["rank"] < X.shape[1] + 1
    assert _candidate(fit, S)[0] == "推定不能"
    fit, coefs = _ols_model(X.iloc[:3, :2], y.iloc[:3], None, S)   # 自由度 0
    assert _candidate(fit, S)[0] == "推定不能"


def test_overfit_model_is_held():
    rng = np.random.default_rng(1)
    X = pd.DataFrame(rng.normal(size=(60, 30)), columns=[f"x{i}" for i in range(30)])
    y = pd.Series(rng.normal(size=60))                       # 説明変数と無関係
    fit, _ = _ols_model(X, y, pd.Series(np.repeat(range(6), 10)), S)
    assert fit["r2"] > 0.3                                   # 学習では当てはまって見える
    cand, reasons = _candidate(fit, S)
    assert cand == "保留" and "学習でだけ良好" in reasons[-1]


def test_cv_standardizes_on_training_side_only():
    rng = np.random.default_rng(2)
    Xtr = pd.DataFrame(rng.normal(size=(40, 3)), columns=list("abc"))
    ytr = pd.Series(Xtr @ [1.0, -2.0, 0.5] + rng.normal(size=40))
    Xte = pd.DataFrame(rng.normal(size=(5, 3)), columns=list("abc"))
    p1 = _ols_fit_predict(Xtr, ytr, Xte)
    Xte2 = Xte.copy()
    Xte2.iloc[4] = [100, 100, 100]                           # 検証側の別の行を変えても予測は変わらない
    p2 = _ols_fit_predict(Xtr, ytr, Xte2)
    assert np.allclose(p1[:4], p2[:4])


def test_groups_follow_hierarchy(cfg):
    org = pd.DataFrame({"unit_id": ["D", "S", "E"], "parent_id": [None, "D", None], "name": ["部", "課", "別部"],
                        "level": ["部局", "課", "部局"], "order": [1, 2, 3]})
    resp = pd.DataFrame({"respondent_id": ["a", "b", "c", "d"], "unit_id": ["D", "S", "E", None]})
    g = _groups(cfg, resp, org, "部局")
    assert g.tolist()[:3] == ["D", "D", "E"] and pd.isna(g.iloc[3])


def test_missing_settings_block_models_only(cfg, data):
    c, rs, norm = data
    c2 = copy.deepcopy(c)
    c2.raw["models"]["tree"]["min_leaf"] = None
    with pytest.raises(ValueError, match="min_leaf"):
        fit_models(c2, rs, norm["respondents"], norm["org"])
