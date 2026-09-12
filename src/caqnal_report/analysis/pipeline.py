"""解析パイプライン: 取込 → スコア化 → 集計 → ランキング → 類型 → KD → 自由記述 → 判定 → 示唆。

出力（output/intermediate/）は render ステージが読む唯一の入力。
  scores.csv, dist.csv, rankings.csv, segments.csv, segment_drivers.csv, drivers.csv,
  comments.csv, comment_themes.csv, judgement.csv, priority_orgs.csv,
  insights.json, summary.json, settings_snapshot.yaml
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .. import __version__
from ..config import ClientConfig, dump_yaml
from ..ingest.loaders import load_current, load_previous
from ..ingest.yoy import comparable_pairs
from .aggregate import aggregate, build_scopes
from .comments import analyze_comments
from .drivers import key_drivers
from .insights import build_insights
from .judgement import judge_questions, priority_orgs
from .ranking import rankings
from .scoring import score_wave
from .segmentation import segment_driver_gaps, segment_shares

CSV_TABLES = ["scores", "dist", "rankings", "segments", "segment_drivers", "drivers",
              "comments", "comment_themes", "judgement", "priority_orgs"]


@dataclass
class AnalysisResult:
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    insights: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)

    def __getitem__(self, k: str) -> pd.DataFrame:
        return self.tables[k]


def _attr_qids(cfg: ClientConfig, questions: list[dict], id_map_rev: dict[str, str] | None) -> tuple[dict, str | None]:
    """属性表示名 → この年度の設問 ID、組織設問 ID。id_map_rev: 当年度 ID → この年度 ID"""
    attr = {}
    for key, spec in cfg.attributes.items():
        qid = spec["question"]
        qid = id_map_rev.get(qid) if id_map_rev is not None else qid
        if qid:
            attr[key] = qid
    org_q = cfg.org.get("question")
    if id_map_rev is not None and org_q:
        org_q = id_map_rev.get(org_q)
    return attr, org_q


def run_analysis(cfg: ClientConfig) -> AnalysisResult:
    cur_wave = load_current(cfg)
    attr, org_q = _attr_qids(cfg, cfg.questions, None)
    cur = score_wave(cfg, cur_wave, cfg.questions, attr, org_q)

    prev = None
    yoy_pairs: dict[str, tuple[str, str]] = {}
    if cfg.prev is not None and cfg.prev.yoy_map is not None:
        yoy_pairs = comparable_pairs(cfg.prev.yoy_map)
        id_map = {p: c for c, (p, _) in yoy_pairs.items()}          # 前年度 ID → 当年度 ID
        id_map_rev = {c: p for c, (p, _) in yoy_pairs.items()}
        prev_wave = load_previous(cfg, cfg.prev)
        pattr, porg = _attr_qids(cfg, cfg.prev.questions, id_map_rev)
        prev = score_wave(cfg, prev_wave, cfg.prev.questions, pattr, porg, id_map=id_map)

    scopes = build_scopes(cfg, cur, prev)
    scores, dist = aggregate(cfg, cur, prev, yoy_pairs, scopes)
    ranks = rankings(cfg, scores)
    segments, th, seg_series = segment_shares(cfg, cur, prev, scopes)
    seg_drv = segment_driver_gaps(cfg, cur, seg_series)
    drivers, kd_meta = key_drivers(cfg, cur, scopes)
    comments, comment_themes = analyze_comments(cfg, cur_wave, cur.frame)
    judgement = judge_questions(cfg, scores, drivers, comment_themes)
    priority = priority_orgs(cfg, scores, drivers, segments, comment_themes, judgement)
    insights = build_insights(cfg, scores, judgement, drivers, segments, comment_themes, priority)

    min_n = cfg.analysis["anonymity"]["min_n"]
    org_counts = cur.frame["org"].value_counts()
    target = cfg.client.get("target_count")
    summary = {
        "client": cfg.client,
        "fiscal_year": cfg.fiscal_year,
        "previous_fiscal_year": cfg.prev.fiscal_year if cfg.prev else None,
        "previous_label": (cfg.raw["data"]["previous"][0].get("fiscal_year_label") if cfg.previous else None),
        "respondents": int(cur.frame.shape[0]),
        "respondents_prev": int(prev.frame.shape[0]) if prev is not None else None,
        "response_rate": (100 * cur.frame.shape[0] / target) if target else None,
        "question_count": len(cfg.likert_ids()),
        "question_count_total": len(cfg.questions),
        "categories": cfg.categories(),
        "axes": [a for a in cfg.axes() if a != "その他"],
        "indicators": {k: {"name": cfg.indicator_name(k), "questions": cfg.indicator_questions(k)} for k in cfg.indicators},
        "org_level": cfg.org_level,
        "organizations": [{"name": u, "n": int(org_counts.get(u, 0)), "suppressed": bool(org_counts.get(u, 0) < min_n)}
                          for u in cfg.org_units],
        "attributes": {k: {"question": v["question"],
                           "counts": {str(a): int(b) for a, b in cur.frame[k].value_counts().items()}}
                       for k, v in cfg.attributes.items()},
        "segmentation": {"x": cfg.raw["segmentation"]["x"], "y": cfg.raw["segmentation"]["y"],
                         "threshold_x": th["x"], "threshold_y": th["y"], "method": th["method"]},
        "key_driver_meta": kd_meta,
        "yoy": {"pairs": len(yoy_pairs),
                "comparable": sum(1 for v in yoy_pairs.values() if v[1] == "比較可"),
                "reference": sum(1 for v in yoy_pairs.values() if v[1] == "参考比較")},
        "anonymity": cfg.analysis["anonymity"],
        "version": __version__,
    }
    tables = {"scores": scores, "dist": dist, "rankings": ranks, "segments": segments, "segment_drivers": seg_drv,
              "drivers": drivers, "comments": comments, "comment_themes": comment_themes,
              "judgement": judgement, "priority_orgs": priority}
    return AnalysisResult(tables=tables, insights=insights, summary=summary)


def write_intermediate(res: AnalysisResult, out_dir: Path, cfg: ClientConfig | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in CSV_TABLES:
        df = res.tables.get(name, pd.DataFrame())
        df.to_csv(out_dir / f"{name}.csv", index=False, float_format="%.6f", encoding="utf-8-sig")
    (out_dir / "insights.json").write_text(json.dumps(res.insights, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(res.summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if cfg is not None:
        snap = {"client": cfg.raw, "analysis": cfg.analysis, "version": __version__}
        dump_yaml(snap, out_dir / "settings_snapshot.yaml", "# 解析時の設定値スナップショット（仕様書 §13 処理ログ・設定値）\n")


def load_intermediate(inter_dir: Path) -> AnalysisResult:
    res = AnalysisResult()
    for name in CSV_TABLES:
        p = inter_dir / f"{name}.csv"
        res.tables[name] = pd.read_csv(p, encoding="utf-8-sig") if p.exists() and p.stat().st_size > 3 else pd.DataFrame()
    for name in ("scores", "segments", "comment_themes", "priority_orgs"):
        df = res.tables[name]
        if "suppressed" in df.columns:
            df["suppressed"] = df["suppressed"].astype(bool)
    res.insights = json.loads((inter_dir / "insights.json").read_text(encoding="utf-8"))
    res.summary = json.loads((inter_dir / "summary.json").read_text(encoding="utf-8"))
    return res
