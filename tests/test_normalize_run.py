"""normalize 工程と run 管理（仕様 v2 §4, §5, §16）。"""
from __future__ import annotations

import copy
import shutil

import pandas as pd
import pytest

from caqnal_report.ingest.normalize import NormalizeError, bin_label, normalize, normalize_id
from caqnal_report.run import Run, RunError


@pytest.fixture
def case(cfg, tmp_path):
    """テスト市を tmp に複製し、対象者リスト（PII 列を含む）を付けた案件。"""
    c = copy.deepcopy(cfg)
    (tmp_path / "data").mkdir()
    shutil.copy(cfg.path(cfg.raw["data"]["responses"]), tmp_path / "data" / "R8_回答.csv")
    for f in ("client.yaml", "questions.yaml"):
        shutil.copy(cfg.root / f, tmp_path / f)
    c.root = tmp_path
    c.raw["data"]["previous"] = []
    c.previous = []
    n = len(pd.read_csv(tmp_path / "data" / "R8_回答.csv"))
    ids = [f"Ｒ8-{i:04d}" for i in range(1, n + 1)]           # 全角混じり → 半角に正規化して照合
    ids = ids[5:] + ["R8-9001", "R8-9002"]                    # 先頭 5 名は名簿に無い（未照合）、2 名は未回答
    roster = pd.DataFrame({
        "職員番号": ids,
        "氏名": [f"氏名{i}" for i in range(len(ids))],
        "メール": [f"x{i}@example.jp" for i in range(len(ids))],
        "区分": ["休職" if i == 0 else "在職" for i in range(len(ids))],
        "生年月日": ["1990-04-02" if i % 2 else "1970-01-01" for i in range(len(ids))],
    })
    roster.to_csv(tmp_path / "data" / "R8_対象者リスト_PII.csv", index=False, encoding="utf-8-sig")
    c.raw["inputs"] = [{"id": "roster", "path": "data/R8_対象者リスト_PII.csv"}]
    c.raw["attribute_rules"] = {"base_date": "2026-04-01", "age_bins": [29, 39, 49]}
    c.raw["normalize"] = {"roster": {"input": "roster", "id_column": "職員番号", "status_column": "区分",
                                     "exclude_values": ["休職"],
                                     "attributes": {"年齢区分": {"column": "生年月日", "kind": "age"}}}}
    return c


def test_id_and_bins():
    assert normalize_id(" Ｒ８-００１ ") == "R8-001"
    assert normalize_id("12", zfill=5) == "00012"
    assert [bin_label(y, [29, 39, 49], "歳") for y in (29, 30, 39, 49, 50)] == \
        ["29歳以下", "30～39歳", "30～39歳", "40～49歳", "50歳以上"]


def test_roster_join_stages_and_no_pii(case):
    res = normalize(case)
    st = res.log["stages"]
    resp = res.tables["respondents"]
    assert st["未照合"] == 5
    assert st["対象外"] == 1
    assert st["対象"] == len(resp) - 5 - 1
    assert st["有効回答（照合済）"] == st["対象"] - 2              # 名簿にいるが未回答の 2 名
    assert set(resp.loc[resp["status"] == "未照合", "respondent_id"]) == {f"R8-{i:04d}" for i in range(1, 6)}
    ages = res.tables["attributes"].query("dimension_id == '年齢区分'")["value"]
    assert set(ages) == {"50歳以上", "30～39歳"}                   # 1990-04-02 は 2026-04-01 時点で 35 歳
    dumped = "\n".join(t.to_csv() for t in res.tables.values())
    assert "氏名" not in dumped and "example.jp" not in dumped and "1990-04-02" not in dumped


def test_future_birthdate_stops_until_rule(case):
    df = pd.read_csv(case.path("data/R8_対象者リスト_PII.csv"), dtype=str)
    df.loc[0, "生年月日"] = "2030-01-01"
    df.to_csv(case.path("data/R8_対象者リスト_PII.csv"), index=False, encoding="utf-8-sig")
    with pytest.raises(NormalizeError, match="future_date_rule"):
        normalize(case)
    case.raw["attribute_rules"]["future_date_rule"] = "missing"
    assert any("基準日より後" in w for w in normalize(case).warnings)


def test_duplicate_ids_need_rule(case):
    df = pd.read_csv(case.path("data/R8_対象者リスト_PII.csv"), dtype=str)
    pd.concat([df, df.iloc[[3]]]).to_csv(case.path("data/R8_対象者リスト_PII.csv"), index=False, encoding="utf-8-sig")
    with pytest.raises(NormalizeError, match="重複 ID"):
        normalize(case)
    case.raw["normalize"]["roster"]["duplicates"] = "keep_last"
    normalize(case)


def test_unknown_label_stops(case):
    df = pd.read_csv(case.path("data/R8_回答.csv"), dtype=str)
    col = case.q(case.likert_ids()[0])["column"]
    df.iloc[0, col] = "とてもそう思う"
    df.to_csv(case.path("data/R8_回答.csv"), index=False, encoding="utf-8-sig")
    with pytest.raises(NormalizeError, match="尺度に無い選択肢"):
        normalize(case)


def test_same_name_units_cannot_match_by_name(case):
    case.org_dictionary = {"levels": ["部局", "課"], "units": [
        {"id": "1", "name": "総務部", "parent": None, "level": "部局"},
        {"id": "2", "name": "総務部", "parent": None, "level": "部局"}]}
    with pytest.raises(NormalizeError, match="重複"):
        normalize(case)


def test_run_order_staleness_and_invalidation(case):
    run = Run.create(case, "R8_test")
    with pytest.raises(RunError, match="validate"):
        run.require("normalize")
    run.record("validate", "passed", status="validated")
    run.require("normalize")
    run.record("normalize", "passed")
    d = run.step_dir("aggregate")
    d.mkdir(parents=True)
    (d / "x.csv").write_text("1")
    run.record("aggregate", "passed", outputs=[d / "x.csv"])
    run.record("normalize", "failed")                     # やり直すと後の工程は無効
    assert run.step("aggregate") is None and not d.exists()
    with pytest.raises(RunError, match="aggregate"):
        run.require("models")
    with pytest.raises(RunError, match="passed ではありません"):
        run.require("aggregate")
    # 原資料が変わったら再開できない
    p = case.path("data/R8_対象者リスト_PII.csv")
    p.write_text(p.read_text(encoding="utf-8-sig") + "\n", encoding="utf-8-sig")
    with pytest.raises(RunError, match="原資料"):
        run.require("normalize")
    with pytest.raises(RunError, match="既に"):
        Run.create(case, "R8_test")
