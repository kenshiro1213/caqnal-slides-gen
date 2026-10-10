"""aggregate 工程: 標準データ → private 集計（仕様 v2 §5, §6, §8）。

入力は normalize の標準データだけ（元ファイルは読まない）。丸めはしない（表示時に丸める）。
出力（run/private/aggregate/）
  response_status.csv  scope_type, scope_id, scope_name, target_n, responded_n, excluded_n, rate
                       rate は対象人数が分からない区分では空（計算不能）
  stats.csv            scope_type, scope_id, scope_name, unit_type, unit_id, unit_name,
                       n, mean, sd, score, pos, neu, neg
                       scope_type: 全庁 / 組織 / 属性。unit_type: 設問 / カテゴリ / 目的指標
                       組織は配下の組織を含む（部局 = 直轄 + 各課）
  correlations.csv     objective_id, unit_type, unit_id, n, r, ci_low, ci_high（全庁、Pearson、Fisher z の 95% 区間）
  respondent_scores.csv  respondent_id × 単位の点数（models 工程の入力。private）
  aggregate_log.json   照合結果・警告・未確定
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import LIKERT_ROLES, ClientConfig

ALL = "全庁"


@dataclass
class Aggregated:
    tables: dict[str, pd.DataFrame]
    log: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 定義
def objectives(cfg: ClientConfig) -> list[dict]:
    """目的指標。v2 の objectives が無ければ outcome 設問を 1 問ずつ目的指標にする。"""
    if cfg.raw.get("objectives"):
        return [o for o in cfg.raw["objectives"] if o.get("question")]
    return [{"id": q["id"], "name": q["text"], "short": q["id"], "question": q["id"]} for q in cfg.qs(["outcome"])]


def driver_ids(cfg: ClientConfig) -> list[str]:
    obj = {o["question"] for o in objectives(cfg)}
    return [q["id"] for q in cfg.qs(["driver"]) if q["id"] not in obj]


def categories(cfg: ClientConfig) -> list[tuple[str, list[str]]]:
    """カテゴリ（設問辞書の順）と構成設問（説明設問のみ。目的指標は含めない）。"""
    drivers = set(driver_ids(cfg))
    out: list[tuple[str, list[str]]] = []
    for c in cfg.categories():
        members = [i for i in cfg.category_questions(c) if i in drivers]
        if members:
            out.append((c, members))
    return out


def scores_long(cfg: ClientConfig, responses: pd.DataFrame) -> pd.DataFrame:
    """回答値 → 点数。逆転はここで 1 回だけ適用する。"""
    v = responses[responses["value"].notna()].copy()
    rows = []
    for qid, g in v.groupby("question_id", sort=False):
        q = cfg.q(qid)
        sc = cfg.scale(q.get("scale") or "agree5")
        labels = {str(k): float(s) for k, s in sc["labels"].items()}
        lo, hi = float(sc.get("min", min(labels.values()))), float(sc.get("max", max(labels.values())))
        cls = {float(x): name for name in ("pos", "neu", "neg")
               for x in sc.get({"pos": "positive", "neu": "neutral", "neg": "negative"}[name], [])}
        s = g["value"].map(labels)
        if s.isna().any():
            raise ValueError(f"{qid}: 尺度に無い値 {g.loc[s.isna(), 'value'].unique()[:3]}")
        if q.get("reverse"):
            s = lo + hi - s          # 肯定・否定も逆転後の点数で分類する（肯定 = 望ましい側）
        rows.append(pd.DataFrame({"respondent_id": g["respondent_id"].values, "question_id": qid,
                                  "score": s.values, "cls": s.map(cls.get).values, "lo": lo, "hi": hi}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["respondent_id", "question_id", "score", "cls", "lo", "hi"])


# ---------------------------------------------------------------- スコープ
def org_members(org: pd.DataFrame, respondents: pd.DataFrame) -> dict[str, set[str]]:
    """組織 → 配下を含む回答者（対象・回答者・未照合の区別なし。呼び出し側で絞る）。"""
    children: dict[str, list[str]] = {}
    for u, p in zip(org["unit_id"], org["parent_id"]):
        if p is not None:
            children.setdefault(p, []).append(u)

    def desc(u: str) -> list[str]:
        out = [u]
        for c in children.get(u, []):
            out += desc(c)
        return out

    by_unit = respondents.groupby("unit_id")["respondent_id"].apply(set).to_dict()
    return {u: set().union(*[by_unit.get(d, set()) for d in desc(u)]) for u in org["unit_id"]}


def scopes(org: pd.DataFrame, respondents: pd.DataFrame, attrs: pd.DataFrame) -> list[tuple[str, str, str, set[str]]]:
    everyone = set(respondents["respondent_id"])
    out = [(ALL, ALL, ALL, everyone)]
    members = org_members(org, respondents)
    names = dict(zip(org["unit_id"], org["name"]))
    for u in org.sort_values("order", key=lambda s: s.astype(float), kind="stable")["unit_id"]:
        out.append(("組織", u, names[u], members[u]))
    for (dim, val), g in attrs.groupby(["dimension_id", "value"], sort=False):
        out.append(("属性", f"{dim}={val}", f"{dim}：{val}", set(g["respondent_id"])))
    return out


# ---------------------------------------------------------------- 集計
def _stat_row(scores: pd.DataFrame, lo: float, hi: float) -> dict:
    """scores: respondent ごとの点数（カテゴリは回答者平均）と分類件数。"""
    n = len(scores)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "score": None, "pos": None, "neu": None, "neg": None}
    m = float(scores["score"].mean())
    out = {"n": n, "mean": m, "sd": float(scores["score"].std(ddof=1)) if n > 1 else None,
           "score": (m - lo) / (hi - lo) * 100}
    total = scores[["pos", "neu", "neg"]].to_numpy().sum()
    for c in ("pos", "neu", "neg"):
        out[c] = float(scores[c].sum() / total) if total else None
    return out


def _fisher_ci(r: float, n: int) -> tuple[float | None, float | None]:
    if n <= 3 or r is None or abs(r) >= 1:
        return None, None
    z, se = np.arctanh(r), 1 / np.sqrt(n - 3)
    return float(np.tanh(z - 1.96 * se)), float(np.tanh(z + 1.96 * se))


def aggregate(cfg: ClientConfig, norm: dict[str, pd.DataFrame]) -> Aggregated:
    out = Aggregated(tables={})
    respondents = norm["respondents"].copy()
    respondents["responded"] = respondents["responded"].astype(str).isin(["True", "true", "1"])
    org, attrs = norm["org"], norm["attributes"]

    rule = (cfg.raw.get("aggregate") or {}).get("category_missing")
    if rule is None:
        rule = "all_answered"
        out.unresolved.append("aggregate.category_missing（カテゴリの欠損ルール）未確定のため all_answered（全構成設問に回答した職員）で集計")
    if rule not in ("all_answered", "any_answered"):
        raise ValueError(f"aggregate.category_missing は all_answered | any_answered: {rule}")

    # 集計対象: 回答した職員のうち、対象（名簿あり）または回答者（名簿なし）。未照合・対象外は含めない
    analysed = respondents[respondents["responded"] & respondents["status"].isin(["対象", "回答者"])]
    keep = set(analysed["respondent_id"])
    out.log["analysed_n"] = len(keep)
    sl = scores_long(cfg, norm["responses"])
    sl = sl[sl["respondent_id"].isin(keep)]
    for c in ("pos", "neu", "neg"):
        sl[c] = (sl["cls"] == c).astype(int)

    # 回答者 × 単位の点数
    wide = sl.pivot(index="respondent_id", columns="question_id", values="score").reindex(sorted(keep))
    bounds = sl.groupby("question_id")[["lo", "hi"]].first()
    units: list[tuple[str, str, str, list[str]]] = []
    for o in objectives(cfg):
        units.append(("目的指標", o["id"], o.get("name") or o["id"], [o["question"]]))
    for c, members in categories(cfg):
        units.append(("カテゴリ", c, c, members))
    for q in cfg.qs(LIKERT_ROLES):
        units.append(("設問", q["id"], q.get("text") or q["id"], [q["id"]]))

    rs = pd.DataFrame(index=wide.index)
    for ut, uid, _, members in units:
        cols = [m for m in members if m in wide.columns]
        if not cols:
            continue
        sub = wide[cols]
        ok = sub.notna().all(axis=1) if rule == "all_answered" else sub.notna().any(axis=1)
        rs[f"{ut}:{uid}"] = sub.mean(axis=1).where(ok)
    rs.index.name = "respondent_id"
    out.tables["respondent_scores"] = rs.reset_index()

    # 記述統計（単位ごとに 回答者 × 点数・肯定/中立/否定の件数 を作ってからスコープで切る）
    frames: dict[str, tuple[pd.DataFrame, float, float]] = {}
    for ut, uid, _, qs in units:
        key = f"{ut}:{uid}"
        if key not in rs.columns:
            continue
        col = rs[key].dropna()
        sub = sl[sl["question_id"].isin(qs) & sl["respondent_id"].isin(col.index)]
        cnt = sub.groupby("respondent_id")[["pos", "neu", "neg"]].sum()
        b = bounds.loc[[q for q in qs if q in bounds.index]]
        frames[key] = (pd.DataFrame({"score": col}).join(cnt), float(b["lo"].min()), float(b["hi"].max()))
    rows = []
    for st, sid, sname, members in scopes(org, analysed, attrs):
        ids = members & keep
        for ut, uid, uname, _ in units:
            key = f"{ut}:{uid}"
            if key not in frames:
                continue
            frame, lo, hi = frames[key]
            frame = frame[frame.index.isin(ids)]
            rows.append({"scope_type": st, "scope_id": sid, "scope_name": sname, "unit_type": ut, "unit_id": uid,
                         "unit_name": uname, **_stat_row(frame, lo, hi)})
    out.tables["stats"] = pd.DataFrame(rows)

    # 回答状況（対象人数は名簿がある場合だけ）
    has_roster = (respondents["status"] != "回答者").any() and (respondents["status"] == "対象").any()
    target_count = cfg.client.get("target_count")
    srows = []
    for st, sid, sname, members in scopes(org, respondents, attrs):
        sub = respondents[respondents["respondent_id"].isin(members)]
        target = int((sub["status"] == "対象").sum()) if has_roster else (target_count if st == ALL else None)
        responded = int((sub["responded"] & sub["status"].isin(["対象", "回答者"])).sum())
        srows.append({"scope_type": st, "scope_id": sid, "scope_name": sname, "target_n": target,
                      "responded_n": responded, "excluded_n": int((sub["status"] == "対象外").sum()),
                      "rate": responded / target if target else None})
    out.tables["response_status"] = pd.DataFrame(srows)
    if not has_roster:
        out.unresolved.append("対象者リストが無いため、所属別・属性別の回答率は計算不能")

    # 照合: 所属ごとの回答者の合計 + 所属なし = 全庁（最上位の組織で独立に数える）
    tops = org.loc[org["parent_id"].isna(), "unit_id"]
    resp_status = out.tables["response_status"].set_index(["scope_type", "scope_id"])
    top_sum = int(sum(resp_status.loc[("組織", u), "responded_n"] for u in tops))
    no_unit = int((analysed["unit_id"].isna()).sum())
    total = int(resp_status.loc[(ALL, ALL), "responded_n"])
    out.log["reconcile"] = {"最上位組織の合計": top_sum, "所属なし": no_unit, "全庁": total}
    if top_sum + no_unit != total:
        raise ValueError(f"所属別の回答者合計 {top_sum} + 所属なし {no_unit} が全庁 {total} と一致しません")
    for dim, g in attrs[attrs["respondent_id"].isin(keep)].groupby("dimension_id"):
        known = g["respondent_id"].nunique()
        if known != len(keep):
            out.warnings.append(f"属性 {dim}: 区分が無い回答者 {len(keep) - known} 名（属性別の合計は全庁より少ない）")

    # 相関（全庁）
    crows = []
    for o in objectives(cfg):
        y = rs.get(f"目的指標:{o['id']}")
        if y is None:
            continue
        cands = [("カテゴリ", c) for c, _ in categories(cfg)] + [("設問", q) for q in driver_ids(cfg)]
        for ut, uid in cands:
            x = rs.get(f"{ut}:{uid}")
            if x is None:
                continue
            pair = pd.concat([x, y], axis=1).dropna()
            n = len(pair)
            r = float(pair.iloc[:, 0].corr(pair.iloc[:, 1])) if n > 2 else None
            if r is not None and np.isnan(r):
                r = None
            lo, hi = _fisher_ci(r, n) if r is not None else (None, None)
            crows.append({"objective_id": o["id"], "unit_type": ut, "unit_id": uid, "n": n, "r": r,
                          "ci_low": lo, "ci_high": hi})
    out.tables["correlations"] = pd.DataFrame(crows)
    out.log["n_correlations"] = len(crows)
    out.log["category_missing"] = rule
    return out


def write_aggregated(res: Aggregated, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, df in res.tables.items():
        p = out_dir / f"{name}.csv"
        df.to_csv(p, index=False, encoding="utf-8", lineterminator="\n")
        paths.append(p)
    p = out_dir / "aggregate_log.json"
    p.write_text(json.dumps({"log": res.log, "warnings": res.warnings, "unresolved": res.unresolved},
                            ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return paths + [p]
