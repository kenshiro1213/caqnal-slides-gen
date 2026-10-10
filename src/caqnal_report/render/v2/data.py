"""render が読むデータ: 読者別の公開データ（run/public/<読者>/）と compose の文章（run/compose/<読者>/）。

private の回答・集計は読まない。設問文・指標名などの定義は設定（ClientConfig）から取る。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ...analysis.describe import categories, driver_ids, objectives
from ...analysis.publish import read_public
from ...config import ClientConfig

ALL = "全庁"
HIDDEN = "非表示"
NA = "－"


def isnum(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def f1(v, digits: int = 1) -> str:
    return f"{v:.{digits}f}" if isnum(v) else NA


def pct(v, digits: int = 1) -> str:
    return f"{v * 100:.{digits}f}%" if isnum(v) else NA


def diff(v, digits: int = 1) -> str:
    return f"{v:+.{digits}f}" if isnum(v) else NA


def num(v) -> str:
    return f"{int(v):,}" if isnum(v) else NA


@dataclass
class ReportData:
    cfg: ClientConfig
    reader_id: str
    tables: dict[str, pd.DataFrame]
    manifest: dict
    compose: dict

    @classmethod
    def load(cls, cfg: ClientConfig, public_dir: Path, compose_dir: Path, reader_id: str) -> "ReportData":
        tables, manifest = read_public(public_dir, reader_id)
        cpath = compose_dir / reader_id / "compose.json"
        compose = json.loads(cpath.read_text(encoding="utf-8")) if cpath.exists() else {}
        return cls(cfg, reader_id, tables, manifest, compose)

    # ---- 定義（設定から）
    @property
    def objectives(self) -> list[dict]:
        st = self.tables["stats"]
        have = set(st.loc[st.unit_type == "目的指標", "unit_id"])
        return [o for o in objectives(self.cfg) if o["id"] in have]

    @property
    def categories(self) -> list[str]:
        return [c for c, _ in categories(self.cfg)]

    @property
    def drivers(self) -> list[str]:
        return driver_ids(self.cfg)

    def qlabel(self, qid: str, width: int | None = None) -> str:
        q = self.cfg.q(qid)
        text = (q.get("text") or "").replace("\n", " ")
        if width and len(text) > width:
            text = text[: width - 1] + "…"
        return f"{qid} {text}"

    def obj_name(self, oid: str, short: bool = False) -> str:
        o = next(o for o in objectives(self.cfg) if o["id"] == oid)
        return (o.get("short") if short else o.get("name")) or oid

    # ---- 組織
    @property
    def org(self) -> pd.DataFrame:
        o = self.tables["org"].copy()
        o["order"] = o["order"].astype(float)
        return o.sort_values("order", kind="stable")

    def unit_name(self, uid: str) -> str:
        o = self.tables["org"]
        return o.loc[o.unit_id == uid, "name"].iloc[0]

    def children(self, uid: str | None) -> list[str]:
        o = self.org
        if uid is None:
            return list(o.loc[o.parent_id.isna(), "unit_id"])
        return list(o.loc[o.parent_id == uid, "unit_id"])

    @property
    def own_unit(self) -> str | None:
        return self.manifest.get("own_unit")

    # ---- 集計値
    def stat(self, scope_type: str, scope_id: str, unit_type: str, unit_id: str) -> dict | None:
        st = self.tables["stats"]
        r = st[(st.scope_type == scope_type) & (st.scope_id == scope_id) & (st.unit_type == unit_type)
               & (st.unit_id == unit_id)]
        return None if r.empty else r.iloc[0].to_dict()

    def stats_of(self, scope_type: str, scope_id: str, unit_type: str) -> pd.DataFrame:
        st = self.tables["stats"]
        return st[(st.scope_type == scope_type) & (st.scope_id == scope_id) & (st.unit_type == unit_type)]

    def status(self, scope_type: str, scope_id: str) -> dict | None:
        rs = self.tables["response_status"]
        r = rs[(rs.scope_type == scope_type) & (rs.scope_id == scope_id)]
        return None if r.empty else r.iloc[0].to_dict()

    def hidden(self, scope_type: str, scope_id: str) -> bool:
        s = self.status(scope_type, scope_id)
        return s is not None and isinstance(s.get("suppressed"), str)

    def cell(self, scope_type: str, scope_id: str, unit_type: str, unit_id: str, field: str = "score",
             fmt=f1) -> str:
        r = self.stat(scope_type, scope_id, unit_type, unit_id)
        if r is None:
            return NA
        if isinstance(r.get("suppressed"), str):
            return HIDDEN
        return fmt(r.get(field))

    def attributes(self) -> list[str]:
        st = self.tables["stats"]
        dims: list[str] = []
        for sid in st.loc[st.scope_type == "属性", "scope_id"]:
            d = sid.split("=", 1)[0]
            if d not in dims:
                dims.append(d)
        order = list(self.cfg.attributes) + list((self.cfg.raw.get("attribute_rules") or {}).get("dimensions") or [])
        return sorted(dims, key=lambda d: order.index(d) if d in order else len(order))

    def attr_values(self, dim: str) -> list[str]:
        """属性の区分（設定の order 順。無ければ出現順）。"""
        order = (self.cfg.attributes.get(dim) or {}).get("order")
        rs = self.tables["response_status"]
        vals = [s.split("=", 1)[1] for s in rs.loc[rs.scope_type == "属性", "scope_id"] if s.split("=", 1)[0] == dim]
        return [v for v in order if v in vals] if order else vals

    def corr(self, oid: str, unit_type: str) -> pd.DataFrame:
        c = self.tables.get("correlations", pd.DataFrame())
        if c.empty:
            return c
        return c[(c.objective_id == oid) & (c.unit_type == unit_type)]

    # ---- compose の文章
    def text(self, key: str) -> str | None:
        return (self.compose.get("insights") or {}).get(key)
