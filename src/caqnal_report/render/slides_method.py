"""分析手法解説デッキ（インナー向け）の builder 群。type 名は `m_` で始める。

レポート本体と同じ ctx（設定＋中間データ）から生成するので、閾値・n・モデル統計などはレポートと一致する。
"""
from __future__ import annotations

import numpy as np

from ..analysis.judgement import SIGNAL_LABELS, SIGNALS
from ..analysis.segmentation import QUADRANTS
from ..config import LIKERT_ROLES
from .charts import fmt, fmt_pct
from .pptx_helpers import add_rect, add_table, add_text, bullets
from .slides import Ctx, _frame, slide_type

STD_FOOT = "本資料は社内向け。数値・設定値は output/intermediate/ の中間データと settings_snapshot.yaml から生成。"


def _boxes_flow(slide, th, x, y, w, h, steps: list[tuple[str, list[str]]], color_keys: list[str]):
    """横並びの工程ボックスと矢印。steps=[(見出し, [箇条書き])]"""
    n = len(steps)
    gap = 0.25
    bw = (w - gap * (n - 1)) / n
    for i, (title, items) in enumerate(steps):
        bx = x + i * (bw + gap)
        add_rect(slide, th, bx, y, bw, 0.34, th.hexv(color_keys[i % len(color_keys)]))
        add_text(slide, th, bx, y, bw, 0.34, title, size=9.5, bold=True, color="FFFFFF", anchor="middle", align="center")
        add_rect(slide, th, bx, y + 0.34, bw, h - 0.34, th.hexv("bg"), th.hexv("line"))
        bullets(slide, th, bx + 0.05, y + 0.4, bw - 0.1, h - 0.45, items, size=7.5, spacing=1.05)
        if i < n - 1:
            add_text(slide, th, bx + bw, y + h / 2 - 0.2, gap, 0.4, "▶", size=12, color=th.hexv("brand"), align="center", anchor="middle", margin=0)


@slide_type("m_pipeline")
def m_pipeline(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    _frame(slide, ctx, spec, "分析の全体像（3ステージ・ファイル結合）",
           "受領データ → 設定ファイル化 → 解析（中間データ）→ 描画 の順に、各ステージはファイルだけで結合する。同じ入力と設定から常に同じ結果が出る。",
           footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    sm = ctx.summary
    steps = [
        ("① 取込・設定化", ["caqnal profile: 列名・分布を md に要約", "caqnal init: 設問マスタ xlsx → questions.yaml",
                            "経年対応表 yoy_map.yaml の下書き", "人が role / 逆転 / 条件 / 経年区分を確定", "caqnal validate: 仕様書1-3 のチェック"]),
        ("② 解析", [f"回答 {sm['respondents']:,} 件を読み込みスコア化", "全庁 / 部局 / 属性 × 設問 / カテゴリ / 軸 / 指標 を集計",
                    "ランキング・4類型・Key Driver・自由記述", "6観点の判定・重点部局スコア", "output/intermediate/ に CSV / JSON を出力"]),
        ("③ 描画", ["config/decks/*.yaml のスライドリストを順に生成", "図表は matplotlib → PNG、表は python-pptx",
                    "カクナルスライド.pptx のレイアウトを使用", "全庁 / 部局別×N / One Pager / 手法解説 / Excel", "--pdf で LibreOffice 変換"]),
    ]
    _boxes_flow(slide, th, x, y, w, 2.55, steps, ["brand_dark", "positive", "brand"])
    rows = [["ステージ", "入力", "出力", "コマンド"],
            ["取込・設定化", "data/*.csv, *.xlsx", "client.yaml / questions.yaml / yoy_map.yaml / profile/*.md", "profile / init / validate"],
            ["解析", "上記 YAML + data/", "scores.csv, rankings.csv, segments.csv, drivers.csv, judgement.csv, insights.json …", "analyze"],
            ["描画", "output/intermediate/ + decks/*.yaml + theme.yaml", "*.pptx / *.pdf / 詳細集計.xlsx", "render / all"],
            ["再現性確認", "同上", "2回実行の SHA-256 比較", "verify"]]
    add_table(slide, th, x, y + 2.7, w, rows, col_widths=[1.3, 2.2, 4.2, 1.4], row_h=0.28, size=7.5, aligns=["left"] * 4)


@slide_type("m_inputs")
def m_inputs(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    c = ctx.cfg.client
    d = ctx.cfg.raw["data"]
    sm = ctx.summary
    _frame(slide, ctx, spec, "入力データと前処理", "受領 CSV をそのまま使わず、設問マスタと突合して正規化する。前処理のルールは全て設定ファイルに書く。",
           footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    prev = d.get("previous", [{}])[0] if d.get("previous") else {}
    rows = [["項目", "今回の設定・実績"],
            ["当年度回答", f"{d['responses']}　{sm['respondents']:,} 行 × {sm['question_count_total']} 列（encoding {d.get('encoding', 'utf-8-sig')}）"],
            ["前年度回答", f"{prev.get('responses', '－')}　{sm.get('respondents_prev') or '－'} 行" if prev else "なし"],
            ["回答者 ID", ("列 " + d["respondent_id"]["name"]) if d.get("respondent_id", {}).get("source") == "column"
             else f"CSV に無いため行番号から採番（{d.get('respondent_id', {}).get('prefix', '')}0001…）。個人と紐づく情報は保持しない"],
            ["ヘッダー突合", "questions.yaml の column / header と CSV ヘッダーを完全一致で検証（不一致は停止）"],
            ["ラベル正規化", "先頭の丸数字（①②…）と直後の空白を除去してテキストで扱う。年度でコードが変わっても文言で突合できる"],
            ["条件付き設問", "／".join(f"{q['id']}（{q['condition']['attribute']}={q['condition']['equals']} の回答のみ採用）" for q in ctx.cfg.questions if q.get("condition")) or "なし"],
            ["逆転項目", "・".join(f"{q['id']} {q['text'][:18]}…" for q in ctx.cfg.questions if q.get("reverse")) or "なし"],
            ["未回答", "当該設問のみ欠損。回答者全体は除外しない"],
            ["除外回答", "「わからない」等は scales.excluded に列挙し分母から除外（本調査では該当なし）" if not ctx.cfg.scale("agree5").get("excluded") else str(ctx.cfg.scale("agree5")["excluded"])],
            ["組織", f"{ctx.org_level} {len(ctx.cfg.org_units)} 単位。設問マスタの選択肢順を表示順とする"],
            ["対象者数", f"{c.get('target_count'):,}" if c.get("target_count") else "未設定（回答率は「－」表示）"]]
    add_table(slide, th, x, y, w, rows, col_widths=[1.5, 7.6], row_h=0.3, size=7.5, aligns=["left", "left"])


@slide_type("m_question_structure")
def m_question_structure(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    _frame(slide, ctx, spec, "設問構造（軸・カテゴリ・指標）",
           "設問マスタ（questions.yaml）の role / axis / category で集計単位を決める。指標の構成設問は client.yaml で明示する。", footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    qs = ctx.cfg.qs(LIKERT_ROLES)
    rows = [["カテゴリ", "軸", "設問数", "設問ID"]]
    for cat in ctx.summary["categories"]:
        ids = ctx.cfg.category_questions(cat)
        axes = sorted({ctx.cfg.q(i).get("axis", "") for i in ids})
        rows.append([cat, "・".join(axes), len(ids), " ".join(ids)])
    rows.append(["計", "", len(qs), ""])
    add_table(slide, th, x, y, 5.6, rows, col_widths=[1.7, 1.1, 0.6, 2.2], row_h=min(0.28, (h - 0.2) / len(rows)), size=7.2,
              aligns=["left", "left", "center", "left"], bold_cells={(len(rows) - 1, c) for c in range(4)})
    items = []
    for key, sp in ctx.summary["indicators"].items():
        items.append([{"text": f"{sp['name']}（{key}）: ", "bold": True}, {"text": f"{len(sp['questions'])} 問　" + " ".join(sp["questions"][:6]) + ("…" if len(sp["questions"]) > 6 else "")}])
    add_text(slide, th, x + 5.8, y, 3.3, 0.25, "指標（結果変数・分析軸）", size=9, bold=True, color=th.hexv("brand_dark"))
    bullets(slide, th, x + 5.8, y + 0.27, 3.3, 1.6, items, size=7.5, marker="", spacing=1.1)
    roles = {"outcome": "結果指標（総合エンゲージメント）", "driver": "ドライバー（要因候補）", "attribute": "属性", "freetext": "自由記述"}
    cnt = {r: len(ctx.cfg.qs((r,))) for r in roles}
    add_text(slide, th, x + 5.8, y + 1.95, 3.3, 0.25, "role の内訳", size=9, bold=True, color=th.hexv("brand_dark"))
    bullets(slide, th, x + 5.8, y + 2.22, 3.3, h - 2.3, [f"{v}: {cnt[k]} 問" for k, v in roles.items()]
            + ["Key Driver では outcome を目的変数、driver を説明変数にする", "カテゴリ・軸のスコアは構成設問の回答者平均"], size=7.5, spacing=1.1)


@slide_type("m_scoring")
def m_scoring(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    sc = ctx.cfg.scale("agree5")
    _frame(slide, ctx, spec, "尺度とスコアの定義（仕様書 §3）",
           "選択肢ラベルを数値化し、平均・肯定率・否定率・標準偏差・0-100 換算を算出する。丸数字の順序は自治体ごとに逆になることがあるため、ラベル→スコアの写像を明示する。",
           footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    rows = [["選択肢（正規化後）", "スコア", "区分"]]
    cls = {}
    for k in ("positive", "neutral", "negative"):
        for v in sc.get(k, []):
            cls[float(v)] = {"positive": "肯定", "neutral": "中立", "negative": "否定"}[k]
    for lab, v in sc["labels"].items():
        rows.append([lab, v, cls.get(float(v), "除外")])
    add_table(slide, th, x, y, 3.6, rows, col_widths=[2.2, 0.6, 0.8], row_h=0.3, size=8, aligns=["left", "center", "center"])
    defs = [
        [{"text": "平均 Mean　", "bold": True}, {"text": "有効回答スコアの合計 ÷ 有効回答数（設問）。カテゴリ・軸・指標は回答者ごとの構成設問平均を平均"}],
        [{"text": "肯定率 Positive%　", "bold": True}, {"text": "スコア 5・4 の回答数 ÷ 有効回答数 × 100。複合単位は構成設問の全回答をプール"}],
        [{"text": "否定率 / 中立率　", "bold": True}, {"text": "スコア 2・1 ／ 3 の割合"}],
        [{"text": "標準偏差 SD　", "bold": True}, {"text": "回答者スコアの標本標準偏差（ばらつき分析に使用）"}],
        [{"text": "0-100 換算　", "bold": True}, {"text": "(平均 − 1) ÷ (5 − 1) × 100。尺度が異なる年度・調査との参考比較用"}],
        [{"text": "逆転項目　", "bold": True}, {"text": "score' = (min + max) − score。肯定／否定の区分も反転後のスコアで判定。原回答は保持"}],
        [{"text": "指標の算出条件　", "bold": True}, {"text": f"構成設問の {int(ctx.cfg.analysis['score']['indicator_min_answer_ratio'] * 100)}% 以上に回答した回答者のみ指標スコアを算出"}],
        [{"text": "表示桁　", "bold": True}, {"text": f"平均は小数 {ctx.cfg.analysis['score']['decimals_mean']} 桁、割合は整数 %。非表示は「－」"}],
    ]
    bullets(slide, th, x + 3.8, y, 5.3, h, defs, size=7.8, spacing=1.15)


@slide_type("m_aggregation")
def m_aggregation(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    a = ctx.cfg.analysis["anonymity"]
    sm = ctx.summary
    _frame(slide, ctx, spec, "集計単位と匿名性（仕様書 §2-2, §4-1）",
           f"scope（誰の）× unit（何の）の全組み合わせを同じロジックで計算し scores.csv に 1 行ずつ出力する。n<{a['min_n']} の scope は全指標を非表示にする。",
           footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    n_scopes = 1 + len(ctx.cfg.org_units) + sum(len(v.get("order", [])) for v in ctx.cfg.attributes.values())
    n_units = len(ctx.cfg.likert_ids()) + len(sm["categories"]) + len(sm["axes"]) + len(sm["indicators"])
    rows = [["scope（行）", "件数", "unit（列）", "件数"],
            ["全庁", 1, "設問", len(ctx.cfg.likert_ids())],
            [ctx.org_level, len(ctx.cfg.org_units), "カテゴリ", len(sm["categories"])],
            ["属性値（" + "・".join(ctx.cfg.attributes) + "）", n_scopes - 1 - len(ctx.cfg.org_units), "軸", len(sm["axes"])],
            ["", "", "指標", len(sm["indicators"])],
            ["合計", n_scopes, "合計", n_units]]
    add_table(slide, th, x, y, 5.0, rows, col_widths=[2.4, 0.6, 1.2, 0.6], row_h=0.3, size=7.8, aligns=["left", "center", "left", "center"],
              bold_cells={(5, c) for c in range(4)})
    add_text(slide, th, x, y + 1.95, 5.0, 0.25, f"scores.csv の列（{n_scopes} × {n_units} = {n_scopes * n_units:,} 行）", size=9, bold=True, color=th.hexv("brand_dark"))
    cols = ["n / n_ans: 回答者数 / 回答数", "mean / sd / pos / neu / neg / norm", "diff_all / p_all / sig_all: 全庁差とその検定",
            "prev_mean / prev_n / diff_yoy / p_yoy / sig_yoy / yoy_status: 前年比較", "suppressed: 匿名性抑制フラグ"]
    bullets(slide, th, x, y + 2.22, 5.0, h - 2.3, cols, size=7.5, spacing=1.05)
    sup = [o["name"] + f"（n={o['n']}）" for o in sm["organizations"] if o["suppressed"]]
    right = [
        [{"text": "抑制ルール　", "bold": True}, {"text": f"回答者 n < {a['min_n']} の組織・属性値は mean/pos/neg/diff を全て NaN にし、表示は「－」。有意差フラグは 0"}],
        [{"text": "今回の該当　", "bold": True}, {"text": "・".join(sup) or "なし"}],
        [{"text": "自由記述　", "bold": True}, {"text": f"件数 < {a['min_comments']} の組織はテーマ構成を非表示"}],
        [{"text": "部局別レポート　", "bold": True}, {"text": "抑制対象の部局は pptx を生成せず、一覧に「非表示」として残す"}],
        [{"text": "二次秘匿　", "bold": True}, {"text": "上位組織から逆算できる場合の追加秘匿は CAQNAL と協議（自動化していない）"}],
        [{"text": "全庁差　", "bold": True}, {"text": "scope の平均 − 全庁平均。検定は scope vs 全庁のうち scope 以外（Welch t）"}],
    ]
    add_text(slide, th, x + 5.2, y, 3.9, 0.25, "匿名性・全庁差", size=9, bold=True, color=th.hexv("brand_dark"))
    bullets(slide, th, x + 5.2, y + 0.27, 3.9, h - 0.3, right, size=7.5, spacing=1.15)


@slide_type("m_yoy")
def m_yoy(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    sm = ctx.summary
    _frame(slide, ctx, spec, "経年比較の対応付け（仕様書 §2-3）",
           "設問番号は年度でずれるため、正規化した設問文で前年度設問と対応付ける。文言が変わった設問は「参考比較」、新設は「比較不可」。",
           footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    if not ctx.has_prev:
        add_text(slide, th, x, y, w, 0.5, "前年度データが無いため、本年度は経年比較を行っていない。", size=10)
        return
    ym = ctx.cfg.prev.yoy_map or {}
    entries = ym["map"] if isinstance(ym, dict) else ym
    rows = [["区分", "判定基準", "扱い", "件数"]]
    cnt = {s: sum(1 for e in entries if e.get("status") == s and ctx.cfg.q(e["current"]).get("role") in LIKERT_ROLES)
           for s in ("比較可", "参考比較", "比較不可")}
    rows += [["比較可", "設問文が実質同一（句読点・表記揺れは無視）", "前年差を通常表示", cnt["比較可"]],
             ["参考比較", "文言軽微変更・カテゴリ移動・構成設問の一部変更", "前年差に ※ を付けて表示", cnt["参考比較"]],
             ["比較不可", "新設設問／対応なし", "前年差を出さない（－）", cnt["比較不可"]]]
    add_table(slide, th, x, y, w, rows, col_widths=[1.0, 4.0, 3.0, 0.8], row_h=0.3, size=7.8, aligns=["left", "left", "left", "center"])
    ex = [e for e in entries if e.get("status") == "参考比較" or (e.get("note") and "カテゴリ" in e.get("note", ""))][:4]
    items = [[{"text": f"{e['current']} ← {e.get('previous') or '－'}（{e['status']}）", "bold": True},
              {"text": "　" + (e.get("note") or "")}] for e in ex]
    add_text(slide, th, x, y + 1.4, w, 0.25, "対応付けの例（yoy_map.yaml）", size=9, bold=True, color=th.hexv("brand_dark"))
    bullets(slide, th, x, y + 1.67, w, 1.1, items or ["該当なし"], size=7.5, marker="", spacing=1.05)
    right = ["複合単位（カテゴリ・軸・指標）は構成設問の対応状況で判定: 全て比較可→比較可、一部参考／新設が半数未満→参考比較、対応が半数未満→比較不可",
             "前年度の組織・属性は同じ設問文の属性設問で対応付け、ラベル（文言）で突合する（選択肢コードの変更に影響されない）",
             f"前年度 n<{ctx.cfg.analysis['anonymity']['min_n']} の scope は前年値を非表示",
             f"今回: 前年度 {sm.get('respondents_prev'):,} 件、比較可 {sm['yoy']['comparable']} 問 ／ 参考比較 {sm['yoy']['reference']} 問"]
    add_text(slide, th, x, y + 2.85, w, 0.25, "ルール", size=9, bold=True, color=th.hexv("brand_dark"))
    bullets(slide, th, x, y + 3.1, w, h - 3.1, right, size=7.5, spacing=1.05)


@slide_type("m_significance")
def m_significance(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    a = ctx.cfg.analysis["significance"]
    _frame(slide, ctx, spec, "統計的有意差の扱い（仕様書 §4-3）",
           f"差の大きさ・n と併記する補助情報として算出する。両側 α={a['alpha']}。p 値だけで課題判定はしない。", footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    rows = [["比較", "検定", "対象データ", "レポートでの表示"],
            ["全庁差（部局・属性 vs 全庁）", "Welch の t 検定（等分散を仮定しない）", "scope の回答者スコア vs scope 以外の回答者スコア", "表のセル背景: 青＝有意に高い、桃＝有意に低い"],
            ["前年差", "Welch の t 検定", "当年回答者 vs 前年回答者（独立標本として扱う）", "同上。※は参考比較"],
            ["比率差（肯定率）", "二比率の z 検定（実装済み、表示は平均差を優先）", "肯定数 / 有効数", "－"],
            ["相関", "Pearson 相関の p 値", "回答者スコアのペア", "Key Driver 表に併記可"]]
    add_table(slide, th, x, y, w, rows, col_widths=[2.0, 2.4, 2.9, 1.8], row_h=0.36, size=7.5, aligns=["left"] * 4)
    notes = ["「有意差なし」＝「差がない」ではない。n が小さい部局は検出力が不足する（n=10 程度では 0.3 以上の差でも有意にならないことが多い）",
             "多重比較の補正は行っていない。部局 × カテゴリのように多数の比較を同時に見るときは、有意フラグの数が偶然多く出ることを前提に読む",
             "分散 0・n<2 の場合は検定不能として有意差なし扱い",
             "効果量の目安として全庁差 ±0.15、前年差 ±0.10（5段階平均）を判定閾値に使う（次ページ）"]
    add_text(slide, th, x, y + 2.05, w, 0.25, "解釈上の注意", size=9, bold=True, color=th.hexv("brand_dark"))
    bullets(slide, th, x, y + 2.32, w, h - 2.35, notes, size=7.8, spacing=1.15)


@slide_type("m_segmentation")
def m_segmentation(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    seg = ctx.cfg.raw["segmentation"]
    sm = ctx.summary["segmentation"]
    lab = seg["labels"]
    _frame(slide, ctx, spec, "職員タイプ 4類型（仕様書 §4-5）",
           f"回答者ごとの {ctx.cfg.indicator_name(seg['x']) if seg['x'] in ctx.cfg.indicators else seg['x']} と {seg['y']} のスコアを、当年度の全庁{ '平均' if sm['method'] == 'mean' else sm['method']}で高低に分けて 4 類型に分類する。",
           footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    rows = [["設定", "値"],
            ["x 軸（意欲）", f"{seg['x']} = " + " ".join(ctx.cfg.indicator_questions(seg["x"])) if seg["x"] in ctx.cfg.indicators else seg["x"]],
            ["y 軸（環境・支援）", f"{seg['y']}（{len(ctx.cfg.axis_questions(seg['y'])) if seg['y'] in ctx.cfg.axes() else '－'} 問の回答者平均）"],
            ["しきい値の決め方", f"{ctx.cfg.analysis['segmentation']['threshold']}（当年度・全庁）"],
            ["しきい値の実値", f"x = {fmt(sm['threshold_x'])} ／ y = {fmt(sm['threshold_y'])}"],
            ["前年度の分類", "当年度のしきい値で固定して分類（構成比の推移を比較可能にする）"],
            ["分類", f"x≥閾値 & y≥閾値 → {lab['high_high']}／x≥ & y< → {lab['high_low']}／x< & y≥ → {lab['low_high']}／x< & y< → {lab['low_low']}"]]
    add_table(slide, th, x, y, 5.6, rows, col_widths=[1.5, 4.1], row_h=0.34, size=7.5, aligns=["left", "left"])
    segs = ctx.res["segments"]
    d = segs[(segs.scope_type == "全庁")].set_index("segment").reindex(QUADRANTS)
    rows2 = [["類型", "当年", "前年", "差(pt)"]] + [[lab[k], fmt_pct(d.loc[k, "share"]), fmt_pct(d.loc[k, "prev_share"]), fmt(d.loc[k, "diff"], 1, signed=True)] for k in QUADRANTS]
    add_text(slide, th, x + 5.8, y, 3.3, 0.25, "全庁の構成比（結果）", size=9, bold=True, color=th.hexv("brand_dark"))
    add_table(slide, th, x + 5.8, y + 0.27, 3.3, rows2, col_widths=[1.4, 0.65, 0.65, 0.6], row_h=0.28, size=7.5)
    bullets(slide, th, x + 5.8, y + 1.9, 3.3, h - 1.9,
            ["部局・属性ごとの構成比を算出し、要支援型が全庁比 +10pt 以上の組織を「特徴的な組織」とする",
             "類型別にドライバー設問の平均を出し、活躍実感型と要支援型の差が大きい設問を「タイプを分ける要因候補」とする",
             "名称は自治体向けの中立的な表現（コーン・フェリーの社員活性化レベル分析を参考に再設計）"], size=7.5, spacing=1.1)


@slide_type("m_key_driver")
def m_key_driver(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    kd = ctx.cfg.analysis["key_driver"]
    j = ctx.cfg.analysis["judgement"]
    meta = next((m for m in ctx.summary.get("key_driver_meta", []) if m.get("scope") == "全庁"), None)
    _frame(slide, ctx, spec, "Key Driver 分析（仕様書 §4-4）",
           f"目的変数 {ctx.target_name} と各ドライバー設問の関連を、相関と標準化重回帰で測り、現状スコアと組み合わせて優先度ゾーンに分ける。因果ではなく「関連」。",
           footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    steps = [("① 相関", [f"Pearson r（ペア n ≥ {kd['min_n']}）", "回答者スコア同士", "p 値も算出"]),
             ("② 多重共線性", ["VIF を算出", "10 超は注意として表に併記", "除外はしない（判断材料）"]),
             ("③ 重回帰", ["全変数を z 化して OLS", f"n ≥ 説明変数 × {kd['ols_min_ratio']} のときのみ", "標準化係数 β・R²"]),
             ("④ 影響度", ["r の順位と β の順位の百分位を平均", f"上位 {j['high_impact_top_pct']}% を高影響", "部局別は相関のみ"]),
             ("⑤ ゾーン", [f"現状スコア: ドライバー平均の{ '中央値' if kd['score_split'] == 'median' else '平均' }で高低", "A 高影響×低 / B 高影響×高", "C 低影響×低 / D 低影響×高"])]
    _boxes_flow(slide, th, x, y, w, 2.0, steps, ["brand_dark", "brand", "positive", "brand_dark", "brand"])
    rows = [["項目", "今回の値"],
            ["目的変数", f"{ctx.target_name}（回答者の {'+'.join(ctx.cfg.indicator_questions(ctx.target))} 平均）"],
            ["説明変数", f"ドライバー設問 {meta['k'] if meta else '－'} 問（除外: {'・'.join(ctx.cfg.raw['key_driver'].get('exclude', [])) or 'なし'}）"],
            ["重回帰", f"n={meta['n']:,}、R²={meta['r2']:.3f}、調整済み R²={meta['adj_r2']:.3f}" if meta else "n 不足のため未実施"],
            ["部局別", f"n ≥ {kd['dept_min_n']} の部局で相関のみ算出。未満は全庁の結果を参照表示"]]
    add_table(slide, th, x, y + 2.15, 5.6, rows, col_widths=[1.2, 4.4], row_h=0.3, size=7.5, aligns=["left", "left"])
    bullets(slide, th, x + 5.8, y + 2.15, 3.3, h - 2.2,
            ["表現ルール: 「原因」「上がる」と断定せず、「関連が強い」「改善優先度が高い要因候補」と書く",
             "β は他要因を統制した独自の寄与。r が高くても β が小さい設問は他設問と情報が重なっている",
             "R² は目的変数の分散のうち説明変数で説明できる割合"], size=7.5, spacing=1.1)


@slide_type("m_comments")
def m_comments(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    ct = ctx.cfg.comment_themes
    _frame(slide, ctx, spec, "自由記述の分析（仕様書 §4-8）",
           "個人特定情報をマスキングした上で、キーワード辞書でテーマ分類し、定量結果と接続する。原文はレポートに載せない。", footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    rows = [["工程", "処理"],
            ["前処理", "空欄・記号のみ・重複を除外。10 字未満は参考扱い（short フラグ）"],
            ["マスキング", "／".join(p["name"] for p in ct["masking"]["patterns"]) + " を正規表現で置換。氏名は辞書化できないため人手で最終確認"],
            ["テーマ分類", f"{len(ct['themes'])} テーマのキーワード辞書（config/comment_themes.yaml）。複数該当可、該当なしは「その他」"],
            ["トーン", "肯定語・否定語の出現数比較による参考値（断定的な感情推定はしない）"],
            ["集計", f"全庁・{ctx.org_level}別にテーマ件数と構成比。件数 < {ctx.cfg.analysis['anonymity']['min_comments']} は非表示"],
            ["定量接続", "カテゴリ→テーマ対応表（action_library.yaml）で低スコアカテゴリと同テーマの件数を照合し、判定の「自由記述」シグナルにする"]]
    add_table(slide, th, x, y, 5.6, rows, col_widths=[1.1, 4.5], row_h=0.36, size=7.5, aligns=["left", "left"])
    items = [[{"text": f"{t}: ", "bold": True}, {"text": "・".join(kws[:6]) + ("…" if len(kws) > 6 else "")}] for t, kws in ct["themes"].items()]
    add_text(slide, th, x + 5.8, y, 3.3, 0.25, "テーマ辞書（抜粋）", size=9, bold=True, color=th.hexv("brand_dark"))
    bullets(slide, th, x + 5.8, y + 0.27, 3.3, h - 0.3, items, size=6.8, marker="", spacing=1.05)


@slide_type("m_judgement")
def m_judgement(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    j = ctx.cfg.analysis["judgement"]
    po = ctx.cfg.analysis["priority_org"]
    _frame(slide, ctx, spec, "自動判定：強み・課題・重点部局（仕様書 §4-9, §9, §9-1）",
           "閾値は analysis_defaults.yaml の標準案（client.yaml で上書き可）。AI は候補抽出まで、確定は CAQNAL レビュー。", footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    rows = [["判定", "標準案（今回の値）"],
            ["高評価", f"全庁上位 {j['high']['top_pct']}% または 肯定率 {j['high']['positive_pct_at_least']}% 以上"],
            ["低評価", f"全庁下位 {j['low']['bottom_pct']}% または 否定率 {j['low']['negative_pct_at_least']}% 以上"],
            ["改善 / 悪化", f"前年差 {j['improved_at_least']:+.2f} 以上 / {j['worsened_at_most']:+.2f} 以下"],
            ["組織偏在", f"いずれかの{ctx.org_level}で全庁差 −{j['org_gap_abs_at_least']} 以下"],
            ["高影響", f"Key Driver 影響度 上位 {j['high_impact_top_pct']}%"],
            ["自由記述", f"関連テーマの件数 {j['comment_link_min_count']} 以上"],
            ["ばらつき大", f"SD 上位 {j['high_sd_top_pct']}%"],
            ["重点課題候補", f"6観点（{'・'.join(SIGNAL_LABELS[s] for s in SIGNALS)}）のうち {j['priority_issue_min_signals']} 以上"],
            ["強み", "高評価 かつ 悪化なし"]]
    add_table(slide, th, x, y, 4.6, rows, col_widths=[1.2, 3.4], row_h=0.3, size=7.3, aligns=["left", "left"])
    pts = po["points"]
    lab = {"overall_bottom_pct": "総合が全庁下位20%", "worsened": "前年差が悪化", "org_gap_negative": f"全庁差 ≤ −{j['org_gap_abs_at_least']}",
           "driver_low": "KD 上位項目が低評価（全庁差 ≤ −0.15）", "comment_theme": "自由記述で重点課題テーマが多い", "support_type_gap": f"要支援型が全庁比 +{po['support_type_gap_pt']}pt"}
    rows2 = [["重点部局の加点条件", "点"]] + [[lab[k], f"+{v}"] for k, v in pts.items()]
    rows2.append([f"≥{po['bands']['priority']} 重点フォロー候補 / {po['bands']['watch']}〜{po['bands']['priority'] - 1} 要注視 / それ以下 通常", ""])
    rows2.append([f"好事例候補: 総合が上位 {po['good_practice']['top_pct']}% かつ 前年差 ≥ {po['good_practice']['improved_at_least']:+.2f}", ""])
    add_table(slide, th, x + 4.8, y, 4.3, rows2, col_widths=[3.7, 0.6], row_h=0.3, size=7.3, aligns=["left", "center"])
    jd = ctx.res["judgement"]
    pr = ctx.res["priority_orgs"]
    bullets(slide, th, x + 4.8, y + 2.85, 4.3, h - 2.9,
            [f"今回: 重点課題候補 {int(jd['priority_issue'].sum())} 問、強み {int(jd['strength'].sum())} 問",
             f"重点フォロー候補 {int((pr['band'] == '重点フォロー候補').sum())} 部局、要注視 {int((pr['band'] == '要注視').sum())} 部局",
             "当年スコア×前年差の 4 象限（維持／改善途上／要注視／最優先）は設問平均の中央値と 0 で分割"], size=7.5, spacing=1.1)


@slide_type("m_insights")
def m_insights(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    _frame(slide, ctx, spec, "示唆文の生成（仕様書 §7）",
           "数値計算・抽出・ランキングは決定的処理で確定し、文章はその結果から規則的に組み立てる。LLM を使う場合も insights.json を入力にする。", footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    rows = [["insights.json のフィールド", "内容", "元データ"],
            ["scope / n", "全庁 or 部局名、回答者数", "scores.csv"],
            ["overall_score / overall_pos / yoy_diff / org_diff", "総合指標の平均・肯定率・前年差・全庁差（有意フラグ付き）", "scores.csv"],
            ["top_strengths / top_issues", "設問ID・文・平均・肯定/否定率・全庁差・前年差 各3件", "judgement.csv / rankings.csv"],
            ["key_drivers", "設問ID・r・β・現状スコア・ゾーン 上位5件", "drivers.csv"],
            ["segment_mix", "4類型の構成比（全庁比較用に全庁分も）", "segments.csv"],
            ["comment_themes", "テーマ・件数・構成比 上位5件", "comment_themes.csv"],
            ["evidence_flags / priority_band", "重点課題候補の設問ID／部局の重点区分", "judgement.csv / priority_orgs.csv"],
            ["sentences", "fact / feature / cause / hint / action の5文", "上記から規則生成"]]
    add_table(slide, th, x, y, w, rows, col_widths=[2.6, 4.4, 2.1], row_h=0.27, size=7.2, aligns=["left"] * 3)
    ins = (ctx.res.insights.get("全庁") or {}).get("sentences", {})
    ex = [[{"text": "① 事実: ", "bold": True}, {"text": ins.get("fact", "")}],
          [{"text": "② 特徴: ", "bold": True}, {"text": ins.get("feature", "")}],
          [{"text": "③ 要因候補: ", "bold": True}, {"text": ins.get("cause", "")}],
          [{"text": "④ 示唆 / ⑤ 方向性: ", "bold": True}, {"text": (ins.get("hint", "") + ins.get("action", ""))}]]
    add_text(slide, th, x, y + 2.55, w, 0.25, "生成例（全庁）と表現ルール", size=9, bold=True, color=th.hexv("brand_dark"))
    bullets(slide, th, x, y + 2.8, 5.8, h - 2.85, ex, size=6.8, marker="", spacing=1.02)
    bullets(slide, th, x + 6.0, y + 2.8, 3.1, h - 2.85,
            ["根拠数値を必ず併記（比較対象を明記）", "因果断定・過度な一般化をしない", "「職員」「組織運営」、施策は「検討が考えられる」", "少数コメントから全体を語らない"],
            size=7.2, spacing=1.05)


@slide_type("m_outputs")
def m_outputs(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    c = ctx.cfg.client
    n_org = sum(1 for o in ctx.summary["organizations"] if not o["suppressed"])
    _frame(slide, ctx, spec, "成果物と再現性（仕様書 §13）",
           "全成果物は同じ中間データから生成する。同一入力＋同一設定で同一バイト列になることを verify コマンドで確認している。", footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    rows = [["成果物", "ファイル", "内容"],
            ["全庁分析レポート", f"{c['name']}_{ctx.cfg.fiscal_year}_全庁分析レポート.pptx / .pdf", "Executive Summary → 全庁結果 → 類型 → KD → 部局 → 属性 → 自由記述 → 総括 → 施策 → 付録"],
            ["部局別レポート", f"部局別/{c['name']}_{ctx.cfg.fiscal_year}_<部局>.pptx（{n_org} 本）", "サマリー / カテゴリ・設問 / 経年 / 4類型 / KD / アクション仮説"],
            ["Executive One Pager", f"{c['name']}_{ctx.cfg.fiscal_year}_ExecutiveOnePager.pptx", "首長・幹部向け 1 枚"],
            ["詳細集計", f"{c['name']}_{ctx.cfg.fiscal_year}_詳細集計.xlsx", "全 scope × unit のスコア、部局×設問、属性、KD、判定、マスク済み自由記述"],
            ["分析手法解説", f"{c['name']}_{ctx.cfg.fiscal_year}_分析手法解説_社内向け.pptx", "本資料"],
            ["分析データ一式", "output/intermediate/*.csv, insights.json, summary.json", "中間データ（監査・再計算用）"],
            ["処理ログ・設定値", "output/intermediate/settings_snapshot.yaml", "解析時の全設定値とバージョン"]]
    add_table(slide, th, x, y, w, rows, col_widths=[1.5, 3.6, 4.0], row_h=0.3, size=7.3, aligns=["left"] * 3)
    bullets(slide, th, x, y + 2.55, w, h - 2.6,
            ["PPTX: zip のタイムスタンプと docProps を固定値に書き換え、同じ内容なら同じバイト列になる（normalize_pptx）",
             "PNG: matplotlib のバージョン情報を除去。フォントはリポジトリ内の Noto Sans JP を使用",
             "Excel: 作成日時を固定。PDF は LibreOffice が日時を埋め込むため比較対象外",
             f"バージョン: caqnal-report {ctx.summary['version']}。verify は analyze+render を 2 回実行し全ファイルの SHA-256 を比較"], size=7.5, spacing=1.1)


@slide_type("m_config_files")
def m_config_files(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    _frame(slide, ctx, spec, "設定ファイルの役割（どこを変えると何が変わるか）",
           "コードを変えずに YAML だけで自治体差・閾値・スライド構成を吸収する。委託先が独自に変更してはいけない項目（§12-1）は client.yaml 側。", footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    rows = [["ファイル", "内容", "変更する場面"],
            [f"local-governments/{ctx.cfg.name}/client.yaml", "自治体名・年度・データパス・尺度・属性・組織・指標・4類型軸・KD目的変数・匿名性・閾値上書き", "新自治体／新年度／CAQNAL が確定した設定値の反映"],
            ["questions.yaml / questions_R7.yaml", "設問マスタ（CSV ヘッダー verbatim、軸・カテゴリ・role・逆転・条件）", "設問変更、逆転・条件付きの確定"],
            ["yoy_map.yaml", "前年度設問との対応（比較可／参考比較／比較不可）", "経年比較の可否判断"],
            ["config/analysis_defaults.yaml", "§9 の標準閾値、§9-1 加点、匿名性、KD・4類型の方法", "標準案そのものを見直すとき（全自治体に影響）"],
            ["config/decks/*.yaml", "スライドの並びとパラメータ（type / unit_type / attributes / title / lead）", "ページ構成の変更、自治体固有ページの追加"],
            ["config/theme.yaml", "色・フォント（Noto / Meiryo）・座標・レイアウト名", "デザイン変更、テンプレート更新"],
            ["config/action_library.yaml", "課題テーマ別の現場確認・施策候補、カテゴリ→テーマ対応、WS の問い", "施策ライブラリの更新"],
            ["config/comment_themes.yaml", "自由記述のテーマ辞書・トーン語・マスキング正規表現", "テーマの追加、誤分類の修正"]]
    add_table(slide, th, x, y, w, rows, col_widths=[2.6, 4.2, 2.3], row_h=0.36, size=7.2, aligns=["left"] * 3)


@slide_type("m_qa")
def m_qa(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    _frame(slide, ctx, spec, "品質チェック（仕様書 §11）",
           "数値再現性・匿名性・分析妥当性・文章根拠・体裁の 5 観点。自動化している確認と、人が見る確認を分けている。", footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    rows = [["観点", "自動（コード・テスト）", "人手（納品前）"],
            ["数値再現性", "validate でヘッダー1:1・尺度・組織紐付けを検査／pytest で原データからの手計算と一致を確認／verify でバイト一致", "ランダム抽出した組織・設問のサンプル再計算（検証表）"],
            ["匿名性", f"n<{ctx.cfg.analysis['anonymity']['min_n']} を全 scope で抑制、部局別 pptx を生成しない、自由記述はマスク済みのみ出力", "二次秘匿の要否、コメントの個人特定リスク"],
            ["分析妥当性", "KD の目的・説明・除外変数を設定から導出、経年は yoy_map の区分のみ、同率順位は設問ID順", "逆転項目・条件付き設問の妥当性、モデル統計の確認"],
            ["文章根拠", "全文が数値付きテンプレート、因果表現を含まない語彙", "固有名詞・過大解釈・自治体固有事情との整合"],
            ["体裁", "座標・フォント・色を theme.yaml で固定、PDF 変換で文字切れを目視", "全組織名の判読、凡例・n 数、ページ間の位置ずれ"]]
    add_table(slide, th, x, y, w, rows, col_widths=[1.2, 4.6, 3.3], row_h=0.5, size=7.2, aligns=["left"] * 3)
    bullets(slide, th, x, y + 3.15, w, h - 3.2,
            ["本格制作前に、主要分析結果・強み課題候補・KD・重点組織候補・示唆案を一覧化して CAQNAL 確認を受ける（§12）",
             "委託先が独自に変更してはいけない項目: 結果指標／ドライバー設問の追加削除、匿名性閾値、経年比較可否、KD 目的変数、重点課題・重点部局の閾値"], size=7.5, spacing=1.1)


@slide_type("m_limitations")
def m_limitations(slide, ctx: Ctx, spec: dict):
    th = ctx.theme
    _frame(slide, ctx, spec, "制約・注意事項と今後の拡張", "現時点の実装で割り切っている点と、追加で対応できる点。", footnote=STD_FOOT)
    x, y, w, h = ctx.content()
    lim = ["自治体固有の前提（結果指標の構成、逆転項目、条件付き設問）は config にコメントで明記し CAQNAL 確認事項としている",
           f"組織階層は{ctx.org_level}のみ（課の回答項目が無い）。課長向けアクションシート（仕様書 D）は課データが来れば同じ仕組みで追加できる",
           "対象者数が未設定の場合、回答率は「－」表示",
           "自由記述のテーマ分類はキーワード辞書。文脈依存の表現は「その他」に落ちる。件数の少ない組織は非表示",
           "有意差は多重比較を補正していない。n が小さい部局は検出力不足",
           "前年度との比較は独立標本として扱う（同一個人の追跡はしていない）",
           "テンプレートのフォント（Meiryo UI）はこの環境に無いため Noto Sans JP で描画。Windows で Meiryo に切り替える場合は theme.yaml の preset を変更し PDF を再確認"]
    ext = ["LLM による示唆文生成: insights.json をそのまま入力にし、生成文を sentences に差し替える段を追加",
           "自由記述の LLM 分類: 辞書分類をフォールバックに残し、テーマ・トーン・要望タイプを付与",
           "ベンチマーク（他自治体平均）: scores.csv に bench_* 列を追加し、全庁差と同じ表現で比較",
           "課レベル・複数階層の組織: organization.levels に階層を追加し、部局→課のロールアップを実装",
           "経年 3 年以上: data.previous に複数年度を並べ、折線・推移表を追加"]
    add_text(slide, th, x, y, 4.5, 0.25, "制約・注意事項", size=9, bold=True, color=th.hexv("brand_dark"))
    bullets(slide, th, x, y + 0.27, 4.5, h - 0.3, lim, size=7.3, spacing=1.08)
    add_text(slide, th, x + 4.7, y, 4.4, 0.25, "今後の拡張候補", size=9, bold=True, color=th.hexv("brand_dark"))
    bullets(slide, th, x + 4.7, y + 0.27, 4.4, h - 0.3, ext, size=7.3, spacing=1.08)
