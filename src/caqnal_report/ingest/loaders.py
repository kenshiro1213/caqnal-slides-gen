"""回答 CSV の読み込みと正規化。

- encoding は client.yaml（既定 utf-8-sig）
- 丸数字プレフィクス（①②…）と直後の空白を除去してラベルテキストに揃える
- 回答者 ID が無いデータは行番号から匿名 ID を採番する
- questions.yaml の column / header と CSV ヘッダーが一致することを検証する
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ..config import ClientConfig, ConfigError, Wave

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚"
_WS = re.compile(r"[\s　]+")


def normalize_label(value, mode: str = "strip_circled_prefix") -> str | None:
    """選択肢ラベルを正規化する。空文字・NaN は None。"""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    # NFKC は ①→1 / （→( に変換してしまうので使わない。空白の統一のみ行う
    s = str(value).strip()
    if not s:
        return None
    if mode == "strip_circled_prefix" and s[0] in CIRCLED:
        s = s[1:].lstrip()
    return _WS.sub(" ", s).strip()


def read_csv(path: Path, encoding: str = "utf-8-sig") -> pd.DataFrame:
    if not path.exists():
        raise ConfigError(f"回答データが見つかりません: {path}")
    df = pd.read_csv(path, encoding=encoding, dtype=str, keep_default_na=False)
    df = df.replace({"": None})
    df.columns = [str(c) for c in df.columns]
    return df


@dataclass
class WaveData:
    fiscal_year: str
    df: pd.DataFrame          # respondent_id + 設問ID 列（ラベル正規化済み）
    questions: list[dict]
    raw_columns: list[str]

    @property
    def n(self) -> int:
        return len(self.df)


def check_headers(raw: pd.DataFrame, questions: list[dict]) -> list[str]:
    """questions.yaml の column/header と CSV ヘッダーの不一致を列挙する。"""
    problems: list[str] = []
    for q in questions:
        col = q.get("column")
        if col is None:
            problems.append(f"{q['id']}: column が未設定")
            continue
        if col >= len(raw.columns):
            problems.append(f"{q['id']}: column={col} が CSV の列数 {len(raw.columns)} を超えています")
            continue
        actual = raw.columns[col].strip()
        expected = str(q.get("header", "")).strip()
        if expected and actual != expected:
            problems.append(f"{q['id']}: header 不一致\n    yaml: {expected}\n    csv : {actual}")
    return problems


def load_wave(cfg: ClientConfig, fiscal_year: str, responses: Path, questions: list[dict],
              id_prefix: str | None = None) -> WaveData:
    data_cfg = cfg.raw.get("data", {})
    encoding = data_cfg.get("encoding", "utf-8-sig")
    raw = read_csv(responses, encoding)
    problems = check_headers(raw, questions)
    if problems:
        raise ConfigError(f"{responses.name}: CSV ヘッダーと設問マスタが一致しません\n  " + "\n  ".join(problems))

    mode = cfg.raw.get("label_normalize", "strip_circled_prefix")
    cols: dict[str, pd.Series] = {}
    for q in questions:
        s = raw.iloc[:, q["column"]]
        if q.get("role") == "freetext":
            cols[q["id"]] = s.map(lambda v: (str(v).strip() or None) if v is not None else None)
        else:
            cols[q["id"]] = s.map(lambda v: normalize_label(v, mode))
    df = pd.DataFrame(cols)

    id_spec = data_cfg.get("respondent_id", {"source": "row_index"})
    if id_spec.get("source") == "column":
        rid = raw[id_spec["name"]].astype(str)
    else:
        prefix = id_prefix if id_prefix is not None else id_spec.get("prefix", f"{fiscal_year}-")
        rid = pd.Series([f"{prefix}{i + 1:04d}" for i in range(len(raw))])
    df.insert(0, "respondent_id", rid.values)
    return WaveData(fiscal_year=fiscal_year, df=df, questions=questions, raw_columns=list(raw.columns))


def load_current(cfg: ClientConfig) -> WaveData:
    data_cfg = cfg.raw["data"]
    return load_wave(cfg, cfg.fiscal_year, cfg.path(data_cfg["responses"]), cfg.questions)


def load_previous(cfg: ClientConfig, wave: Wave) -> WaveData:
    return load_wave(cfg, wave.fiscal_year, wave.responses, wave.questions,
                     id_prefix=f"{wave.fiscal_year}-")
