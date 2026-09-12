"""設問マスタ xlsx → questions.yaml 下書き（`caqnal init`）。

自治体ごとに xlsx の列構成が違うので adapter で正規化する。
- hachinohe : 働きがい・働きやすさ / カテゴリ / # / 設問 / 回答
- saga      : No. / 働きがい・働きやすさ / 評価カテゴリ（大） / 項目種類 / 調査設問 / 設問 / 質問意図 / 回答
- standard  : No. / 大分類 / 測定項目 / 設問案 / 選択肢   （十和田市・板橋区の標準設問セット）

回答 CSV のヘッダーを渡すと `Q{n}.` で突合して column / header / text（CSV 版を正とする）を埋める。
role（attribute / outcome / driver / freetext / id）は規則で初期値を付け、人が確定する。
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import openpyxl

from .loaders import normalize_label

ADAPTERS = {
    "hachinohe": {
        "detect": ["#", "設問", "カテゴリ"],
        "map": {"axis": "働きがい・働きやすさ", "category": "カテゴリ", "no": "#", "text": "設問", "choices": "回答"},
        "attribute_categories": ["属性"],
        "outcome_categories": ["総合的な質問"],
        "freetext_categories": ["自由記述"],
    },
    "saga": {
        "detect": ["調査設問", "設問", "評価カテゴリ（大）"],
        "map": {"bank_id": "No.", "axis": "働きがい / 働きやすさ", "category": "評価カテゴリ（大）",
                "kind": "項目種類", "no": "調査設問", "text": "設問", "intent": "質問意図", "choices": "回答"},
        "outcome_axes": ["エンゲージメント"],
    },
    "standard": {
        "detect": ["No.", "大分類", "測定項目"],
        "map": {"no": "No.", "category": "大分類", "subcategory": "測定項目", "text": "設問案", "choices": "選択肢"},
        "outcome_categories": ["総合エンゲージメント", "Outcome"],
        "attribute_categories": ["属性"],
    },
}

_Q_HEADER = re.compile(r"^(?:【(?P<cat>[^】]+)】)?\s*Q(?P<no>\d+)[.．]\s*(?P<text>.*)$", re.S)


def read_rows(path: Path, sheet: str | None) -> list[list]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _cell(v) -> str:
    return "" if v is None else str(v).strip()


def find_header(rows: list[list]) -> tuple[int, dict[str, int]]:
    """ヘッダー行を探し、列名→index を返す。"""
    for i, r in enumerate(rows[:15]):
        cells = [_cell(c) for c in r]
        joined = "".join(cells)
        if ("設問" in joined) and any(k in cells for k in ("#", "No.", "調査設問")):
            return i, {c: j for j, c in enumerate(cells) if c}
    raise ValueError("ヘッダー行が見つかりません（'設問' と '#'/'No.'/'調査設問' を含む行）")


def detect_adapter(header: dict[str, int]) -> str:
    for name, spec in ADAPTERS.items():
        if all(k in header for k in spec["detect"]):
            return name
    raise ValueError(f"adapter を判定できません。ヘッダー: {list(header)}。--adapter で指定してください")


def _pick_text(s: str) -> str:
    """板橋区 v2.0 の `元の文\\n→短縮案` 併記は後者を採用。"""
    if "→" in s and "\n" in s:
        parts = [p.strip() for p in s.split("→") if p.strip()]
        return parts[-1].lstrip("→").strip()
    return s.strip()


def parse_master(path: Path, sheet: str | None, adapter: str | None = None) -> tuple[str, list[dict]]:
    rows = read_rows(path, sheet)
    hi, header = find_header(rows)
    adapter = adapter or detect_adapter(header)
    spec = ADAPTERS[adapter]
    idx = {k: header.get(col) for k, col in spec["map"].items()}
    entries: list[dict] = []
    last: dict = {}
    for r in rows[hi + 1:]:
        get = lambda k: _cell(r[idx[k]]) if idx.get(k) is not None and idx[k] < len(r) else ""  # noqa: E731
        no_raw = get("no")
        text = _pick_text(get("text"))
        if not no_raw or not text or not re.search(r"\d", no_raw):
            continue  # サマリ行・罫線目印などをスキップ
        try:
            no: int | str = int(float(no_raw))
        except ValueError:
            no = no_raw  # 「追加1」など
        e = {
            "no": no,
            "axis": get("axis") or last.get("axis"),
            "category": get("category") or last.get("category"),
            "subcategory": get("subcategory") or None,
            "text": text,
            "choices": get("choices") or None,
        }
        if idx.get("intent") is not None:
            e["intent"] = get("intent") or None
        if idx.get("bank_id") is not None:
            e["bank_id"] = get("bank_id") or None
        last = {k: e[k] for k in ("axis", "category") if e.get(k)}
        entries.append(e)
    return adapter, entries


def parse_csv_header(columns: list[str]) -> dict[int, dict]:
    """CSV ヘッダーを `Q{n}.` で分解して no → {column, header, text, csv_category}。"""
    out: dict[int, dict] = {}
    for j, col in enumerate(columns):
        m = _Q_HEADER.match(str(col).strip())
        if not m:
            continue
        out[int(m.group("no"))] = {
            "column": j,
            "header": str(col).strip(),
            "text": m.group("text").strip(),
            "csv_category": m.group("cat"),
        }
    return out


_NOTE = re.compile(r"\s*[（(]\s*※(?P<note>.*?)[)）]\s*$", re.S)


def _split_note(text: str) -> tuple[str, str | None]:
    """設問文末尾の「（※…）」注記を分離する。"""
    m = _NOTE.search(text or "")
    if not m:
        return (text or "").strip(), None
    return text[: m.start()].strip(), m.group("note").strip()


def _guess_role(e: dict, adapter: str) -> str:
    spec = ADAPTERS[adapter]
    cat, axis, text, choices = e.get("category") or "", e.get("axis") or "", e["text"], e.get("choices") or ""
    if "自由記述" in choices or "ご記載" in text or cat in spec.get("freetext_categories", []):
        return "freetext"
    if "ID" in unicodedata.normalize("NFKC", text).upper() and "入力" in text:
        return "id"
    if cat in spec.get("attribute_categories", []):
        return "attribute"
    if cat in spec.get("outcome_categories", []) or axis in spec.get("outcome_axes", []):
        return "outcome"
    return "driver"


def build_questions(entries: list[dict], adapter: str, csv_columns: list[str] | None,
                    scale_name: str = "agree5") -> list[dict]:
    csv_map = parse_csv_header(csv_columns) if csv_columns else {}
    out: list[dict] = []
    for e in entries:
        role = _guess_role(e, adapter)
        no = e["no"]
        qid = f"Q{no}"
        q: dict = {"id": qid, "no": no}
        c = csv_map.get(no) if isinstance(no, int) else None
        if c:
            q["column"] = c["column"]
            q["header"] = c["header"]
            q["text"], note = _split_note(c["text"])
            if note:
                q["note"] = note
            if c.get("csv_category") and c["csv_category"] != e.get("category"):
                q["csv_category"] = c["csv_category"]
        else:
            q["column"] = None
            q["header"] = None
            q["text"], note = _split_note(e["text"])
            if note:
                q["note"] = note
        xlsx_text = _split_note(e["text"])[0]
        q["xlsx_text"] = xlsx_text if q["text"] != xlsx_text else None
        q["axis"] = e.get("axis")
        q["category"] = e.get("category")
        if e.get("subcategory"):
            q["subcategory"] = e["subcategory"]
        q["role"] = role
        q["scale"] = scale_name if role in ("outcome", "driver") else None
        q["reverse"] = False
        q["condition"] = None
        if role == "attribute" and e.get("choices"):
            q["choices"] = [normalize_label(x) for x in re.split(r"[／/]", e["choices"]) if x.strip()]
        elif e.get("choices") and role in ("outcome", "driver"):
            q["choices_note"] = e["choices"]
        for k in ("intent", "bank_id"):
            if e.get(k):
                q[k] = e[k]
        out.append({k: v for k, v in q.items() if v is not None or k in ("column", "header", "condition")})
    # 条件付き設問（管理職限定など）の候補にメモを付ける
    for q in out:
        if "のみ" in (q.get("note") or "") or ("管理職" in q.get("text", "") and "のみ" in q.get("text", "")):
            q["condition_note"] = "設問文に回答者限定の記述あり。condition を設定すること"
    return out
