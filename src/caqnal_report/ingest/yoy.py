"""経年対応表（yoy_map.yaml）の生成と適用。

設問番号は年度間でずれるので、正規化した設問文で突合する。
status: 比較可 / 参考比較 / 比較不可 / 要確認（下書き時のみ。人が確定する）
"""
from __future__ import annotations

import difflib
import re
import unicodedata

_PUNCT = re.compile(r"[\s　、。，．,.・「」『』（）()【】\[\]〔〕:：;；!！?？\-－―ー〜~]")
_SYNONYM = [("ちょうどいい", "ちょうど良い"), ("必要あると", "必要があると"), ("業務内容を", "業務の内容について")]

COMPARABLE = ("比較可", "参考比較")


def norm_text(t: str | None) -> str:
    s = unicodedata.normalize("NFKC", t or "")
    s = re.sub(r"[（(]※.*?[)）]", "", s)  # 注記を除く
    s = _PUNCT.sub("", s).lower()
    for a, b in _SYNONYM:
        s = s.replace(a, b)
    return s


def build_yoy_map(current: list[dict], previous: list[dict], fuzzy: float = 0.75) -> dict:
    roles = {"outcome", "driver", "attribute", "freetext"}
    prev_by_norm: dict[str, dict] = {}
    for p in previous:
        if p.get("role") in roles:
            prev_by_norm.setdefault(norm_text(p.get("text")), p)
    used: set[str] = set()
    entries: list[dict] = []
    for c in current:
        if c.get("role") not in roles:
            continue
        cn = norm_text(c.get("text"))
        entry: dict = {"current": c["id"], "current_text": c.get("text")}
        p = prev_by_norm.get(cn)
        if p is not None:
            entry.update(previous=p["id"], status="比較可")
            if (p.get("category") or "") != (c.get("category") or ""):
                entry["note"] = f"カテゴリ変更: {p.get('category')} → {c.get('category')}"
        else:
            cands = [(difflib.SequenceMatcher(None, cn, k).ratio(), k) for k in prev_by_norm if k not in used]
            cands.sort(reverse=True)
            if cands and cands[0][0] >= fuzzy:
                p = prev_by_norm[cands[0][1]]
                entry.update(previous=p["id"], previous_text=p.get("text"), status="要確認",
                             note=f"文言差あり（類似度 {cands[0][0]:.2f}）。参考比較か比較不可かを判断")
            else:
                entry.update(previous=None, status="比較不可", note="新規設問")
        if p is not None:
            used.add(norm_text(p.get("text")))
        entries.append(entry)
    unmatched = [{"previous": p["id"], "previous_text": p.get("text"),
                  "note": "当年度に対応なし（削除または大幅な文言変更）"}
                 for k, p in prev_by_norm.items() if k not in used]
    return {"map": entries, "unmatched_previous": unmatched}


def comparable_pairs(yoy_map: dict | list | None) -> dict[str, tuple[str, str]]:
    """current_id → (previous_id, status)。比較可・参考比較のみ。"""
    if not yoy_map:
        return {}
    entries = yoy_map["map"] if isinstance(yoy_map, dict) else yoy_map
    out: dict[str, tuple[str, str]] = {}
    for e in entries:
        if e.get("previous") and e.get("status") in COMPARABLE:
            out[e["current"]] = (e["previous"], e["status"])
    return out
