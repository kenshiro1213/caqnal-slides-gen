"""仕様 v2 の slide type（docs/参考デッキ構成_佐賀市v2.md）。

各 builder は (ctx, spec) を受け取り 1 枚以上の頁を足す。数値の事実だけを自動で書き、
解釈・示唆・施策は compose の文章（interpretation.yaml）が無ければ「未記入」の枠にする。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..pptx_helpers import add_line, add_rect, add_text
from .base import (PLACEHOLDER, V2Theme, bar_chart, frame, insight_box, kpi, line_chart, scatter_chart, table,
                   text)
from .data import ALL, HIDDEN, NA, ReportData, diff, f1, isnum, num, pct

REGISTRY: dict = {}


def register(name):
    def deco(fn):
        REGISTRY[name] = fn
        return fn
    return deco


@dataclass
class Ctx:
    prs: object
    th: V2Theme
    d: ReportData
    label: str
    footer: str
    scope: tuple[str, str] = (ALL, ALL)          # 本文の主語（全庁 or 自所属）
    chapter: str = ""
    toc: list[tuple[str, int]] = field(default_factory=list)
    toc_slide: object = None
    page_index: dict = field(default_factory=dict)
    kind: str = "調査結果・施策提案"                # 頁右上の資料区分
    extra: dict = field(default_factory=dict)    # 社内向け資料など、公開データ以外に使う記録（run のログ等）

    @property
    def scope_name(self) -> str:
        return ALL if self.scope[0] == ALL else self.d.unit_name(self.scope[1])

    def page(self, title: str, message: str | None = None, note: str | None = None, chapter: str | None = None):
        n = len(self.prs.slides._sldIdLst) + 1  # noqa: SLF001
        ch = self.chapter if chapter is None else chapter
        if ch and (not self.toc or _chapter_key(self.toc[-1][0]) != _chapter_key(ch)):
            self.toc.append((ch, n))
        return frame(self.prs, self.th, title, self.label, self.footer, n, message, note, kind=self.kind)


def _chapter_key(ch: str) -> str:
    """同じ章番号の頁は目次で 1 行にまとめる（番号が無ければ章名で比べる）。"""
    m = re.match(r"(\d+)\. ", ch)
    return m.group(1) if m else ch


def _chap(ctx: Ctx, spec: dict, title: str) -> str:
    no = spec.get("chapter_no")
    return f"{no}. {title}" if no not in (None, "") else title


def _chapter(ctx: Ctx, spec: dict, default: str) -> str:
    """目次に載る章名。デッキ設定の chapter_title で上書きできる。"""
    return _chap(ctx, spec, spec.get("chapter_title") or default)


def _scope_or_all(ctx: Ctx):
    return ctx.scope if ctx.scope[0] != ALL else (ALL, ALL)


def _vs_all_note(ctx: Ctx) -> str:
    return "" if ctx.scope[0] == ALL else "全庁の値は自所属を含む。"


# ================================================================= 表紙・目次・見方
@register("cover")
def cover(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    s = ctx.prs.slides.add_slide(ctx.prs.slide_layouts[6])
    add_rect(s, th, 0, 0, th.W, th.H, th.hexv("navy"))
    add_rect(s, th, th.mx, 3.0, 1.2, 0.08, th.hexv("gold"))
    c = d.cfg.client
    add_text(s, th, th.mx, 3.25, th.W - 2 * th.mx, 1.1,
             f"{c.get('fiscal_year_label', '')} {c['name']} {c.get('survey_title', 'エンゲージメント調査')}",
             size=34, bold=True, color="FFFFFF")
    add_text(s, th, th.mx, 4.45, th.W - 2 * th.mx, 0.6, f"{spec.get('subtitle', '調査結果・施策提案')}｜{ctx.label}",
             size=18, color="FFFFFF")
    add_text(s, th, th.mx, th.H - 1.3, th.W - 2 * th.mx, 0.4,
             f"{c.get('report_date', '')}　© CAQNAL CO.,LTD.", size=11, color="D5D8DA")


@register("hidden_notice")
def hidden_notice(ctx: Ctx, spec: dict) -> None:
    """自所属が少人数・差し引き推測の防止で非表示のときの説明頁。数値は載せない。"""
    th, d = ctx.th, ctx.d
    p = ctx.page(f"{ctx.scope_name}の結果について", "回答者が少ないため、所属単位の結果は掲載しない",
                 chapter="結果の扱い", note="非表示の基準は全所属に同じく適用している。人数そのものも推測につながるため記載しない。")
    text(p, p.left, p.body_top, p.width * 0.62, 3.6, [
        f"{ctx.scope_name}は、有効回答が最低公開人数（{d.manifest.get('min_n')} 名）に満たないか、"
        "他の所属の値から差し引きで推測できるため、点数・回答分布・人数を非表示にしている。",
        "職場の状態は、全庁向けレポートの全庁の結果・カテゴリ・キードライバーを参考に、職員との対話で確かめる。",
        "次頁の記入シートで、重点として確認する事項と、人事・全庁へ相談する事項を整理する。"], size=th.sizes["body"])


@register("toc")
def toc(ctx: Ctx, spec: dict) -> None:
    p = ctx.page("目次", chapter="")
    ctx.toc_slide = p


def fill_toc(ctx: Ctx) -> None:
    if ctx.toc_slide is None:
        return
    p, th = ctx.toc_slide, ctx.th
    rows = [c for c in ctx.toc if c[0]]
    half = math.ceil(len(rows) / 2)
    for col, chunk in enumerate((rows[:half], rows[half:])):
        x = p.left + col * (p.width / 2)
        for i, (title, page) in enumerate(chunk):
            y = 1.6 + i * 0.52
            add_text(p.slide, th, x, y, p.width / 2 - 1.4, 0.45, title, size=th.sizes["body"] + 1,
                     color=th.hexv("ink"), anchor="middle")
            add_text(p.slide, th, x + p.width / 2 - 1.4, y, 0.8, 0.45, f"{page:02d}", size=th.sizes["body"] + 1,
                     bold=True, color=th.hexv("navy"), align="right", anchor="middle")
            add_line(p.slide, th, x, y + 0.48, x + p.width / 2 - 0.5, y + 0.48, th.hexv("line"), 0.5)


@register("reading_guide")
def reading_guide(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    p = ctx.page("レポートの見方", "数値の事実、組織人事の示唆、実施できる施策を順に読む", chapter="レポートの見方")
    steps = [("01", "状態をつかむ", "サマリー", "全体の状態と、強み・課題・改善の方向を確認する。"),
             ("02", "背景を確かめる", "分析", "指標・所属・属性の違いと職員の声を合わせて読む。"),
             ("03", "行動を決める", "施策・計画", "権限・担当・時間を確認し、少数の職場で試して判断する。"),
             ("04", "根拠を確認する", "資料・詳細付録", "設問全文と有効回答人数、詳細な数値・用語を確かめる。")]
    w = (p.width - 0.6) / 4
    for i, (no, head, part, body) in enumerate(steps):
        x = p.left + i * (w + 0.2)
        add_rect(p.slide, th, x, p.body_top, w, 2.6, th.hexv("bg"), th.hexv("line"))
        text(p, x + 0.25, p.body_top + 0.2, w - 0.5, 0.6, no, size=26, bold=True, color=th.hexv("gold"))
        text(p, x + 0.25, p.body_top + 0.85, w - 0.5, 0.4, head, size=th.sizes["body"] + 1, bold=True,
             color=th.hexv("navy"))
        text(p, x + 0.25, p.body_top + 1.25, w - 0.5, 0.35, part, size=th.sizes["note"], color=th.hexv("ink_soft"))
        text(p, x + 0.25, p.body_top + 1.65, w - 0.5, 0.9, body, size=th.sizes["small"])
    sc = d.cfg.scale((d.cfg.q(d.objectives[0]["question"]).get("scale") if d.objectives else None) or "agree5")
    pos = "・".join(str(v) for v in sc.get("positive", []))
    neg = "・".join(str(v) for v in sc.get("negative", []))
    neu = "・".join(str(v) for v in sc.get("neutral", [])) or "なし"
    y = p.body_top + 3.0
    text(p, p.left, y, p.width, 0.4, "数値と示唆の読み分け", size=th.sizes["body"], bold=True, color=th.hexv("navy"))
    items = [("点数・肯定的回答率", f"点数は100点満点。肯定的回答率は回答 {pos} を選んだ割合（中立 {neu}、否定 {neg}）。"
              "2つは別の数値なので、有効回答人数と合わせて読む。"),
             ("関連・相関係数 r", "一緒に高低する傾向を示す。原因や施策の効果とは区別する。"),
             ("示唆・施策", "事実から背景の仮説を立て、職場で確認して具体策を選ぶ（〔協議案〕は合意前の案）。")]
    for i, (h, b) in enumerate(items):
        x = p.left + i * (p.width / 3)
        text(p, x, y + 0.5, p.width / 3 - 0.3, 0.4, h, size=th.sizes["small"], bold=True, color=th.hexv("ink"))
        text(p, x, y + 0.9, p.width / 3 - 0.3, 1.3, b, size=th.sizes["small"], color=th.hexv("ink_soft"))
    if d.cfg.common9.get("status") == "not_conducted":
        text(p, p.left, p.body_bottom - 0.45, p.width, 0.4,
             "共通9問は実施していないため、総合指標・3要素の指標は掲載しない。目的指標は単純平均して総合点にせず、個別に読む。",
             size=th.sizes["note"], color=th.hexv("ink_soft"))


# ================================================================= 回答状況
@register("response_overview")
def response_overview(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    sc = _scope_or_all(ctx)
    st = d.status(*sc) or {}
    n, target, rate = st.get("responded_n"), st.get("target_n"), st.get("rate")
    nq = len(d.objectives) + len(d.drivers)
    msg = (f"対象 {num(target)} 名のうち有効回答 {num(n)} 名を集計した" if isnum(target)
           else f"有効回答 {num(n)} 名を集計した（対象人数が未確定のため回答率は計算しない）")
    p = ctx.page("調査概要と回答状況", msg, chapter="調査概要と回答状況",
                 note="回答率＝有効回答者／対象者。設問ごとの有効回答人数とは異なる。" + (
                     "" if isnum(target) else "対象者リストを照合していないため所属別・属性別の回答率は計算不能。"))
    w = (p.width - 0.6) / 4
    cards = [("回答率", pct(rate) if isnum(rate) else "計算不能", "", [f"{num(n)}／{num(target)}名"]),
             ("有効回答", num(n), "名", [f"{ctx.scope_name}の集計人数"]),
             ("集計した設問", str(nq), "問", [f"説明設問 {len(d.drivers)} 問＋目的指標 {len(d.objectives)} 問",
                                         f"カテゴリ {len(d.categories)}"]),
             ("最低公開人数", str(d.manifest.get("min_n")), "名", ["これ未満の組織・区分・設問は非表示",
                                                             "差し引きで推測できる区分も非表示"])]
    for i, (t, v, u, sub) in enumerate(cards):
        kpi(p, p.left + i * (w + 0.2), p.body_top + 0.2, w, 2.4, t, v, u, sub)
    period = d.cfg.client.get("survey_period") or NA
    rows = [["項目", "内容"], ["調査名", d.cfg.client.get("survey_title", "")], ["実施時期", period],
            ["集計の範囲", "回答した職員のうち対象者に照合できた回答（対象者リストが無い場合は全回答）"],
            ["尺度", "5段階など設問票の回答尺度。点数は100点満点に換算"]]
    table(p, p.left, p.body_top + 3.0, p.width, rows, widths=[1, 4], row_h=0.42, aligns=["left", "left"])


@register("response_by_attribute")
def response_by_attribute(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    dims = spec.get("attributes") or d.attributes()
    per = spec.get("per_page", 2)
    total = (d.status(ALL, ALL) or {}).get("responded_n")
    for i in range(0, len(dims), per):
        chunk = dims[i:i + per]
        p = ctx.page(f"属性別の回答状況：{'・'.join(chunk)}", "主要な区分の回答人数と構成を確認する",
                     chapter=spec.get("chapter", "調査概要と回答状況"),
                     note="構成比＝区分の有効回答者／全庁の有効回答者。区分が無い回答者は合計に含まれない。非表示は最低公開人数未満か差し引きで推測できる区分。")
        w = (p.width - 0.4) / len(chunk)
        for j, dim in enumerate(chunk):
            x = p.left + j * (w + 0.4)
            vals = d.attr_values(dim)
            rows = [["区分", "対象", "有効回答", "回答率", "構成比"]]
            chart_cats, chart_vals = [], []
            for v in vals:
                s = d.status("属性", f"{dim}={v}") or {}
                if isinstance(s.get("suppressed"), str):
                    rows.append([v, HIDDEN, HIDDEN, HIDDEN, HIDDEN])
                    continue
                share = s["responded_n"] / total if isnum(s.get("responded_n")) and total else None
                rows.append([v, num(s.get("target_n")), num(s.get("responded_n")),
                             pct(s.get("rate")) if isnum(s.get("rate")) else "計算不能", pct(share)])
                chart_cats.append(v)
                chart_vals.append(round(share * 100, 1) if share is not None else None)
            text(p, x, p.body_top, w, 0.4, f"{dim}別", size=th.sizes["body"], bold=True, color=th.hexv("navy"))
            table(p, x, p.body_top + 0.45, w, rows, widths=[2.2, 1, 1, 1, 1], row_h=0.34)
            ch_y = p.body_top + 0.6 + 0.34 * len(rows)
            if chart_cats and p.body_bottom - ch_y > 1.2:
                bar_chart(p, x, ch_y, w, min(p.body_bottom - ch_y, 0.42 * len(chart_cats) + 0.5), chart_cats,
                          {"構成比（%）": chart_vals}, [th.hexv("navy")], max_value=100)


@register("indicator_guide")
def indicator_guide(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    p = ctx.page("今回の指標と分析の読み方", f"{len(d.objectives)}つの目的指標を分けて捉え、職場の状態との関連から改善の論点を探る",
                 chapter="調査概要と回答状況", note="目的指標を単純平均した総合点は設けていない。点数の差は統計的な有意差を示すものではない。")
    rows = [["目的指標", "設問", "設問文"]]
    for o in d.objectives:
        rows.append([o.get("name") or o["id"], o["question"], d.cfg.q(o["question"]).get("text", "")])
    table(p, p.left, p.body_top, p.width * 0.62, rows, widths=[1.3, 0.6, 4], row_h=0.5,
          aligns=["left", "center", "left"])
    x = p.left + p.width * 0.65
    w = p.width * 0.35
    add_rect(p.slide, th, x, p.body_top, w, 2.2, th.hexv("navy_pale"))
    text(p, x + 0.25, p.body_top + 0.15, w - 0.5, 0.4, "職場の状態を確認する分析", size=th.sizes["body"], bold=True,
         color=th.hexv("navy"))
    text(p, x + 0.25, p.body_top + 0.6, w - 0.5, 1.5,
         [f"{len(d.categories)}カテゴリ／{len(d.drivers)}設問", "目的指標との相関、重回帰（カテゴリ別・設問別）、"
          "決定木で回答が分かれる条件を読む。"], size=th.sizes["small"])
    insight_box(p, p.left, p.body_top + 2.9, p.width, 2.0, "読み方の注意",
                "相関の大きさだけで施策を選ばず、点数・回答分布・職員の声と業務の実態を合わせて読む。"
                "改善を試し、実施負担と職員の理解を確認してから広げる。")


# ================================================================= サマリー
def _rank_questions(d: ReportData, sc, n: int, top: bool):
    st = d.stats_of(*sc, "設問")
    st = st[st.unit_id.isin(d.drivers) & st.score.notna()]
    st = st.sort_values("score", ascending=not top, kind="stable")
    return st.head(n)


@register("summary_results")
def summary_results(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    sc = _scope_or_all(ctx)
    objs = d.objectives
    vals = [(o, d.stat(*sc, "目的指標", o["id"])) for o in objs]
    shown = [(o, v) for o, v in vals if v and isnum(v.get("score"))]
    if shown:
        lo = min(shown, key=lambda t: t[1]["score"])
        hi = max(shown, key=lambda t: t[1]["score"])
        msg = f"{hi[0]['name']}は{f1(hi[1]['score'])}点、{lo[0]['name']}は{f1(lo[1]['score'])}点"
    else:
        msg = "目的指標は非表示"
    p = ctx.page(_chap(ctx, spec, f"{ctx.scope_name if sc[0] != ALL else '全庁'}の結果サマリー"), msg,
                 chapter=_chapter(ctx, spec, "サマリー"),
                 note="点数は100点満点。肯定＝肯定的回答の割合。" + _vs_all_note(ctx))
    k = max(len(objs), 1)
    w = min(3.6, (p.width * 0.62 - 0.2 * (k - 1)) / k)
    for i, (o, v) in enumerate(vals):
        if v is None or isinstance(v.get("suppressed"), str):
            kpi(p, p.left + i * (w + 0.2), p.body_top, w, 2.5, o.get("name") or o["id"], HIDDEN, "", [])
            continue
        sub = [f"肯定 {pct(v['pos'])}", f"有効回答 {num(v['n'])}名"]
        if sc[0] != ALL:
            a = d.stat(ALL, ALL, "目的指標", o["id"])
            sub.append(f"全庁差 {diff(v['score'] - a['score'])}pt" if a and isnum(a.get("score")) else "")
        kpi(p, p.left + i * (w + 0.2), p.body_top, w, 2.5, f"{o.get('name')}（{o['question']}）", f1(v["score"]),
            "点 / 100", sub)
    x = p.left + p.width * 0.64
    bw = p.width * 0.36
    for j, (head, top) in enumerate((("維持・伸長する強み（点数の高い設問）", True), ("確認する課題（点数の低い設問）", False))):
        y = p.body_top + j * 1.35
        add_rect(p.slide, th, x, y, bw, 1.2, th.hexv("navy_pale") if top else th.hexv("pale"))
        text(p, x + 0.2, y + 0.08, bw - 0.4, 0.35, head, size=th.sizes["small"], bold=True, color=th.hexv("navy"))
        items = [f"{d.qlabel(r.unit_id, 30)}　{f1(r.score)}点" for r in _rank_questions(d, sc, 2, top).itertuples()]
        text(p, x + 0.2, y + 0.45, bw - 0.4, 0.75, items, size=th.sizes["note"])
    rows = [["目的指標", "点数", "肯定的回答率", "有効回答"]] + [
        [o.get("name"), d.cell(*sc, "目的指標", o["id"]), d.cell(*sc, "目的指標", o["id"], "pos", pct),
         d.cell(*sc, "目的指標", o["id"], "n", num)] for o in objs]
    table(p, p.left, p.body_top + 2.8, p.width * 0.62, rows, widths=[2.5, 1, 1, 1], row_h=0.4)
    insight_box(p, x, p.body_top + 2.8, bw, p.body_bottom - p.body_top - 2.9, "全体の見立て", d.text("summary_results"),
                "insights.summary_results")


@register("summary_state")
def summary_state(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    rows_in = (d.compose.get("summary_state") or [])
    p = ctx.page(_chap(ctx, spec, "サマリー｜組織状態・示唆・解決策"),
                 d.text("summary_state_message") or f"{PLACEHOLDER} 現在の組織状態を一文で（interpretation.yaml の insights.summary_state_message）",
                 chapter=_chapter(ctx, spec, "サマリー"),
                 note="事実から導いた解釈と協議案。解釈は仮説であり、各職場の業務と運用で確かめる。")
    heads = ["", "調査で確認した事実", "組織人事としての示唆", "実行する方向〔協議案〕"]
    if not rows_in:
        rows_in = [{"theme": f["theme"], "fact": f["fact"]} for f in (d.compose.get("facts") or [])[:3]]
    rows = [heads]
    colors = {}
    for i, r in enumerate(rows_in[:4], start=1):
        ins, dirn = r.get("insight"), r.get("direction")
        rows.append([r.get("theme", ""), r.get("fact", ""), ins or PLACEHOLDER, dirn or PLACEHOLDER])
        if not ins:
            colors[(i, 2)] = th.hexv("placeholder")
        if not dirn:
            colors[(i, 3)] = th.hexv("placeholder")
    if len(rows) == 1:
        rows.append(["", PLACEHOLDER, PLACEHOLDER, PLACEHOLDER])
    rh = min(1.5, (p.body_bottom - p.body_top - 0.5) / len(rows))
    table(p, p.left, p.body_top, p.width, rows, widths=[1.1, 3, 3, 3], row_h=rh,
          aligns=["center", "left", "left", "left"], colors=colors, size=th.sizes["small"])


# ================================================================= 指標・カテゴリ・設問
@register("indicator_distribution")
def indicator_distribution(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    sc = _scope_or_all(ctx)
    objs = [(o, d.stat(*sc, "目的指標", o["id"])) for o in d.objectives]
    objs = [(o, v) for o, v in objs if v and isnum(v.get("score"))]
    msg = "、".join(f"{o.get('short') or o['name']} {f1(v['score'])}点" for o, v in objs)
    p = ctx.page(_chap(ctx, spec, f"{len(d.objectives)}指標の点数と回答分布"), msg, chapter=_chapter(ctx, spec, "指標と職場の状態"),
                 note="、".join(f"{o['question']} n＝{num(v['n'])}" for o, v in objs) + "。" + _vs_all_note(ctx))
    cats = [o.get("name") for o, _ in objs]
    series = {"肯定": [round(v["pos"] * 100, 1) for _, v in objs], "中立": [round(v["neu"] * 100, 1) for _, v in objs],
              "否定": [round(v["neg"] * 100, 1) for _, v in objs]}
    cw = p.width * 0.58
    text(p, p.left, p.body_top, cw, 0.35, "回答分布（%）", size=th.sizes["small"], bold=True, color=th.hexv("navy"))
    bar_chart(p, p.left, p.body_top + 0.35, cw, min(5.2, 1.0 * len(cats) + 1.0), cats, series,
              [th.hexv("pos"), th.hexv("neu"), th.hexv("neg")], percent=True, legend=True)
    rows = [["指標", "点数"] + (["全庁差"] if sc[0] != ALL else [])]
    for o, v in objs:
        row = [o.get("short") or o["name"], f1(v["score"])]
        if sc[0] != ALL:
            a = d.stat(ALL, ALL, "目的指標", o["id"])
            row.append(diff(v["score"] - a["score"]) if a and isnum(a.get("score")) else NA)
        rows.append(row)
    table(p, p.left + cw + 0.3, p.body_top + 0.35, p.width * 0.4 - 0.3, rows, row_h=0.42)
    insight_box(p, p.left + cw + 0.3, p.body_top + 0.6 + 0.42 * len(rows), p.width * 0.4 - 0.3,
                p.body_bottom - p.body_top - 0.7 - 0.42 * len(rows), "組織・人事への示唆",
                d.text("indicator_distribution"), "insights.indicator_distribution")


@register("category_scores")
def category_scores(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    sc = _scope_or_all(ctx)
    rows = [(c, d.stat(*sc, "カテゴリ", c)) for c in d.categories]
    rows = [(c, v) for c, v in rows if v and isnum(v.get("score"))]
    low = sorted(rows, key=lambda t: t[1]["score"])[:2]
    msg = "、".join(f"{c} {f1(v['score'])}点" for c, v in low) + " が相対的に低い" if low else ""
    ns = [int(v["n"]) for _, v in rows]
    p = ctx.page(_chap(ctx, spec, "カテゴリ別の職場の状態"), msg, chapter=_chapter(ctx, spec, "指標と職場の状態"),
                 note=f"カテゴリは構成設問に回答した職員の平均（n＝{min(ns):,}～{max(ns):,}）。設問ごとの結果は詳細付録で確認する。"
                 if ns else None)
    series = {ctx.scope_name: [round(v["score"], 1) for _, v in rows]}
    colors = [th.hexv("navy")]
    if sc[0] != ALL:
        series["全庁"] = [round((d.stat(ALL, ALL, "カテゴリ", c) or {}).get("score") or 0, 1) for c, _ in rows]
        colors.append(th.hexv("gold"))
    cw = p.width * 0.58
    bar_chart(p, p.left, p.body_top, cw, p.body_bottom - p.body_top, [c for c, _ in rows], series, colors,
              max_value=100, legend=len(series) > 1)
    insight_box(p, p.left + cw + 0.3, p.body_top, p.width * 0.42 - 0.3, p.body_bottom - p.body_top,
                "組織・人事への示唆", d.text("category_scores"), "insights.category_scores")


@register("question_strengths")
def question_strengths(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    sc = _scope_or_all(ctx)
    n = spec.get("n", 3)
    top, bot = _rank_questions(d, sc, n, True), _rank_questions(d, sc, n, False)
    msg = (f"最も高いのは {top.iloc[0].unit_id}（{f1(top.iloc[0].score)}点）、最も低いのは {bot.iloc[0].unit_id}"
           f"（{f1(bot.iloc[0].score)}点）") if len(top) and len(bot) else ""
    p = ctx.page(_chap(ctx, spec, "設問からみる強み・課題"), msg, chapter=_chapter(ctx, spec, "指標と職場の状態"),
                 note=f"説明設問 {len(d.drivers)} 問の上位{n}問と下位{n}問。所属・設問ごとの分布と全文は詳細付録へ。")
    cw = p.width * 0.29
    for j, (head, df, color) in enumerate((("点数の高い設問", top, th.hexv("navy")),
                                           ("点数の低い設問", bot, th.hexv("gold")))):
        x = p.left + j * (cw + 0.25)
        text(p, x, p.body_top, cw, 0.35, head, size=th.sizes["small"], bold=True, color=th.hexv("navy"))
        bar_chart(p, x, p.body_top + 0.35, cw, 2.6, [d.qlabel(q, 22) for q in df.unit_id],
                  {"点数": [round(v, 1) for v in df.score]}, [color], max_value=100)
    insight_box(p, p.left + 2 * cw + 0.5, p.body_top, p.width - 2 * cw - 0.5, p.body_bottom - p.body_top,
                "組織・人事への示唆", d.text("question_strengths"), "insights.question_strengths")
    rows = [["設問", "点数", "肯定", "否定"]]
    for df in (top, bot):
        for r in df.itertuples():
            rows.append([d.qlabel(r.unit_id, 46), f1(r.score), pct(r.pos), pct(r.neg)])
    table(p, p.left, p.body_top + 3.15, 2 * cw + 0.25, rows, widths=[5, 0.8, 0.8, 0.8], row_h=0.36,
          aligns=["left", "center", "center", "center"], size=th.sizes["note"])


# ================================================================= 所属・属性
def _scope_units(ctx: Ctx) -> list[tuple[str, int]]:
    """表に並べる組織と字下げ。全庁読者は最上位（と配下）、所属長は同じ部局内。"""
    d = ctx.d
    out: list[tuple[str, int]] = []

    def walk(u, depth):
        out.append((u, depth))
        for c in d.children(u):
            walk(c, depth + 1)

    roots = d.children(None)
    if ctx.scope[0] != ALL:
        units = set(d.manifest.get("units") or [])
        roots = [r for r in roots if r in units]
    for r in roots:
        walk(r, 0)
    return out


def _paged(items: list, per: int) -> list[list]:
    return [items[i:i + per] for i in range(0, len(items), per)] or [[]]


def _diff_color(th, v, base, pt):
    if not (isnum(v) and isnum(base)):
        return None
    return th.hexv("high") if v - base >= pt else th.hexv("low") if v - base <= -pt else None


@register("org_indicator_table")
def org_indicator_table(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    units = _scope_units(ctx)
    pages = _paged(units, spec.get("rows_per_page", 14))
    hl = spec.get("highlight_pt", 3)
    for k, chunk in enumerate(pages, 1):
        suffix = f" {k}/{len(pages)}" if len(pages) > 1 else ""
        p = ctx.page(_chap(ctx, spec, (spec.get("title") or "所属別の主要指標") + suffix),
                     "組織順に指標を確認し、各所属で背景と必要な支援を話し合う" if k == 1 else None,
                     chapter=_chapter(ctx, spec, "所属・属性による違い"),
                     note=f"組織順。部局は配下の職員を含む。全庁との差が ±{hl}pt 以上を色で表示（青＝高い、茶＝低い）。少人数と差し引きで推測できる所属は人数・率・点数をまとめて非表示。")
        head = ["所属", "回答／対象", "回答率"] + [o.get("short") or o["name"] for o in d.objectives]
        rows = [head]
        colors = {}
        base = {o["id"]: (d.stat(ALL, ALL, "目的指標", o["id"]) or {}).get("score") for o in d.objectives}
        a = d.status(ALL, ALL) or {}
        rows.append(["全庁", f"{num(a.get('responded_n'))}／{num(a.get('target_n'))}", pct(a.get("rate"))]
                    + [f1(base[o["id"]]) for o in d.objectives])
        for uid, depth in chunk:
            name = "　" * depth + d.unit_name(uid)
            if d.hidden("組織", uid):
                rows.append([name] + [HIDDEN] * (len(head) - 1))
                continue
            s = d.status("組織", uid) or {}
            row = [name, f"{num(s.get('responded_n'))}／{num(s.get('target_n'))}", pct(s.get("rate"))]
            for j, o in enumerate(d.objectives):
                v = d.stat("組織", uid, "目的指標", o["id"])
                row.append(HIDDEN if v and isinstance(v.get("suppressed"), str) else f1((v or {}).get("score")))
                c = _diff_color(th, (v or {}).get("score"), base[o["id"]], hl)
                if c:
                    colors[(len(rows), 3 + j)] = c
            rows.append(row)
        top = p.body_top if k == 1 else 1.3
        table(p, p.left, top, p.width, rows, widths=[3, 1.3, 1] + [1] * len(d.objectives),
              row_h=min(0.4, (p.body_bottom - top) / len(rows)), colors=colors,
              fills={(1, c): th.hexv("pale") for c in range(len(head))})


@register("org_category_table")
def org_category_table(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    units = _scope_units(ctx)
    pages = _paged(units, spec.get("rows_per_page", 14))
    hl = spec.get("highlight_pt", 3)
    cats = d.categories
    for k, chunk in enumerate(pages, 1):
        suffix = f" {k}/{len(pages)}" if len(pages) > 1 else ""
        p = ctx.page(_chap(ctx, spec, (spec.get("title") or "所属別のカテゴリ比較") + suffix),
                     "カテゴリの違いを確認し、全庁共通の課題と所属ごとの支援を整理する" if k == 1 else None,
                     chapter=_chapter(ctx, spec, "所属・属性による違い"),
                     note=f"点数は100点満点。全庁との差が ±{hl}pt 以上を色で表示（青＝高い、茶＝低い）。非表示は少人数または差し引きで推測できる所属。")
        rows = [["所属"] + [c[:8] for c in cats]]
        base = {c: (d.stat(ALL, ALL, "カテゴリ", c) or {}).get("score") for c in cats}
        rows.append(["全庁"] + [f1(base[c]) for c in cats])
        colors = {}
        for uid, depth in chunk:
            name = "　" * depth + d.unit_name(uid)
            if d.hidden("組織", uid):
                rows.append([name] + [HIDDEN] * len(cats))
                continue
            row = [name]
            for j, c in enumerate(cats):
                v = d.stat("組織", uid, "カテゴリ", c) or {}
                row.append(HIDDEN if isinstance(v.get("suppressed"), str) else f1(v.get("score")))
                col = _diff_color(th, v.get("score"), base[c], hl)
                if col:
                    colors[(len(rows), 1 + j)] = col
            rows.append(row)
        top = p.body_top if k == 1 else 1.3
        table(p, p.left, top, p.width, rows, widths=[3] + [1] * len(cats),
              row_h=min(0.4, (p.body_bottom - top) / len(rows)), colors=colors,
              fills={(1, c): th.hexv("pale") for c in range(len(cats) + 1)}, size=th.sizes["note"])


@register("attribute_indicators")
def attribute_indicators(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    dims = spec.get("attributes") or d.attributes()
    palette = [th.hexv("navy"), th.hexv("gold"), "5B8DB8", "8C6D1F", "9AA3A9"]
    for dim in dims:
        vals = [v for v in d.attr_values(dim) if not d.hidden("属性", f"{dim}={v}")]
        if not vals:
            continue
        series = {}
        for o in d.objectives:
            series[o.get("short") or o["name"]] = [
                (lambda r: round(r["score"], 1) if r and isnum(r.get("score")) else None)(
                    d.stat("属性", f"{dim}={v}", "目的指標", o["id"])) for v in vals]
        first = d.objectives[0] if d.objectives else None
        msg = ""
        if first:
            pts = [(v, s) for v, s in zip(vals, series[first.get("short") or first["name"]]) if s is not None]
            if pts:
                lo, hi = min(pts, key=lambda t: t[1]), max(pts, key=lambda t: t[1])
                msg = f"{first['name']}は {hi[0]} {f1(hi[1])}点、{lo[0]} {f1(lo[1])}点"
        p = ctx.page(_chap(ctx, spec, f"{dim}別の{len(d.objectives)}指標"), msg,
                     chapter=_chapter(ctx, spec, "所属・属性による違い"),
                     note="区分間の比較で、個人の経年変化ではない。属性が原因とは判断しない（所属・職責の重なりは未調整）。非表示の区分は掲載しない。")
        cw = p.width * 0.58
        line_chart(p, p.left, p.body_top, cw, p.body_bottom - p.body_top - 0.1, vals, series,
                   palette[:len(series)])
        insight_box(p, p.left + cw + 0.3, p.body_top, p.width * 0.42 - 0.3, p.body_bottom - p.body_top,
                    "組織・人事への示唆", d.text(f"attribute_indicators:{dim}"), f"insights['attribute_indicators:{dim}']")


# ================================================================= キードライバー
def _objective_ids(d: ReportData, spec: dict) -> list[str]:
    """spec.objective: 省略 = 先頭の目的指標、all = 全て、それ以外 = その ID。"""
    obj = spec.get("objective")
    if obj == "all":
        return [o["id"] for o in d.objectives]
    if obj is None:
        return [d.objectives[0]["id"]] if d.objectives else []
    return [obj]


@register("kd_category")
def kd_category(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    for oid in _objective_ids(d, spec):
        c = d.corr(oid, "カテゴリ")
        rows_d = []
        for cat in d.categories:
            r = c[c.unit_id == cat]
            v = d.stat(ALL, ALL, "カテゴリ", cat) or {}
            rows_d.append((cat, v.get("score"), r["r"].iloc[0] if len(r) else None, r["n"].iloc[0] if len(r) else None))
        best = max((t for t in rows_d if isnum(t[2])), key=lambda t: t[2], default=None)
        msg = f"{d.obj_name(oid)}との相関は {best[0]}（r＝{best[2]:.3f}）が最も強い" if best else ""
        ns = [t[3] for t in rows_d if isnum(t[3])]
        p = ctx.page(_chap(ctx, spec, f"カテゴリ別キードライバー｜{d.obj_name(oid, True)}"), msg,
                     chapter=_chapter(ctx, spec, "改善につながる項目"),
                     note=f"Pearson 相関（全庁）。カテゴリは構成設問に回答した職員の平均。ペアの有効回答 n＝{min(ns):,}～{max(ns):,}。相関は因果効果を示さない。" if ns else None)
        rows = [["カテゴリ", "点数", "相関 r"]] + [[t[0], f1(t[1]), f1(t[2], 3)] for t in rows_d]
        tw = p.width * 0.36
        table(p, p.left, p.body_top, tw, rows, widths=[3, 1, 1], row_h=0.38)
        scatter_chart(p, p.left + tw + 0.3, p.body_top, p.width * 0.36, p.body_bottom - p.body_top,
                      [(t[0], t[1], t[2]) for t in rows_d if isnum(t[1]) and isnum(t[2])], "点数", "相関 r",
                      highlight={t[0] for t in sorted([t for t in rows_d if isnum(t[2])], key=lambda t: -t[2])[:3]})
        x = p.left + tw + p.width * 0.36 + 0.6
        insight_box(p, x, p.body_top, p.left + p.width - x, p.body_bottom - p.body_top, "組織・人事への示唆",
                    d.text(f"kd_category:{oid}"), f"insights['kd_category:{oid}']")


@register("kd_category_matrix")
def kd_category_matrix(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    p = ctx.page(_chap(ctx, spec, f"カテゴリ別キードライバー｜{len(d.objectives)}指標"),
                 "目的指標ごとに関連の強いカテゴリが異なるかを確認する", chapter=_chapter(ctx, spec, "改善につながる項目"),
                 note="Pearson 相関（全庁）。各指標で r が最も大きいカテゴリを太字。")
    rows = [["カテゴリ", "点数"] + [d.obj_name(o["id"], True) for o in d.objectives]]
    bold = set()
    best = {}
    for o in d.objectives:
        c = d.corr(o["id"], "カテゴリ")
        if len(c):
            best[o["id"]] = c.loc[c["r"].idxmax(), "unit_id"]
    for i, cat in enumerate(d.categories, 1):
        row = [cat, d.cell(ALL, ALL, "カテゴリ", cat)]
        for j, o in enumerate(d.objectives):
            c = d.corr(o["id"], "カテゴリ")
            r = c[c.unit_id == cat]
            row.append(f1(r["r"].iloc[0], 3) if len(r) else NA)
            if best.get(o["id"]) == cat:
                bold.add((i, 2 + j))
        rows.append(row)
    table(p, p.left, p.body_top, p.width * 0.65, rows, widths=[3, 1] + [1] * len(d.objectives), row_h=0.42,
          bold_cells=bold)
    insight_box(p, p.left + p.width * 0.67, p.body_top, p.width * 0.33, p.body_bottom - p.body_top,
                "組織・人事への示唆", d.text("kd_category_matrix"), "insights.kd_category_matrix")


@register("kd_question")
def kd_question(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    n = spec.get("n", 8)
    for oid in _objective_ids(d, {"objective": spec.get("objective", "all")}):
        c = d.corr(oid, "設問").dropna(subset=["r"]).sort_values("r", ascending=False)
        top = c.head(n)
        if top.empty:
            continue
        r0 = top.iloc[0]
        s0 = d.stat(ALL, ALL, "設問", r0.unit_id) or {}
        p = ctx.page(_chap(ctx, spec, f"設問別キードライバー｜{d.obj_name(oid)}"),
                     f"{d.qlabel(r0.unit_id, 28)} は {f1(s0.get('score'))}点、{d.obj_name(oid, True)}との相関は r＝{r0.r:.3f}",
                     chapter=_chapter(ctx, spec, "改善につながる項目"),
                     note=f"目的指標：{d.obj_name(oid)}。説明変数：説明設問 {len(d.drivers)} 問。ペアの有効回答 n＝{int(c.n.min()):,}～{int(c.n.max()):,}。多数の相関を並べているため、上位の順位は誤差で入れ替わり得る。相関は因果効果を示さない。")
        rows = [["関連が強い設問", "点数", "相関 r"]]
        for r in top.itertuples():
            rows.append([d.qlabel(r.unit_id, 34), d.cell(ALL, ALL, "設問", r.unit_id), f"{r.r:.3f}"])
        tw = p.width * 0.44
        table(p, p.left, p.body_top, tw, rows, widths=[5, 0.9, 0.9], row_h=0.4, aligns=["left", "center", "center"],
              size=th.sizes["note"] + 0.5)
        pts = []
        for r in c.itertuples():
            s = d.stat(ALL, ALL, "設問", r.unit_id) or {}
            if isnum(s.get("score")):
                pts.append((r.unit_id, s["score"], r.r))
        scatter_chart(p, p.left + tw + 0.3, p.body_top, p.width * 0.3, p.body_bottom - p.body_top, pts, "点数",
                      "相関 r", highlight=set(top.unit_id[:5]))
        x = p.left + tw + p.width * 0.3 + 0.6
        insight_box(p, x, p.body_top, p.left + p.width - x, p.body_bottom - p.body_top, "組織・人事への示唆",
                    d.text(f"kd_question:{oid}"), f"insights['kd_question:{oid}']")


# ================================================================= モデル
def _fit(d: ReportData, oid: str, model: str) -> dict | None:
    f = d.tables.get("model_fit")
    if f is None or f.empty:
        return None
    r = f[(f.objective_id == oid) & (f.model == model)]
    return None if r.empty else r.iloc[0].to_dict()


def _status_of(fit: dict) -> str:
    dec = fit.get("decision")
    return dec if isinstance(dec, str) and dec else f"{fit.get('candidate')}（候補）"


@register("model_guide")
def model_guide(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    s = d.cfg.models
    cv, tree = s.get("cv") or {}, s.get("tree") or {}
    p = ctx.page(_chap(ctx, spec, "相関・重回帰・決定木の読み方"), "3つの分析は問いが違う。どれも原因や施策の効果を示すものではない",
                 chapter=_chapter(ctx, spec, "改善につながる項目"),
                 note=f"検証: {cv.get('group_level') or '回答者'}単位で {cv.get('folds')} 分割し、学習に使わなかった{cv.get('group_level') or '回答者'}を予測。"
                 f"決定木: 深さ {tree.get('max_depth')}／葉の最低 {tree.get('min_leaf')} 名。")
    items = [("相関", "1つの項目と目的指標が一緒に高低する程度。他の項目の影響を含む。"),
             ("重回帰", "複数の項目を同時に入れたときに、それぞれに残る関連（標準化係数 β）。項目同士が似ていると係数は不安定になる。"),
             ("決定木", "目的指標の平均が大きく分かれる回答の条件。閾値はこの調査の標本での条件で、配置や評価の基準値ではない。"),
             ("未学習データでの検証", "学習に使わなかった組織の職員を予測し、平均だけで予測した場合と誤差（MAE、点）を比べる。学習データでの当てはまりだけでは採用しない。"),
             ("採否", "採用・参考・保留・推定不能の候補を出し、担当者が理由を確認して決める。保留・推定不能の係数は施策の根拠に使わない。")]
    for i, (h, b) in enumerate(items):
        y = p.body_top + i * 1.15
        add_rect(p.slide, th, p.left, y, 2.6, 1.0, th.hexv("navy"))
        text(p, p.left + 0.1, y, 2.4, 1.0, h, size=th.sizes["body"], bold=True, color="FFFFFF", align="center",
             anchor="middle")
        text(p, p.left + 2.8, y, p.width - 2.8, 1.0, b, size=th.sizes["small"], anchor="middle")


def _cluster_note(fit: dict) -> str:
    if not isnum(fit.get("cluster_sign_unstable")):
        return ""
    k = int(fit["cluster_sign_unstable"])
    return (f"組織単位の区間（感度分析）で 0 を含むようになる項目 {k} 件。" if k
            else "組織単位の区間（感度分析）でも結論は変わらない。")


@register("ols")
def ols(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    model = spec.get("model", "category")
    label = "カテゴリ" if model == "category" else "設問"
    coefs = d.tables.get("ols_coefs")
    for oid in _objective_ids(d, {"objective": spec.get("objective")}):
        fit = _fit(d, oid, model)
        if fit is None:
            continue
        title = _chap(ctx, spec, f"重回帰｜{d.obj_name(oid, True)}と関連する{label}")
        if fit.get("candidate") in ("推定不能", "保留") and not isinstance(fit.get("decision"), str):
            p = ctx.page(title, f"この重回帰は{fit['candidate']}。係数は施策の根拠に使わない",
                         chapter=_chapter(ctx, spec, "改善につながる項目"))
            text(p, p.left, p.body_top, p.width * 0.6, 3, ["理由: " + str(fit.get("reasons") or ""),
                 "代わりの読み方: 相関と点数の表、所属・属性の比較、職員の声を合わせて改善の論点を確認する。"],
                 size=th.sizes["body"])
            continue
        cf = coefs[(coefs.objective_id == oid) & (coefs.model == model)].copy()
        cf = cf.reindex(cf["beta"].abs().sort_values(ascending=False).index).head(spec.get("n", 12))
        cf = cf.sort_values("beta", ascending=False)
        names = [t if model == "category" else d.qlabel(t, 26) for t in cf.term]
        top = cf.iloc[0]
        msg = (f"{len(cf) if model == 'category' else len(d.drivers)}の{label}を同時に考慮しても、"
               f"{top.term if model == 'category' else top.term}との関連が残る（β＝{top.beta:.3f}）")
        p = ctx.page(title, msg, chapter=_chapter(ctx, spec, "改善につながる項目"),
                     note=f"標準化係数 β／95%区間（{d.cfg.models.get('ols', {}).get('interval') or 'HC3'}）。n＝{num(fit['n'])}。"
                     f"VIF 最大 {f1(fit.get('vif_max'))}。{_cluster_note(fit)}採否: {_status_of(fit)}。"
                     "係数は他の項目を同時に入れた条件での関連で、改善幅ではない。")
        rows = [[label, "β", "95%区間"]] + [[n, f"{r.beta:.3f}", f"{r.ci_low:.3f}～{r.ci_high:.3f}"]
                                            for n, r in zip(names, cf.itertuples())]
        tw = p.width * 0.42
        table(p, p.left, p.body_top, tw, rows, widths=[4, 1, 1.6], row_h=min(0.38, 5.6 / len(rows)),
              aligns=["left", "center", "center"], size=th.sizes["note"] + 0.5)
        bar_chart(p, p.left + tw + 0.3, p.body_top, p.width * 0.3, p.body_bottom - p.body_top - 1.1, names,
                  {"β": [round(b, 3) for b in cf.beta]}, [th.hexv("navy")], number_format="0.000")
        kx = p.left + tw + 0.3
        text(p, kx, p.body_bottom - 1.0, p.width * 0.3, 0.9,
             [f"調整済み R²＝{f1(fit.get('adj_r2'), 3)}　検証 R²＝{f1(fit.get('cv_r2'), 3)}",
              f"検証 MAE＝{f1(fit.get('cv_mae'))}点（平均のみ {f1(fit.get('base_mae'))}点）"], size=th.sizes["note"],
             color=th.hexv("ink_soft"))
        x = p.left + tw + p.width * 0.3 + 0.6
        insight_box(p, x, p.body_top, p.left + p.width - x, p.body_bottom - p.body_top, "組織・人事への示唆",
                    d.text(f"ols:{model}:{oid}"), f"insights['ols:{model}:{oid}']")


@register("model_fit_table")
def model_fit_table(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    f = d.tables.get("model_fit")
    p = ctx.page(_chap(ctx, spec, f"{len(d.objectives)}指標・3モデルの当てはまり"),
                 "未学習の組織での誤差を、平均だけで予測した場合と比べる", chapter=_chapter(ctx, spec, "改善につながる項目"),
                 note="検証 R² は全ての未学習データの予測を結合して算出。MAE＝平均絶対誤差（100点換算の点）。大きな R² は因果効果や施策による得点上昇を示さない。")
    names = {"category": "カテゴリ重回帰", "question": "設問重回帰", "tree": "決定木"}
    rows = [["目的指標", "モデル", "n", "R²", "調整済み R²", "検証 R²", "検証 MAE", "平均のみ MAE", "採否", "理由"]]
    for r in f.itertuples():
        rows.append([d.obj_name(r.objective_id, True), names.get(r.model, r.model), num(r.n), f1(r.r2, 3),
                     f1(r.adj_r2, 3), f1(r.cv_r2, 3), f1(r.cv_mae), f1(r.base_mae), _status_of(r._asdict()),
                     r.reasons if isinstance(r.reasons, str) else ""])
    table(p, p.left, p.body_top, p.width, rows, widths=[1, 1.2, 0.6, 0.6, 0.8, 0.7, 0.7, 0.8, 0.9, 3.5],
          row_h=min(0.42, (p.body_bottom - p.body_top) / len(rows)), size=th.sizes["note"],
          aligns=["left", "left"] + ["center"] * 7 + ["left"])


def _cond(d: ReportData, feature: str, thr: float, side: str) -> str:
    lo = math.floor(thr)
    return f"{feature}：回答 {lo} 以下" if side == "le" else f"{feature}：回答 {lo + 1} 以上"


@register("tree")
def tree(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    nodes = d.tables.get("tree_nodes")
    for oid in _objective_ids(d, {"objective": spec.get("objective", "all")}):
        nd = nodes[nodes.objective_id == oid] if nodes is not None and not nodes.empty else None
        fit = _fit(d, oid, "tree")
        if nd is None or nd.empty or fit is None:
            continue
        nd = nd.set_index("node")
        root = nd.loc[0]
        stab = d.tables.get("tree_stability")
        freq = None
        if stab is not None and not stab.empty:
            s = stab[(stab.objective_id == oid) & (stab.feature == root.feature)]
            freq = s.root_freq.iloc[0] if len(s) else 0.0
        p = ctx.page(_chap(ctx, spec, f"決定木｜{d.obj_name(oid)}"),
                     f"{d.qlabel(root.feature, 30)}への回答で、{d.obj_name(oid, True)}の平均が分かれる",
                     chapter=_chapter(ctx, spec, "改善につながる項目"),
                     note=f"n＝{num(root.n)}、完全回答者の群平均。深さ {d.cfg.models['tree']['max_depth']}／最小葉 {d.cfg.models['tree']['min_leaf']} 名。"
                     f"検証 R²＝{f1(fit.get('cv_r2'), 3)}。最初の分岐の再抽出頻度 {pct(freq) if freq is not None else NA}。採否: {_status_of(fit)}。"
                     "群の平均差は、施策で同じ幅だけ改善することを示さない。")
        depth = int(nd["depth"].max())
        tw = p.width * 0.68
        levels: dict[int, list[int]] = {}
        for i, r in nd.sort_index().iterrows():
            levels.setdefault(int(r.depth), []).append(i)
        bh = 1.15
        gap = (p.body_bottom - p.body_top - bh * (depth + 1)) / max(depth, 1)
        pos: dict[int, tuple[float, float]] = {}
        for lv in range(depth + 1):
            ids = levels.get(lv, [])
            slots = 2 ** lv
            bw = min(3.6, tw / slots - 0.25)
            for i in ids:
                # 親から位置を決める（左=le、右=gt）
                if lv == 0:
                    k = 0
                else:
                    par = int(nd.loc[i, "parent"])
                    k = pos[par][2] * 2 + (0 if nd.loc[i, "side"] == "le" else 1)
                cx = p.left + (k + 0.5) * tw / slots
                y = p.body_top + lv * (bh + gap)
                pos[i] = (cx, y, k)
                r = nd.loc[i]
                leaf = bool(r.is_leaf)
                add_rect(p.slide, th, cx - bw / 2, y, bw, bh, th.hexv("navy") if lv == 0 else (
                    th.hexv("pale") if leaf else th.hexv("navy_pale")), th.hexv("line"))
                col = "FFFFFF" if lv == 0 else th.hexv("ink")
                lines = []
                if lv > 0:
                    pr = nd.loc[int(r.parent)]
                    lines.append([{"text": _cond(d, pr.feature, pr.threshold, r.side), "bold": True}])
                else:
                    lines.append([{"text": d.qlabel(r.feature, 22), "bold": True}])
                if isnum(r["n"]):
                    lines.append(f"{num(r['n'])}名／{f1(r['mean'])}点" + (f"／肯定 {pct(r['pos'])}" if leaf else ""))
                else:
                    lines.append(HIDDEN)
                if not leaf and lv > 0:
                    lines.append(f"次の分岐：{r.feature}")
                add_text(p.slide, th, cx - bw / 2 + 0.08, y + 0.05, bw - 0.16, bh - 0.1, lines,
                         size=th.sizes["note"], color=col, align="center", anchor="middle")
                if lv > 0:
                    px, py, _ = pos[int(r.parent)]
                    add_line(p.slide, th, px, py + bh, cx, y, th.hexv("ink_soft"), 1)
        x = p.left + tw + 0.3
        insight_box(p, x, p.body_top, p.left + p.width - x, p.body_bottom - p.body_top, "組織・人事への示唆",
                    d.text(f"tree:{oid}"), f"insights['tree:{oid}']")


# ================================================================= 職員の声・施策（compose の文章）
def _placeholder_page(ctx: Ctx, spec: dict, title: str, chapter: str, what: list[str], key: str) -> None:
    th = ctx.th
    p = ctx.page(_chap(ctx, spec, title), f"{PLACEHOLDER} {title}は担当者が作成する", chapter=_chapter(ctx, spec, chapter),
                 note=f"interpretation.yaml の {key} に記入すると、この頁が置き換わる。")
    add_rect(p.slide, th, p.left, p.body_top, p.width, p.body_bottom - p.body_top, th.hexv("bg"), th.hexv("gold"))
    text(p, p.left + 0.4, p.body_top + 0.3, p.width - 0.8, p.body_bottom - p.body_top - 0.6,
         ["記入する内容:"] + [f"・{w}" for w in what], size=th.sizes["body"], color=th.hexv("placeholder"))


@register("comments")
def comments(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    cm = d.compose.get("comments")
    if not cm or not cm.get("themes"):
        _placeholder_page(ctx, spec, "職員の声｜全体の傾向", "職員の声",
                          ["テーマ別の記述者数（同じ記述者の同じテーマは1回）と分母（記述者数）",
                           "人が確認した匿名の要約（個人・職場・事案を推測できる表現を除く）",
                           "頻度と深刻さを分けた読み方。全職員の意見率としては扱わない"], "comments")
        return
    total = cm.get("writers")
    themes = cm["themes"]
    p = ctx.page(_chap(ctx, spec, "職員の声｜全体の傾向"), cm.get("message") or "声を改善場面の仮説に変え、定量結果と合わせて背景を確認する",
                 chapter=_chapter(ctx, spec, "職員の声"),
                 note=f"複数テーマ分類。分母は記述者 {num(total)} 名。全職員の意見分布や重要度順位ではない。")
    cw = p.width * 0.5
    bar_chart(p, p.left, p.body_top, cw, p.body_bottom - p.body_top, [t["name"] for t in themes],
              {"記述者数": [t["writers"] for t in themes]}, [th.hexv("navy")], number_format="0")
    insight_box(p, p.left + cw + 0.3, p.body_top, p.width - cw - 0.3, p.body_bottom - p.body_top, "組織・人事への示唆",
                cm.get("insight"), "comments.insight")


@register("policy")
def policy(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    pol = d.compose.get("policy")
    if not pol:
        _placeholder_page(ctx, spec, "改善の大方針〔協議案〕", "施策の方向",
                          ["大方針の一文と、それを支える 2〜3 の柱（自治体の基本方針とのつながり）",
                           "観測事実 → 背景仮説 → 大方針 の筋道（根拠の頁と有効 n）",
                           "部局・人事・首長部局の役割の分け方"], "policy")
        return
    p = ctx.page(_chap(ctx, spec, "改善の大方針〔協議案〕"), pol.get("message"), chapter=_chapter(ctx, spec, "施策の方向"),
                 note=pol.get("note"))
    add_rect(p.slide, th, p.left, p.body_top, p.width, 1.0, th.hexv("navy"))
    text(p, p.left + 0.3, p.body_top, p.width - 0.6, 1.0, pol.get("title", ""), size=18, bold=True, color="FFFFFF",
         anchor="middle")
    pillars = pol.get("pillars") or []
    w = (p.width - 0.3 * (len(pillars) - 1)) / max(len(pillars), 1)
    for i, pl in enumerate(pillars):
        x = p.left + i * (w + 0.3)
        add_rect(p.slide, th, x, p.body_top + 1.3, w, 2.4, th.hexv("pale"))
        text(p, x + 0.25, p.body_top + 1.45, w - 0.5, 0.5, f"{i + 1:02d}　{pl.get('title', '')}", size=th.sizes["body"],
             bold=True, color=th.hexv("navy"))
        text(p, x + 0.25, p.body_top + 2.0, w - 0.5, 1.6, pl.get("text", ""), size=th.sizes["small"])
    insight_box(p, p.left, p.body_top + 4.0, p.width, p.body_bottom - p.body_top - 4.0, "方針の説明",
                pol.get("insight"), "policy.insight")


@register("measures")
def measures(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    ms = d.compose.get("measures") or []
    if not ms:
        _placeholder_page(ctx, spec, "重点施策〔協議案〕", "施策の方向",
                          ["対象・実行内容・担当・支援・権限（課長が決められる運用と制度判断を分ける）",
                           "着手条件（責任者・時間と権限・既存の会議や面談への接続）",
                           "30日・90日の手順、工数の仮定（人数×回数×時間）、拡大・修正・中止の条件"], "measures")
        return
    for i, m in enumerate(ms, 1):
        p = ctx.page(_chap(ctx, spec, f"重点施策{i}｜{m.get('title', '')}"), m.get("message"),
                     chapter=_chapter(ctx, spec, "施策の方向"), note=m.get("note"))
        cols = [("結果と組織人事の示唆", m.get("evidence")), ("実行内容", m.get("actions")),
                ("担当・支援・権限", m.get("owners")), ("試行と確認", m.get("trial"))]
        w = (p.width - 0.9) / 4
        for j, (h, body) in enumerate(cols):
            x = p.left + j * (w + 0.3)
            add_rect(p.slide, th, x, p.body_top, w, 0.5, th.hexv("navy"))
            text(p, x + 0.15, p.body_top, w - 0.3, 0.5, h, size=th.sizes["small"], bold=True, color="FFFFFF",
                 anchor="middle")
            body = body if isinstance(body, list) else [body] if body else [PLACEHOLDER]
            text(p, x + 0.15, p.body_top + 0.65, w - 0.3, p.body_bottom - p.body_top - 0.8, body,
                 size=th.sizes["small"], color=th.hexv("ink") if body != [PLACEHOLDER] else th.hexv("placeholder"))


@register("kpi_plan")
def kpi_plan(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    rows_in = d.compose.get("kpis") or []
    p = ctx.page(_chap(ctx, spec, "実施・理解・実行性を確認する指標〔協議案〕"),
                 "件数で終わらず、正味の負担と職員の理解を確認する" if rows_in else f"{PLACEHOLDER} 確認指標は担当者が設定する",
                 chapter=_chapter(ctx, spec, "実行計画と確認指標"),
                 note="短期の運用指標と次回調査の状態指標を組み合わせる。回帰係数から得点の改善を保証しない。")
    rows = [["重点領域", "実施・職員の実感", "90日の判断材料", "次回調査で確認する設問"]]
    for r in rows_in:
        rows.append([r.get("area", ""), r.get("practice", ""), r.get("judge", ""), r.get("survey", "")])
    if len(rows) == 1:
        rows += [[PLACEHOLDER, "", "", ""] for _ in range(3)]
    table(p, p.left, p.body_top, p.width, rows, widths=[1.2, 2.5, 2.5, 1.8], row_h=0.9,
          aligns=["left"] * 4, size=th.sizes["small"])


@register("action_plan_sheet")
def action_plan_sheet(ctx: Ctx, spec: dict) -> None:
    th = ctx.th
    p = ctx.page(_chap(ctx, spec, spec.get("title") or "アクションプラン記入シート"), "重点課題を選び、実行できる内容と確認方法を合意する",
                 chapter=_chapter(ctx, spec, "施策の方向"), note="重点施策の案を参考に、職場で優先する課題を選んで各欄へ記入する。")
    text(p, p.left, p.body_top, p.width, 0.4, f"対象：{ctx.scope_name}　　記入者：　　　　　　　　記入日：　　　年　　月　　日",
         size=th.sizes["body"])
    boxes = spec.get("boxes") or ["重点課題", "根拠となる結果", "具体的な行動", "担当・支援者",
                                  "期限・必要な時間", "確認指標・見直しの条件"]
    rows_n = math.ceil(len(boxes) / 2)
    w, h = (p.width - 0.3) / 2, (p.body_bottom - p.body_top - 0.6 - 0.2 * (rows_n - 1)) / rows_n
    for i, b in enumerate(boxes):
        x = p.left + (i % 2) * (w + 0.3)
        y = p.body_top + 0.6 + (i // 2) * (h + 0.2)
        add_rect(p.slide, th, x, y, w, h, th.hexv("bg"), th.hexv("navy"))
        add_rect(p.slide, th, x, y, w, 0.42, th.hexv("navy_pale"))
        text(p, x + 0.15, y, w - 0.3, 0.42, b, size=th.sizes["small"], bold=True, color=th.hexv("navy"), anchor="middle")


# ================================================================= 資料
@register("audit_notes")
def audit_notes(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    p = ctx.page("資料. 集計上の確認事項", chapter="資料", note="未確定の項目は担当者が確認して設定に記録する。")
    units = set(d.tables["org"].unit_id)
    names = []
    for h in d.manifest.get("hidden_scopes") or []:
        kind, sid = h.split(":", 1)
        if kind == "組織" and sid in units:
            names.append(d.unit_name(sid))
        elif kind == "属性":
            names.append(sid.replace("=", "："))
    a = d.status(ALL, ALL) or {}
    rows = [["項目", "内容"],
            ["集計人数", f"有効回答 {num(a.get('responded_n'))} 名（対象 {num(a.get('target_n'))} 名）"],
            ["公開条件", f"最低 {d.manifest.get('min_n')} 名。差し引きで推測できる区分も非表示"
                      + ("（二次秘匿あり）" if d.manifest.get("secondary_suppression") else "")],
            ["非表示の組織・区分", "、".join(names) or "なし"],
            ["欠損の扱い", "未回答・除外選択肢・条件外（対象外の職員）は分母から除く。カテゴリは構成設問に回答した職員で集計"],
            ["比較の基準", "全庁の値は自所属を含む。差は点数の差（pt）で、有意差ではない"]]
    unresolved = d.compose.get("unresolved") or []
    rows.append(["未確定の事項", "／".join(unresolved[:6]) + ("…" if len(unresolved) > 6 else "") or "なし"])
    table(p, p.left, p.body_top, p.width, rows, widths=[1.2, 6], row_h=0.62, aligns=["left", "left"],
          size=th.sizes["small"])


GLOSSARY = [("点数", "回答の平均を100点満点に換算した値。"),
            ("肯定的回答率", "肯定側の選択肢を選んだ人の割合。点数とは別の数値。"),
            ("有効回答 n", "その集計に使った回答者数。設問・カテゴリごとに異なる。"),
            ("全庁差", "所属や区分の点数 − 全庁の点数（pt）。全庁には自所属を含む。"),
            ("相関 r", "−1〜1。2つの値が一緒に高低する程度。因果ではない。"),
            ("標準化係数 β", "重回帰で、他の項目を同時に考慮したときの関連の大きさ。"),
            ("検証 R²・MAE", "学習に使わなかったデータを予測したときの当てはまりと平均誤差（点）。"),
            ("決定木", "目的指標の平均が大きく分かれる回答の条件を順に示した図。"),
            ("非表示", "回答者が少ない、または差し引きで少人数が推測できるため、人数・率・点数を出さない。"),
            ("〔協議案〕", "合意前の案。権限・予算・担当を確認して決める。")]


@register("glossary")
def glossary(ctx: Ctx, spec: dict) -> None:
    th = ctx.th
    p = ctx.page("資料. 用語集", chapter="資料")
    rows = [["用語", "意味"]] + [[a, b] for a, b in GLOSSARY]
    table(p, p.left, p.body_top, p.width, rows, widths=[1.2, 5], row_h=0.55, aligns=["left", "left"],
          size=th.sizes["small"])


@register("appendix_guide")
def appendix_guide(ctx: Ctx, spec: dict) -> None:
    """本編の最後に置く、別冊の詳細付録の案内。spec.deck に付録のデッキ名。"""
    from .deck import deck_spec  # 循環 import を避ける

    th, d = ctx.th, ctx.d
    c = d.cfg.client
    deck = deck_spec(d.cfg, spec.get("deck", "全庁_詳細付録"))
    fname = deck["filename"].format(client=c["name"], fy=d.cfg.fiscal_year, reader=d.reader_id)
    units = _appendix_units(ctx)
    n_hidden = sum(d.hidden("組織", u) for u, _ in units)
    p = ctx.page("資料. 詳細付録の案内", "所属ごとの全カテゴリ・全設問は別冊の詳細付録で確認する", chapter="資料",
                 note="付録の値は本編と同じ集計から作成。組織順に並べ、順位は付けていない。")
    rows = [["項目", "内容"],
            ["ファイル", fname],
            ["収録範囲", f"{len(units)} 所属（うち少人数等で非表示 {n_hidden}）"],
            ["収録内容", f"主要指標の一覧、所属ごとのカテゴリ・目的指標、全{len(d.cfg.likert_ids())}問の設問全文・n・点数・"
                       "肯定／中立／否定・全庁差"],
            ["探し方", "付録冒頭の「所属別の詳細ページ案内」に所属ごとのページ範囲を記載"]]
    table(p, p.left, p.body_top, p.width, rows, widths=[1.2, 6], row_h=0.62, aligns=["left", "left"],
          size=th.sizes["small"])


# ================================================================= 詳細付録
def _appendix_units(ctx: Ctx) -> list[tuple[str, int]]:
    return _scope_units(ctx)


def _q_pages(d: ReportData, per: int) -> list[list[str]]:
    qs = [o["question"] for o in d.objectives]
    likert = [q for q in d.cfg.likert_ids() if q not in qs] + qs
    return _paged(likert, per)


@register("appendix_index")
def appendix_index(ctx: Ctx, spec: dict) -> None:
    th, d = ctx.th, ctx.d
    units = _appendix_units(ctx)
    per = spec.get("questions_per_page", 9)
    nq = len(_q_pages(d, per))
    start = len(ctx.prs.slides._sldIdLst) + 1 + math.ceil((len(units) + 1) / 16)  # noqa: SLF001
    rows_all = [["所属", "区分", "詳細ページ", "掲載の扱い"]]
    page = start
    for uid, depth in units:
        hidden = d.hidden("組織", uid)
        n_pages = 1 if hidden else 1 + nq
        lvl = d.tables["org"].set_index("unit_id").loc[uid, "level"]
        rows_all.append(["　" * depth + d.unit_name(uid), lvl, f"{page}～{page + n_pages - 1}",
                         HIDDEN if hidden else f"カテゴリ＋全{len(d.cfg.likert_ids())}問"])
        page += n_pages
    chunks = _paged(rows_all[1:], 16)
    for k, chunk in enumerate(chunks, 1):
        p = ctx.page(f"A1. 所属別の詳細ページ案内" + (f" {k}/{len(chunks)}" if len(chunks) > 1 else ""),
                     chapter="詳細付録", note="掲載範囲は読者に許された所属だけ。別部局の個別結果・自由記述の原文は含まない。")
        table(p, p.left, 1.3, p.width, [rows_all[0]] + chunk, widths=[3, 1, 1.2, 2], row_h=0.4)


@register("unit_appendix")
def unit_appendix(ctx: Ctx, spec: dict) -> None:
    """所属ごとに カテゴリ・目的指標 1 頁 ＋ 設問詳細（per 問ずつ）。非表示の所属は 1 頁。"""
    th, d = ctx.th, ctx.d
    per = spec.get("questions_per_page", 9)
    qpages = _q_pages(d, per)
    parent_of = d.tables["org"].set_index("unit_id")["parent_id"].to_dict()
    for uid, _ in _appendix_units(ctx):
        name = d.unit_name(uid)
        st = d.status("組織", uid) or {}
        if d.hidden("組織", uid):
            p = ctx.page(f"A2. {name}｜非表示", "この所属の定量結果は非表示", chapter="詳細付録")
            text(p, p.left, p.body_top, p.width, 2,
                 [f"有効回答者 {d.manifest.get('min_n')} 名未満、または差し引きで少人数が推測できるため。",
                  "点数・分布・比較差・有効回答人数は、本文と付録を通じて同じ扱いとしている。",
                  "回答者を推測しないよう、集約できる上位組織の結果を参照する。"], size=th.sizes["body"])
            continue
        par = parent_of.get(uid)
        par = None if not isinstance(par, str) else par
        sub = f"有効回答 {num(st.get('responded_n'))}名" + (f"／対象 {num(st.get('target_n'))}名（{pct(st.get('rate'))}）"
                                                         if isnum(st.get("target_n")) else "")
        p = ctx.page(f"A2. {name}｜カテゴリ・{len(d.objectives)}指標", sub, chapter="詳細付録",
                     note="カテゴリは構成設問に回答した職員の平均。単位：点／差は pt。少人数の項目は個別にも非表示。")
        rows = [["カテゴリ", "n", "点数", "全庁差"]]
        for c in d.categories:
            v = d.stat("組織", uid, "カテゴリ", c) or {}
            a = d.stat(ALL, ALL, "カテゴリ", c) or {}
            hid = isinstance(v.get("suppressed"), str)
            rows.append([c, HIDDEN if hid else num(v.get("n")), HIDDEN if hid else f1(v.get("score")),
                         HIDDEN if hid else diff((v.get("score") or float("nan")) - (a.get("score") or float("nan")))])
        table(p, p.left, p.body_top, p.width * 0.55, rows, widths=[3, 0.8, 0.8, 0.8], row_h=0.4)
        rows = [["指標", "点数", "肯定 %"]] + [[o.get("name"), d.cell("組織", uid, "目的指標", o["id"]),
                                            d.cell("組織", uid, "目的指標", o["id"], "pos", pct)] for o in d.objectives]
        table(p, p.left + p.width * 0.58, p.body_top, p.width * 0.42, rows, widths=[2.5, 1, 1], row_h=0.4)
        for k, qs in enumerate(qpages, 1):
            first, last = qs[0], qs[-1]
            p = ctx.page(f"A2. {name}｜設問詳細 {k}/{len(qpages)}", None, chapter="詳細付録",
                         note=f"{name}｜{first}～{last}。平均は回答の点数（逆転設問は逆転後）。部局差＝上位組織との差、全庁差＝全庁との差（pt）。")
            rows = [["設問（全文）", "n", "平均", "点数", "肯定 %", "中立 %", "否定 %", "部局差", "全庁差"]]
            for q in qs:
                v = d.stat("組織", uid, "設問", q) or {}
                if isinstance(v.get("suppressed"), str) or not v:
                    rows.append([d.qlabel(q)] + [HIDDEN if v else NA] * 8)
                    continue
                a = d.stat(ALL, ALL, "設問", q) or {}
                pv = d.stat("組織", par, "設問", q) if par else None
                rows.append([d.qlabel(q), num(v.get("n")), f1(v.get("mean"), 2), f1(v.get("score")), pct(v.get("pos")),
                             pct(v.get("neu")), pct(v.get("neg")),
                             diff(v["score"] - pv["score"]) if pv and isnum(pv.get("score")) and isnum(v.get("score")) else "—",
                             diff(v["score"] - a["score"]) if isnum(a.get("score")) and isnum(v.get("score")) else NA])
            table(p, p.left, 1.3, p.width, rows, widths=[7, 0.6, 0.6, 0.6, 0.7, 0.7, 0.7, 0.7, 0.7],
                  row_h=min(0.75, (p.body_bottom - 1.3) / len(rows)), size=th.sizes["note"],
                  aligns=["left"] + ["center"] * 8)
