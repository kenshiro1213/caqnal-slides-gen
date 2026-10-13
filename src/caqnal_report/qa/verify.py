"""verify 工程: 候補 PPTX の品質検証と納品版の確定（仕様 v2 §16、プロンプト集 v2.1 の 8・10）。

自動で検査するもの（結果は ok / ng / open）
  1 データ監査      private 集計 ⇄ 公開データの一致、グラフの値・表の数値 ⇄ 公開データ
  2 分析監査        保留・推定不能のモデル（担当者の判断なし）の係数を本文に載せていない
  5 匿名性・範囲監査 非表示の所属の行に数値が無い、読者の範囲外の所属名が無い、自由記述の原文・他自治体名が無い、
                    非表示頁が無い（本文・表・ノート・グラフの項目名・埋込 Excel を対象）
  6 内容監査        サマリー 2 枚・用語集・所属長向け記入シート 1 枚、「未記入」の枠、未確定事項
  7 編集性・表示監査 グラフが画像でなくネイティブ、グラフのキャッシュ ⇄ 埋込 Excel、（--images）PDF の頁数と PNG 書出し
  8 最終版監査      PPTX を再読込できる、同じ公開データから作り直すとバイト一致する

人が確認するもの（manual。qa/signoff.json に確認者・日付・確認項目を記録する）
  3 解釈監査 / 4 方針・出典監査 / 全頁の個別目視 / PowerPoint アプリでの編集性
  ※ 実際に行っていない確認を ok にしない。PNG を書き出しただけでは目視済みにしない

release_ready の条件: ng と open が 0、signoff が全 manual 項目を含み、signoff 時の成果物の指紋が今の成果物と一致。
release_ready になったら output/release/<run_id>/ に成果物と QA 記録を複製する（既存の版は上書きしない）。
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from ..analysis.describe import objectives
from ..analysis.publish import VALUE_COLS, read_public
from ..ingest.normalize import read_normalized
from ..run import Run, sha_file
from .pptx_text import DeckContent, read_deck

AREAS = {1: "データ監査", 2: "分析監査", 3: "解釈監査", 4: "方針・出典監査", 5: "匿名性・範囲監査", 6: "内容監査",
         7: "編集性・表示監査", 8: "最終版監査"}
MANUAL = {"interpretation": (3, "観測事実・仮説・実施案・実施条件が区別され、相関を原因・βを効果として書いていない"),
          "sources": (4, "方針の名称・改定日・正式用語・印刷頁、設問全文、前回値の出典"),
          "pages": (7, "全頁を十分な解像度で 1 頁ずつ目視（文字切れ・重なり・表の溢れ・軸と凡例）"),
          "editable": (7, "PowerPoint アプリで開き、文字・表・グラフが編集できる")}
PLACEHOLDER = "〔未記入〕"
HIDDEN = "非表示"
CELL_NUM = re.compile(r"^[+\-]?[\d,]+(?:\.\d+)?%?$")


@dataclass
class Check:
    area: int
    id: str
    status: str            # ok / ng / open / manual
    detail: str
    items: list[str] = field(default_factory=list)


@dataclass
class QAReport:
    run_id: str
    checks: list[Check]
    decks: dict[str, dict]
    fingerprint: str
    signoff: dict | None
    release_ready: bool

    def count(self, status: str) -> int:
        return sum(c.status == status for c in self.checks)

    def to_json(self) -> str:
        return json.dumps({"run_id": self.run_id, "fingerprint": self.fingerprint, "release_ready": self.release_ready,
                           "signoff": self.signoff, "decks": self.decks, "checks": [asdict(c) for c in self.checks]},
                          ensure_ascii=False, indent=2, default=str) + "\n"

    def to_md(self) -> str:
        mark = {"ok": "OK", "ng": "**NG**", "open": "未完", "manual": "人の確認"}
        lines = [f"# QA 記録 {self.run_id}", "",
                 f"- 判定: {'release_ready' if self.release_ready else 'draft_qa_pending（納品不可）'}",
                 f"- NG {self.count('ng')} / 未完 {self.count('open')} / 人の確認 {self.count('manual')} / "
                 f"OK {self.count('ok')}",
                 f"- 成果物の指紋: `{self.fingerprint[:16]}…`",
                 "- 確認者: " + (f"{self.signoff['reviewer']}（{self.signoff['date']}）" if self.signoff else "未記録"),
                 "", "## 成果物", "", "| ファイル | 読者 | 頁 | グラフ | 表 |", "|---|---|---|---|---|"]
        for name, d in self.decks.items():
            lines.append(f"| {name} | {d['reader']} | {d['slides']} | {d['charts']} | {d['tables']} |")
        for a, title in AREAS.items():
            cs = [c for c in self.checks if c.area == a]
            if not cs:
                continue
            lines += ["", f"## {a}. {title}", "", "| 結果 | 項目 | 内容 |", "|---|---|---|"]
            for c in cs:
                lines.append(f"| {mark[c.status]} | {c.id} | {c.detail} |")
                for it in c.items[:20]:
                    lines.append(f"|  |  | - {it} |")
                if len(c.items) > 20:
                    lines.append(f"|  |  | …ほか {len(c.items) - 20} 件 |")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 準備
def reader_decks(run: Run) -> dict[str, list[tuple[str, Path]]]:
    """読者 ID → [(デッキ名, PPTX パス)]。render と同じ規則でファイル名を決める。"""
    from ..render.v2.deck import DEFAULT_DECKS, deck_spec

    cfg = run.cfg
    out = {}
    for reader in cfg.readers or [{"id": "全庁", "kind": "all_staff"}]:
        lst = []
        for name in reader.get("decks") or DEFAULT_DECKS[reader["kind"]]:
            fname = deck_spec(cfg, name)["filename"].format(client=cfg.client["name"], fy=cfg.fiscal_year,
                                                           reader=reader["id"])
            lst.append((name, run.step_dir("render") / fname))
        out[reader["id"]] = lst
    return out


def _readers(run: Run) -> dict[str, dict]:
    return {r["id"]: r for r in (run.cfg.readers or [{"id": "全庁", "kind": "all_staff"}])}


def _decimals(x: float) -> int:
    s = repr(float(x))
    return min(len(s.split(".")[1]), 4) if "." in s and "e" not in s else 4


def _allowed_values(tables: dict[str, pd.DataFrame], compose: dict) -> dict[int, set[float]]:
    """公開データ（と compose の数値）から、図表に出てよい値を桁数ごとに作る。"""
    raw: list[float] = []
    for df in tables.values():
        if df is None or df.empty:
            continue
        for c in df.columns:
            v = pd.to_numeric(df[c], errors="coerce").dropna()
            if len(v) and len(v) == len(df[c].dropna()):          # 数値だけの列
                raw += [float(x) for x in v]
    rs = tables.get("response_status")
    if rs is not None and not rs.empty:          # 属性の構成比（区分の有効回答／全庁の有効回答）
        tot = rs.loc[rs.scope_type == "全庁", "responded_n"]
        if len(tot) and pd.notna(tot.iloc[0]) and tot.iloc[0]:
            raw += [float(x) / float(tot.iloc[0]) for x in pd.to_numeric(rs.responded_n, errors="coerce").dropna()]

    st = tables.get("stats")
    if st is not None and not st.empty:          # 全庁差・親組織との差
        base = st[st.scope_type == "全庁"].set_index(["unit_type", "unit_id"])
        org = tables.get("org")
        parent = dict(zip(org.unit_id, org.parent_id)) if org is not None and not org.empty else {}
        for r in st.itertuples():
            refs = [base]
            if r.scope_type == "組織" and isinstance(parent.get(r.scope_id), str):
                refs.append(st[(st.scope_type == "組織") & (st.scope_id == parent[r.scope_id])]
                            .set_index(["unit_type", "unit_id"]))
            for ref in refs:
                if (r.unit_type, r.unit_id) in ref.index:
                    b = ref.loc[(r.unit_type, r.unit_id)]
                    for c in ("score", "pos", "mean"):
                        if pd.notna(getattr(r, c)) and pd.notna(b[c]):
                            raw.append(float(getattr(r, c)) - float(b[c]))

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            raw.append(float(o))
    walk(compose)
    out: dict[int, set[float]] = {}
    for d in range(5):
        s = set()
        for x in raw:
            if math.isfinite(x):
                s.add(round(x, d))
                s.add(round(x * 100, d))
        out[d] = s
    return out


def _in_allowed(x: float, allowed: dict[int, set[float]]) -> bool:
    d = _decimals(x)
    return round(x, d) in allowed[d]


def _norm_name(s: str) -> str:
    return s.replace("　", "").strip()


# ---------------------------------------------------------------- 検査
def _check_public_vs_private(run: Run, readers: list[str]) -> list[Check]:
    agg = run.step_dir("aggregate")
    priv = {n: pd.read_csv(agg / f"{n}.csv", dtype={"scope_id": str, "unit_id": str}) for n in ("stats", "response_status")}
    keys = {"stats": ["scope_type", "scope_id", "unit_type", "unit_id"], "response_status": ["scope_type", "scope_id"]}
    vals = {"stats": VALUE_COLS, "response_status": ["target_n", "responded_n", "excluded_n", "rate"]}
    bad, leaked, n_cmp = [], [], 0
    for rid in readers:
        pub, _ = read_public(run.step_dir("publish"), rid)
        for name in ("stats", "response_status"):
            m = pub[name].merge(priv[name], on=keys[name], how="left", suffixes=("", "_p"), indicator=True)
            for c in vals[name]:
                if c not in pub[name].columns:
                    continue
                shown = m["suppressed"].isna()
                a, b = pd.to_numeric(m[c], errors="coerce"), pd.to_numeric(m[f"{c}_p"], errors="coerce")
                diff = shown & ~((a - b).abs().fillna(0) < 1e-9) & ~(a.isna() & b.isna())
                n_cmp += int(shown.sum())
                bad += [f"{rid}/{name} {tuple(r[k] for k in keys[name])} {c}" for _, r in m[diff].iterrows()]
                leak = ~shown & a.notna()
                leaked += [f"{rid}/{name} {tuple(r[k] for k in keys[name])} {c}" for _, r in m[leak].iterrows()]
            missing = m["_merge"] != "both"
            bad += [f"{rid}/{name} private に無い行 {tuple(r[k] for k in keys[name])}" for _, r in m[missing].iterrows()]
    return [Check(1, "公開データ⇄private 集計", "ng" if bad else "ok",
                  f"表示している値 {n_cmp:,} 件を照合" + (f"、不一致 {len(bad)} 件" if bad else "、すべて一致"), bad),
            Check(5, "非表示の値が公開データに無い", "ng" if leaked else "ok",
                  f"非表示の行に値が残っている {len(leaked)} 件" if leaked else "非表示の行は値が空", leaked)]


def _check_deck_numbers(deck: DeckContent, allowed: dict[int, set[float]], label: str) -> list[Check]:
    bad_chart, bad_cell, n_chart, n_cell = [], [], 0, 0
    for c in deck.charts:
        for v in c.values:
            n_chart += 1
            if not _in_allowed(v, allowed):
                bad_chart.append(f"p{c.slide} {c.title[:30]} 値 {v}")
    for s in deck.slides:
        for t in s.tables:
            for row in t[1:]:
                for cell in row[1:]:
                    cell = cell.strip()
                    if not CELL_NUM.match(cell):
                        continue
                    n_cell += 1
                    v = float(cell.replace(",", "").rstrip("%"))
                    if not (_in_allowed(v, allowed) or _in_allowed(-v, allowed)):
                        bad_cell.append(f"p{s.no} {s.title[:30]} 「{cell}」")
    return [Check(1, f"グラフの値⇄公開データ（{label}）", "ng" if bad_chart else "ok",
                  f"{n_chart:,} 値を照合" + (f"、公開データに無い値 {len(bad_chart)}" if bad_chart else "、すべて一致"),
                  bad_chart),
            Check(1, f"表の数値⇄公開データ（{label}）", "ng" if bad_cell else "ok",
                  f"点数・率・差・人数 {n_cell:,} 件を照合" + (f"、不一致 {len(bad_cell)}" if bad_cell else "、すべて一致"),
                  bad_cell)]


def _check_leaks(deck: DeckContent, label: str, hidden_names: set[str], out_of_scope: set[str], comments: list[str],
                 others: list[str]) -> list[Check]:
    out = []
    bad = []
    for s in deck.slides:
        for t in s.tables:
            for row in t:
                if row and _norm_name(row[0]) in hidden_names:
                    nums = [c for c in row[1:] if CELL_NUM.match(c.strip())]
                    if nums:
                        bad.append(f"p{s.no} {row[0].strip()}: {' / '.join(nums)}")
    out.append(Check(5, f"非表示の所属の行に数値が無い（{label}）", "ng" if bad else "ok",
                     f"非表示 {len(hidden_names)} 所属の行を点検" + (f"、数値あり {len(bad)}" if bad else ""), bad))
    bad = []
    for s in deck.slides:
        cells = {_norm_name(c) for t in s.tables for r in t for c in r} | {_norm_name(x) for x in s.texts}
        hit = sorted(cells & out_of_scope)
        bad += [f"p{s.no} {h}" for h in hit]
    out.append(Check(5, f"読者の範囲外の所属名が無い（{label}）", "ng" if bad else "ok",
                     f"範囲外 {len(out_of_scope)} 所属を点検" + (f"、出現 {len(bad)}" if bad else ""), bad))
    text = deck.all_text() + "\n" + deck.path.name
    bad = [c[:40] + "…" for c in comments if c in text]
    out.append(Check(5, f"自由記述の原文が無い（{label}）", "ng" if bad else "ok",
                     f"原文 {len(comments):,} 件（12 字以上）と本文・表・ノート・グラフ・埋込 Excel を照合", bad))
    bad = [o for o in others if o in text]
    out.append(Check(5, f"他自治体の名称が無い（{label}）", "ng" if bad else "ok",
                     f"{len(others)} 自治体名を照合", bad))
    hidden_slides = [f"p{s.no}" for s in deck.slides if s.hidden]
    out.append(Check(5, f"非表示頁が無い（{label}）", "ng" if hidden_slides else "ok",
                     "非表示に設定された頁" + (f" {len(hidden_slides)}" if hidden_slides else "なし"), hidden_slides))
    return out


def _check_models(run: Run, deck: DeckContent, label: str, rid: str) -> Check:
    tables, _ = read_public(run.step_dir("publish"), rid)
    fit = tables.get("model_fit", pd.DataFrame())
    bad = []
    if not fit.empty:
        held = fit[fit.candidate.isin(["保留", "推定不能"]) & fit.decision.isna() & (fit.model != "tree")]
        names = {o["id"]: (o.get("short") or o.get("name") or o["id"]) for o in objectives(run.cfg)}
        for r in held.itertuples():
            lbl = "カテゴリ" if r.model == "category" else "設問"
            title = f"重回帰｜{names.get(r.objective_id, r.objective_id)}と関連する{lbl}"
            bad += [f"p{s.no} {title}" for s in deck.slides if title in s.title and s.tables]
    return Check(2, f"保留・推定不能のモデルの係数を載せていない（{label}）", "ng" if bad else "ok",
                 "担当者の判断が無い保留・推定不能モデルの係数表" + ("あり" if bad else "なし"), bad)


def _check_charts(deck: DeckContent, label: str) -> list[Check]:
    pics = [f"p{s.no}（{s.n_pictures}）" for s in deck.slides if s.n_pictures]
    mismatch = []
    for c in deck.charts:
        a = sorted(round(v, 6) for v in c.values)
        b = sorted(round(v, 6) for v in c.workbook_values)
        if not set(a) <= set(b):
            mismatch.append(f"p{c.slide} {c.title[:30]}")
    return [Check(7, f"グラフ・表がネイティブ（{label}）", "ng" if pics else "ok",
                  f"グラフ {len(deck.charts)}・表 {sum(len(s.tables) for s in deck.slides)}。画像 "
                  + (f"{len(pics)} 頁にあり" if pics else "なし"), pics),
            Check(7, f"グラフのキャッシュ⇄埋込 Excel（{label}）", "ng" if mismatch else "ok",
                  f"{len(deck.charts)} グラフを照合" + (f"、不一致 {len(mismatch)}" if mismatch else "、すべて一致"),
                  mismatch)]


def _check_content(deck: DeckContent, label: str, kind: str, deck_name: str) -> list[Check]:
    out = []
    ph = [f"p{s.no} {s.title[:30]}" for s in deck.slides if any(PLACEHOLDER in t for t in s.texts)]
    out.append(Check(6, f"未記入の枠（{label}）", "open" if ph else "ok",
                     f"{len(ph)} 頁に「未記入」の枠（interpretation.yaml に記入）" if ph else "なし", ph))
    if "付録" in deck_name:
        return out
    titles = [s.title for s in deck.slides]
    n_sum = sum("サマリー" in t for t in titles)
    out.append(Check(6, f"サマリー 2 枚（{label}）", "ok" if n_sum == 2 else "ng", f"{n_sum} 枚"))
    out.append(Check(6, f"用語集（{label}）", "ok" if any("用語集" in t for t in titles) else "ng",
                     "あり" if any("用語集" in t for t in titles) else "なし"))
    if kind in ("dept_head", "section_head", "hr"):
        n = sum("記入シート" in t for t in titles)
        out.append(Check(6, f"記入シート 1 枚（{label}）", "ok" if n == 1 else "ng", f"{n} 枚"))
    return out


def _render_images(path: Path, out_dir: Path, n_slides: int) -> Check:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(path)],
                       check=True, capture_output=True, timeout=600)
        pdf = out_dir / (path.stem + ".pdf")
        subprocess.run(["pdftoppm", "-r", "110", "-png", str(pdf), str(out_dir / "p")], check=True,
                       capture_output=True, timeout=1200)
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        return Check(7, f"画像の書出し（{path.name}）", "open", f"LibreOffice / pdftoppm で書き出せない: {e}")
    n = len(list(out_dir.glob("p-*.png")))
    return Check(7, f"画像の書出し（{path.name}）", "ok" if n == n_slides else "ng",
                 f"PDF {n} 頁（スライド {n_slides} 枚）→ {out_dir.relative_to(out_dir.parents[2])}/。目視は signoff の pages")


def _check_reproducible(run: Run, decks: dict[str, list[tuple[str, Path]]]) -> Check:
    from ..render.v2.deck import render_reader

    bad = []
    with tempfile.TemporaryDirectory() as td:
        for rid, reader in _readers(run).items():
            render_reader(run.cfg, run.step_dir("publish"), run.step_dir("compose"), reader, Path(td))
            for _, p in decks[rid]:
                q = Path(td) / p.name
                if not q.exists() or sha_file(q) != sha_file(p):
                    bad.append(p.name)
    return Check(8, "同じ公開データから作り直すとバイト一致", "ng" if bad else "ok",
                 "一致しない: " + "、".join(bad) if bad else "すべて一致", bad)


def render_fingerprint(paths: list[Path]) -> str:
    import hashlib

    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: x.name):
        h.update(p.name.encode())
        h.update(sha_file(p).encode())
    return h.hexdigest()


# ---------------------------------------------------------------- 本体
def verify(run: Run, images: bool = False) -> QAReport:
    cfg = run.cfg
    decks = reader_decks(run)
    readers = _readers(run)
    checks: list[Check] = []
    checks += _check_public_vs_private(run, list(readers))

    norm = read_normalized(run.step_dir("normalize"))
    all_units = dict(zip(norm["org"].unit_id, norm["org"].name))
    comments = [t.strip() for t in norm["comments"]["text"].dropna() if len(t.strip()) >= 12]
    others = sorted({(o.get("client") or {}).get("name") for o in _other_clients(cfg)} - {None, cfg.client["name"]})
    others = [o for o in others if o not in cfg.client["name"]]

    info, paths = {}, []
    for rid, lst in decks.items():
        reader = readers[rid]
        tables, manifest = read_public(run.step_dir("publish"), rid)
        compose_path = run.step_dir("compose") / rid / "compose.json"
        compose = json.loads(compose_path.read_text(encoding="utf-8")) if compose_path.exists() else {}
        allowed = _allowed_values(tables, compose)
        hidden = {_norm_name(all_units[h.split(":", 1)[1]]) for h in manifest.get("hidden_scopes", [])
                  if h.startswith("組織:") and h.split(":", 1)[1] in all_units}
        scope = set(manifest.get("units") or [])
        out_of_scope = {_norm_name(n) for u, n in all_units.items() if u not in scope}
        for name, path in lst:
            if not path.exists():
                checks.append(Check(8, f"PPTX がある（{path.name}）", "ng", "render の出力が無い"))
                continue
            try:
                deck = read_deck(path)
            except Exception as e:  # noqa: BLE001
                checks.append(Check(8, f"PPTX を再読込できる（{path.name}）", "ng", str(e)))
                continue
            paths.append(path)
            label = path.stem
            info[path.name] = {"reader": rid, "deck": name, "slides": len(deck.slides), "charts": len(deck.charts),
                               "tables": sum(len(s.tables) for s in deck.slides), "sha256": sha_file(path)}
            checks.append(Check(8, f"PPTX を再読込できる（{label}）", "ok", f"{len(deck.slides)} 頁"))
            checks += _check_deck_numbers(deck, allowed, label)
            checks.append(_check_models(run, deck, label, rid))
            checks += _check_leaks(deck, label, hidden, out_of_scope, comments, others)
            checks += _check_charts(deck, label)
            checks += _check_content(deck, label, reader["kind"], name)
            if images:
                checks.append(_render_images(path, run.step_dir("verify") / "pages" / path.stem, len(deck.slides)))
    if paths:
        checks.append(_check_reproducible(run, decks))

    unresolved = [f"{s}: {u}" for s in ("validate", "normalize", "aggregate", "models", "publish", "compose")
                  for u in (run.step(s) or {}).get("unresolved", [])]
    checks.append(Check(6, "未確定事項", "open" if unresolved else "ok",
                        f"{len(unresolved)} 件（確定して設定に記録するまで納品版にしない）" if unresolved else "なし",
                        unresolved))

    fp = render_fingerprint(paths)
    signoff = read_signoff(run)
    for key, (area, desc) in MANUAL.items():
        done = bool(signoff and key in signoff.get("checked", []) and signoff.get("fingerprint") == fp)
        checks.append(Check(area, f"人の確認: {key}", "ok" if done else "manual",
                            desc + (f"（{signoff['reviewer']} {signoff['date']}）" if done else "（未記録）")))
    stale_signoff = signoff is not None and signoff.get("fingerprint") != fp
    if stale_signoff:
        checks.append(Check(8, "確認記録と成果物の指紋", "open", "signoff の後に成果物が変わった。確認をやり直す"))
    ready = not any(c.status in ("ng", "open", "manual") for c in checks)
    return QAReport(run.run_id, checks, info, fp, signoff, ready)


def _other_clients(cfg) -> list[dict]:
    from ..config import PROJECT_ROOT, load_yaml

    files = {p.resolve() for base in (cfg.root.parent, PROJECT_ROOT / "local-governments")
             for p in base.glob("*/client.yaml")}
    out = []
    for p in sorted(files):
        if p.parent == cfg.root.resolve():
            continue
        try:
            out.append(load_yaml(p) or {})
        except Exception:  # noqa: BLE001
            continue
    return out


# ---------------------------------------------------------------- 確認記録と納品版
def signoff_path(run: Run) -> Path:
    return run.dir / "signoff.json"


def read_signoff(run: Run) -> dict | None:
    p = signoff_path(run)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def write_signoff(run: Run, reviewer: str, checked: list[str], when: str | None = None, note: str = "") -> dict:
    unknown = [c for c in checked if c not in MANUAL]
    if unknown:
        raise ValueError(f"未知の確認項目 {unknown}（{list(MANUAL)}）")
    paths = [p for lst in reader_decks(run).values() for _, p in lst if p.exists()]
    rec = {"reviewer": reviewer, "date": when or date.today().isoformat(), "checked": sorted(set(checked)),
           "fingerprint": render_fingerprint(paths), "files": {p.name: sha_file(p) for p in sorted(paths)},
           "note": note}
    signoff_path(run).write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rec


def write_report(rep: QAReport, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    a, b = out_dir / "qa_report.json", out_dir / "qa_report.md"
    a.write_text(rep.to_json(), encoding="utf-8")
    b.write_text(rep.to_md(), encoding="utf-8")
    return [a, b]


def release(run: Run, rep: QAReport) -> Path:
    """納品版を output/release/<run_id>/ に複製する。既にあれば止める（上書きしない）。"""
    dest = run.cfg.out_dir / "release" / run.run_id
    if dest.exists():
        raise FileExistsError(f"{dest} は既にあります（納品版は上書きしない）")
    dest.mkdir(parents=True)
    for name in rep.decks:
        shutil.copy2(run.step_dir("render") / name, dest / name)
    for p in ("qa_report.md", "qa_report.json"):
        shutil.copy2(run.step_dir("verify") / p, dest / p)
    shutil.copy2(signoff_path(run), dest / "signoff.json")
    return dest
