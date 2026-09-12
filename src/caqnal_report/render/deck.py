"""デッキ組立: decks/*.yaml のスライドリスト → pptx。部局別は scope を置換してループ。

normalize_pptx で zip のタイムスタンプ・docProps を固定し、同じ入力から同じバイト列を得る。
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from ..analysis.pipeline import AnalysisResult
from ..config import ClientConfig, load_yaml
from .pptx_helpers import new_slide, open_template, set_page_number
from . import slides_method  # noqa: F401  （手法解説デッキの builder を REGISTRY に登録）
from .slides import REGISTRY, Ctx
from .theme import Theme

FIXED_DT = (2020, 1, 1, 0, 0, 0)


def normalize_pptx(path: Path) -> None:
    with zipfile.ZipFile(path) as zin:
        entries = sorted(zin.infolist(), key=lambda i: i.filename)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
            for info in entries:
                data = zin.read(info.filename)
                if info.filename == "docProps/core.xml":
                    text = data.decode("utf-8")
                    text = re.sub(r"(<dcterms:(created|modified)[^>]*>)[^<]*(</dcterms:\2>)",
                                  r"\g<1>2020-01-01T00:00:00Z\g<3>", text)
                    data = text.encode("utf-8")
                zi = zipfile.ZipInfo(info.filename, date_time=FIXED_DT)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o600 << 16
                zout.writestr(zi, data)
    path.write_bytes(buf.getvalue())


def _layout_key(spec: dict) -> str:
    if spec.get("layout"):
        return spec["layout"]
    t = spec["type"]
    return "cover" if t == "cover" else "section" if t == "section" else "toc" if t == "toc" else "body"


def build_deck(cfg: ClientConfig, res: AnalysisResult, theme: Theme, deck: dict, out_path: Path, charts_dir: Path,
               scope_type: str = "全庁", scope: str = "全庁") -> Path:
    prs = open_template(theme)
    ctx = Ctx(cfg=cfg, res=res, theme=theme, charts_dir=charts_dir, scope_type=scope_type, scope=scope, deck=deck)
    for i, spec in enumerate(deck["slides"], start=1):
        builder = REGISTRY.get(spec["type"])
        if builder is None:
            raise KeyError(f"未知のスライド type: {spec['type']}")
        slide = new_slide(prs, theme, _layout_key(spec))
        builder(slide, ctx, spec)
        if spec["type"] != "cover":
            set_page_number(slide, theme, i)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))
    normalize_pptx(out_path)
    return out_path


def _fname(cfg: ClientConfig, pattern: str, scope: str = "全庁") -> str:
    return pattern.format(client=cfg.client["name"], fy=cfg.fiscal_year, scope=scope)


def render_all(cfg: ClientConfig, res: AnalysisResult, out_dir: Path, decks: list[str], pdf: bool = False) -> list[Path]:
    theme = Theme(cfg)
    written: list[Path] = []
    min_n = cfg.analysis["anonymity"]["min_n"]
    for name in decks:
        if name == "excel":
            from .excel import write_excel

            written.append(write_excel(cfg, res, out_dir / _fname(cfg, "{client}_{fy}_詳細集計.xlsx")))
            continue
        deck = load_yaml(cfg.deck_path(name))
        if deck.get("per_org"):
            for org in res.summary["organizations"]:
                if org["n"] < min_n:
                    continue
                out = out_dir / _fname(cfg, deck["filename"], org["name"])
                charts = out_dir / "charts" / name / org["name"]
                written.append(build_deck(cfg, res, theme, deck, out, charts, cfg.org_level, org["name"]))
        else:
            out = out_dir / _fname(cfg, deck["filename"])
            written.append(build_deck(cfg, res, theme, deck, out, out_dir / "charts" / name))
    if pdf:
        from .pdf import convert_to_pdf

        for p in list(written):
            if p.suffix == ".pptx":
                written.append(convert_to_pdf(p, p.parent))
    return written
