"""ラベル → スコア変換、逆転、条件付き設問、肯定/中立/否定分類、long 形式化（仕様書 §2, §3）。

出力の中心は 2 つ:
- long   : respondent_id, question_id, label, score, cls   （1 行 = 1 回答。未回答は行なし）
- frame  : respondent_id + 属性（表示名 key）+ org        （回答者マスタ）
そこから respondent × unit（設問/カテゴリ/軸/指標）の回答者スコア行列 R を作る。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import LIKERT_ROLES, ClientConfig
from ..ingest.loaders import WaveData

UNIT_TYPES = ("設問", "カテゴリ", "軸", "指標")


@dataclass
class UnitDef:
    unit_type: str
    unit: str
    name: str
    members: list[str]           # 設問 ID
    min_ratio: float = 0.0       # 回答者スコアを出すのに必要な回答割合


@dataclass
class Scored:
    fiscal_year: str
    long: pd.DataFrame
    frame: pd.DataFrame          # respondent_id, org, <attr keys>...
    R: pd.DataFrame              # index=respondent_id, columns=unit key（"設問:Q7" 等）
    units: list[UnitDef]
    question_ids: list[str] = field(default_factory=list)

    def unit_key(self, unit_type: str, unit: str) -> str:
        return f"{unit_type}:{unit}"


def unit_defs(cfg: ClientConfig, question_ids: list[str] | None = None) -> list[UnitDef]:
    """当年度の設問マスタから 設問 / カテゴリ / 軸 / 指標 の単位定義を作る。
    question_ids を渡すと、その ID に含まれる設問だけを members にする（前年度用）。"""
    allow = set(question_ids) if question_ids is not None else None
    ratio = cfg.analysis["score"]["indicator_min_answer_ratio"]

    def keep(ids: list[str]) -> list[str]:
        return [i for i in ids if allow is None or i in allow]

    units: list[UnitDef] = []
    for q in cfg.qs(LIKERT_ROLES):
        if allow is None or q["id"] in allow:
            units.append(UnitDef("設問", q["id"], q["text"], [q["id"]]))
    for c in cfg.categories():
        units.append(UnitDef("カテゴリ", c, c, keep(cfg.category_questions(c))))
    for a in cfg.axes():
        if a in ("その他",):
            continue
        units.append(UnitDef("軸", a, a, keep(cfg.axis_questions(a))))
    for key in cfg.indicators:
        units.append(UnitDef("指標", key, cfg.indicator_name(key), keep(cfg.indicator_questions(key)), ratio))
    return [u for u in units if u.members]


def _score_map(cfg: ClientConfig, scale_name: str) -> tuple[dict[str, float], dict[float, str], float, float]:
    sc = cfg.scale(scale_name)
    labels: dict[str, float] = {str(k): float(v) for k, v in sc["labels"].items()}
    cls: dict[float, str] = {}
    for name in ("positive", "neutral", "negative"):
        for v in sc.get(name, []):
            cls[float(v)] = name
    lo = float(sc.get("min", min(labels.values())))
    hi = float(sc.get("max", max(labels.values())))
    return labels, cls, lo, hi


def score_wave(cfg: ClientConfig, wave: WaveData, questions: list[dict],
               attr_qid: dict[str, str], org_qid: str | None,
               id_map: dict[str, str] | None = None) -> Scored:
    """WaveData → Scored。

    questions : この年度の設問マスタ
    attr_qid  : 属性表示名 → この年度の設問 ID
    org_qid   : 組織設問のこの年度の ID
    id_map    : この年度の設問 ID → 当年度の設問 ID（前年度データを当年度 ID に揃える）
    """
    df = wave.df
    id_map = id_map or {}
    qby = {q["id"]: q for q in questions}

    # ---- 回答者マスタ
    frame = pd.DataFrame({"respondent_id": df["respondent_id"].values})
    for key, qid in attr_qid.items():
        frame[key] = df[qid].values if qid in df.columns else None
    frame["org"] = df[org_qid].values if org_qid and org_qid in df.columns else None
    frame = frame.set_index("respondent_id", drop=False)

    # ---- long
    rows: list[pd.DataFrame] = []
    for q in questions:
        if q.get("role") not in LIKERT_ROLES or q["id"] not in df.columns:
            continue
        cur_id = id_map.get(q["id"], q["id"]) if id_map else q["id"]
        if id_map and q["id"] not in id_map:
            continue  # 当年度に対応が無い前年度設問は使わない
        labels, cls, lo, hi = _score_map(cfg, q.get("scale") or "agree5")
        s = df[q["id"]]
        mask = s.notna()
        if not mask.any():
            continue
        lab = s[mask].astype(str)
        score = lab.map(labels)
        unknown = lab[score.isna()].unique()
        if len(unknown):
            raise ValueError(f"{wave.fiscal_year} {q['id']}: 尺度に無いラベル {list(unknown)[:5]}")
        # 条件付き設問: 条件外の回答者は除外
        cond = q.get("condition")
        if cond:
            attr_vals = frame.loc[df.loc[mask, "respondent_id"].values, cond["attribute"]].values
            keep = attr_vals == cond["equals"]
            lab, score = lab[keep], score[keep]
            rid = df.loc[mask, "respondent_id"].values[keep]
        else:
            rid = df.loc[mask, "respondent_id"].values
        if q.get("reverse"):
            score = (lo + hi) - score
        c = score.map(cls).fillna("excluded")
        score = score.where(c != "excluded", np.nan)
        rows.append(pd.DataFrame({"respondent_id": rid, "question_id": cur_id,
                                  "label": lab.values, "score": score.values, "cls": c.values}))
    long = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["respondent_id", "question_id", "label", "score", "cls"])
    long = long.sort_values(["question_id", "respondent_id"], kind="stable").reset_index(drop=True)

    # ---- 回答者 × 単位 行列
    qids = sorted(long["question_id"].unique(), key=lambda x: (len(x), x))
    wide = long.pivot(index="respondent_id", columns="question_id", values="score")
    wide = wide.reindex(frame.index)
    units = unit_defs(cfg, qids)
    R = pd.DataFrame(index=frame.index)
    for u in units:
        cols = [m for m in u.members if m in wide.columns]
        if not cols:
            continue
        sub = wide[cols]
        if u.unit_type == "設問":
            R[f"{u.unit_type}:{u.unit}"] = sub.iloc[:, 0]
            continue
        answered = sub.notna().sum(axis=1)
        need = max(1, int(np.ceil(len(cols) * u.min_ratio))) if u.min_ratio else 1
        val = sub.mean(axis=1)
        R[f"{u.unit_type}:{u.unit}"] = val.where(answered >= need, np.nan)
    return Scored(fiscal_year=wave.fiscal_year, long=long, frame=frame, R=R, units=units, question_ids=qids)
