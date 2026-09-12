"""描画テーマ（config/theme.yaml）。色・フォント・座標を一元管理し、matplotlib にフォントを登録する。"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
from pptx.util import Inches  # noqa: E402

from ..config import PROJECT_ROOT, ClientConfig  # noqa: E402


class Theme:
    def __init__(self, cfg: ClientConfig):
        self.t = cfg.theme
        self.colors: dict = self.t["colors"]
        self.sizes: dict = self.t["font"]["size"]
        self.slide: dict = self.t["slide"]
        self.dpi: int = int(self.slide.get("dpi", 200))
        fp = self.t["font"]
        preset = fp["presets"][fp["preset"]]
        fallback = fp["presets"]["noto"]
        self.family: str = preset["family"]
        self.regular_file = PROJECT_ROOT / (preset.get("regular") or fallback["regular"])
        self.bold_file = PROJECT_ROOT / (preset.get("bold") or fallback["bold"])
        self.mpl_family = preset["family"] if preset.get("regular") else fallback["family"]
        for f in (self.regular_file, self.bold_file):
            if f.exists():
                font_manager.fontManager.addfont(str(f))
        matplotlib.rcParams["font.family"] = self.mpl_family
        matplotlib.rcParams["axes.unicode_minus"] = False
        matplotlib.rcParams["svg.hashsalt"] = "caqnal"
        self.template: Path = PROJECT_ROOT / self.t["template"]

    # ---- 色
    def c(self, name: str) -> str:
        v = self.colors[name]
        return v if v.startswith("#") else f"#{v}"

    def hexv(self, name: str) -> str:
        return self.colors[name].lstrip("#")

    def seg_color(self, key: str) -> str:
        return "#" + self.colors["segments"][key].lstrip("#")

    def zone_color(self, key: str) -> str:
        return "#" + self.colors["zones"][key].lstrip("#")

    def dist_colors(self) -> list[str]:
        return ["#" + c.lstrip("#") for c in self.colors["dist"]]

    # ---- フォント（matplotlib）
    def fp(self, size: float, bold: bool = False) -> FontProperties:
        f = self.bold_file if bold else self.regular_file
        return FontProperties(fname=str(f), size=size)

    # ---- 座標
    def box(self, key: str) -> tuple[float, float, float, float]:
        b = self.slide[key]
        return b["x"], b["y"], b["w"], b["h"]

    def box_emu(self, key: str):
        x, y, w, h = self.box(key)
        return Inches(x), Inches(y), Inches(w), Inches(h)
