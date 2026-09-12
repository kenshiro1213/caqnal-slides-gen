"""ランキング（仕様書 §4-2）。同率は設問 ID 順で一貫させる（§11-1）。"""
from __future__ import annotations

import pandas as pd

from ..config import ClientConfig


def _qsort_key(qid: str) -> tuple[int, str]:
    return (len(qid), qid)


def _ranked(df: pd.DataFrame, value: str, ascending: bool, top_n: int, kind: str,
            scope_type: str, scope: str) -> list[dict]:
    d = df.dropna(subset=[value]).copy()
    d["_k"] = d["unit"].map(_qsort_key)
    d = d.sort_values([value, "_k"], ascending=[ascending, True], kind="stable").head(top_n)
    return [{"kind": kind, "scope_type": scope_type, "scope": scope, "rank": i + 1, "question_id": r["unit"],
             "value": r[value], "mean": r["mean"], "pos": r["pos"], "neg": r["neg"], "diff_yoy": r["diff_yoy"],
             "diff_all": r["diff_all"], "yoy_status": r["yoy_status"]}
            for i, (_, r) in enumerate(d.iterrows())]


def rankings(cfg: ClientConfig, scores: pd.DataFrame) -> pd.DataFrame:
    top_n = cfg.analysis["ranking"]["top_n"]
    gap_n = cfg.analysis["ranking"]["org_gap_top_n"]
    q = scores[(scores["unit_type"] == "設問") & (~scores["suppressed"])]
    rows: list[dict] = []
    for (stype, scope), g in q.groupby(["scope_type", "scope"], sort=False):
        if stype not in ("全庁", cfg.org_level):
            continue
        rows += _ranked(g, "mean", False, top_n, "高評価", stype, scope)
        rows += _ranked(g, "mean", True, top_n, "低評価", stype, scope)
        comparable = g[g["yoy_status"].isin(["比較可", "参考比較"])]
        rows += _ranked(comparable, "diff_yoy", False, top_n, "改善", stype, scope)
        rows += _ranked(comparable, "diff_yoy", True, top_n, "悪化", stype, scope)
        if stype != "全庁":
            rows += _ranked(g, "diff_all", False, gap_n, "全庁比プラス", stype, scope)
            rows += _ranked(g, "diff_all", True, gap_n, "全庁比マイナス", stype, scope)
    return pd.DataFrame(rows)
