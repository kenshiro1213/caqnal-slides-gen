"""publish 工程: private 集計 → 読者別の公開データ（仕様 v2 §7, §15）。

手順
1. 一次秘匿: 回答者数が最低人数未満の組織・属性区分は、人数・率・点数をまとめて非表示。
   セル（設問・カテゴリ・目的指標）の有効 n が最低人数未満なら、そのセルを非表示
2. 二次秘匿: 親（組織の親・全庁）から公開済みの子を差し引いた残り（直轄・所属なし・区分なしを含む）が
   1〜(最低人数−1) 人になる場合、公開している子のうち人数が最も少ないものを追加で非表示にする。
   回答者数とセルの有効 n の両方で行う
3. 読者の範囲で切り出す: 全庁・人事は全組織、部局長は自部局と配下、課長は自課が属する部局と配下。
   部局が決まらなければ止める（全庁へ広げない）
4. 公開データには非表示の値を残さない（行は残し、値を空にして suppressed に理由を入れる）

出力（run/public/<reader_id>/）
  stats.csv / response_status.csv / correlations.csv / model_fit.csv / ols_coefs.csv / tree_nodes.csv /
  tree_stability.csv / org.csv / manifest.json
  run/public/suppression.csv  非表示の一覧（scope, unit, 理由）。読者共通
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..config import ClientConfig
from .describe import parent_map

ALL = "全庁"
VALUE_COLS = ["n", "mean", "sd", "score", "pos", "neu", "neg"]


class PublishError(Exception):
    pass


@dataclass
class Published:
    readers: dict[str, dict[str, pd.DataFrame]]
    manifests: dict[str, dict]
    suppression: pd.DataFrame
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 秘匿
def protect_family(parent_n: float, children: dict[str, float], hidden: set[str], min_n: int) -> set[str]:
    """親から公開済みの子を引いた残りが 0 か最低人数以上になるまで、小さい子を追加で隠す。"""
    hidden = set(hidden)
    while True:
        shown = {k: v for k, v in children.items() if k not in hidden}
        rest = parent_n - sum(shown.values())
        if rest <= 0 or rest >= min_n or not shown:
            return hidden
        hidden.add(min(shown, key=lambda k: (shown[k], k)))


def families(org: pd.DataFrame, attr_scopes: list[str]) -> list[tuple[tuple[str, str], list[tuple[str, str]]]]:
    """（親スコープ, 子スコープの一覧）。組織の親子と、全庁 → 各属性の区分。"""
    fams = []
    kids: dict[str | None, list[str]] = {}
    for u, p in parent_map(org).items():
        kids.setdefault(p, []).append(u)
    fams.append(((ALL, ALL), [("組織", u) for u in kids.get(None, [])]))
    for p, cs in kids.items():
        if p is not None:
            fams.append((("組織", p), [("組織", c) for c in cs]))
    by_dim: dict[str, list[str]] = {}
    for sid in attr_scopes:
        by_dim.setdefault(sid.split("=", 1)[0], []).append(sid)
    for dim, sids in by_dim.items():
        fams.append(((ALL, ALL), [("属性", s) for s in sids]))
    return fams


def suppress(stats: pd.DataFrame, status: pd.DataFrame, org: pd.DataFrame, min_n: int,
             secondary: bool = True) -> tuple[set, set, list[dict]]:
    """非表示にするスコープとセルを決める。戻り値: (スコープ集合, (スコープ, 単位) 集合, 理由の行)。"""
    log: list[dict] = []
    n_scope = {(r.scope_type, r.scope_id): r.responded_n for r in status.itertuples()}
    hidden_scopes = {k for k, n in n_scope.items() if k != (ALL, ALL) and 0 < n < min_n}
    for k in sorted(hidden_scopes):
        log.append({"scope_type": k[0], "scope_id": k[1], "unit_type": None, "unit_id": None,
                    "reason": f"回答者 {min_n} 名未満"})
    attr_ids = sorted(s for t, s in n_scope if t == "属性")
    fams = families(org, attr_ids)
    if secondary:
        changed = True
        while changed:                       # 親が隠れると上の家族の計算が変わるので収束まで回す
            changed = False
            for parent, children in fams:
                if parent in hidden_scopes:
                    continue
                kids = {c: n_scope.get(c, 0) for c in children}
                extra = protect_family(n_scope.get(parent, 0), kids, hidden_scopes & set(children), min_n) \
                    - hidden_scopes
                for c in sorted(extra):
                    log.append({"scope_type": c[0], "scope_id": c[1], "unit_type": None, "unit_id": None,
                                "reason": f"差し引きで {parent[1]} 内の少人数が推測できるため"})
                if extra:
                    hidden_scopes |= extra
                    changed = True

    # セル
    hidden_cells: set = set()
    n_cell = {(r.scope_type, r.scope_id, r.unit_type, r.unit_id): r.n for r in stats.itertuples()}
    for (st, sid, ut, uid), n in n_cell.items():
        if (st, sid) not in hidden_scopes and (st, sid) != (ALL, ALL) and 0 < n < min_n:
            hidden_cells.add(((st, sid), (ut, uid)))
            log.append({"scope_type": st, "scope_id": sid, "unit_type": ut, "unit_id": uid,
                        "reason": f"有効回答 {min_n} 名未満"})
    if secondary:
        units = sorted({(ut, uid) for _, _, ut, uid in n_cell})
        for unit in units:
            for parent, children in fams:
                if parent in hidden_scopes or (parent, unit) in hidden_cells:
                    continue
                kids = {c: n_cell.get((*c, *unit), 0) for c in children}
                already = {c for c in children if c in hidden_scopes or (c, unit) in hidden_cells}
                extra = protect_family(n_cell.get((*parent, *unit), 0), kids, already, min_n) - already
                for c in sorted(extra):
                    hidden_cells.add((c, unit))
                    log.append({"scope_type": c[0], "scope_id": c[1], "unit_type": unit[0], "unit_id": unit[1],
                                "reason": f"差し引きで {parent[1]} 内の少人数が推測できるため"})
    return hidden_scopes, hidden_cells, log


# ---------------------------------------------------------------- 読者の範囲
def _descendants(org: pd.DataFrame, unit: str) -> list[str]:
    kids: dict[str, list[str]] = {}
    for u, p in parent_map(org).items():
        if p is not None:
            kids.setdefault(p, []).append(u)
    out, stack = [], [unit]
    while stack:
        u = stack.pop(0)
        out.append(u)
        stack = kids.get(u, []) + stack
    return out


def _top(org: pd.DataFrame, unit: str) -> str | None:
    parent = parent_map(org)
    seen, cur = set(), unit
    while cur is not None and cur not in seen:
        if cur not in parent:
            return None
        seen.add(cur)
        if parent[cur] is None:
            return cur
        cur = parent[cur]
    return None


def reader_units(reader: dict, org: pd.DataFrame) -> tuple[list[str], str | None]:
    """読者が見てよい組織と、自所属（部局長・課長）。"""
    kind = reader["kind"]
    if kind in ("all_staff", "hr"):
        return list(org["unit_id"]), None
    unit = None if reader.get("unit") is None else str(reader["unit"])
    if unit is None or unit not in set(org["unit_id"]):
        raise PublishError(f"読者 {reader['id']}: 所属 {unit} が組織辞書にありません（全庁へ広げない）")
    top = _top(org, unit)
    if top is None:
        raise PublishError(f"読者 {reader['id']}: 所属 {unit} の部局が決まりません（全庁へ広げない）")
    return _descendants(org, top), unit


# ---------------------------------------------------------------- 本体
def publish(cfg: ClientConfig, agg: dict[str, pd.DataFrame], models: dict[str, pd.DataFrame],
            org: pd.DataFrame) -> Published:
    min_n = cfg.publication.get("min_n")
    if min_n is None:
        raise PublishError("publication.min_n（公開の最低人数）が未確定のため公開データを作りません")
    secondary = cfg.publication.get("secondary_suppression", True)
    readers = cfg.readers or [{"id": ALL, "kind": "all_staff"}]
    stats, status = agg["stats"], agg["response_status"]
    hidden_scopes, hidden_cells, log = suppress(stats, status, org, min_n, secondary)
    warnings = []

    def mask_stats(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["suppressed"] = None
        for i, r in enumerate(df.itertuples()):
            sc = (r.scope_type, r.scope_id)
            if sc in hidden_scopes:
                df.iat[i, df.columns.get_loc("suppressed")] = "組織・区分を非表示"
            elif (sc, (r.unit_type, r.unit_id)) in hidden_cells:
                df.iat[i, df.columns.get_loc("suppressed")] = "セルを非表示"
        df.loc[df["suppressed"].notna(), VALUE_COLS] = None
        return df

    def mask_status(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        hide = [(t, s) in hidden_scopes for t, s in zip(df["scope_type"], df["scope_id"])]
        df["suppressed"] = ["組織・区分を非表示" if h else None for h in hide]
        df.loc[hide, ["target_n", "responded_n", "excluded_n", "rate"]] = None
        return df

    pub_stats, pub_status = mask_stats(stats), mask_status(status)
    nodes = models.get("tree_nodes", pd.DataFrame())
    if not nodes.empty:
        n_hidden = int(nodes["below_min_n"].astype(str).isin(["True", "true"]).sum())
        if n_hidden:
            warnings.append(f"決定木の {n_hidden} ノードが最低人数未満のため人数・平均を非表示")
        nodes = nodes.copy()
        below = nodes["below_min_n"].astype(str).isin(["True", "true"])
        nodes.loc[below, ["n", "mean", "pos"]] = None

    out: dict[str, dict[str, pd.DataFrame]] = {}
    manifests: dict[str, dict] = {}
    for reader in readers:
        units, own = reader_units(reader, org)
        allowed_attr = reader.get("attributes")
        keep_scope = lambda t, s: (t == ALL or (t == "組織" and s in units)
                                   or (t == "属性" and (allowed_attr is None or s.split("=", 1)[0] in allowed_attr)))
        st = pub_stats[[keep_scope(t, s) for t, s in zip(pub_stats["scope_type"], pub_stats["scope_id"])]]
        rs = pub_status[[keep_scope(t, s) for t, s in zip(pub_status["scope_type"], pub_status["scope_id"])]]
        tables = {"stats": st.reset_index(drop=True), "response_status": rs.reset_index(drop=True),
                  "correlations": agg["correlations"], "org": org[org["unit_id"].isin(units)].reset_index(drop=True)}
        for name in ("model_fit", "ols_coefs", "tree_stability"):
            tables[name] = models.get(name, pd.DataFrame())
        tables["tree_nodes"] = nodes
        out[reader["id"]] = tables
        manifests[reader["id"]] = {
            "reader": reader, "own_unit": own, "units": units, "min_n": min_n,
            "secondary_suppression": secondary,
            "hidden_scopes": sorted(f"{t}:{s}" for t, s in hidden_scopes if t == "属性" or s in units),
            "note": "全庁の値は自所属を含む",
        }
    return Published(readers=out, manifests=manifests, suppression=pd.DataFrame(
        log, columns=["scope_type", "scope_id", "unit_type", "unit_id", "reason"]), warnings=warnings)


def write_published(res: Published, out_dir: Path) -> list[Path]:
    paths = []
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "suppression.csv"
    res.suppression.to_csv(p, index=False, encoding="utf-8", lineterminator="\n")
    paths.append(p)
    for rid, tables in res.readers.items():
        d = out_dir / rid
        d.mkdir(parents=True, exist_ok=True)
        for name, df in tables.items():
            p = d / f"{name}.csv"
            df.to_csv(p, index=False, encoding="utf-8", lineterminator="\n")
            paths.append(p)
        p = d / "manifest.json"
        p.write_text(json.dumps(res.manifests[rid], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths.append(p)
    return paths


def read_public(out_dir: Path, reader_id: str) -> tuple[dict[str, pd.DataFrame], dict]:
    d = out_dir / reader_id
    tables = {}
    for p in sorted(d.glob("*.csv")):
        try:
            tables[p.stem] = pd.read_csv(p, dtype={"scope_id": str, "unit_id": str, "parent_id": str})
        except pd.errors.EmptyDataError:
            tables[p.stem] = pd.DataFrame()
    return tables, json.loads((d / "manifest.json").read_text(encoding="utf-8"))
