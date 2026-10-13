"""仕様 v2 の頁部品（docs/参考デッキ構成_佐賀市v2.md「共通の頁部品」）。

頁の骨格: タイトル（左上）／読者ラベル（右上）／結論の 1 文／本体／示唆ボックス／注記／フッター・頁番号。
図表は python-pptx のネイティブ chart と表（PowerPoint で編集できる）。図形に影を付けない。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION, XL_TICK_LABEL_POSITION
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from ...config import ClientConfig
from ..pptx_helpers import add_line, add_rect, add_table, add_text

PLACEHOLDER = "〔未記入〕"


class V2Theme:
    """pptx_helpers の関数が使う属性（family / sizes / hexv）を v2 の値で持つ。"""

    def __init__(self, cfg: ClientConfig):
        t = cfg.theme["v2"]
        fp = cfg.theme["font"]
        self.family = fp["presets"][fp["preset"]]["family"]
        self.colors = dict(t["colors"])
        self.colors.setdefault("brand_pale", self.colors["pale"])
        self.sizes = dict(t["size"])
        self.sizes.setdefault("small", t["size"]["small"])
        self.W, self.H, self.mx = t["width_in"], t["height_in"], t["margin_x"]

    def hexv(self, name: str) -> str:
        return self.colors[name].lstrip("#")


@dataclass
class Page:
    """1 枚分の描画状態。body_top〜body_bottom が本体の領域。"""

    slide: object
    th: V2Theme
    body_top: float = 2.05
    body_bottom: float = 8.45
    notes: list[str] = field(default_factory=list)

    @property
    def left(self) -> float:
        return self.th.mx

    @property
    def width(self) -> float:
        return self.th.W - 2 * self.th.mx


def new_presentation(th: V2Theme) -> Presentation:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(th.W), Inches(th.H)
    return prs


def blank(prs: Presentation):
    return prs.slides.add_slide(prs.slide_layouts[6])


# ---------------------------------------------------------------- 骨格
def frame(prs, th: V2Theme, title: str, label: str, footer: str, page_no: int, message: str | None = None,
          note: str | None = None) -> Page:
    s = blank(prs)
    W, H, mx = th.W, th.H, th.mx
    add_rect(s, th, 0, 0, W, 0.12, th.hexv("navy"))
    add_text(s, th, mx, 0.35, W * 0.62, 0.6, title, size=th.sizes["title"], bold=True, color=th.hexv("navy"),
             anchor="middle")
    add_text(s, th, W * 0.62, 0.35, W * 0.38 - mx, 0.3, label, size=th.sizes["note"], color=th.hexv("ink_soft"),
             align="right")
    add_text(s, th, W * 0.62, 0.62, W * 0.38 - mx, 0.3, "調査結果・施策提案", size=th.sizes["note"],
             color=th.hexv("ink_soft"), align="right")
    add_line(s, th, mx, 1.05, W - mx, 1.05, th.hexv("gold"), 1.5)
    if message:
        add_text(s, th, mx, 1.18, W - 2 * mx, 0.75, message, size=th.sizes["message"], bold=True,
                 color=th.hexv("ink"), anchor="middle")
    if note:
        add_text(s, th, mx, H - 0.95, W - 2 * mx, 0.42, note, size=th.sizes["note"], color=th.hexv("ink_soft"),
                 anchor="bottom")
    add_line(s, th, mx, H - 0.45, W - mx, H - 0.45, th.hexv("line"), 0.75)
    add_text(s, th, mx, H - 0.42, W * 0.7, 0.3, footer, size=th.sizes["note"] - 1, color=th.hexv("ink_soft"))
    add_text(s, th, W - mx - 1.0, H - 0.42, 1.0, 0.3, f"{page_no:02d}", size=th.sizes["note"], bold=True,
             color=th.hexv("navy"), align="right")
    return Page(s, th, body_top=2.05 if message else 1.3, body_bottom=H - 1.05 if note else H - 0.6)


def insight_box(p: Page, x, y, w, h, title: str, text: str | None, key: str | None = None) -> None:
    """「組織・人事への示唆」ボックス。text が無ければ未記入の枠（interpretation.yaml のキーを示す）。"""
    th = p.th
    add_rect(p.slide, th, x, y, w, h, th.hexv("pale"))
    add_rect(p.slide, th, x, y, 0.07, h, th.hexv("gold"))
    add_text(p.slide, th, x + 0.2, y + 0.12, w - 0.35, 0.35, title, size=th.sizes["small"], bold=True,
             color=th.hexv("navy"))
    if text:
        add_text(p.slide, th, x + 0.2, y + 0.5, w - 0.35, h - 0.6, text, size=th.sizes["small"],
                 color=th.hexv("ink"), line_spacing=1.25)
    else:
        hint = f"{PLACEHOLDER} 担当者が解釈を記入する（interpretation.yaml の {key}）" if key else PLACEHOLDER
        add_text(p.slide, th, x + 0.2, y + 0.5, w - 0.35, h - 0.6, hint, size=th.sizes["small"],
                 color=th.hexv("placeholder"))


def kpi(p: Page, x, y, w, h, title: str, value: str, unit: str, sub: list[str]) -> None:
    th = p.th
    add_rect(p.slide, th, x, y, w, h, th.hexv("bg"), th.hexv("line"))
    add_rect(p.slide, th, x, y, w, 0.07, th.hexv("navy"))
    add_text(p.slide, th, x + 0.2, y + 0.2, w - 0.4, 0.4, title, size=th.sizes["small"], bold=True,
             color=th.hexv("navy"))
    add_text(p.slide, th, x + 0.2, y + 0.6, w - 0.4, 0.9,
             [[{"text": value, "size": th.sizes["kpi"], "bold": True, "color": th.hexv("navy")},
               {"text": f" {unit}", "size": th.sizes["small"], "color": th.hexv("ink_soft")}]], anchor="middle")
    add_text(p.slide, th, x + 0.2, y + 1.55, w - 0.4, h - 1.65, sub, size=th.sizes["note"], color=th.hexv("ink_soft"))


def table(p: Page, x, y, w, rows: list[list], widths=None, row_h=0.36, aligns=None, colors=None, fills=None,
          size=None, bold_cells=None):
    th = p.th
    colors = dict(colors or {})
    for c in range(len(rows[0])):                 # 見出し行だけ白文字（本文は既定の濃い文字）
        colors.setdefault((0, c), "FFFFFF")
    return add_table(p.slide, th, x, y, w, rows, col_widths=widths, row_h=row_h, size=size or th.sizes["table"],
                     header_fill=th.hexv("navy"), aligns=aligns, colors=colors, fills=fills, bold_cells=bold_cells)


def text(p: Page, x, y, w, h, body, **kw):
    kw.setdefault("size", p.th.sizes["body"])
    return add_text(p.slide, p.th, x, y, w, h, body, **kw)


# ---------------------------------------------------------------- グラフ
def _rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h.lstrip("#"))


def _chart_font(chart, th: V2Theme, size: float) -> None:
    """グラフの文字に日本語フォントを入れる（a:latin だけだと東アジアの文字がテーマ既定に戻る）。"""
    chart.font.size = Pt(size)
    chart.font.name = th.family
    chart.font.color.rgb = _rgb(th.hexv("ink"))
    defrpr = chart._chartSpace.find(".//" + qn("a:defRPr"))  # noqa: SLF001
    if defrpr is not None and defrpr.find(qn("a:ea")) is None:
        etree.SubElement(defrpr, qn("a:ea")).set("typeface", th.family)


def _axis_style(axis, th: V2Theme, visible=True, gridlines=False):
    axis.visible = visible
    axis.has_major_gridlines = gridlines
    if gridlines:
        axis.major_gridlines.format.line.color.rgb = _rgb(th.hexv("line"))
    axis.format.line.color.rgb = _rgb(th.hexv("line"))


def bar_chart(p: Page, x, y, w, h, categories: list[str], series: dict[str, list], colors: list[str],
              horizontal=True, stacked=False, percent=False, number_format='0.0', labels=True, legend=None,
              max_value=None, label_size=None):
    th = p.th
    if not categories:                           # 全て非表示などで項目が無い
        return None
    data = CategoryChartData(number_format=number_format)
    data.categories = categories
    for name, vals in series.items():
        data.add_series(name, vals)
    if horizontal:
        kind = XL_CHART_TYPE.BAR_STACKED if stacked else XL_CHART_TYPE.BAR_CLUSTERED
        if percent:
            kind = XL_CHART_TYPE.BAR_STACKED_100
    else:
        kind = XL_CHART_TYPE.COLUMN_STACKED if stacked else XL_CHART_TYPE.COLUMN_CLUSTERED
    gf = p.slide.shapes.add_chart(kind, Inches(x), Inches(y), Inches(w), Inches(h), data)
    chart = gf.chart
    _chart_font(chart, th, label_size or th.sizes["note"] + 0.5)
    plot = chart.plots[0]
    plot.gap_width = 60
    if stacked or percent:
        plot.overlap = 100
    for s, c in zip(plot.series, colors):
        s.format.fill.solid()
        s.format.fill.fore_color.rgb = _rgb(c)
        s.invert_if_negative = False
    if labels:
        plot.has_data_labels = True
        dl = plot.data_labels
        dl.number_format = number_format
        dl.number_format_is_linked = False
        dl.font.size = Pt(label_size or th.sizes["note"])
        if stacked or percent:
            dl.position = XL_LABEL_POSITION.CENTER
        else:
            dl.position = XL_LABEL_POSITION.OUTSIDE_END
    ca, va = chart.category_axis, chart.value_axis
    _axis_style(ca, th)
    _axis_style(va, th, visible=False, gridlines=False)
    if horizontal:
        ca.reverse_order = True                  # 上から順に並べる
    if any(isinstance(v, (int, float)) and v < 0 for vals in series.values() for v in vals):
        ca.tick_label_position = XL_TICK_LABEL_POSITION.LOW   # 負の棒と項目名が重ならないよう軸の外側へ
    if max_value is not None:
        va.maximum_scale = max_value
        va.minimum_scale = 0
    chart.has_legend = bool(legend)
    if legend:
        chart.legend.position = XL_LEGEND_POSITION.TOP
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(th.sizes["note"])
    return chart


def line_chart(p: Page, x, y, w, h, categories: list[str], series: dict[str, list], colors: list[str]):
    th = p.th
    if not categories:
        return None
    data = CategoryChartData(number_format="0.0")
    data.categories = categories
    for name, vals in series.items():
        data.add_series(name, vals)
    chart = p.slide.shapes.add_chart(XL_CHART_TYPE.LINE_MARKERS, Inches(x), Inches(y), Inches(w), Inches(h),
                                     data).chart
    _chart_font(chart, th, th.sizes["note"] + 0.5)
    for s, c in zip(chart.plots[0].series, colors):
        s.format.line.color.rgb = _rgb(c)
        s.format.line.width = Pt(2.25)
        s.smooth = False
        s.marker.format.fill.solid()
        s.marker.format.fill.fore_color.rgb = _rgb(c)
        s.marker.format.line.color.rgb = _rgb(c)
    _axis_style(chart.category_axis, th)
    _axis_style(chart.value_axis, th, gridlines=True)
    chart.value_axis.tick_labels.font.size = Pt(th.sizes["note"])
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.TOP
    chart.legend.include_in_layout = False
    return chart


def scatter_chart(p: Page, x, y, w, h, points: list[tuple[str, float, float]], x_title: str, y_title: str,
                  highlight: set[str] | None = None):
    """点数（x）× 相関（y）。highlight の点は金色。各点の名前は Excel のデータに残る（ラベルは上位のみ）。"""
    th = p.th
    data = XyChartData()
    groups = {"関連が強い候補": [pt for pt in points if highlight and pt[0] in highlight],
              "その他": [pt for pt in points if not highlight or pt[0] not in highlight]}
    for name, pts in groups.items():
        if not pts:
            continue
        s = data.add_series(name)
        for _, vx, vy in pts:
            s.add_data_point(vx, vy)
    chart = p.slide.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER, Inches(x), Inches(y), Inches(w), Inches(h),
                                     data).chart
    _chart_font(chart, th, th.sizes["note"])
    colors = [th.hexv("gold"), th.hexv("navy")] if groups["関連が強い候補"] else [th.hexv("navy")]
    names = [n for n, pts in groups.items() if pts]
    for s, c, name in zip(chart.plots[0].series, colors, names):
        s.format.line.fill.background()
        s.marker.size = 9 if name == "関連が強い候補" else 7
        s.marker.format.fill.solid()
        s.marker.format.fill.fore_color.rgb = _rgb(c)
        s.marker.format.line.color.rgb = _rgb("FFFFFF")
        if name == "関連が強い候補":
            pts = groups[name]
            for i, (label, _, _) in enumerate(pts):
                dl = s.points[i].data_label
                dl.has_text_frame = True
                dl.text_frame.text = label
                dl.position = XL_LABEL_POSITION.RIGHT
                dl.font.size = Pt(th.sizes["note"] - 0.5)
    for axis, title in ((chart.category_axis, x_title), (chart.value_axis, y_title)):
        _axis_style(axis, th, gridlines=True)
        axis.has_title = True
        axis.axis_title.text_frame.text = title
        axis.axis_title.text_frame.paragraphs[0].runs[0].font.size = Pt(th.sizes["note"])
        axis.tick_labels.font.size = Pt(th.sizes["note"])
    chart.has_legend = len(names) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.TOP
        chart.legend.include_in_layout = False
    return chart
