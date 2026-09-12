"""自由記述の規則ベース分析（仕様書 §4-8）。

- マスキング（メール・電話・長い数字・「○○さん/課長」等）→ 原文は残さず masked のみ中間出力
- 重複・空欄・記号のみを除外、極端に短いものは参考扱い（short=True）
- テーマ分類はキーワード辞書（複数テーマ可、該当なし → その他）
- トーンは肯定/否定語の出現数比較の参考値
- 組織別テーマ構成比。件数 < min_comments の組織は非表示
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ..config import ClientConfig
from ..ingest.loaders import WaveData

SHORT_LEN = 10


def mask_text(text: str, patterns: list[dict]) -> str:
    out = text
    for p in patterns:
        out = re.sub(p["regex"], p["replace"], out)
    return out


def classify_themes(text: str, themes: dict[str, list[str]]) -> list[str]:
    hits = [t for t, kws in themes.items() if any(k in text for k in kws)]
    return hits or ["その他"]


def tone(text: str, words: dict[str, list[str]]) -> str:
    pos = sum(text.count(w) for w in words.get("positive", []))
    neg = sum(text.count(w) for w in words.get("negative", []))
    if pos == 0 and neg == 0:
        return "中立"
    if pos > neg:
        return "肯定"
    if neg > pos:
        return "否定"
    return "中立"


def analyze_comments(cfg: ClientConfig, wave: WaveData, frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    spec = cfg.raw.get("comments") or {}
    qid = spec.get("question")
    ct = cfg.comment_themes
    min_c = cfg.analysis["anonymity"]["min_comments"]
    if not qid or qid not in wave.df.columns:
        return pd.DataFrame(), pd.DataFrame()
    df = wave.df[["respondent_id", qid]].dropna(subset=[qid]).copy()
    df["raw_len"] = df[qid].map(len)
    df = df[df[qid].map(lambda s: bool(re.search(r"[ぁ-んァ-ン一-龥A-Za-z0-9]", s)))]   # 記号のみ除外
    df = df.drop_duplicates(subset=[qid])
    df["masked"] = df[qid].map(lambda s: mask_text(s, ct["masking"]["patterns"]))
    df["short"] = df["raw_len"] < SHORT_LEN
    df["themes"] = df["masked"].map(lambda s: classify_themes(s, ct["themes"]))
    df["tone"] = df["masked"].map(lambda s: tone(s, ct.get("tone", {})))
    df["org"] = df["respondent_id"].map(frame["org"])
    comments = df[["respondent_id", "org", "masked", "raw_len", "short", "themes", "tone"]].copy()
    comments["themes"] = comments["themes"].map("|".join)

    # テーマ集計（全庁 + 組織）
    rows: list[dict] = []
    theme_names = list(ct["themes"].keys()) + ["その他"]
    groups = [("全庁", "全庁", df)] + [(cfg.org_level, u, df[df["org"] == u]) for u in cfg.org_units]
    for stype, scope, g in groups:
        total = len(g)
        for t in theme_names:
            cnt = int(g["themes"].map(lambda ts: t in ts).sum())
            rows.append({"scope_type": stype, "scope": scope, "theme": t, "count": cnt if total >= min_c else np.nan,
                         "share": 100 * cnt / total if total >= min_c and total else np.nan,
                         "total_comments": total, "suppressed": total < min_c})
    for stype, scope, g in groups:
        total = len(g)
        vc = g["tone"].value_counts()
        for tn in ("肯定", "中立", "否定"):
            rows.append({"scope_type": stype, "scope": scope, "theme": f"トーン:{tn}",
                         "count": int(vc.get(tn, 0)) if total >= min_c else np.nan,
                         "share": 100 * vc.get(tn, 0) / total if total >= min_c and total else np.nan,
                         "total_comments": total, "suppressed": total < min_c})
    return comments.reset_index(drop=True), pd.DataFrame(rows)
