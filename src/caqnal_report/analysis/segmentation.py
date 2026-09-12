"""職員 4 類型（仕様書 §4-5）。

x = エンゲージメント指標、y = 環境・支援指標 の回答者スコアを、当年度の全庁平均（or 中央値／固定値）で高低に分ける。
経年比較はしきい値を当年度で固定する（trend_threshold: current）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import ClientConfig
from .aggregate import Scope
from .scoring import Scored

QUADRANTS = ["high_high", "high_low", "low_high", "low_low"]


def _unit_key(cfg: ClientConfig, name: str) -> str:
    if name in cfg.indicators:
        return f"指標:{name}"
    if name in cfg.axes():
        return f"軸:{name}"
    if name in cfg.categories():
        return f"カテゴリ:{name}"
    raise ValueError(f"segmentation の軸 {name} は indicators / 軸 / カテゴリ のいずれにもありません")


def thresholds(cfg: ClientConfig, cur: Scored) -> dict:
    seg = cfg.raw["segmentation"]
    a = cfg.analysis["segmentation"]
    xk, yk = _unit_key(cfg, seg["x"]), _unit_key(cfg, seg["y"])
    x, y = cur.R[xk], cur.R[yk]
    if a["threshold"] == "median":
        tx, ty = float(x.median()), float(y.median())
    elif a["threshold"] == "fixed":
        tx = ty = float(a["fixed_value"])
    else:
        tx, ty = float(x.mean()), float(y.mean())
    return {"x_key": xk, "y_key": yk, "x": tx, "y": ty, "method": a["threshold"]}


def classify(R: pd.DataFrame, th: dict) -> pd.Series:
    x, y = R[th["x_key"]], R[th["y_key"]]
    hx, hy = x >= th["x"], y >= th["y"]
    seg = pd.Series(np.where(hx & hy, "high_high", np.where(hx & ~hy, "high_low",
                    np.where(~hx & hy, "low_high", "low_low"))), index=R.index, dtype=object)
    return seg.where(x.notna() & y.notna(), None)


def segment_shares(cfg: ClientConfig, cur: Scored, prev: Scored | None, scopes: list[Scope]) -> tuple[pd.DataFrame, dict, pd.Series]:
    min_n = cfg.analysis["anonymity"]["min_n"]
    th = thresholds(cfg, cur)
    seg_cur = classify(cur.R, th)
    seg_prev = None
    if prev is not None and th["x_key"] in prev.R.columns and th["y_key"] in prev.R.columns:
        seg_prev = classify(prev.R, th)
    rows: list[dict] = []
    for sc in scopes:
        s = seg_cur.reindex(sc.ids).dropna()
        n = len(s)
        sp = seg_prev.reindex(sc.prev_ids).dropna() if (seg_prev is not None and sc.prev_ids is not None) else None
        pn = len(sp) if sp is not None else 0
        for qd in QUADRANTS:
            share = 100 * (s == qd).sum() / n if n >= min_n else np.nan
            pshare = 100 * (sp == qd).sum() / pn if (sp is not None and pn >= min_n) else np.nan
            rows.append({"scope_type": sc.scope_type, "scope": sc.scope, "segment": qd,
                         "label": cfg.raw["segmentation"]["labels"][qd], "n": n, "count": int((s == qd).sum()),
                         "share": share, "prev_n": pn, "prev_share": pshare, "diff": share - pshare,
                         "suppressed": n < min_n})
    return pd.DataFrame(rows), th, seg_cur


def segment_driver_gaps(cfg: ClientConfig, cur: Scored, seg: pd.Series) -> pd.DataFrame:
    """類型別に設問平均を出し、類型間の差が大きいドライバーを見る（§4-5-2）。"""
    rows: list[dict] = []
    for q in cfg.qs(("driver",)):
        key = f"設問:{q['id']}"
        if key not in cur.R.columns:
            continue
        col = cur.R[key]
        r = {"question_id": q["id"], "text": q["text"], "category": q.get("category")}
        for qd in QUADRANTS:
            r[qd] = float(col[seg == qd].mean()) if (seg == qd).any() else np.nan
        r["gap"] = r["high_high"] - r["low_low"]
        rows.append(r)
    return pd.DataFrame(rows).sort_values("gap", ascending=False, kind="stable").reset_index(drop=True)
