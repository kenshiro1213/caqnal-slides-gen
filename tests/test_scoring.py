import numpy as np
import pandas as pd

from caqnal_report.ingest.loaders import load_current, normalize_label
from caqnal_report.ingest.yoy import build_yoy_map, norm_text
from caqnal_report.analysis.scoring import score_wave


def test_normalize_label_strips_circled_prefix_only():
    assert normalize_label("①そう思う") == "そう思う"
    assert normalize_label("③ ①②両方あり") == "①②両方あり"          # 2つ目以降の丸数字は残す
    assert normalize_label("管理職（課長級以上）") == "管理職（課長級以上）"  # 全角括弧を壊さない
    assert normalize_label("") is None and normalize_label(None) is None


def test_scale_is_reversed_and_reverse_items_flip(cfg):
    wave = load_current(cfg)
    sc = score_wave(cfg, wave, cfg.questions, {"役職": "Q2"}, "Q5")
    long = sc.long
    q7 = long[long["question_id"] == "Q7"]
    assert q7[q7["label"] == "そう思う"]["score"].eq(5).all()
    assert q7[q7["label"] == "そう思わない"]["score"].eq(1).all()
    assert (q7[q7["label"] == "そう思う"]["cls"] == "positive").all()
    q43 = long[long["question_id"] == "Q43"]              # 逆転項目
    assert q43[q43["label"] == "そう思う"]["score"].eq(1).all()
    assert (q43[q43["label"] == "そう思う"]["cls"] == "negative").all()


def test_conditional_question_only_counts_managers(cfg):
    wave = load_current(cfg)
    sc = score_wave(cfg, wave, cfg.questions, {"役職": "Q2"}, "Q5")
    q56 = sc.long[sc.long["question_id"] == "Q56"]
    roles = sc.frame.loc[q56["respondent_id"], "役職"]
    assert (roles == "管理職（課長級以上）").all()
    assert len(q56) > 0


def test_indicator_is_mean_of_member_questions(cfg):
    wave = load_current(cfg)
    sc = score_wave(cfg, wave, cfg.questions, {"役職": "Q2"}, "Q5")
    wide = sc.long.pivot(index="respondent_id", columns="question_id", values="score")
    expected = wide[["Q58", "Q59", "Q60"]].mean(axis=1)
    got = sc.R["指標:overall_engagement"].reindex(expected.index)
    pd.testing.assert_series_equal(got, expected, check_names=False)


def test_yoy_map_matches_by_text_not_number(cfg):
    ymap = build_yoy_map(cfg.questions, cfg.prev.questions)
    m = {e["current"]: e for e in ymap["map"]}
    assert m["Q18"]["previous"] == "Q17" and m["Q18"]["status"] == "比較可"   # 番号がずれても文で対応
    assert m["Q52"]["previous"] == "Q36" and "カテゴリ変更" in m["Q52"].get("note", "")
    assert m["Q17"]["status"] == "比較不可"                                 # R8 新設
    assert m["Q55"]["status"] == "要確認"                                   # 文言変更 → 人が確定
    assert norm_text("業務上、必要な知識") == norm_text("業務上必要な知識")
