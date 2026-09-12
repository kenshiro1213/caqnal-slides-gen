"""図表を PNG に描く（matplotlib Agg）。全関数は (theme, path, ...) → path。

仕様書 §8 の推奨表現に対応:
  hbar_scores(カテゴリ横棒＋基準線＋前年差) / dist_stacked(回答分布) / heatmap(組織・属性) /
  bubble(ポジショニング・Impact×Score) / stacked100(4類型) / scatter_labeled(4象限・ばらつき) /
  bar_counts(件数) / render_table(密な表)
色の意味は固定（ポジティブ=青、注意=桃、中立=グレー）。軸範囲は固定して差を誇張しない。
"""
from __future__ import annotations

import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.patches import Rectangle

from .theme import Theme


# --------------------------------------------------------------------------- utils
def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=fig.dpi, facecolor="white", metadata={"Software": None})
    plt.close(fig)
    return path


def _fig(theme: Theme, w: float, h: float):
    fig = plt.figure(figsize=(w, h), dpi=theme.dpi)
    return fig


def wrap_cjk(text: str, n: int, max_lines: int = 2) -> str:
    text = str(text)
    lines = [text[i:i + n] for i in range(0, len(text), n)]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max(1, n - 1)] + "…"
    return "\n".join(lines)


def fmt(v, nd: int = 2, signed: bool = False, na: str = "－") -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return na
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isnan(v):
        return na
    return f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}"


def fmt_pct(v, na: str = "－") -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return na
    return f"{float(v):.0f}%"


def _style_axes(ax, theme: Theme, grid_axis: str | None = "x"):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(theme.c("line"))
    ax.tick_params(colors=theme.c("ink_soft"), length=0)
    if grid_axis:
        ax.grid(axis=grid_axis, color=theme.c("line"), linewidth=0.6, alpha=0.7)
        ax.set_axisbelow(True)


def diverging_cmap(theme: Theme):
    hm = theme.colors["heatmap"]
    return LinearSegmentedColormap.from_list("cql", ["#" + hm["low"].lstrip("#"), "#" + hm["mid"].lstrip("#"),
                                                     "#" + hm["high"].lstrip("#")])


# --------------------------------------------------------------------------- charts
def hbar_scores(theme: Theme, path: Path, labels: list[str], values: list[float], *, prev: list[float] | None = None,
                diffs: list[float] | None = None, diff_title: str = "前年差", ref: float | None = None,
                ref_label: str = "全庁", xlim=(1, 5), w: float = 6.0, h: float = 4.0, label_chars: int = 18,
                colors: list[str] | None = None, sig: list[int] | None = None, n_labels: list[str] | None = None,
                font: float = 8.0, left: float = 0.30) -> Path:
    """横棒（平均）。◇=前回値、右列=差分、点線=基準。left=ラベル領域の割合。"""
    n = len(labels)
    fig = _fig(theme, w, h)
    right = 0.86 if diffs is not None else 0.97
    ax = fig.add_axes([left, 0.06, right - left, 0.88])
    ys = np.arange(n)[::-1]
    cols = colors or [theme.c("positive") if (ref is None or v >= ref) else theme.c("brand_light") for v in values]
    vals = [np.nan if v is None else v for v in values]
    ax.barh(ys, [0 if np.isnan(v) else v - xlim[0] for v in vals], left=xlim[0], height=0.62, color=cols, zorder=3)
    for y, v in zip(ys, vals):
        if not np.isnan(v):
            ax.text(v + 0.03, y, fmt(v), va="center", ha="left", fontproperties=theme.fp(font), color=theme.c("ink"))
        else:
            ax.text(xlim[0] + 0.03, y, "－", va="center", ha="left", fontproperties=theme.fp(font), color=theme.c("ink_soft"))
    if prev is not None:
        pv = [np.nan if p is None else p for p in prev]
        ax.scatter(pv, ys, marker="D", s=22, facecolor="white", edgecolor=theme.c("ink_soft"), zorder=4, linewidth=1)
    if ref is not None:
        ax.axvline(ref, color=theme.c("ink_soft"), linestyle="--", linewidth=0.9, zorder=2)
        ax.text(ref, n - 0.35, f"{ref_label} {fmt(ref)}", ha="center", va="bottom", fontproperties=theme.fp(font - 1),
                color=theme.c("ink_soft"))
    ax.set_yticks(ys)
    ticklabels = [wrap_cjk(l, label_chars, 2) for l in labels]
    if n_labels:
        ticklabels = [f"{t}\n(n={m})" if m else t for t, m in zip(ticklabels, n_labels)]
    ax.set_yticklabels(ticklabels, fontproperties=theme.fp(font))
    ax.set_xlim(xlim[0], xlim[1] + 0.35)
    ax.set_ylim(-0.6, n - 0.4 + (0.5 if ref is not None else 0))
    ax.set_xticks(np.arange(xlim[0], xlim[1] + 0.01, 1))
    ax.set_xticklabels([str(int(t)) for t in np.arange(xlim[0], xlim[1] + 0.01, 1)], fontproperties=theme.fp(font - 1))
    _style_axes(ax, theme, "x")
    if diffs is not None:
        ax2 = fig.add_axes([right + 0.005, 0.06, 0.97 - right, 0.88])
        ax2.set_ylim(ax.get_ylim())
        ax2.axis("off")
        ax2.text(0.5, n - 0.35, diff_title, ha="center", va="bottom", fontproperties=theme.fp(font - 0.5, True),
                 color=theme.c("ink_soft"))
        for i, (y, d) in enumerate(zip(ys, diffs)):
            s = sig[i] if sig is not None else 0
            col = theme.c("positive") if s > 0 else theme.c("negative") if s < 0 else theme.c("ink")
            ax2.text(0.5, y, fmt(d, signed=True), ha="center", va="center", fontproperties=theme.fp(font, s != 0), color=col)
    if prev is not None:
        ax.scatter([], [], marker="D", s=22, facecolor="white", edgecolor=theme.c("ink_soft"), label="前回")
        ax.legend(loc="lower right", prop=theme.fp(font - 1), frameon=False)
    return _save(fig, path)


def dist_stacked(theme: Theme, path: Path, labels: list[str], dists: list[list[float]], legend: list[str], *,
                 w: float = 6.0, h: float = 2.4, label_chars: int = 22, font: float = 8.0, show_pct_min: float = 6) -> Path:
    """回答分布の 100% 積み上げ横棒（左=肯定）。"""
    n = len(labels)
    fig = _fig(theme, w, h)
    ax = fig.add_axes([0.34, 0.20, 0.64, 0.74])
    ys = np.arange(n)[::-1]
    cols = theme.dist_colors()
    for i, lab in enumerate(labels):
        left = 0.0
        for k, share in enumerate(dists[i]):
            share = 0 if share is None or np.isnan(share) else share
            ax.barh(ys[i], share, left=left, height=0.6, color=cols[k % len(cols)], zorder=3, edgecolor="white", linewidth=0.5)
            if share >= show_pct_min:
                ax.text(left + share / 2, ys[i], f"{share:.0f}", ha="center", va="center", fontproperties=theme.fp(font - 1),
                        color="white" if k in (0, len(dists[i]) - 1) else theme.c("ink"))
            left += share
    ax.set_yticks(ys)
    ax.set_yticklabels([wrap_cjk(l, label_chars, 2) for l in labels], fontproperties=theme.fp(font))
    ax.set_xlim(0, 100)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xticklabels(["0", "25", "50", "75", "100%"], fontproperties=theme.fp(font - 1))
    _style_axes(ax, theme, None)
    handles = [Rectangle((0, 0), 1, 1, color=cols[k % len(cols)]) for k in range(len(legend))]
    ax.legend(handles, legend, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=len(legend), frameon=False,
              prop=theme.fp(font - 1), handlelength=1.0, columnspacing=1.0)
    return _save(fig, path)


def heatmap(theme: Theme, path: Path, row_labels: list[str], col_labels: list[str], values: np.ndarray, *,
            annot: np.ndarray | None = None, vlim: float = 0.3, w: float = 9.0, h: float = 4.2, font: float = 7.5,
            row_chars: int = 12, col_chars: int = 6, n_col: list | None = None, cbar_label: str = "全庁差",
            first_col_values: np.ndarray | None = None, first_col_title: str = "", title: str = "") -> Path:
    """発散カラーのヒートマップ。NaN は「－」。first_col_values は左端に絶対値列（無着色）として描く。"""
    R, Cn = values.shape
    has_first = first_col_values is not None
    ncols = Cn + (1 if has_first else 0)
    fig = _fig(theme, w, h)
    left = 0.17 if row_chars <= 12 else 0.22
    top_pad = 0.17 if h >= 2.5 else min(0.30, 0.55 / h)
    ax = fig.add_axes([left, 0.03, 0.97 - left, 0.97 - top_pad - 0.03])
    cmap = diverging_cmap(theme)
    norm = TwoSlopeNorm(vmin=-vlim, vcenter=0, vmax=vlim)
    full = np.full((R, ncols), np.nan)
    off = 1 if has_first else 0
    full[:, off:] = values
    ax.imshow(np.ma.masked_invalid(full), cmap=cmap, norm=norm, aspect="auto")
    ax.set_xlim(-0.5, ncols - 0.5)
    for i in range(R):
        if has_first:
            ax.add_patch(Rectangle((-0.5, i - 0.5), 1, 1, color=theme.c("bg_soft")))
            ax.text(0, i, fmt(first_col_values[i]), ha="center", va="center", fontproperties=theme.fp(font, True), color=theme.c("ink"))
        for j in range(Cn):
            v = values[i, j]
            jj = j + off
            if np.isnan(v):
                ax.add_patch(Rectangle((jj - 0.5, i - 0.5), 1, 1, color="#F0F0F0"))
                ax.text(jj, i, "－", ha="center", va="center", fontproperties=theme.fp(font), color=theme.c("ink_soft"))
            else:
                txt = annot[i, j] if annot is not None else fmt(v, signed=True)
                ax.text(jj, i, txt, ha="center", va="center", fontproperties=theme.fp(font),
                        color="white" if abs(v) > vlim * 0.75 else theme.c("ink"))
    ax.set_xticks(range(ncols))
    cl = ([first_col_title] if has_first else []) + list(col_labels)
    ax.set_xticklabels([wrap_cjk(c, col_chars, 3) for c in cl], fontproperties=theme.fp(font - 0.5))
    ax.xaxis.tick_top()
    ax.set_yticks(range(R))
    ylabels = [wrap_cjk(r, row_chars, 2) for r in row_labels]
    if n_col is not None:
        ylabels = [f"{l}  n={n}" for l, n in zip(ylabels, n_col)]
    ax.set_yticklabels(ylabels, fontproperties=theme.fp(font))
    ax.tick_params(length=0, pad=2)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_xticks(np.arange(-0.5, ncols, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, R, 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.5)
    cax = fig.add_axes([0.80, 0.985 - 0.02, 0.16, 0.015])
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
    cb.set_ticks([-vlim, 0, vlim])
    cb.ax.tick_params(labelsize=font - 2, length=0, pad=1)
    for t in cb.ax.get_xticklabels():
        t.set_fontproperties(theme.fp(font - 2))
    fig.text(0.79, 0.975, cbar_label, ha="right", va="center", fontproperties=theme.fp(font - 1), color=theme.c("ink_soft"))
    if title:
        fig.text(0.01, 0.975, title, ha="left", va="center", fontproperties=theme.fp(font + 1.5, True), color=theme.c("brand_dark"))
    return _save(fig, path)


def bubble(theme: Theme, path: Path, xs, ys, sizes, labels, *, xline: float | None = None, yline: float | None = None,
           xlabel: str = "", ylabel: str = "", w: float = 6.2, h: float = 4.3, font: float = 7.5,
           colors: list[str] | None = None, quadrant_text: dict | None = None, xlim=None, ylim=None,
           size_scale: float = 6.0, zone_fill: dict | None = None) -> Path:
    """バブル散布図。quadrant_text={"tl":..,"tr":..,"bl":..,"br":..}。zone_fill で象限を淡く塗る。"""
    fig = _fig(theme, w, h)
    ax = fig.add_axes([0.10, 0.11, 0.87, 0.85])
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    ok = ~(np.isnan(xs) | np.isnan(ys))
    if xlim is None:
        pad = (np.nanmax(xs) - np.nanmin(xs)) * 0.18 + 0.05
        xlim = (np.nanmin(xs) - pad, np.nanmax(xs) + pad)
    if ylim is None:
        pad = (np.nanmax(ys) - np.nanmin(ys)) * 0.18 + 0.05
        ylim = (np.nanmin(ys) - pad, np.nanmax(ys) + pad)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    if zone_fill and xline is not None and yline is not None:
        for key, col in zone_fill.items():
            x0 = xlim[0] if key[1] == "l" else xline
            x1 = xline if key[1] == "l" else xlim[1]
            y0 = yline if key[0] == "t" else ylim[0]
            y1 = ylim[1] if key[0] == "t" else yline
            ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, color=col, alpha=0.10, zorder=0))
    if xline is not None:
        ax.axvline(xline, color=theme.c("ink_soft"), linestyle="--", linewidth=0.8, zorder=1)
    if yline is not None:
        ax.axhline(yline, color=theme.c("ink_soft"), linestyle="--", linewidth=0.8, zorder=1)
    sz = np.asarray([0 if (s is None or np.isnan(s)) else s for s in sizes], float)
    smax = sz.max() if sz.max() > 0 else 1
    cols = colors or [theme.c("positive")] * len(xs)
    ax.scatter(xs[ok], ys[ok], s=(sz[ok] / smax) * 900 * size_scale / 6 + 30, c=[cols[i] for i in np.where(ok)[0]],
               alpha=0.55, edgecolor="white", linewidth=0.8, zorder=3)
    offsets = [(6, 6), (6, -9), (-6, 6), (-6, -9), (9, 0), (-9, 0), (0, 10), (0, -12)]
    for k, i in enumerate(np.where(ok)[0]):
        dx, dy = offsets[k % len(offsets)]
        ax.annotate(labels[i], (xs[i], ys[i]), xytext=(dx, dy), textcoords="offset points",
                    ha="left" if dx > 0 else "right" if dx < 0 else "center", va="center",
                    fontproperties=theme.fp(font), color=theme.c("ink"), zorder=5,
                    arrowprops=dict(arrowstyle="-", color=theme.c("line"), lw=0.5) if k % 2 else None)
    if quadrant_text:
        pos = {"tl": (0.02, 0.97, "left", "top"), "tr": (0.98, 0.97, "right", "top"),
               "bl": (0.02, 0.03, "left", "bottom"), "br": (0.98, 0.03, "right", "bottom")}
        for key, txt in quadrant_text.items():
            x, y, ha, va = pos[key]
            ax.text(x, y, txt, transform=ax.transAxes, ha=ha, va=va, fontproperties=theme.fp(font, True),
                    color=theme.c("ink_soft"), zorder=6)
    ax.set_xlabel(xlabel, fontproperties=theme.fp(font + 0.5), color=theme.c("ink"))
    ax.set_ylabel(ylabel, fontproperties=theme.fp(font + 0.5), color=theme.c("ink"))
    for t in ax.get_xticklabels() + ax.get_yticklabels():
        t.set_fontproperties(theme.fp(font - 1))
    _style_axes(ax, theme, None)
    return _save(fig, path)


def scatter_labeled(theme: Theme, path: Path, xs, ys, labels, *, xline=None, yline=None, xlabel="", ylabel="",
                    w=6.2, h=4.3, font=7.0, colors=None, quadrant_text=None, xlim=None, ylim=None) -> Path:
    sizes = [1.0] * len(xs)
    return bubble(theme, path, xs, ys, sizes, labels, xline=xline, yline=yline, xlabel=xlabel, ylabel=ylabel, w=w, h=h,
                  font=font, colors=colors, quadrant_text=quadrant_text, xlim=xlim, ylim=ylim, size_scale=1.2)


def stacked100(theme: Theme, path: Path, labels: list[str], shares: list[list[float]], seg_labels: list[str],
               seg_colors: list[str], *, w: float = 6.0, h: float = 4.2, font: float = 8.0, label_chars: int = 14,
               n_labels: list | None = None, show_pct_min: float = 7) -> Path:
    """4 類型などの 100% 積み上げ横棒。"""
    n = len(labels)
    fig = _fig(theme, w, h)
    bottom = min(0.34, 0.62 / h)
    ax = fig.add_axes([0.26, bottom, 0.72, 0.96 - bottom])
    ys = np.arange(n)[::-1]
    for i in range(n):
        left = 0.0
        row = shares[i]
        if all(v is None or np.isnan(v) for v in row):
            ax.text(50, ys[i], "－（非表示）", ha="center", va="center", fontproperties=theme.fp(font), color=theme.c("ink_soft"))
            continue
        for k, v in enumerate(row):
            v = 0 if v is None or np.isnan(v) else v
            ax.barh(ys[i], v, left=left, height=0.62, color=seg_colors[k], zorder=3, edgecolor="white", linewidth=0.5)
            if v >= show_pct_min:
                ax.text(left + v / 2, ys[i], f"{v:.0f}", ha="center", va="center", fontproperties=theme.fp(font - 1), color="white")
            left += v
    ax.set_yticks(ys)
    tl = [wrap_cjk(l, label_chars, 2) for l in labels]
    if n_labels:
        tl = [f"{t} (n={m})" for t, m in zip(tl, n_labels)]
    ax.set_yticklabels(tl, fontproperties=theme.fp(font))
    ax.set_xlim(0, 100)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xticklabels(["0", "25", "50", "75", "100%"], fontproperties=theme.fp(font - 1))
    _style_axes(ax, theme, None)
    handles = [Rectangle((0, 0), 1, 1, color=c) for c in seg_colors]
    ax_h_in = h * (0.96 - bottom)
    ax.legend(handles, seg_labels, loc="upper center", bbox_to_anchor=(0.5, -0.30 / ax_h_in), ncol=len(seg_labels),
              frameon=False, prop=theme.fp(font - 0.5), handlelength=1.0, columnspacing=1.2)
    return _save(fig, path)


def bar_counts(theme: Theme, path: Path, labels: list[str], counts: list[float], *, w: float = 3.0, h: float = 2.0,
               font: float = 7.5, color: str | None = None, horizontal: bool = True, label_chars: int = 12,
               pct: bool = False, total: float | None = None) -> Path:
    fig = _fig(theme, w, h)
    ax = fig.add_axes([0.44 if horizontal else 0.08, 0.10, 0.54 if horizontal else 0.9, 0.86])
    col = color or theme.c("brand")
    vals = [0 if (c is None or (isinstance(c, float) and np.isnan(c))) else c for c in counts]
    tot = total or (sum(vals) or 1)
    if horizontal:
        ys = np.arange(len(labels))[::-1]
        ax.barh(ys, vals, color=col, height=0.62, zorder=3)
        for y, v in zip(ys, vals):
            txt = f"{v:.0f}" + (f" ({100 * v / tot:.0f}%)" if pct else "")
            ax.text(v + max(vals) * 0.02, y, txt, va="center", fontproperties=theme.fp(font - 0.5), color=theme.c("ink"))
        ax.set_yticks(ys)
        ax.set_yticklabels([wrap_cjk(l, label_chars, 2) for l in labels], fontproperties=theme.fp(font))
        ax.set_xlim(0, max(vals) * (1.7 if pct else 1.35) if max(vals) else 1)
        ax.set_xticks([])
    else:
        xs = np.arange(len(labels))
        ax.bar(xs, vals, color=col, width=0.62, zorder=3)
        for x, v in zip(xs, vals):
            ax.text(x, v + max(vals) * 0.02, f"{v:.0f}", ha="center", fontproperties=theme.fp(font - 0.5))
        ax.set_xticks(xs)
        ax.set_xticklabels([wrap_cjk(l, label_chars, 2) for l in labels], fontproperties=theme.fp(font))
        ax.set_yticks([])
    _style_axes(ax, theme, None)
    ax.spines["left"].set_visible(horizontal)
    ax.spines["bottom"].set_visible(not horizontal)
    return _save(fig, path)


def render_table(theme: Theme, path: Path, columns: list[dict], rows: list[dict], *, row_h: float = 0.28,
                 header_h: float = 0.42, font: float = 7.5, zebra: bool = True) -> Path:
    """密な表を PNG 化。
    columns: {id, title, width(in), kind: text|num|sig|bar3|pct, align, chars}
    rows:    {cells: {col_id: value}, style: item|category|total, fill?}
      sig  : (text, flag)  → flag>0 淡青 / flag<0 淡桃
      bar3 : (pos, neu, neg) 100% バー
    """
    W = sum(c["width"] for c in columns)
    H = header_h + row_h * len(rows)
    fig = _fig(theme, W, H)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    # header
    ax.add_patch(Rectangle((0, 0), W, header_h, color=theme.c("brand_pale")))
    x = 0.0
    for c in columns:
        ax.text(x + c["width"] / 2, header_h / 2, wrap_cjk(c["title"], c.get("chars", 10), 2), ha="center", va="center",
                fontproperties=theme.fp(font, True), color=theme.c("ink"))
        x += c["width"]
    y = header_h
    for i, r in enumerate(rows):
        style = r.get("style", "item")
        if r.get("fill"):
            ax.add_patch(Rectangle((0, y), W, row_h, color=r["fill"]))
        elif style == "category":
            ax.add_patch(Rectangle((0, y), W, row_h, color=theme.c("bg_soft")))
        elif zebra and i % 2 == 1:
            ax.add_patch(Rectangle((0, y), W, row_h, color="#FAFAFA"))
        x = 0.0
        for c in columns:
            v = r["cells"].get(c["id"])
            kind = c.get("kind", "text")
            align = c.get("align", "left" if kind == "text" else "center")
            tx = x + 0.06 if align == "left" else x + c["width"] / 2 if align == "center" else x + c["width"] - 0.06
            bold = style in ("category", "total")
            if kind == "sig" and isinstance(v, tuple):
                txt, flag = v
                if flag > 0:
                    ax.add_patch(Rectangle((x + 0.02, y + 0.03), c["width"] - 0.04, row_h - 0.06, color=theme.c("positive_soft")))
                elif flag < 0:
                    ax.add_patch(Rectangle((x + 0.02, y + 0.03), c["width"] - 0.04, row_h - 0.06, color=theme.c("negative_soft")))
                ax.text(tx, y + row_h / 2, txt, ha=align, va="center", fontproperties=theme.fp(font, bold or flag != 0),
                        color=theme.c("ink"))
            elif kind == "bar3" and isinstance(v, tuple):
                pos, neu, neg = [0 if (t is None or np.isnan(t)) else t for t in v]
                bw = c["width"] - 0.12
                left = x + 0.06
                cols3 = [theme.c("positive"), theme.c("neutral"), theme.c("negative")]
                for share, col in zip((pos, neu, neg), cols3):
                    ww = bw * share / 100
                    ax.add_patch(Rectangle((left, y + 0.06), ww, row_h - 0.12, color=col))
                    if share >= 12:
                        ax.text(left + ww / 2, y + row_h / 2, f"{share:.0f}", ha="center", va="center",
                                fontproperties=theme.fp(font - 1.5), color="white" if col != theme.c("neutral") else theme.c("ink"))
                    left += ww
            else:
                if isinstance(v, (int, np.integer)) and not isinstance(v, bool):
                    txt = str(int(v))
                elif isinstance(v, (float, np.floating)):
                    txt = fmt_pct(v) if kind == "pct" else fmt(v, signed=c.get("signed", False))
                else:
                    txt = "" if v is None else str(v)
                if kind == "text":
                    txt = wrap_cjk(txt, c.get("chars", 20), c.get("lines", 1))
                ax.text(tx, y + row_h / 2, txt, ha=align, va="center", fontproperties=theme.fp(font, bold),
                        color=c.get("color", theme.c("ink")))
            x += c["width"]
        ax.plot([0, W], [y + row_h, y + row_h], color=theme.c("line"), linewidth=0.5)
        y += row_h
    return _save(fig, path)
