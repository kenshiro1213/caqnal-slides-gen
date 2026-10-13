"""compose 工程: 公開データ＋担当者の解釈 → 読者別の文章（仕様 v2 §11, §12, §15）。

private の回答は読まない。入力は run/public/<読者>/ と local-governments/<自治体>/interpretation.yaml。
自動で作るのは「観測事実」（点数・相関・有効 n）の下書きだけ。示唆・方針・施策は担当者が書く。

interpretation.yaml（任意。無ければ該当頁は「未記入」の枠になる）
  insights:        {頁キー: 示唆の本文}            例: indicator_distribution, "kd_question:<目的指標ID>"
  summary_state:   [{theme, fact, insight, direction}]
  comments:        {writers, themes: [{name, writers}], insight, message}   人が確認した要約だけ
  policy:          {title, message, pillars: [{title, text}], insight, note}
  measures:        [{title, message, evidence, actions, owners, trial, note}]
  kpis:            [{area, practice, judge, survey}]
  sources:         [{name, revised, pages}]
  status:          draft（AI・担当者の下書き。未確認） | confirmed（担当者が確認済み）。無ければ draft 扱い
  confirmed_by / confirmed_on: 確認者・確認日（status: confirmed のとき）
  unit_readers:    部局長・課長向けの共通の文章（上と同じ構造）。上の全体向けの文章は全庁・人事の読者にだけ使う
  readers:         {読者ID: 上と同じ構造（その読者だけ上書き）}
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pandas as pd

from ..config import ClientConfig, deep_merge, load_yaml
from .describe import categories
from .publish import read_public

ALL = "全庁"


def facts(cfg: ClientConfig, tables: dict[str, pd.DataFrame]) -> list[dict]:
    """点数の低いカテゴリ 3 つについて、点数・目的指標との相関・有効 n を事実として並べる。"""
    st = tables["stats"]
    cat = st[(st.scope_type == ALL) & (st.unit_type == "カテゴリ") & st.score.notna()]
    cat = cat[cat.unit_id.isin([c for c, _ in categories(cfg)])].sort_values("score")
    corr = tables.get("correlations", pd.DataFrame())
    out = []
    for r in cat.head(3).itertuples():
        line = f"{r.unit_id} {r.score:.1f}点（n＝{int(r.n):,}）"
        if not corr.empty:
            c = corr[(corr.unit_type == "カテゴリ") & (corr.unit_id == r.unit_id)].sort_values("r", ascending=False)
            if len(c):
                line += f"。目的指標との相関 r＝{c.iloc[0].r:.3f}（{c.iloc[0].objective_id}）"
        q = st[(st.scope_type == ALL) & (st.unit_type == "設問") & st.score.notna()]
        members = [m for c, ms in categories(cfg) if c == r.unit_id for m in ms]
        q = q[q.unit_id.isin(members)].sort_values("score").head(1)
        if len(q):
            line += f"。最も低い設問 {q.iloc[0].unit_id} {q.iloc[0].score:.1f}点"
        out.append({"theme": r.unit_id, "fact": line, "evidence": {"unit_type": "カテゴリ", "unit_id": r.unit_id}})
    return out


def compose(cfg: ClientConfig, public_dir: Path, run_unresolved: list[str]) -> dict[str, dict]:
    path = cfg.root / "interpretation.yaml"
    interp = load_yaml(path) if path.exists() else {}
    interp = interp or {}
    per_reader = interp.pop("readers", {}) or {}
    unit_common = interp.pop("unit_readers", {}) or {}
    meta = {k: interp.pop(k, None) for k in ("status", "drafted_by", "drafted_on", "confirmed_by", "confirmed_on")}
    if path.exists() and meta["status"] != "confirmed":
        run_unresolved = run_unresolved + ["interpretation.yaml は下書き（担当者が確認して status: confirmed にするまで納品版にしない）"]
    out = {}
    for reader in cfg.readers or [{"id": ALL, "kind": "all_staff"}]:
        tables, _ = read_public(public_dir, reader["id"])
        base = interp if reader["kind"] in ("all_staff", "hr") else unit_common   # 全体向けの文章を所属長版に流用しない
        body = deep_merge(copy.deepcopy(base), per_reader.get(reader["id"]))
        body["interpretation_status"] = meta
        body["facts"] = facts(cfg, tables)
        body["unresolved"] = run_unresolved
        body["source"] = {"interpretation": path.name if path.exists() else None,
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None}
        out[reader["id"]] = body
    return out


def write_compose(res: dict[str, dict], out_dir: Path) -> list[Path]:
    paths = []
    for rid, body in res.items():
        p = out_dir / rid / "compose.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        paths.append(p)
    return paths
