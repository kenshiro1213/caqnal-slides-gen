"""案件設定・辞書の入口点検（仕様 v2）。"""
from __future__ import annotations

import copy

import pytest

from caqnal_report.config import CONFIG_DIR, load_yaml
from caqnal_report.ingest.case_check import check_case

ORG = {
    "levels": ["部局", "課"],
    "units": [
        {"id": "100", "name": "総務部", "parent": None, "level": "部局", "order": 1},
        {"id": "101", "name": "人事課", "parent": "100", "level": "課", "order": 2},
        {"id": "200", "name": "建設部", "parent": None, "level": "部局", "order": 3},
        {"id": "201", "name": "庶務課", "parent": "200", "level": "課", "order": 4},
        {"id": "102", "name": "庶務課", "parent": "100", "level": "課", "order": 5},
    ],
}
CONFIRMED = {
    "inputs": [],
    "population": {"target_basis": "名簿", "exclusion_rule": "休職者を除く", "duplicate_rule": "最終回答"},
    "publication": {"min_n": 5},
    "common9": {"status": "not_conducted"},
    "aggregate": {"category_missing": "all_answered"},
    "objectives": [],
    "org_dictionary": "org.yaml",
    "readers": [{"id": "全庁", "kind": "all_staff"}, {"id": "総務部長", "kind": "dept_head", "unit": "100"},
                {"id": "人事課長", "kind": "section_head", "unit": "101"}],
    "models": {"cv": {"group_level": "部局", "folds": 5}, "tree": {"max_depth": 2, "min_leaf": 30},
               "bootstrap": {"n": 200, "seed": 0}},
}


@pytest.fixture
def case(cfg):
    c = copy.deepcopy(cfg)
    c.raw.update(copy.deepcopy(CONFIRMED))
    c.org_dictionary = copy.deepcopy(ORG)
    return c


def _msgs(rep) -> str:
    return "\n".join(rep.errors + rep.unresolved + rep.warnings + rep.infos)


def test_v1_config_is_draft_not_blocked(cfg):
    rep = check_case(cfg)
    assert rep.status == "draft_unconfirmed"
    assert not rep.errors


def test_confirmed_case_is_validated(case):
    case.raw["inputs"] = [{"id": "x", "path": "client.yaml", "received": "2026-10-01", "received_by": "担当",
                           "header_row": 1, "sha256": None}]
    rep = check_case(case)
    case.raw["inputs"][0]["sha256"] = rep.fingerprints["x"]
    rep = check_case(case)
    assert rep.status == "validated", _msgs(rep)
    assert "異なる親に同名の組織" in _msgs(rep)          # 同名の課は ID で区別（エラーにしない）


def test_null_means_unconfirmed(case):
    case.raw["publication"]["min_n"] = None
    rep = check_case(case)
    assert rep.status == "draft_unconfirmed"
    assert "publication.min_n" in rep.unresolved


def test_sha_mismatch_blocks(case):
    case.raw["inputs"] = [{"id": "x", "path": "client.yaml", "received": "d", "received_by": "p", "header_row": 1,
                           "sha256": "0" * 64}]
    assert check_case(case).status == "blocked"


@pytest.mark.parametrize("mutate, needle", [
    (lambda o: o["units"].append({"id": "101", "name": "重複", "parent": "100", "level": "課"}), "重複"),
    (lambda o: o["units"].append({"id": "300", "name": "孤立課", "parent": None, "level": "課"}), "孤立"),
    (lambda o: o["units"].append({"id": "301", "name": "親なし", "parent": "999", "level": "課"}), "存在しません"),
    (lambda o: o["units"].extend([{"id": "A", "name": "a", "parent": "B", "level": "課"},
                                  {"id": "B", "name": "b", "parent": "A", "level": "課"}]), "循環"),
    (lambda o: o["units"].append({"id": "103", "name": "人事課", "parent": "100", "level": "課"}), "同じ親の下に同名"),
])
def test_org_structure_errors_block(case, mutate, needle):
    mutate(case.org_dictionary)
    rep = check_case(case)
    assert rep.status == "blocked"
    assert needle in "\n".join(rep.errors)


def test_reader_scope_never_widens(case):
    case.raw["readers"].append({"id": "不明課長", "kind": "section_head", "unit": "999"})
    rep = check_case(case)
    assert any("不明課長" in e for e in rep.errors)
    case.raw["readers"][-1]["unit"] = "100"               # 課長に部局を指定
    assert any("部局そのもの" in e for e in check_case(case).errors)
    case.raw["readers"][-1] = {"id": "部局長", "kind": "dept_head", "unit": "101"}
    assert any("最上位階層" in e for e in check_case(case).errors)


def test_reader_without_org_dictionary_is_error(case):
    case.org_dictionary = None
    case.raw["org_dictionary"] = None
    rep = check_case(case)
    assert any("全庁へ広げない" in e for e in rep.errors)


def test_scale_without_neutral_is_allowed(case):
    case.raw["scales"]["agree4"] = {"labels": {"そう思う": 4, "ややそう思う": 3, "あまりそう思わない": 2, "そう思わない": 1},
                                    "positive": [4, 3], "neutral": [], "negative": [2, 1], "min": 1, "max": 4}
    rep = check_case(case)
    assert not rep.errors
    assert "中立なし" in _msgs(rep)
    case.raw["scales"]["agree4"]["neutral"] = [3]          # 3 番目を中立に流用すると肯定と重複
    assert check_case(case).status == "blocked"


def test_common9_states(case):
    case.raw["common9"] = {"status": "implemented", "questions": ["Q1"]}
    assert any("9 問必要" in e for e in check_case(case).errors)
    case.raw["common9"] = {"status": "partial"}
    assert "同名で出さない" in _msgs(check_case(case))
    case.raw["common9"] = {"status": "unknown"}
    assert check_case(case).status == "blocked"


def test_objective_must_be_likert(case, cfg):
    likert = cfg.likert_ids()[0]
    case.raw["objectives"] = [{"id": "a", "question": likert}, {"id": "a", "question": "Q999"}]
    errs = "\n".join(check_case(case).errors)
    assert "Q999" in errs and "重複" in errs


def test_template_lists_everything_as_unconfirmed(cfg):
    c = copy.deepcopy(cfg)
    c.raw.update(load_yaml(CONFIG_DIR / "client_template.yaml"))
    rep = check_case(c)
    assert rep.status in ("draft_unconfirmed", "blocked")
    for key in ("publication.min_n", "common9.status", "population.exclusion_rule", "models.tree.min_leaf"):
        assert key in rep.unresolved


def test_common_code_has_no_case_specific_logic():
    """共通処理に自治体名・固定の設問番号を入れない（仕様 v2 §2, §18）。入力変換の説明と CLI の使用例は除く。"""
    import re

    from caqnal_report.config import CLIENTS_DIR, PROJECT_ROOT

    names = [p.name.split("_")[0] for p in CLIENTS_DIR.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))]
    allowed = {"ingest/question_master.py", "cli.py"}
    hits = []
    for path in sorted((PROJECT_ROOT / "src" / "caqnal_report").rglob("*.py")):
        rel = path.relative_to(PROJECT_ROOT / "src" / "caqnal_report").as_posix()
        text = path.read_text(encoding="utf-8").replace("参考デッキ構成_佐賀市v2", "")   # 参照資料のファイル名
        if rel not in allowed:
            hits += [f"{rel}: {n}" for n in set(names) if n in text]
        hits += [f"{rel}: {m}" for m in re.findall(r"[\"']Q\d+[\"']", text)]
    for path in sorted(CONFIG_DIR.rglob("*.yaml")):
        text = path.read_text(encoding="utf-8").replace("参考デッキ構成_佐賀市v2", "")
        hits += [f"config/{path.name}: {n}" for n in set(names) if n in text]
        hits += [f"config/{path.name}: {m}" for m in re.findall(r"\bQ\d+\b", text)]
    assert not hits, hits
