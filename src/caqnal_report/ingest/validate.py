"""分析開始前チェック（仕様書 §1-3）。NG は具体的なメッセージで報告する。"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..config import LIKERT_ROLES, ClientConfig
from ..ingest.loaders import check_headers, normalize_label, read_csv
from ..ingest.yoy import comparable_pairs


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    def render(self) -> str:
        out = [f"[validate] {len(self.errors)} errors / {len(self.warnings)} warnings"]
        out += [f"  ERROR  {e}" for e in self.errors]
        out += [f"  WARN   {w}" for w in self.warnings]
        out += [f"  INFO   {i}" for i in self.infos]
        return "\n".join(out)


def _check_wave(cfg: ClientConfig, rep: Report, label: str, path, questions: list[dict]) -> pd.DataFrame | None:
    if not path.exists():
        rep.errors.append(f"{label}: 回答データがありません: {path}")
        return None
    raw = read_csv(path, cfg.raw["data"].get("encoding", "utf-8-sig"))
    rep.infos.append(f"{label}: {len(raw)} 行 × {len(raw.columns)} 列 ({path.name})")
    for p in check_headers(raw, questions):
        rep.errors.append(f"{label}: {p}")
    cols_used = {q.get("column") for q in questions}
    extra = [c for i, c in enumerate(raw.columns) if i not in cols_used]
    if extra:
        rep.warnings.append(f"{label}: 設問マスタに無い列 {len(extra)} 件: {extra[:3]}")
    # 尺度ラベル
    mode = cfg.raw.get("label_normalize", "strip_circled_prefix")
    for q in questions:
        if q.get("role") not in LIKERT_ROLES or q.get("column") is None or q["column"] >= len(raw.columns):
            continue
        sc = cfg.raw["scales"].get(q.get("scale") or "")
        if sc is None:
            rep.errors.append(f"{label} {q['id']}: 尺度 {q.get('scale')} が client.yaml に未定義")
            continue
        labels = set(map(str, sc["labels"])) | set(map(str, sc.get("excluded_labels", [])))
        vals = set(raw.iloc[:, q["column"]].dropna().map(lambda v: normalize_label(v, mode)))
        unknown = vals - labels - {None}
        if unknown:
            rep.errors.append(f"{label} {q['id']}: 尺度に無い回答値 {sorted(unknown)[:5]}")
    return raw


def validate(cfg: ClientConfig) -> Report:
    rep = Report()
    data = cfg.raw["data"]
    raw = _check_wave(cfg, rep, cfg.fiscal_year, cfg.path(data["responses"]), cfg.questions)

    # 必須ロール
    roles = {q.get("role") for q in cfg.questions}
    for need in ("outcome", "driver"):
        if need not in roles:
            rep.errors.append(f"role={need} の設問がありません")
    for key, spec in cfg.attributes.items():
        if spec["question"] not in cfg.questions_by_id:
            rep.errors.append(f"属性 {key} の設問 {spec['question']} が questions.yaml にありません")
    org_q = cfg.org.get("question")
    if org_q and org_q not in cfg.questions_by_id:
        rep.errors.append(f"組織設問 {org_q} が questions.yaml にありません")

    # 指標
    for key in cfg.indicators:
        try:
            qids = cfg.indicator_questions(key)
        except Exception as e:  # noqa: BLE001
            rep.errors.append(str(e))
            continue
        missing = [i for i in qids if i not in cfg.questions_by_id]
        if missing:
            rep.errors.append(f"指標 {key} の構成設問 {missing} が questions.yaml にありません")
        rep.infos.append(f"指標 {key}: {len(qids)} 問")

    # 組織紐付け・匿名性
    if raw is not None and org_q:
        mode = cfg.raw.get("label_normalize", "strip_circled_prefix")
        col = raw.iloc[:, cfg.q(org_q)["column"]].map(lambda v: normalize_label(v, mode))
        counts = col.value_counts(dropna=False)
        unknown = set(col.dropna()) - set(cfg.org_units)
        if unknown:
            rep.errors.append(f"組織マスタに無い所属: {sorted(unknown)}")
        na = int(col.isna().sum())
        if na:
            rep.warnings.append(f"所属未回答 {na} 名（全庁集計には含める。組織別からは除外）")
        min_n = cfg.analysis["anonymity"]["min_n"]
        small = [f"{u}({int(counts.get(u, 0))})" for u in cfg.org_units if counts.get(u, 0) < min_n]
        if small:
            rep.warnings.append(f"n<{min_n} で非表示になる組織: {', '.join(small)}")
        rep.infos.append(f"匿名性 min_n={min_n}, min_comments={cfg.analysis['anonymity']['min_comments']}")

    # 経年
    for w in cfg.previous:
        _check_wave(cfg, rep, w.fiscal_year, w.responses, w.questions)
        if w.yoy_map is None:
            rep.warnings.append(f"{w.fiscal_year}: yoy_map.yaml がありません。経年比較は行いません")
            continue
        entries = w.yoy_map["map"] if isinstance(w.yoy_map, dict) else w.yoy_map
        pending = [e["current"] for e in entries if e.get("status") == "要確認"]
        if pending:
            rep.errors.append(f"{w.fiscal_year}: yoy_map の status=要確認 が未確定: {pending}")
        pairs = comparable_pairs(w.yoy_map)
        likert = [q["id"] for q in cfg.qs(LIKERT_ROLES)]
        n_ok = sum(1 for i in likert if i in pairs and pairs[i][1] == "比較可")
        n_ref = sum(1 for i in likert if i in pairs and pairs[i][1] == "参考比較")
        rep.infos.append(f"{w.fiscal_year}: 経年対応 比較可 {n_ok} / 参考比較 {n_ref} / 比較不可 {len(likert) - n_ok - n_ref}")
        for e in entries:
            if e.get("previous") and e["previous"] not in w.questions_by_id:
                rep.errors.append(f"{w.fiscal_year}: yoy_map の previous={e['previous']} が questions_{w.fiscal_year}.yaml にありません")

    if cfg.client.get("target_count") in (None, 0):
        rep.warnings.append("target_count 未設定のため回答率は「－」表示")
    return rep
