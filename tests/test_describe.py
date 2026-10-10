"""aggregate 工程（仕様 v2 §5, §6, §8）。"""
from __future__ import annotations

import copy

import pandas as pd
import pytest

from caqnal_report.analysis.describe import aggregate, org_members, scores_long
from caqnal_report.ingest.normalize import normalize


@pytest.fixture(scope="module")
def norm(cfg):
    return normalize(cfg).tables


@pytest.fixture(scope="module")
def agg(cfg, norm):
    return aggregate(cfg, norm)


def test_reconciles_and_counts(agg, norm):
    rec = agg.log["reconcile"]
    assert rec["最上位組織の合計"] + rec["所属なし"] == rec["全庁"] == len(norm["respondents"])
    st = agg.tables["stats"]
    q = st[(st.scope_type == "全庁") & (st.unit_type == "設問")]
    assert ((q["pos"] + q["neu"] + q["neg"]).round(9) == 1).all()
    assert ((q["score"] >= 0) & (q["score"] <= 100)).all()


def test_category_uses_complete_respondents(cfg, agg):
    rs = agg.tables["respondent_scores"].set_index("respondent_id")
    cat = next(c for c in rs.columns if c.startswith("カテゴリ:"))
    st = agg.tables["stats"]
    row = st[(st.scope_type == "全庁") & (st.unit_type == "カテゴリ") & (st.unit_id == cat.split(":", 1)[1])].iloc[0]
    assert row["n"] == rs[cat].notna().sum()
    assert row["mean"] == pytest.approx(rs[cat].mean())
    assert "category_missing" in " ".join(agg.unresolved)        # 未設定は未確定として残す


def test_reverse_applied_once(cfg, norm):
    c = copy.deepcopy(cfg)
    qid = c.qs(["driver"])[0]["id"]
    base = scores_long(c, norm["responses"]).query("question_id == @qid").set_index("respondent_id")["score"]
    c.q(qid)["reverse"] = True
    rev = scores_long(c, norm["responses"]).query("question_id == @qid").set_index("respondent_id")["score"]
    assert (base + rev == 6).all()


def test_four_point_scale_has_no_neutral(cfg):
    c = copy.deepcopy(cfg)
    c.raw["scales"]["agree4"] = {"labels": {"そう思う": 4, "ややそう思う": 3, "あまりそう思わない": 2, "そう思わない": 1},
                                 "positive": [4, 3], "neutral": [], "negative": [2, 1], "min": 1, "max": 4}
    qid = c.likert_ids()[0]
    c.q(qid)["scale"] = "agree4"
    resp = pd.DataFrame({"respondent_id": ["a", "b", "c"], "question_id": qid,
                         "value": ["そう思う", "ややそう思う", "あまりそう思わない"], "missing_reason": None})
    sl = scores_long(c, resp)
    assert list(sl["cls"]) == ["pos", "pos", "neg"]
    assert list(sl["score"]) == [4, 3, 2]


def test_parent_unit_includes_children():
    org = pd.DataFrame({"unit_id": ["D", "S1", "S2", "E"], "parent_id": [None, "D", "D", None],
                        "name": ["部", "課1", "課2", "別部"], "level": ["部局", "課", "課", "部局"], "order": [1, 2, 3, 4]})
    resp = pd.DataFrame({"respondent_id": ["a", "b", "c", "d", "e"], "unit_id": ["D", "S1", "S2", "S2", "E"]})
    m = org_members(org, resp)
    assert m["D"] == {"a", "b", "c", "d"}                     # 部局直轄 + 各課
    assert m["S2"] == {"c", "d"} and m["E"] == {"e"}


def test_correlations_exclude_objectives_from_drivers(cfg, agg):
    corr = agg.tables["correlations"]
    outcome = {q["id"] for q in cfg.qs(["outcome"])}
    assert not set(corr.loc[corr.unit_type == "設問", "unit_id"]) & outcome
    assert corr["r"].between(-1, 1).all()
    assert (corr["ci_low"] <= corr["r"]).all() and (corr["r"] <= corr["ci_high"]).all()


def test_response_rate_unknown_without_roster(agg):
    rs = agg.tables["response_status"]
    assert rs.loc[rs.scope_type == "組織", "rate"].isna().all()   # 計算不能（0% にしない）
    assert rs.loc[rs.scope_type == "属性", "rate"].isna().all()
