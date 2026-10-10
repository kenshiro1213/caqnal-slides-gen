"""normalize 工程: 原資料 → 標準データ（仕様 v2 §4, §5, §15）。

出力（run/private/normalized/、全て UTF-8 CSV。private 領域で配布しない）
  respondents.csv  respondent_id, responded, unit_id, status, reason, match
                   status: 対象 / 対象外 / 未照合（対象者リストに無い回答） / 回答者（対象者リストなし）
  responses.csv    respondent_id, question_id, value, missing_reason
                   missing_reason: 未回答 / 除外選択肢 / 条件外（value は空）
  questions.csv    question_id, display_no, text, scale_id, role, category, axis, reverse, condition
  org.csv          unit_id, parent_id, name, level, order
  attributes.csv   respondent_id, dimension_id, value, source, base_date
  comments.csv     comment_id, respondent_id, question_id, text, text_sha
  normalize_log.json  段階ごとの人数、補正・除外の記録、警告

規則
- 元ファイルの列位置はここでだけ扱う。以後の工程は標準 ID で参照する
- ID は文字列で照合する（空白除去・全角→半角、必要ならゼロ埋め）。氏名・メールはキーにしない
- 対象者リスト（PII）は設定で指定した列だけを読む。生年月日・採用日は区分に変換して捨てる
- 次の場合は止める: 設問列の不一致、未知の選択肢、未知の所属、重複 ID（採用規則が無いとき）
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from ..config import LIKERT_ROLES, ClientConfig
from .loaders import check_headers, normalize_label, read_csv

TABLES = ("respondents", "responses", "questions", "org", "attributes", "comments")


class NormalizeError(Exception):
    """処理を止める誤り（仕様 v2 §16）。"""


@dataclass
class Normalized:
    tables: dict[str, pd.DataFrame]
    log: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- ID・日付
def normalize_id(value, zfill: int | None = None) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = unicodedata.normalize("NFKC", str(value)).strip()
    if not s:
        return None
    if zfill and s.isdigit():
        s = s.zfill(zfill)
    return s


def _parse_date(value) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    ts = pd.to_datetime(unicodedata.normalize("NFKC", str(value)).strip(), errors="coerce")
    return None if pd.isna(ts) else ts.date()


def _years(start: date, base: date) -> int:
    return base.year - start.year - ((base.month, base.day) < (start.month, start.day))


def bin_label(years: int, bins: list[int], unit: str) -> str:
    """bins=[29,39,49], unit="歳" → 29歳以下 / 30～39歳 / 40～49歳 / 50歳以上。"""
    if years <= bins[0]:
        return f"{bins[0]}{unit}以下"
    for lo, hi in zip(bins, bins[1:]):
        if lo < years <= hi:
            return f"{lo + 1}～{hi}{unit}"
    return f"{bins[-1] + 1}{unit}以上"


# ---------------------------------------------------------------- 組織
def org_table(cfg: ClientConfig) -> pd.DataFrame:
    """組織辞書。無ければ v1 の organization.units から最上位だけの辞書を作る。"""
    if cfg.org_units_dict:
        rows = [{"unit_id": u["id"], "parent_id": u.get("parent"), "name": u["name"], "level": u.get("level"),
                 "order": u.get("order", i)} for i, u in enumerate(cfg.org_units_dict, 1)]
    else:
        level = cfg.org_level
        rows = [{"unit_id": f"U{i:03d}", "parent_id": None, "name": name, "level": level, "order": i}
                for i, name in enumerate(cfg.org_units, 1)]
    return pd.DataFrame(rows, columns=["unit_id", "parent_id", "name", "level", "order"])


def _unit_lookup(org: pd.DataFrame, match: str) -> dict[str, str]:
    if match == "id":
        return {u: u for u in org["unit_id"]}
    names = org["name"].tolist()
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise NormalizeError(f"所属名 {dup} が組織辞書で重複しているため名称では照合できません（unit_match: id を使う）")
    return dict(zip(org["name"], org["unit_id"]))


# ---------------------------------------------------------------- 本体
def _dedupe(df: pd.DataFrame, rule: str | None, label: str, log: dict) -> pd.DataFrame:
    dup = df["respondent_id"].duplicated(keep=False) & df["respondent_id"].notna()
    n_dup = int(df.loc[dup, "respondent_id"].nunique())
    log[f"{label}_duplicate_ids"] = n_dup
    if not n_dup:
        return df
    if rule == "keep_last":
        return df.drop_duplicates("respondent_id", keep="last")
    if rule == "keep_first":
        return df.drop_duplicates("respondent_id", keep="first")
    raise NormalizeError(f"{label}: 重複 ID {n_dup} 件。normalize.duplicates（keep_last / keep_first）を確定してください")


def _read_roster(cfg: ClientConfig, spec: dict, zfill: int | None, base: date | None,
                 out: Normalized) -> pd.DataFrame:
    src = next((i for i in cfg.inputs if i.get("id") == spec["input"]), None)
    if src is None:
        raise NormalizeError(f"normalize.roster.input={spec['input']} が inputs にありません")
    attrs = spec.get("attributes") or {}
    cols = [spec["id_column"]] + [c for c in (spec.get("unit_column"), spec.get("status_column")) if c] \
        + [a["column"] for a in attrs.values()]
    df = pd.read_csv(cfg.path(src["path"]), encoding=spec.get("encoding", "utf-8-sig"), dtype=str,
                     keep_default_na=False, usecols=lambda c: str(c) in set(cols))
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise NormalizeError(f"対象者リストに列 {missing} がありません")
    df = df.replace({"": None})
    df = df[df[spec["id_column"]].notna()].copy()     # Excel 由来の空行
    df["respondent_id"] = df[spec["id_column"]].map(lambda v: normalize_id(v, zfill))
    df = _dedupe(df, spec.get("duplicates"), "対象者リスト", out.log)

    rules = cfg.raw.get("attribute_rules") or {}
    future = rules.get("future_date_rule")
    for dim, a in attrs.items():
        raw = df[a["column"]]
        kind = a.get("kind", "category")
        if kind == "category":
            df[f"attr:{dim}"] = raw.map(lambda v: normalize_label(v, "none"))
            continue
        if base is None:
            raise NormalizeError(f"属性 {dim}: 日付から算出するには attribute_rules.base_date が必要です")
        bins = rules.get("age_bins" if kind == "age" else "tenure_bins")
        if not bins:
            raise NormalizeError(f"属性 {dim}: attribute_rules.{'age_bins' if kind == 'age' else 'tenure_bins'} が未確定")
        unit = "歳" if kind == "age" else "年"
        vals, n_future, n_bad = [], 0, 0
        for v in raw:
            d = _parse_date(v)
            if d is None:
                n_bad += v is not None
                vals.append(None)
            elif d > base:
                n_future += 1
                if future != "missing":
                    raise NormalizeError(f"属性 {dim}: 基準日より後の日付 {n_future} 件。attribute_rules.future_date_rule を確定してください")
                vals.append(None)
            else:
                vals.append(bin_label(_years(d, base), bins, unit))
        df[f"attr:{dim}"] = vals
        if n_bad:
            out.warnings.append(f"属性 {dim}: 日付として読めない値 {n_bad} 件（欠損として扱う）")
        if n_future:
            out.warnings.append(f"属性 {dim}: 基準日より後の日付 {n_future} 件（欠損として扱う）")
    return df.drop(columns=[c for c in df.columns if c in [a["column"] for a in attrs.values()]
                            and c not in (spec["id_column"], spec.get("unit_column"), spec.get("status_column"))])


def normalize(cfg: ClientConfig) -> Normalized:
    spec = cfg.raw.get("normalize") or {}
    zfill = (spec.get("id_rule") or {}).get("zfill")
    rules = cfg.raw.get("attribute_rules") or {}
    base = _parse_date(rules.get("base_date")) if rules.get("base_date") else None
    out = Normalized(tables={})
    log = out.log

    # ---- 設問
    qrows = [{"question_id": q["id"], "display_no": f"Q{int(q['no']):02d}" if str(q.get("no", "")).isdigit() else q["id"],
              "text": q.get("text"), "scale_id": q.get("scale"), "role": q.get("role"), "category": q.get("category"),
              "axis": q.get("axis"), "reverse": bool(q.get("reverse")),
              "condition": json.dumps(q["condition"], ensure_ascii=False) if q.get("condition") else None}
             for q in cfg.questions]
    out.tables["questions"] = pd.DataFrame(qrows)

    # ---- 組織
    org = org_table(cfg)
    out.tables["org"] = org

    # ---- 回答
    path = cfg.path(cfg.raw["data"]["responses"])
    raw = read_csv(path, cfg.raw["data"].get("encoding", "utf-8-sig"))
    problems = check_headers(raw, cfg.questions)
    if problems:
        raise NormalizeError(f"{path.name}: 回答の列と設問辞書が一致しません\n  " + "\n  ".join(problems))
    log["responses_rows"] = len(raw)
    id_spec = cfg.raw["data"].get("respondent_id", {"source": "row_index"})
    if id_spec.get("source") == "column":
        rid = raw[id_spec["name"]].map(lambda v: normalize_id(v, zfill))
        n_blank = int(rid.isna().sum())
        if n_blank:
            out.warnings.append(f"回答者 ID が空の回答 {n_blank} 件（除外）")
        log["responses_blank_id"] = n_blank
    else:
        prefix = id_spec.get("prefix", f"{cfg.fiscal_year}-")
        rid = pd.Series([f"{prefix}{i + 1:04d}" for i in range(len(raw))])
    raw = raw.assign(respondent_id=rid.values)
    raw = raw[raw["respondent_id"].notna()]
    raw = _dedupe(raw, spec.get("duplicates"), "回答", log)
    log["responses_used"] = len(raw)

    mode = cfg.raw.get("label_normalize", "strip_circled_prefix")
    attr_rows: list[dict] = []
    # 回答の属性設問（v1 の attributes）
    for dim, a in cfg.attributes.items():
        q = cfg.q(a["question"])
        vals = raw.iloc[:, q["column"]].map(lambda v: normalize_label(v, mode))
        order = a.get("order") or []
        unknown = sorted(set(vals.dropna()) - set(order)) if order else []
        if unknown:
            raise NormalizeError(f"属性 {dim}（{q['id']}）: 定義に無い区分 {unknown}")
        attr_rows += [{"respondent_id": r, "dimension_id": dim, "value": v, "source": q["id"], "base_date": None}
                      for r, v in zip(raw["respondent_id"], vals) if v is not None]

    # 所属（回答の所属設問）
    unit_of: dict[str, str | None] = {}
    org_q = cfg.org.get("question")
    if org_q:
        lookup = _unit_lookup(org, spec.get("unit_match", "name"))
        labels = raw.iloc[:, cfg.q(org_q)["column"]].map(lambda v: normalize_label(v, mode))
        unknown = sorted(set(labels.dropna()) - set(lookup))
        if unknown:
            raise NormalizeError(f"組織辞書に無い所属: {unknown}")
        unit_of = {r: (lookup[v] if v is not None else None) for r, v in zip(raw["respondent_id"], labels)}

    # ---- 対象者リスト
    resp_ids = list(raw["respondent_id"])
    roster_spec = spec.get("roster")
    if roster_spec:
        roster = _read_roster(cfg, roster_spec, zfill, base, out)
        log["roster_rows"] = len(roster)
        status_col, excl = roster_spec.get("status_column"), set(roster_spec.get("exclude_values") or [])
        if roster_spec.get("unit_column"):
            lookup = _unit_lookup(org, roster_spec.get("unit_match", "id"))
            ru = roster[roster_spec["unit_column"]].map(lambda v: normalize_id(v) if roster_spec.get("unit_match", "id") == "id"
                                                         else normalize_label(v, "none"))
            unknown = sorted(set(ru.dropna()) - set(lookup))
            if unknown:
                raise NormalizeError(f"対象者リストの所属 {unknown[:10]} が組織辞書にありません")
            for r, u in zip(roster["respondent_id"], ru):
                unit_of[r] = lookup[u] if u is not None else None
        for dim in (roster_spec.get("attributes") or {}):
            attr_rows += [{"respondent_id": r, "dimension_id": dim, "value": v, "source": "対象者リスト",
                           "base_date": rules.get("base_date") if (roster_spec["attributes"][dim].get("kind") in ("age", "tenure")) else None}
                          for r, v in zip(roster["respondent_id"], roster[f"attr:{dim}"]) if v is not None]
        roster_ids = set(roster["respondent_id"])
        excluded = {r: s for r, s in zip(roster["respondent_id"], roster[status_col])} if status_col else {}
        rows = []
        for r in roster["respondent_id"]:
            reason = excluded.get(r)
            is_excl = reason in excl if reason is not None else False
            rows.append({"respondent_id": r, "responded": r in set(resp_ids), "unit_id": unit_of.get(r),
                         "status": "対象外" if is_excl else "対象", "reason": reason if is_excl else None,
                         "match": "照合済" if r in set(resp_ids) else "未回答"})
        for r in resp_ids:
            if r not in roster_ids:
                rows.append({"respondent_id": r, "responded": True, "unit_id": None, "status": "未照合",
                             "reason": "対象者リストに無い", "match": "未照合"})
        respondents = pd.DataFrame(rows)
    else:
        respondents = pd.DataFrame({"respondent_id": resp_ids, "responded": True,
                                    "unit_id": [unit_of.get(r) for r in resp_ids], "status": "回答者",
                                    "reason": None, "match": "名簿なし"})
        out.unresolved.append("対象者リストを読んでいないため、母数は回答者数（回答率は client.target_count から）")
    respondents = respondents.sort_values("respondent_id", kind="stable").reset_index(drop=True)
    out.tables["respondents"] = respondents

    # ---- 回答（long）
    attrs = pd.DataFrame(attr_rows, columns=["respondent_id", "dimension_id", "value", "source", "base_date"])
    attr_val = {(r, d): v for r, d, v in zip(attrs["respondent_id"], attrs["dimension_id"], attrs["value"])}
    rrows: list[dict] = []
    unknown_labels: list[str] = []
    for q in cfg.qs(LIKERT_ROLES):
        sc = cfg.scale(q.get("scale") or "agree5")
        known = set(map(str, sc["labels"]))
        excluded_labels = set(map(str, sc.get("excluded_labels", [])))
        cond = q.get("condition")
        vals = raw.iloc[:, q["column"]].map(lambda v: normalize_label(v, mode))
        bad = sorted(set(vals.dropna()) - known - excluded_labels)
        if bad:
            unknown_labels.append(f"{q['id']}: {bad[:5]}")
        for r, v in zip(raw["respondent_id"], vals):
            reason = None
            if v is None:
                reason = "未回答"
            elif v in excluded_labels:
                reason = "除外選択肢"
            elif cond and attr_val.get((r, cond["attribute"])) != cond["equals"]:
                reason = "条件外"
            rrows.append({"respondent_id": r, "question_id": q["id"], "value": None if reason else v,
                          "missing_reason": reason})
    if unknown_labels:
        raise NormalizeError("尺度に無い選択肢: " + " / ".join(unknown_labels))
    responses = pd.DataFrame(rrows, columns=["respondent_id", "question_id", "value", "missing_reason"])
    log["missing"] = responses["missing_reason"].value_counts().sort_index().to_dict()
    out.tables["responses"] = responses
    out.tables["attributes"] = attrs.sort_values(["dimension_id", "respondent_id"], kind="stable").reset_index(drop=True)

    # ---- 自由記述
    crow: list[dict] = []
    for q in cfg.qs(["freetext"]):
        for r, v in zip(raw["respondent_id"], raw.iloc[:, q["column"]]):
            text = str(v).strip() if v is not None else ""
            if text:
                sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                crow.append({"comment_id": f"{q['id']}-{sha[:12]}-{r}", "respondent_id": r, "question_id": q["id"],
                             "text": text, "text_sha": sha})
    out.tables["comments"] = pd.DataFrame(crow, columns=["comment_id", "respondent_id", "question_id", "text", "text_sha"])

    # ---- 段階ごとの人数（§5）
    st = respondents["status"].value_counts().to_dict()
    log["stages"] = {
        "対象者リスト": int(log.get("roster_rows", 0)) or None,
        "対象外": int(st.get("対象外", 0)),
        "対象": int(st.get("対象", 0)) or None,
        "有効回答（照合済）": int(((respondents["status"] == "対象") & respondents["responded"]).sum()) or None,
        "未照合": int(st.get("未照合", 0)),
        "回答者（名簿なし）": int(st.get("回答者", 0)),
        "所属なし": int((respondents["responded"] & respondents["unit_id"].isna()).sum()),
    }
    log["corrections"] = cfg.raw.get("corrections") or []
    if log["stages"]["未照合"]:
        out.warnings.append(f"対象者リストに無い回答 {log['stages']['未照合']} 件（population.unmatched_rule に従う）")
    if log["stages"]["所属なし"]:
        out.warnings.append(f"所属が決まらない回答 {log['stages']['所属なし']} 件（全庁には含め、所属別からは除く）")
    return out


def write_normalized(res: Normalized, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in TABLES:
        p = out_dir / f"{name}.csv"
        res.tables[name].to_csv(p, index=False, encoding="utf-8", lineterminator="\n")
        paths.append(p)
    p = out_dir / "normalize_log.json"
    p.write_text(json.dumps({"log": res.log, "warnings": res.warnings, "unresolved": res.unresolved},
                            ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    paths.append(p)
    return paths


def read_normalized(out_dir: Path) -> dict[str, pd.DataFrame]:
    return {name: pd.read_csv(out_dir / f"{name}.csv", dtype=str, keep_default_na=False).replace({"": None})
            for name in TABLES}
