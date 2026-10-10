"""案件設定・辞書の入口点検（仕様 v2 §2, §3, §4, §6, §7, §15 validate）。

回答データの中身は読まない（データの照合は validate.validate と normalize 工程が行う）。
結果の状態:
  blocked            構造の誤り（参照切れ・循環・重複 ID など）。直すまで先へ進めない
  draft_unconfirmed  確認待ち（null）や未受領の入力が残る。確定している準備だけ進める
  validated          設定・辞書の入口確認が済んだ
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ..config import LIKERT_ROLES, ClientConfig

READER_KINDS = ("all_staff", "hr", "dept_head", "section_head")
INDICATOR_KINDS = ("objective", "category", "common9")
COMMON9_STATUS = ("not_conducted", "undefined", "implemented", "partial")

# 確認しないと数字の意味が決まらない項目（未記載・null は未確定一覧へ）
REQUIRED = {
    "population": ["target_basis", "exclusion_rule", "duplicate_rule"],
    "publication": ["min_n"],
    "common9": ["status"],
}
MODEL_REQUIRED = [("cv", "group_level"), ("cv", "folds"), ("tree", "max_depth"), ("tree", "min_leaf"),
                  ("bootstrap", "n")]


@dataclass
class CaseReport:
    errors: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    fingerprints: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.errors:
            return "blocked"
        if self.unresolved:
            return "draft_unconfirmed"
        return "validated"

    def render(self) -> str:
        out = [f"[case] status={self.status}  errors {len(self.errors)} / 未確定 {len(self.unresolved)} / "
               f"warnings {len(self.warnings)}"]
        out += [f"  ERROR  {e}" for e in self.errors]
        out += [f"  未確定 {u}" for u in self.unresolved]
        out += [f"  WARN   {w}" for w in self.warnings]
        out += [f"  INFO   {i}" for i in self.infos]
        return "\n".join(out)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _nulls(obj, path: str) -> list[str]:
    """dict / list を辿り、値が null の項目のパスを返す。"""
    if obj is None:
        return [path]
    if isinstance(obj, dict):
        return [p for k, v in obj.items() for p in _nulls(v, f"{path}.{k}")]
    if isinstance(obj, list):
        return [p for i, v in enumerate(obj) for p in _nulls(v, f"{path}[{i}]")]
    return []


# ---------------------------------------------------------------- 各点検
def _check_inputs(cfg: ClientConfig, rep: CaseReport) -> None:
    if not cfg.inputs:
        rep.unresolved.append("inputs: 原資料一覧（ファイル・担当・取得日・シート・ヘッダー行・SHA256）が未記載")
        return
    ids = [i.get("id") for i in cfg.inputs]
    for d in sorted({i for i in ids if ids.count(i) > 1}):
        rep.errors.append(f"inputs: id {d} が重複")
    for spec in cfg.inputs:
        label = f"inputs[{spec.get('id')}]"
        for k in ("received", "received_by", "header_row"):
            if spec.get(k) is None:
                rep.unresolved.append(f"{label}.{k}")
        if not spec.get("path"):
            rep.errors.append(f"{label}: path がありません")
            continue
        path = cfg.path(spec["path"])
        if not path.exists():
            rep.unresolved.append(f"{label}: 未受領（{spec['path']}）")
            continue
        digest = sha256(path)
        rep.fingerprints[spec["id"]] = digest
        if spec.get("sha256") is None:
            rep.unresolved.append(f"{label}.sha256: 受領時の値を記録する（現在 {digest}）")
        elif spec["sha256"] != digest:
            rep.errors.append(f"{label}: SHA256 が記録と一致しません（ファイルが差し替わった可能性）")


def _check_required(cfg: ClientConfig, rep: CaseReport) -> None:
    for section, keys in REQUIRED.items():
        body = cfg.raw.get(section)
        if body is None:
            rep.unresolved.append(f"{section}: 未記載")
            continue
        for k in keys:
            if body.get(k) is None:
                rep.unresolved.append(f"{section}.{k}")
    for section in ("population", "publication", "corrections", "attribute_rules"):
        for p in _nulls(cfg.raw.get(section) or {}, section):
            if p not in rep.unresolved:
                rep.unresolved.append(p)
    min_n = cfg.publication.get("min_n")
    if min_n is not None and (not isinstance(min_n, int) or min_n < 1):
        rep.errors.append(f"publication.min_n は 1 以上の整数: {min_n}")


def _check_scales(cfg: ClientConfig, rep: CaseReport) -> None:
    for name, sc in (cfg.raw.get("scales") or {}).items():
        scores = set((sc.get("labels") or {}).values())
        groups = {g: set(sc.get(g) or []) for g in ("positive", "neutral", "negative")}
        for g, vals in groups.items():
            if vals - scores:
                rep.errors.append(f"尺度 {name}: {g} に labels に無い値 {sorted(vals - scores)}")
        if groups["positive"] & groups["negative"] or groups["neutral"] & (groups["positive"] | groups["negative"]):
            rep.errors.append(f"尺度 {name}: 肯定・中立・否定が重複しています")
        if not groups["neutral"]:
            rep.infos.append(f"尺度 {name}: 中立なし（偶数件法として扱う）")
    used = {q.get("scale") for q in cfg.qs(LIKERT_ROLES)}
    for s in sorted(x for x in used if x):
        if s not in (cfg.raw.get("scales") or {}):
            rep.errors.append(f"尺度 {s} が scales に未定義（questions.yaml で参照）")


def _check_questions(cfg: ClientConfig, rep: CaseReport) -> None:
    ids = [q["id"] for q in cfg.questions]
    for d in sorted({i for i in ids if ids.count(i) > 1}):
        rep.errors.append(f"設問 ID {d} が重複")
    for q in cfg.questions:
        if q.get("reverse") and q.get("role") not in LIKERT_ROLES:
            rep.errors.append(f"{q['id']}: 逆転は尺度設問（outcome/driver）にだけ設定できます")


def _check_indicators(cfg: ClientConfig, rep: CaseReport) -> None:
    for key, spec in cfg.indicators.items():
        kind = spec.get("kind")
        if kind is not None and kind not in INDICATOR_KINDS:
            rep.errors.append(f"指標 {key}: kind は {INDICATOR_KINDS} のいずれか: {kind}")
        try:
            qids = cfg.indicator_questions(key)
        except Exception as e:  # noqa: BLE001
            rep.errors.append(str(e))
            continue
        missing = [i for i in qids if i not in cfg.questions_by_id]
        if missing:
            rep.errors.append(f"指標 {key} の構成設問 {missing} が questions.yaml にありません")
        if kind == "objective" and len(qids) > 1 and not spec.get("composite_basis"):
            rep.warnings.append(f"指標 {key}: 複数設問の平均を目的指標にしています。根拠（composite_basis）を記載する")
    obj = cfg.raw.get("objectives")
    if obj is None:
        rep.unresolved.append("objectives: 目的指標（満足・推奨・継続勤務・上司満足など）の対応が未記載")
    for o in obj or []:
        label = f"objectives[{o.get('id')}]"
        qid = o.get("question")
        if qid is None:
            rep.unresolved.append(f"{label}.question")
        elif qid not in cfg.questions_by_id:
            rep.errors.append(f"{label}: 設問 {qid} が questions.yaml にありません")
        elif cfg.q(qid).get("role") not in LIKERT_ROLES:
            rep.errors.append(f"{label}: 設問 {qid} は尺度設問ではありません（role={cfg.q(qid).get('role')}）")
    oids = [o.get("id") for o in obj or []]
    for d in sorted({i for i in oids if oids.count(i) > 1}):
        rep.errors.append(f"objectives: id {d} が重複")
    status = cfg.common9.get("status")
    if status is None:
        return
    if status not in COMMON9_STATUS:
        rep.errors.append(f"common9.status は {COMMON9_STATUS} のいずれか: {status}")
    elif status == "implemented":
        qs = cfg.common9.get("questions") or []
        if len(qs) != 9:
            rep.errors.append(f"common9: implemented なのに設問が {len(qs)} 問（9 問必要）")
        if not cfg.common9.get("definition"):
            rep.unresolved.append("common9.definition: 構成・重み・欠損・尺度の正式定義")
    elif status == "undefined":
        rep.infos.append("common9: 実施済み・定義未確定 → 総合・3 要素の指標は保留")
    elif status == "partial":
        rep.warnings.append("common9: 一部のみ実施 → 正式指標と同名で出さない。欠測を補わない")
    else:
        rep.infos.append("common9: 未実施 → 総合・3 要素の指標は出力しない")


def _org_top_ancestor(by_id: dict[str, dict], uid: str) -> str | None:
    seen: set[str] = set()
    cur = uid
    while cur is not None and cur not in seen:
        seen.add(cur)
        parent = by_id.get(cur, {}).get("parent")
        if parent is None:
            return cur
        cur = parent
    return None


def _check_org(cfg: ClientConfig, rep: CaseReport) -> dict[str, dict]:
    if not cfg.raw.get("org_dictionary"):
        rep.unresolved.append("org_dictionary: 組織辞書（unit_id・親ID・名称・階層・表示順）が未作成")
        return {}
    if cfg.org_dictionary is None:
        rep.errors.append(f"org_dictionary: {cfg.raw['org_dictionary']} がありません")
        return {}
    levels = cfg.org_dictionary.get("levels") or []
    if not levels:
        rep.errors.append("組織辞書: levels（上位から順の階層名）がありません")
    units = cfg.org_units_dict
    ids = [u["id"] for u in units]
    for d in sorted({i for i in ids if ids.count(i) > 1}):
        rep.errors.append(f"組織辞書: unit_id {d} が重複")
    by_id = {u["id"]: u for u in units}
    for u in units:
        uid, parent, level = u["id"], u.get("parent"), u.get("level")
        if not u.get("name"):
            rep.errors.append(f"組織辞書 {uid}: name がありません")
        if levels and level not in levels:
            rep.errors.append(f"組織辞書 {uid}: level {level} が levels {levels} にありません")
        if parent is None:
            if levels and level != levels[0]:
                rep.errors.append(f"組織辞書 {uid}（{u.get('name')}）: 親がありません（孤立）。最上位は {levels[0]} のみ")
            continue
        if parent not in by_id:
            rep.errors.append(f"組織辞書 {uid}: 親 {parent} が存在しません")
            continue
        if levels and level in levels and by_id[parent].get("level") in levels \
                and levels.index(by_id[parent]["level"]) >= levels.index(level):
            rep.errors.append(f"組織辞書 {uid}: 親 {parent} の階層が同じか下位です")
    for u in units:
        if _org_top_ancestor(by_id, u["id"]) is None:
            rep.errors.append(f"組織辞書 {u['id']}: 親子関係が循環しています")
    names = [(u.get("parent"), u.get("name")) for u in units]
    for d in sorted({n for n in names if names.count(n) > 1}, key=str):
        rep.errors.append(f"組織辞書: 同じ親の下に同名の組織 {d[1]}")
    plain = [u.get("name") for u in units]
    dup = sorted({n for n in plain if plain.count(n) > 1})
    if dup:
        rep.infos.append(f"組織辞書: 異なる親に同名の組織 {dup}（ID で区別する）")
    for p in _nulls(cfg.org_dictionary.get("units") or [], "org.units"):
        if not p.endswith(".parent"):
            rep.unresolved.append(p)
    rep.infos.append(f"組織辞書: {len(units)} 組織（{' > '.join(levels)}）")
    return by_id


def _check_readers(cfg: ClientConfig, rep: CaseReport, by_id: dict[str, dict]) -> None:
    if not cfg.readers:
        rep.unresolved.append("readers: 成果物の読者と閲覧範囲が未記載")
        return
    ids = [r.get("id") for r in cfg.readers]
    for d in sorted({i for i in ids if ids.count(i) > 1}):
        rep.errors.append(f"readers: id {d} が重複")
    levels = (cfg.org_dictionary or {}).get("levels") or []
    for r in cfg.readers:
        label = f"readers[{r.get('id')}]"
        kind = r.get("kind")
        if kind not in READER_KINDS:
            rep.errors.append(f"{label}: kind は {READER_KINDS} のいずれか: {kind}")
            continue
        if kind not in ("dept_head", "section_head"):
            continue
        unit = r.get("unit")
        if unit is None:
            rep.unresolved.append(f"{label}.unit")
            continue
        unit = str(unit)
        if not by_id:
            rep.errors.append(f"{label}: 組織辞書が無いため所属 {unit} の範囲を決められません（全庁へ広げない）")
            continue
        if unit not in by_id:
            rep.errors.append(f"{label}: 所属 {unit} が組織辞書にありません")
            continue
        top = _org_top_ancestor(by_id, unit)
        if top is None:
            rep.errors.append(f"{label}: 所属 {unit} の部局を組織辞書から決められません（全庁へ広げない）")
        elif kind == "dept_head" and levels and by_id[unit].get("level") != levels[0]:
            rep.errors.append(f"{label}: 部局長の所属 {unit} が最上位階層 {levels[0]} ではありません")
        elif kind == "section_head" and unit == top:
            rep.errors.append(f"{label}: 課長の所属 {unit} が部局そのものです")


def _check_models(cfg: ClientConfig, rep: CaseReport) -> None:
    if not cfg.models:
        rep.unresolved.append("models: 分析条件（検証の分割単位・木の深さと葉の最低人数・再抽出回数）が未記載")
        return
    for a, b in MODEL_REQUIRED:
        if (cfg.models.get(a) or {}).get(b) is None:
            rep.unresolved.append(f"models.{a}.{b}")
    for p in _nulls(cfg.models, "models"):
        if p not in rep.unresolved and not p.endswith(".seed"):
            rep.unresolved.append(p)
    group = (cfg.models.get("cv") or {}).get("group_level")
    levels = (cfg.org_dictionary or {}).get("levels") or []
    if group is not None and levels and group not in levels:
        rep.errors.append(f"models.cv.group_level {group} が組織辞書の levels {levels} にありません")


def check_case(cfg: ClientConfig) -> CaseReport:
    rep = CaseReport()
    _check_inputs(cfg, rep)
    _check_required(cfg, rep)
    _check_scales(cfg, rep)
    _check_questions(cfg, rep)
    _check_indicators(cfg, rep)
    by_id = _check_org(cfg, rep)
    _check_readers(cfg, rep, by_id)
    _check_models(cfg, rep)
    for item in cfg.raw.get("unresolved") or []:
        rep.unresolved.append(f"未確定一覧: {item}")
    return rep
