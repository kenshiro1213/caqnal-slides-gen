"""自動判定（仕様書 §3-3, §4-9, §9, §9-1）。

judgement   : 全庁 設問ごとの 6 観点シグナル（低評価 / 悪化 / 組織偏在 / 高影響 / 自由記述 / ばらつき）と
              強み判定、当年×前年差 4 象限
priority_org: 部局ごとの重点フォロー加点と好事例候補
閾値は analysis_defaults.yaml（client.yaml で上書き）。AI は候補抽出まで、確定は CAQNAL。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import ClientConfig

SIGNALS = ["low", "worsened", "org_skew", "high_impact", "comment_link", "high_sd"]
SIGNAL_LABELS = {"low": "低スコア", "worsened": "悪化", "org_skew": "組織偏在", "high_impact": "Key Driver",
                 "comment_link": "自由記述", "high_sd": "ばらつき"}


def category_theme(cfg: ClientConfig, category: str | None) -> str | None:
    return (cfg.action_library.get("category_theme_map") or {}).get(category)


def _quadrant(mean: float, dyoy: float, mean_split: float, labels: dict) -> str | None:
    if np.isnan(mean) or np.isnan(dyoy):
        return None
    hi = mean >= mean_split
    up = dyoy >= 0
    return labels["high_up" if hi and up else "low_up" if up else "high_down" if hi else "low_down"]


def judge_questions(cfg: ClientConfig, scores: pd.DataFrame, drivers: pd.DataFrame,
                    comment_themes: pd.DataFrame) -> pd.DataFrame:
    j = cfg.analysis["judgement"]
    q = scores[(scores["scope_type"] == "全庁") & (scores["unit_type"] == "設問")].copy()
    q = q.set_index("unit")
    n = len(q)
    k_top = max(1, int(np.ceil(n * j["high"]["top_pct"] / 100)))
    k_bot = max(1, int(np.ceil(n * j["low"]["bottom_pct"] / 100)))
    k_sd = max(1, int(np.ceil(n * j["high_sd_top_pct"] / 100)))
    order_mean = q["mean"].sort_values(ascending=False, kind="stable")
    top_ids = set(order_mean.index[:k_top])
    bot_ids = set(order_mean.index[-k_bot:])
    sd_ids = set(q["sd"].sort_values(ascending=False, kind="stable").index[:k_sd])

    # 組織偏在: いずれかの部局で 全庁差 ≤ -gap（抑制なし）
    org = scores[(scores["scope_type"] == cfg.org_level) & (scores["unit_type"] == "設問") & (~scores["suppressed"])]
    skew_ids = set(org[org["diff_all"] <= -j["org_gap_abs_at_least"]]["unit"])
    skew_orgs = org[org["diff_all"] <= -j["org_gap_abs_at_least"]].groupby("unit")["scope"].apply(list)

    # 高影響: 全庁 KD
    hi_ids: set[str] = set()
    if len(drivers):
        d = drivers[(drivers["scope_type"] == "全庁") & (drivers["high_impact"])]
        hi_ids = set(d["question_id"])

    # 自由記述: カテゴリ→テーマ の件数
    theme_count: dict[str, float] = {}
    if len(comment_themes):
        ct = comment_themes[(comment_themes["scope_type"] == "全庁")]
        theme_count = dict(zip(ct["theme"], ct["count"]))

    rows: list[dict] = []
    for qid, r in q.iterrows():
        qd = cfg.q(qid)
        theme = category_theme(cfg, qd.get("category"))
        comparable = r["yoy_status"] in ("比較可", "参考比較") and not np.isnan(r["diff_yoy"])
        f = {
            "high": qid in top_ids or (not np.isnan(r["pos"]) and r["pos"] >= j["high"]["positive_pct_at_least"]),
            "low": qid in bot_ids or (not np.isnan(r["neg"]) and r["neg"] >= j["low"]["negative_pct_at_least"]),
            "improved": comparable and r["diff_yoy"] >= j["improved_at_least"],
            "worsened": comparable and r["diff_yoy"] <= j["worsened_at_most"],
            "org_skew": qid in skew_ids,
            "high_impact": qid in hi_ids,
            "comment_link": bool(theme) and theme_count.get(theme, 0) >= j["comment_link_min_count"],
            "high_sd": qid in sd_ids,
        }
        signals = [s for s in SIGNALS if f[s]]
        rows.append({
            "question_id": qid, "text": qd["text"], "category": qd.get("category"), "axis": qd.get("axis"),
            "role": qd.get("role"), "theme": theme, "mean": r["mean"], "pos": r["pos"], "neg": r["neg"],
            "sd": r["sd"], "diff_yoy": r["diff_yoy"], "yoy_status": r["yoy_status"], **f,
            "signals": len(signals), "signal_labels": "・".join(SIGNAL_LABELS[s] for s in signals),
            "priority_issue": len(signals) >= j["priority_issue_min_signals"],
            "strength": f["high"] and not f["worsened"],
            "skew_orgs": "・".join(skew_orgs.get(qid, [])),
            "quadrant": _quadrant(r["mean"], r["diff_yoy"], float(q["mean"].median()),
                                  cfg.analysis["quadrant"]["labels"]),
        })
    out = pd.DataFrame(rows)
    out["_k"] = out["question_id"].map(lambda x: (len(x), x))
    return out.sort_values(["signals", "mean", "_k"], ascending=[False, True, True], kind="stable") \
              .drop(columns="_k").reset_index(drop=True)


def priority_orgs(cfg: ClientConfig, scores: pd.DataFrame, drivers: pd.DataFrame, segments: pd.DataFrame,
                  comment_themes: pd.DataFrame, judgement: pd.DataFrame) -> pd.DataFrame:
    j = cfg.analysis["judgement"]
    po = cfg.analysis["priority_org"]
    pts = po["points"]
    target = cfg.raw["key_driver"]["target"]
    lvl = cfg.org_level
    ov = scores[(scores["scope_type"] == lvl) & (scores["unit_type"] == "指標") & (scores["unit"] == target)]
    ov = ov.set_index("scope")
    valid = ov[~ov["suppressed"]]
    k_bot = max(1, int(np.ceil(len(valid) * j["low"]["bottom_pct"] / 100)))
    k_top = max(1, int(np.ceil(len(valid) * po["good_practice"]["top_pct"] / 100)))
    bottom = set(valid["mean"].sort_values(kind="stable").index[:k_bot])
    top = set(valid["mean"].sort_values(ascending=False, kind="stable").index[:k_top])

    hi_drivers = set(drivers[(drivers["scope_type"] == "全庁") & (drivers["high_impact"])]["question_id"]) if len(drivers) else set()
    qscores = scores[(scores["scope_type"] == lvl) & (scores["unit_type"] == "設問")]
    all_seg = segments[(segments["scope_type"] == "全庁") & (segments["segment"] == "low_low")]
    all_support = float(all_seg["share"].iloc[0]) if len(all_seg) else np.nan
    priority_themes = set(judgement[judgement["priority_issue"]]["theme"].dropna())

    rows: list[dict] = []
    for unit in cfg.org_units:
        if unit not in ov.index:
            continue
        r = ov.loc[unit]
        rec = {"unit": unit, "n": int(r["n"]), "suppressed": bool(r["suppressed"]), "mean": r["mean"],
               "diff_all": r["diff_all"], "diff_yoy": r["diff_yoy"], "yoy_status": r["yoy_status"]}
        if r["suppressed"]:
            rec.update({k: 0 for k in pts}, total=0, band="非表示", good_practice=False)
            rows.append(rec)
            continue
        qs = qscores[qscores["scope"] == unit].set_index("unit")
        drv_low = any((qs.loc[i, "diff_all"] <= -j["org_gap_abs_at_least"]) for i in hi_drivers if i in qs.index
                      and not np.isnan(qs.loc[i, "diff_all"]))
        seg = segments[(segments["scope_type"] == lvl) & (segments["scope"] == unit) & (segments["segment"] == "low_low")]
        support = float(seg["share"].iloc[0]) if len(seg) else np.nan
        ct = comment_themes[(comment_themes["scope_type"] == lvl) & (comment_themes["scope"] == unit)
                            & (comment_themes["theme"].isin(priority_themes))] if len(comment_themes) else pd.DataFrame()
        comment_hit = bool(len(ct)) and bool((ct["count"] >= cfg.analysis["anonymity"]["min_comments"]).any())
        p = {
            "overall_bottom_pct": pts["overall_bottom_pct"] if unit in bottom else 0,
            "worsened": pts["worsened"] if (not np.isnan(r["diff_yoy"]) and r["diff_yoy"] <= j["worsened_at_most"]) else 0,
            "org_gap_negative": pts["org_gap_negative"] if (not np.isnan(r["diff_all"]) and r["diff_all"] <= -j["org_gap_abs_at_least"]) else 0,
            "driver_low": pts["driver_low"] if drv_low else 0,
            "comment_theme": pts["comment_theme"] if comment_hit else 0,
            "support_type_gap": pts["support_type_gap"] if (not np.isnan(support) and not np.isnan(all_support)
                                                            and support - all_support >= po["support_type_gap_pt"]) else 0,
        }
        total = sum(p.values())
        band = "重点フォロー候補" if total >= po["bands"]["priority"] else "要注視" if total >= po["bands"]["watch"] else "通常"
        good = unit in top and (np.isnan(r["diff_yoy"]) or r["diff_yoy"] >= po["good_practice"]["improved_at_least"])
        rec.update(p, total=total, band=band, good_practice=bool(good), support_share=support)
        rows.append(rec)
    cols = ["unit", "n", "suppressed", "mean", "diff_all", "diff_yoy", "yoy_status", *pts, "total", "band",
            "good_practice", "support_share"]
    out = pd.DataFrame(rows, columns=cols)   # 組織設問が無い自治体では 0 行（列だけ揃える）
    return out.sort_values(["total", "mean"], ascending=[False, True], kind="stable").reset_index(drop=True)
