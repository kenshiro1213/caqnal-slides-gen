"""publish 工程（仕様 v2 §7）。"""
from __future__ import annotations

import copy

import pandas as pd
import pytest

from caqnal_report.analysis.publish import PublishError, protect_family, publish, reader_units, suppress

ORG = pd.DataFrame({
    "unit_id": ["D1", "S1", "S2", "S3", "D2", "S4"],
    "parent_id": [None, "D1", "D1", "D1", None, "D2"],
    "name": ["総務部", "人事課", "財政課", "国際課", "建設部", "道路課"],
    "level": ["部局", "課", "課", "課", "部局", "課"], "order": [1, 2, 3, 4, 5, 6]})


def _status(n: dict[tuple[str, str], int]) -> pd.DataFrame:
    return pd.DataFrame([{"scope_type": t, "scope_id": s, "scope_name": s, "target_n": None, "responded_n": v,
                          "excluded_n": 0, "rate": None} for (t, s), v in n.items()])


def _stats(n: dict[tuple[str, str], int]) -> pd.DataFrame:
    return pd.DataFrame([{"scope_type": t, "scope_id": s, "scope_name": s, "unit_type": "設問", "unit_id": "Q1",
                          "unit_name": "q", "n": v, "mean": 3.0 + i / 10, "sd": 1.0, "score": 50.0, "pos": .5,
                          "neu": .3, "neg": .2} for i, ((t, s), v) in enumerate(n.items())])


N = {("全庁", "全庁"): 60, ("組織", "D1"): 40, ("組織", "S1"): 20, ("組織", "S2"): 17, ("組織", "S3"): 3,
     ("組織", "D2"): 20, ("組織", "S4"): 20}


def test_protect_family():
    assert protect_family(40, {"a": 20, "b": 17, "c": 3}, {"c"}, 5) == {"c", "b"}   # 残り 3 → 次に小さい b も隠す
    assert protect_family(45, {"a": 20, "b": 17, "c": 3}, {"c"}, 5) == {"c"}        # 直轄 5 名 + c 3 名 = 8
    assert protect_family(40, {"a": 20, "b": 20}, set(), 5) == set()


def test_secondary_suppression_closes_differencing():
    hidden, cells, log = suppress(_stats(N), _status(N), ORG, 5)
    assert ("組織", "S3") in hidden                          # 3 名
    assert ("組織", "S2") in hidden                          # 総務部 40 − 人事課 20 = 20 から推測されないよう追加
    assert any(r["reason"].startswith("差し引き") for r in log)
    for parent, kids in [(("組織", "D1"), ["S1", "S2", "S3"])]:
        shown = [k for k in kids if ("組織", k) not in hidden]
        rest = N[parent] - sum(N[("組織", k)] for k in shown)
        assert rest == 0 or rest >= 5


def _cfg(cfg, readers):
    c = copy.deepcopy(cfg)
    c.raw["publication"] = {"min_n": 5, "secondary_suppression": True}
    c.raw["readers"] = readers
    return c


def test_reader_scope_same_department_only(cfg):
    c = _cfg(cfg, [{"id": "全庁", "kind": "all_staff"}, {"id": "人事課長", "kind": "section_head", "unit": "S1"},
                   {"id": "建設部長", "kind": "dept_head", "unit": "D2"}])
    agg = {"stats": _stats(N), "response_status": _status(N), "correlations": pd.DataFrame()}
    res = publish(c, agg, {}, ORG)
    sec = res.readers["人事課長"]["stats"]
    assert set(sec.loc[sec.scope_type == "組織", "scope_id"]) == {"D1", "S1", "S2", "S3"}
    dept = res.readers["建設部長"]["stats"]
    assert set(dept.loc[dept.scope_type == "組織", "scope_id"]) == {"D2", "S4"}
    assert res.manifests["人事課長"]["own_unit"] == "S1"
    # 非表示の値は公開データに残さない
    allst = res.readers["全庁"]["stats"].set_index("scope_id")
    assert allst.loc[["S2", "S3"], ["n", "mean"]].isna().all().all()
    assert allst.loc[["S2", "S3"], "suppressed"].notna().all()
    rs = res.readers["全庁"]["response_status"].set_index("scope_id")
    assert rs.loc[["S2", "S3"], "responded_n"].isna().all()


@pytest.mark.parametrize("reader", [{"id": "x", "kind": "section_head", "unit": "ZZ"},
                                    {"id": "x", "kind": "dept_head", "unit": None}])
def test_unknown_unit_never_widens(reader):
    with pytest.raises(PublishError, match="全庁へ広げない"):
        reader_units(reader, ORG)


def test_min_n_required(cfg):
    c = _cfg(cfg, None)
    c.raw["publication"]["min_n"] = None
    with pytest.raises(PublishError, match="min_n"):
        publish(c, {"stats": _stats(N), "response_status": _status(N), "correlations": pd.DataFrame()}, {}, ORG)


def test_cell_level_suppression():
    n = dict(N)
    st = _stats(n)
    st.loc[st.scope_id == "S1", "n"] = 4                     # 組織は 20 名だが、この設問の有効回答は 4
    _, cells, _ = suppress(st, _status(n), ORG, 5)
    assert (("組織", "S1"), ("設問", "Q1")) in cells
