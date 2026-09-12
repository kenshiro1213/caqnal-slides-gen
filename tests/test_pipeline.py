import numpy as np
import pandas as pd

from caqnal_report.ingest.loaders import load_current, normalize_label


def _row(scores, stype, scope, utype, unit):
    d = scores[(scores.scope_type == stype) & (scores.scope == scope) & (scores.unit_type == utype) & (scores.unit == unit)]
    assert len(d) == 1
    return d.iloc[0]


def test_small_org_is_suppressed(results, cfg):
    s = results["scores"]
    r = _row(s, "部局", "監査委員事務局", "指標", "overall_engagement")
    assert r["suppressed"] and np.isnan(r["mean"]) and np.isnan(r["pos"]) and r["sig_all"] == 0
    seg = results["segments"]
    assert seg[(seg.scope == "監査委員事務局")]["share"].isna().all()
    assert "監査委員事務局" not in results.insights["部局"]
    pr = results["priority_orgs"]
    assert pr[pr.unit == "監査委員事務局"]["band"].iloc[0] == "非表示"


def test_overall_mean_matches_independent_calculation(results, cfg):
    """仕様書 11-4: 原データから手計算で追跡できること。"""
    raw = pd.read_csv(cfg.path(cfg.raw["data"]["responses"]), encoding="utf-8-sig", dtype=str)
    labels = {k: v for k, v in cfg.scale("agree5")["labels"].items()}
    q58 = raw.iloc[:, cfg.q("Q58")["column"]].map(lambda v: labels.get(normalize_label(v)))
    r = _row(results["scores"], "全庁", "全庁", "設問", "Q58")
    assert abs(r["mean"] - q58.mean()) < 1e-9
    assert abs(r["pos"] - 100 * (q58 >= 4).mean()) < 1e-9
    assert r["n"] == q58.notna().sum()
    # 部局 × 指標
    org = raw.iloc[:, cfg.q("Q5")["column"]].map(normalize_label)
    ids = [cfg.q(q)["column"] for q in ("Q58", "Q59", "Q60")]
    sub = raw[org == "福祉部"]
    m = pd.concat([sub.iloc[:, c].map(lambda v: labels.get(normalize_label(v))) for c in ids], axis=1).mean(axis=1).mean()
    r2 = _row(results["scores"], "部局", "福祉部", "指標", "overall_engagement")
    assert abs(r2["mean"] - m) < 1e-9


def test_yoy_uses_comparable_pairs_only(results, cfg):
    s = results["scores"]
    q17 = _row(s, "全庁", "全庁", "設問", "Q17")     # R8 新設 → 比較不可
    assert q17["yoy_status"] == "比較不可" and np.isnan(q17["diff_yoy"])
    q18 = _row(s, "全庁", "全庁", "設問", "Q18")     # R7 Q17 と比較可
    assert q18["yoy_status"] == "比較可" and not np.isnan(q18["diff_yoy"])
    q55 = _row(s, "全庁", "全庁", "設問", "Q55")     # 参考比較
    assert q55["yoy_status"] == "参考比較" and not np.isnan(q55["diff_yoy"])


def test_drivers_and_judgement_shapes(results, cfg):
    d = results["drivers"]
    allk = d[d.scope_type == "全庁"]
    assert "Q56" not in set(allk.question_id)          # 除外設定
    assert set(allk.zone) <= {"A", "B", "C", "D"}
    assert allk["beta"].notna().any()                   # n が十分なら重回帰あり
    j = results["judgement"]
    assert set(j.columns) >= {"low", "worsened", "org_skew", "high_impact", "comment_link", "high_sd", "signals", "priority_issue"}
    assert (j["signals"] == j[["low", "worsened", "org_skew", "high_impact", "comment_link", "high_sd"]].sum(axis=1)).all()


def test_rankings_are_deterministic_and_tie_broken(results):
    rk = results["rankings"]
    top = rk[(rk.kind == "高評価") & (rk.scope_type == "全庁")].sort_values("rank")
    assert list(top["rank"]) == list(range(1, len(top) + 1))
    assert top["mean"].is_monotonic_decreasing
