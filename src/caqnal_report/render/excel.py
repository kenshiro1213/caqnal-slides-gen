"""詳細集計 Excel（仕様書 §0-1 E / F、§13）。中間データをそのままシートに展開する。"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from ..analysis.pipeline import AnalysisResult
from ..config import ClientConfig

FIXED_CREATED = dt.datetime(2020, 1, 1)


def _pivot(scores: pd.DataFrame, stype: str, utype: str, value: str) -> pd.DataFrame:
    d = scores[(scores["scope_type"] == stype) & (scores["unit_type"] == utype)]
    if d.empty:
        return pd.DataFrame()
    return d.pivot(index="unit", columns="scope", values=value)


def write_excel(cfg: ClientConfig, res: AnalysisResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    scores = res["scores"]
    with pd.ExcelWriter(path, engine="xlsxwriter") as xw:
        xw.book.set_properties({"created": FIXED_CREATED, "author": "CAQNAL", "title": f"{cfg.client['name']} 詳細集計"})
        summ = res.summary
        overview = pd.DataFrame([
            ["自治体", cfg.client["name"]], ["年度", cfg.client.get("fiscal_year_label")],
            ["回答者数", summ["respondents"]], ["前年度回答者数", summ.get("respondents_prev")],
            ["対象者数", cfg.client.get("target_count")], ["回答率(%)", summ.get("response_rate")],
            ["設問数（尺度）", summ["question_count"]], ["匿名性 最小n", summ["anonymity"]["min_n"]],
            ["自由記述 最小件数", summ["anonymity"]["min_comments"]],
            ["経年 比較可 / 参考比較", f"{summ['yoy']['comparable']} / {summ['yoy']['reference']}"],
            ["4類型しきい値 x", summ["segmentation"]["threshold_x"]], ["4類型しきい値 y", summ["segmentation"]["threshold_y"]],
            ["生成バージョン", summ["version"]],
        ], columns=["項目", "値"])
        overview.to_excel(xw, sheet_name="概要", index=False)
        pd.DataFrame(cfg.questions).to_excel(xw, sheet_name="設問マスタ", index=False)

        base_cols = ["scope_type", "scope", "unit_type", "unit", "unit_name", "n", "mean", "sd", "pos", "neu", "neg", "norm",
                     "diff_all", "p_all", "sig_all", "prev_n", "prev_mean", "diff_yoy", "p_yoy", "sig_yoy", "yoy_status", "suppressed"]
        cols = [c for c in base_cols if c in scores.columns]
        scores[cols].to_excel(xw, sheet_name="スコア_全件", index=False)
        for utype in ("指標", "軸", "カテゴリ", "設問"):
            _pivot(scores, cfg.org_level, utype, "mean").to_excel(xw, sheet_name=f"部局×{utype}_平均")
            _pivot(scores, cfg.org_level, utype, "diff_all").to_excel(xw, sheet_name=f"部局×{utype}_全庁差")
        for key in cfg.attributes:
            _pivot(scores, key, "カテゴリ", "mean").to_excel(xw, sheet_name=f"属性_{key}"[:31])
        for name, sheet in (("dist", "回答分布"), ("rankings", "ランキング"), ("segments", "4類型"),
                            ("segment_drivers", "類型別ドライバー"), ("drivers", "KeyDriver"),
                            ("comment_themes", "自由記述テーマ"), ("judgement", "判定"), ("priority_orgs", "重点部局")):
            df = res.tables.get(name, pd.DataFrame())
            (df if len(df) else pd.DataFrame({"note": ["データなし"]})).to_excel(xw, sheet_name=sheet, index=False)
        # 自由記述はマスキング済みのみ（原文は出さない）
        cm = res.tables.get("comments", pd.DataFrame())
        if len(cm):
            cm[["org", "masked", "themes", "tone", "short"]].to_excel(xw, sheet_name="自由記述_マスク済", index=False)
        for ws in xw.book.worksheets():
            ws.freeze_panes(1, 0)
            ws.set_column(0, 0, 18)
            ws.set_column(1, 30, 12)
    return path
