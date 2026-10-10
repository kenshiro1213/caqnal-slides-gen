"""読者別デッキの組立（仕様 v2 §13, §14）。config/decks_v2/<デッキ>.yaml のスライドリスト → PPTX。

読者 kind ごとの既定デッキ:
  all_staff → 全庁, 全庁_詳細付録 / hr → 人事 / dept_head → 部局長 / section_head → 課長
client.yaml の readers[].decks で上書きできる。
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from ...config import CONFIG_DIR, ClientConfig, load_yaml
from ..deck import FIXED_DT
from .base import V2Theme, new_presentation
from .data import ALL, ReportData
from .slides import REGISTRY, Ctx, fill_toc

DEFAULT_DECKS = {"all_staff": ["全庁", "全庁_詳細付録"], "hr": ["人事"], "dept_head": ["部局長"],
                 "section_head": ["課長"]}
READER_LABEL = {"all_staff": "全庁向け", "hr": "人事担当向け", "dept_head": "部局長向け", "section_head": "課長向け"}


def deck_spec(cfg: ClientConfig, name: str) -> dict:
    override = (cfg.raw.get("deck_overrides_v2") or {}).get(name)
    return load_yaml(cfg.path(override) if override else CONFIG_DIR / "decks_v2" / f"{name}.yaml")


def _fixed_zip(data: bytes) -> bytes:
    """zip 内のタイムスタンプと docProps の日時を固定する（埋込 Excel も再帰的に）。"""
    with zipfile.ZipFile(io.BytesIO(data)) as zin:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
            for info in sorted(zin.infolist(), key=lambda i: i.filename):
                body = zin.read(info.filename)
                if info.filename == "docProps/core.xml":
                    body = re.sub(rb"(<dcterms:(created|modified)[^>]*>)[^<]*(</dcterms:\2>)",
                                  rb"\g<1>2020-01-01T00:00:00Z\g<3>", body)
                elif info.filename.endswith(".xlsx"):
                    body = _fixed_zip(body)
                zi = zipfile.ZipInfo(info.filename, date_time=FIXED_DT)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o600 << 16
                zout.writestr(zi, body)
    return buf.getvalue()


def build(cfg: ClientConfig, data: ReportData, deck: dict, reader: dict, out_path: Path) -> Path:
    th = V2Theme(cfg)
    prs = new_presentation(th)
    c = cfg.client
    kind = reader["kind"]
    own = data.own_unit
    scope = (ALL, ALL) if own is None else ("組織", own)
    who = data.unit_name(own) if own else c.get("fiscal_year_label", "")
    label = f"{c['name']}｜{who}｜{reader.get('label') or READER_LABEL[kind]}"
    footer = f"© CAQNAL CO.,LTD.  •  {c['name']} {c.get('fiscal_year_label', '')}{c.get('survey_title', 'エンゲージメント調査')}"
    ctx = Ctx(prs=prs, th=th, d=data, label=label, footer=footer, scope=scope)
    for spec in deck["slides"]:
        fn = REGISTRY.get(spec["type"])
        if fn is None:
            raise KeyError(f"未知の slide type: {spec['type']}（render/v2/slides.py の REGISTRY）")
        fn(ctx, spec)
    fill_toc(ctx)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    prs.save(buf)
    out_path.write_bytes(_fixed_zip(buf.getvalue()))
    return out_path


def render_reader(cfg: ClientConfig, public_dir: Path, compose_dir: Path, reader: dict, out_dir: Path) -> list[Path]:
    data = ReportData.load(cfg, public_dir, compose_dir, reader["id"])
    decks = reader.get("decks") or DEFAULT_DECKS[reader["kind"]]
    out = []
    for name in decks:
        deck = deck_spec(cfg, name)
        fname = deck["filename"].format(client=cfg.client["name"], fy=cfg.fiscal_year, reader=reader["id"])
        out.append(build(cfg, data, deck, reader, out_dir / fname))
    return out
