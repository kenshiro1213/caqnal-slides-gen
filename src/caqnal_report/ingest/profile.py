"""受領ファイルのプロファイル md を生成する（`caqnal profile`）。

目的: 生データを毎回開かずに、列名・行列数・欠損・値分布を参照できるようにする。
- CSV: encoding 自動判定（utf-8-sig → cp932）、全列名 verbatim、値分布（低カーディナリティ列のみ）、
  自由記述らしい列は件数・文字数のみ（値は出力しない）
- xlsx: シート一覧と各シートの内容（設問マスタ想定。上限行数あり）
- ファイル名に `_PII` を含むものは内容を出力しない（行数のみ）
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

MAX_DISTINCT = 30
MAX_XLSX_ROWS = 150
MAX_XLSX_COLS = 12
CELL_MAX = 90


def detect_encoding(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(4096)
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    for enc in ("utf-8", "cp932"):
        try:
            head.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def _trim(v, n: int = CELL_MAX) -> str:
    s = "" if v is None else str(v).replace("\n", "⏎").replace("|", "／")
    return s if len(s) <= n else s[: n - 1] + "…"


def profile_csv(path: Path) -> str:
    lines = [f"# {path.name}", ""]
    if "_PII" in path.name:
        with open(path, "rb") as f:
            n = sum(1 for _ in f)
        lines += ["**個人情報ファイル。内容は出力しない。**", "", f"- 総行数（ヘッダー・空行含む）: {n:,}",
                  "- パイプラインでは読み込まない"]
        return "\n".join(lines) + "\n"
    enc = detect_encoding(path)
    df = pd.read_csv(path, encoding=enc, dtype=str, keep_default_na=False).replace({"": None})
    lines += [f"- encoding: `{enc}`", f"- 行数: {len(df):,}（ヘッダー除く）", f"- 列数: {len(df.columns)}", ""]
    lines += ["## 列一覧", "", "| idx | 列名（verbatim） | 非null | 種類数 |", "|---|---|---|---|"]
    for i, c in enumerate(df.columns):
        s = df[c]
        lines.append(f"| {i} | {_trim(c, 120)} | {int(s.notna().sum())} | {s.nunique(dropna=True)} |")
    lines += ["", "## 値分布", ""]
    for i, c in enumerate(df.columns):
        s = df[c].dropna()
        nun = s.nunique()
        lines.append(f"### [{i}] {_trim(c, 120)}")
        if len(s) == 0:
            lines += ["- 全て欠損", ""]
            continue
        avg_len = s.map(len).mean()
        if nun > MAX_DISTINCT and avg_len > 15:
            lines += [f"- 自由記述: {len(s)} 件 / 平均 {avg_len:.0f} 字 / 最大 {s.map(len).max()} 字（値は出力しない）", ""]
            continue
        vc = s.value_counts()
        if nun > MAX_DISTINCT:
            lines.append(f"- 種類数 {nun}（上位 {MAX_DISTINCT} のみ）")
            vc = vc.head(MAX_DISTINCT)
        for v, n in vc.items():
            lines.append(f"- `{_trim(v, 60)}`: {n}")
        lines.append("")
    return "\n".join(lines) + "\n"


def profile_xlsx(path: Path) -> str:
    import openpyxl

    lines = [f"# {path.name}", ""]
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    lines.append(f"- シート: {', '.join(wb.sheetnames)}")
    lines.append("")
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        lines += [f"## シート: {ws.title}", "", f"- 行数 {len(rows)} / 最大列数 {ws.max_column}", ""]
        # 空列を落とす
        used = [j for j in range(min(ws.max_column or 0, 40))
                if any(r[j] not in (None, "") for r in rows if j < len(r))]
        used = used[:MAX_XLSX_COLS]
        if not used:
            continue
        lines.append("| r | " + " | ".join(f"c{j}" for j in used) + " |")
        lines.append("|---|" + "---|" * len(used))
        shown = 0
        for ri, r in enumerate(rows, start=1):
            vals = [r[j] if j < len(r) else None for j in used]
            if all(v in (None, "") for v in vals):
                continue
            lines.append(f"| {ri} | " + " | ".join(_trim(v) for v in vals) + " |")
            shown += 1
            if shown >= MAX_XLSX_ROWS:
                lines.append(f"| … | （以降 {len(rows) - ri} 行省略） |")
                break
        lines.append("")
    return "\n".join(lines) + "\n"


def profile_file(path: Path) -> str | None:
    suf = path.suffix.lower()
    if suf == ".csv":
        return profile_csv(path)
    if suf in (".xlsx", ".xlsm"):
        return profile_xlsx(path)
    return None


def profile_dir(data_dir: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for p in sorted(data_dir.iterdir()):
        if p.name.startswith(".") or p.name.startswith("~$"):
            continue
        md = profile_file(p)
        if md is None:
            continue
        out = out_dir / f"{p.stem}.md"
        out.write_text(md, encoding="utf-8")
        written.append(out)
    return written
