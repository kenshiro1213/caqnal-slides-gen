"""scope（全庁 / 部局 / 属性値）× unit（設問 / カテゴリ / 軸 / 指標）の集計（仕様書 §3, §4-1）。

- mean / sd  : 回答者スコア（設問は回答値、複合単位は構成設問の回答者平均）の平均・標準偏差
- pos/neu/neg: 構成設問の全回答をプールした肯定・中立・否定の割合（%）
- norm       : 0-100 換算
- diff_all   : 全庁差（scope − 全庁）。p_all / sig_all は Welch t（scope vs それ以外）
- diff_yoy   : 前年差。yoy_status は 比較可 / 参考比較 / 比較不可
- suppressed : n < min_n の抑制（値は NaN、表示は「－」）
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import ClientConfig
from .scoring import Scored
from .significance import sig_flag, welch_p

METRIC_COLS = ["mean", "sd", "pos", "neu", "neg", "norm", "diff_all", "p_all",
               "prev_mean", "prev_sd", "prev_pos", "diff_yoy", "diff_pos_yoy", "p_yoy"]


@dataclass
class Scope:
    scope_type: str
    scope: str
    ids: pd.Index          # respondent_id（当年度）
    prev_ids: pd.Index | None = None


def build_scopes(cfg: ClientConfig, cur: Scored, prev: Scored | None) -> list[Scope]:
    scopes = [Scope("全庁", "全庁", cur.frame.index, prev.frame.index if prev is not None else None)]
    for unit in cfg.org_units:
        ids = cur.frame.index[cur.frame["org"] == unit]
        pids = prev.frame.index[prev.frame["org"] == unit] if prev is not None else None
        scopes.append(Scope(cfg.org_level, unit, ids, pids))
    # 組織マスタに無い所属が回答にあれば末尾に加える（validate で警告済み）
    extra = sorted(set(cur.frame["org"].dropna()) - set(cfg.org_units))
    for unit in extra:
        ids = cur.frame.index[cur.frame["org"] == unit]
        pids = prev.frame.index[prev.frame["org"] == unit] if prev is not None else None
        scopes.append(Scope(cfg.org_level, unit, ids, pids))
    for key, spec in cfg.attributes.items():
        values = list(spec.get("order", []))
        values += sorted(set(cur.frame[key].dropna()) - set(values))
        for v in values:
            ids = cur.frame.index[cur.frame[key] == v]
            pids = prev.frame.index[prev.frame[key] == v] if prev is not None and key in prev.frame else None
            scopes.append(Scope(key, v, ids, pids))
    return scopes


def _scale_bounds(cfg: ClientConfig, qid: str) -> tuple[float, float]:
    sc = cfg.scale(cfg.q(qid).get("scale") or "agree5")
    vals = [float(v) for v in sc["labels"].values()]
    return float(sc.get("min", min(vals))), float(sc.get("max", max(vals)))


def _cls_shares(long: pd.DataFrame, ids: pd.Index, members: list[str]) -> tuple[float, float, float, int]:
    sub = long[long["respondent_id"].isin(ids) & long["question_id"].isin(members) & (long["cls"] != "excluded")]
    n = len(sub)
    if n == 0:
        return np.nan, np.nan, np.nan, 0
    vc = sub["cls"].value_counts()
    return (100 * vc.get("positive", 0) / n, 100 * vc.get("neutral", 0) / n, 100 * vc.get("negative", 0) / n, n)


def unit_yoy_status(members: list[str], yoy_pairs: dict[str, tuple[str, str]]) -> str:
    """構成設問の経年対応から単位の比較区分を決める。"""
    if not yoy_pairs:
        return "比較不可"
    mapped = [yoy_pairs[m][1] for m in members if m in yoy_pairs]
    if not mapped:
        return "比較不可"
    ratio = len(mapped) / len(members)
    if ratio < 0.5:
        return "比較不可"
    if ratio < 1.0 or any(s == "参考比較" for s in mapped):
        return "参考比較"
    return "比較可"


def aggregate(cfg: ClientConfig, cur: Scored, prev: Scored | None,
              yoy_pairs: dict[str, tuple[str, str]], scopes: list[Scope]) -> tuple[pd.DataFrame, pd.DataFrame]:
    min_n = cfg.analysis["anonymity"]["min_n"]
    alpha = cfg.analysis["significance"]["alpha"]
    all_ids = cur.frame.index
    rows: list[dict] = []
    dist_rows: list[dict] = []

    for u in cur.units:
        key = f"{u.unit_type}:{u.unit}"
        if key not in cur.R.columns:
            continue
        col = cur.R[key]
        lo, hi = _scale_bounds(cfg, u.members[0])
        all_vals = col.dropna()
        all_mean = float(all_vals.mean()) if len(all_vals) else np.nan
        status = unit_yoy_status(u.members, yoy_pairs) if prev is not None else "比較不可"
        prev_col = prev.R[key] if (prev is not None and key in prev.R.columns and status != "比較不可") else None
        prev_members = [m for m in u.members if m in yoy_pairs] if prev_col is not None else []

        for sc in scopes:
            vals = col.reindex(sc.ids).dropna()
            n = int(len(vals))
            r: dict = {"scope_type": sc.scope_type, "scope": sc.scope, "unit_type": u.unit_type,
                       "unit": u.unit, "unit_name": u.name, "n": n, "n_scope": int(len(sc.ids)),
                       "yoy_status": status, "suppressed": n < min_n}
            r.update({c: np.nan for c in METRIC_COLS})
            r.update(sig_all=0, sig_yoy=0, prev_n=0, n_ans=0)
            if n >= min_n:
                pos, neu, neg, n_ans = _cls_shares(cur.long, sc.ids, u.members)
                mean = float(vals.mean())
                r.update(mean=mean, sd=float(vals.std(ddof=1)) if n > 1 else np.nan, pos=pos, neu=neu, neg=neg,
                         n_ans=n_ans, norm=(mean - lo) / (hi - lo) * 100)
                if sc.scope_type != "全庁":
                    rest = col.reindex(all_ids.difference(sc.ids)).dropna()
                    r["diff_all"] = mean - all_mean
                    r["p_all"] = welch_p(vals, rest)
                    r["sig_all"] = sig_flag(r["diff_all"], r["p_all"], alpha)
                if prev_col is not None and sc.prev_ids is not None:
                    pvals = prev_col.reindex(sc.prev_ids).dropna()
                    pn = int(len(pvals))
                    r["prev_n"] = pn
                    if pn >= min_n:
                        ppos, _, _, _ = _cls_shares(prev.long, sc.prev_ids, prev_members)
                        pm = float(pvals.mean())
                        r.update(prev_mean=pm, prev_sd=float(pvals.std(ddof=1)) if pn > 1 else np.nan,
                                 prev_pos=ppos, diff_yoy=mean - pm, diff_pos_yoy=pos - ppos)
                        r["p_yoy"] = welch_p(vals, pvals)
                        r["sig_yoy"] = sig_flag(r["diff_yoy"], r["p_yoy"], alpha)
            rows.append(r)

            # 回答分布（設問のみ、全庁・組織）
            if u.unit_type == "設問" and sc.scope_type in ("全庁", cfg.org_level) and n >= min_n:
                sub = cur.long[(cur.long["question_id"] == u.unit) & cur.long["respondent_id"].isin(sc.ids)]
                sub = sub[sub["cls"] != "excluded"]
                vc = sub["score"].value_counts()
                tot = max(len(sub), 1)
                d = {"scope_type": sc.scope_type, "scope": sc.scope, "question_id": u.unit, "n": len(sub)}
                for v in range(int(lo), int(hi) + 1):
                    d[f"s{v}"] = 100 * vc.get(float(v), 0) / tot
                dist_rows.append(d)

    scores = pd.DataFrame(rows)
    dist = pd.DataFrame(dist_rows)
    return scores, dist
