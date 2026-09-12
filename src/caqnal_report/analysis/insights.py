"""示唆の入力 JSON（仕様書 §7-1）とルールベース文章（§7-2, §7-3）。

数値・抽出は全て判定済みの結果から取る。文章は「事実 → 特徴 → 要因候補 → 示唆 → アクション方向性」の
テンプレートで、比較対象明記・因果断定禁止・「検討が考えられる」表現に従う。
LLM を後付けする場合は insights.json をそのまま入力にする。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..config import ClientConfig


def _f(v, nd=2, signed=False) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "－"
    return f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}"


def _pct(v) -> str:
    return "－" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.0f}%"


def _row(scores: pd.DataFrame, stype: str, scope: str, utype: str, unit: str) -> pd.Series | None:
    d = scores[(scores["scope_type"] == stype) & (scores["scope"] == scope) & (scores["unit_type"] == utype)
               & (scores["unit"] == unit)]
    return d.iloc[0] if len(d) else None


def _qitems(cfg: ClientConfig, df: pd.DataFrame, n: int) -> list[dict]:
    out = []
    for _, r in df.head(n).iterrows():
        q = cfg.q(r["unit"])
        out.append({"question_id": r["unit"], "text": q["text"], "category": q.get("category"),
                    "mean": _nan(r["mean"]), "pos": _nan(r["pos"]), "neg": _nan(r["neg"]),
                    "diff_all": _nan(r.get("diff_all")), "diff_yoy": _nan(r.get("diff_yoy")),
                    "yoy_status": r.get("yoy_status")})
    return out


def _nan(v):
    if v is None:
        return None
    try:
        return None if math.isnan(float(v)) else round(float(v), 3)
    except (TypeError, ValueError):
        return v


def scope_insight(cfg: ClientConfig, stype: str, scope: str, scores: pd.DataFrame, judgement: pd.DataFrame,
                  drivers: pd.DataFrame, segments: pd.DataFrame, comment_themes: pd.DataFrame,
                  priority: pd.DataFrame) -> dict | None:
    target = cfg.raw["key_driver"]["target"]
    ov = _row(scores, stype, scope, "指標", target)
    if ov is None or bool(ov["suppressed"]):
        return None
    is_all = stype == "全庁"
    qs = scores[(scores["scope_type"] == stype) & (scores["scope"] == scope) & (scores["unit_type"] == "設問")
                & (~scores["suppressed"])].copy()
    qs["_k"] = qs["unit"].map(lambda x: (len(x), x))
    if is_all:
        st = judgement[judgement["strength"]]
        strengths = qs[qs["unit"].isin(st["question_id"])].sort_values(["mean", "_k"], ascending=[False, True], kind="stable")
        iss = judgement[judgement["signals"] >= 1].sort_values(["signals", "mean"], ascending=[False, True], kind="stable")
        issues = qs.set_index("unit").reindex(iss["question_id"].values).dropna(subset=["mean"]).rename_axis("unit").reset_index()
    else:
        strengths = qs.sort_values(["diff_all", "_k"], ascending=[False, True], kind="stable")
        strengths = strengths[strengths["diff_all"] > 0]
        issues = qs.sort_values(["diff_all", "_k"], ascending=[True, True], kind="stable")
        issues = issues[issues["diff_all"] < 0]
    kd = drivers[(drivers["scope_type"] == stype) & (drivers["scope"] == scope)] if len(drivers) else pd.DataFrame()
    kd_src = stype
    if not len(kd) and len(drivers):
        kd = drivers[drivers["scope_type"] == "全庁"]
        kd_src = "全庁"
    key_drivers = [{"question_id": r["question_id"], "text": cfg.q(r["question_id"])["text"], "r": _nan(r["r"]),
                    "beta": _nan(r["beta"]), "score": _nan(r["score"]), "zone": r["zone"], "zone_label": r["zone_label"]}
                   for _, r in kd.head(5).iterrows()]
    seg = segments[(segments["scope_type"] == stype) & (segments["scope"] == scope)]
    seg_mix = {r["label"]: _nan(r["share"]) for _, r in seg.iterrows()}
    seg_all = {r["label"]: _nan(r["share"]) for _, r in
               segments[(segments["scope_type"] == "全庁")].iterrows()}
    ct = comment_themes[(comment_themes["scope_type"] == stype) & (comment_themes["scope"] == scope)
                        & (~comment_themes["theme"].str.startswith("トーン:"))] if len(comment_themes) else pd.DataFrame()
    themes = [{"theme": r["theme"], "count": _nan(r["count"]), "share": _nan(r["share"])}
              for _, r in ct.sort_values("count", ascending=False, kind="stable").head(5).iterrows()] if len(ct) else []
    flags = judgement[judgement["priority_issue"]]["question_id"].tolist() if is_all else []
    pr = priority[priority["unit"] == scope] if (not is_all and len(priority)) else pd.DataFrame()

    ins = {
        "scope_type": stype, "scope": scope, "n": int(ov["n"]),
        "overall_score": _nan(ov["mean"]), "overall_pos": _nan(ov["pos"]),
        "yoy_diff": _nan(ov["diff_yoy"]), "yoy_status": ov["yoy_status"], "org_diff": _nan(ov.get("diff_all")),
        "sig_yoy": int(ov["sig_yoy"]), "sig_all": int(ov["sig_all"]),
        "top_strengths": _qitems(cfg, strengths, 3), "top_issues": _qitems(cfg, issues, 3),
        "key_drivers": key_drivers, "key_driver_source": kd_src,
        "segment_mix": seg_mix, "segment_mix_all": seg_all, "comment_themes": themes,
        "evidence_flags": flags,
        "priority_band": pr["band"].iloc[0] if len(pr) else None,
        "priority_points": int(pr["total"].iloc[0]) if len(pr) else None,
    }
    ins["sentences"] = sentences(cfg, ins)
    return ins


def sentences(cfg: ClientConfig, ins: dict) -> dict:
    name = cfg.indicator_name(cfg.raw["key_driver"]["target"])
    label = cfg.client["name"] + "全体" if ins["scope_type"] == "全庁" else ins["scope"]
    prev = cfg.prev.fiscal_year if cfg.prev else None
    lab = cfg.raw["segmentation"]["labels"]
    # ① 事実
    fact = f"{label}の{name}は {_f(ins['overall_score'])}（肯定率 {_pct(ins['overall_pos'])}、n={ins['n']}）。"
    if ins["yoy_diff"] is not None:
        ref = "（参考値）" if ins["yoy_status"] == "参考比較" else ""
        sig = "。統計的に有意な差" if ins["sig_yoy"] else ""
        fact += f"前年差 {_f(ins['yoy_diff'], signed=True)}{ref}{sig}。"
    if ins["org_diff"] is not None:
        sig = "（有意）" if ins["sig_all"] else ""
        fact += f"全庁平均比 {_f(ins['org_diff'], signed=True)}{sig}。"
    # ② 特徴
    feat = ""
    if ins["top_strengths"]:
        s = ins["top_strengths"][0]
        ref = f"全庁平均比 {_f(s['diff_all'], signed=True)}" if s["diff_all"] is not None else f"平均 {_f(s['mean'])}"
        feat += f"相対的に高いのは「{s['text']}」（{ref}）。"
    if ins["top_issues"]:
        i = ins["top_issues"][0]
        ref = f"全庁平均比 {_f(i['diff_all'], signed=True)}" if i["diff_all"] is not None else f"平均 {_f(i['mean'])}、否定率 {_pct(i['neg'])}"
        feat += f"相対的に低いのは「{i['text']}」（{ref}）。"
    if ins["segment_mix"]:
        low = ins["segment_mix"].get(lab["low_low"])
        low_all = ins["segment_mix_all"].get(lab["low_low"])
        if low is not None:
            feat += f"{lab['low_low']}の構成比は {_pct(low)}"
            if ins["scope_type"] != "全庁" and low_all is not None:
                feat += f"（全庁 {_pct(low_all)}）"
            feat += "。"
    # ③ 要因候補
    cause = ""
    a_zone = [k for k in ins["key_drivers"] if k["zone"] == "A"]
    if a_zone:
        k = a_zone[0]
        src = "部局内" if ins["key_driver_source"] != "全庁" else "全庁"
        cause = (f"{src}の Key Driver 分析では「{k['text']}」が{name}との関連が強く（r={_f(k['r'])}）、"
                 f"現状スコアも低い（{_f(k['score'])}）ため、改善優先度が高い要因候補である。")
    elif ins["key_drivers"]:
        k = ins["key_drivers"][0]
        cause = f"「{k['text']}」は{name}との関連が最も強い（r={_f(k['r'])}）。因果関係の証明ではない。"
    if ins["comment_themes"]:
        t = ins["comment_themes"][0]
        if t["theme"] != "その他" and t["count"]:
            cause += f"自由記述では「{t['theme']}」に関する言及が最多（{int(t['count'])}件）。"
    # ④ 示唆
    theme = None
    if ins["top_issues"]:
        theme = (cfg.action_library.get("category_theme_map") or {}).get(ins["top_issues"][0]["category"])
    lib = (cfg.action_library.get("themes") or {}).get(theme or "", {})
    hint = ""
    if lib:
        hint = f"管理職との対話では、{'・'.join(lib.get('check', [])[:3])} の実態を確認することが望ましい。"
    # ⑤ アクション方向性
    action = ""
    if lib:
        action = f"「{theme}」については {'・'.join(lib.get('actions', [])[:3])} などの検討が考えられる。"
    return {"fact": fact, "feature": feat, "cause": cause, "hint": hint, "action": action, "theme": theme}


def build_insights(cfg: ClientConfig, scores, judgement, drivers, segments, comment_themes, priority) -> dict:
    out: dict = {"全庁": scope_insight(cfg, "全庁", "全庁", scores, judgement, drivers, segments, comment_themes, priority),
                 cfg.org_level: {}}
    for unit in cfg.org_units:
        ins = scope_insight(cfg, cfg.org_level, unit, scores, judgement, drivers, segments, comment_themes, priority)
        if ins is not None:
            out[cfg.org_level][unit] = ins
    return out
