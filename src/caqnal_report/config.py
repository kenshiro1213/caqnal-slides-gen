"""設定の読み込みと解決。

client.yaml（自治体固有）+ config/analysis_defaults.yaml（標準閾値）+ config/theme.yaml（描画）
+ questions.yaml（設問マスタ）を 1 つの ClientConfig に合成する。
パスは全て自治体フォルダ（local-governments/<自治体名>/）基準で解決する。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
CLIENTS_DIR = PROJECT_ROOT / "local-governments"

LIKERT_ROLES = ("outcome", "driver")


class ConfigError(Exception):
    pass


def load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def dump_yaml(obj: Any, path: Path, header: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, width=200)
    with open(path, "w", encoding="utf-8") as f:
        if header:
            f.write(header.rstrip("\n") + "\n")
        f.write(text)


def deep_merge(base: dict, override: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


@dataclass
class Wave:
    """1 年度分の回答データと設問マスタ。"""

    fiscal_year: str
    responses: Path
    questions: list[dict]
    yoy_map: list[dict] | None = None
    questions_by_id: dict[str, dict] = field(init=False)

    def __post_init__(self) -> None:
        self.questions_by_id = {q["id"]: q for q in self.questions}


@dataclass
class ClientConfig:
    name: str
    root: Path
    raw: dict
    questions: list[dict]
    analysis: dict
    theme: dict
    action_library: dict
    comment_themes: dict
    previous: list[Wave]
    org_dictionary: dict | None = None
    questions_by_id: dict[str, dict] = field(init=False)

    def __post_init__(self) -> None:
        self.questions_by_id = {q["id"]: q for q in self.questions}

    # ---- 基本 ----
    @property
    def client(self) -> dict:
        return self.raw["client"]

    @property
    def fiscal_year(self) -> str:
        return str(self.client["fiscal_year"])

    @property
    def prev(self) -> Wave | None:
        return self.previous[0] if self.previous else None

    def path(self, rel: str | Path) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.root / p

    @property
    def out_dir(self) -> Path:
        return self.path(self.raw.get("output_dir", "output"))

    # ---- 設問 ----
    def q(self, qid: str) -> dict:
        try:
            return self.questions_by_id[qid]
        except KeyError as e:
            raise ConfigError(f"設問 {qid} が questions.yaml にありません") from e

    def qs(self, roles: Iterable[str] | None = None) -> list[dict]:
        if roles is None:
            return list(self.questions)
        roles = set(roles)
        return [q for q in self.questions if q.get("role") in roles]

    def likert_ids(self) -> list[str]:
        return [q["id"] for q in self.qs(LIKERT_ROLES)]

    def categories(self) -> list[str]:
        seen: list[str] = []
        for q in self.qs(LIKERT_ROLES):
            c = q.get("category")
            if c and c not in seen:
                seen.append(c)
        return seen

    def category_questions(self, category: str) -> list[str]:
        return [q["id"] for q in self.qs(LIKERT_ROLES) if q.get("category") == category]

    def axes(self) -> list[str]:
        seen: list[str] = []
        for q in self.qs(LIKERT_ROLES):
            a = q.get("axis")
            if a and a not in seen:
                seen.append(a)
        return seen

    def axis_questions(self, axis: str) -> list[str]:
        return [q["id"] for q in self.qs(LIKERT_ROLES) if q.get("axis") == axis]

    # ---- 指標 ----
    @property
    def indicators(self) -> dict:
        return self.raw.get("indicators", {})

    def indicator_name(self, key: str) -> str:
        spec = self.indicators.get(key, {})
        return spec.get("name", key)

    def indicator_questions(self, key: str) -> list[str]:
        spec = self.indicators.get(key)
        if spec is None:
            raise ConfigError(f"指標 {key} が client.yaml の indicators にありません")
        if "questions" in spec:
            return list(spec["questions"])
        if "from_axis" in spec:
            return self.axis_questions(spec["from_axis"])
        if "from_categories" in spec:
            out: list[str] = []
            for c in spec["from_categories"]:
                out += self.category_questions(c)
            return out
        raise ConfigError(f"指標 {key} の定義に questions / from_axis / from_categories がありません")

    # ---- 尺度 ----
    def scale(self, name: str) -> dict:
        try:
            return self.raw["scales"][name]
        except KeyError as e:
            raise ConfigError(f"尺度 {name} が client.yaml の scales にありません") from e

    # ---- 属性・組織 ----
    @property
    def attributes(self) -> dict[str, dict]:
        return self.raw.get("attributes", {})

    @property
    def org(self) -> dict:
        return self.raw.get("organization", {})

    @property
    def org_level(self) -> str:
        return self.org.get("levels", ["部局"])[0]

    @property
    def org_units(self) -> list[str]:
        return list(self.org.get("units", []))

    # ---- 案件設定（仕様 v2）。未記載は空、記載した null は「確認待ち」 ----
    @property
    def inputs(self) -> list[dict]:
        return list(self.raw.get("inputs") or [])

    @property
    def population(self) -> dict:
        return self.raw.get("population") or {}

    @property
    def publication(self) -> dict:
        return self.raw.get("publication") or {}

    @property
    def readers(self) -> list[dict]:
        return list(self.raw.get("readers") or [])

    @property
    def models(self) -> dict:
        return self.raw.get("models") or {}

    @property
    def common9(self) -> dict:
        return self.raw.get("common9") or {}

    @property
    def org_units_dict(self) -> list[dict]:
        """組織辞書（org.yaml の units）。unit_id は文字列。"""
        return list((self.org_dictionary or {}).get("units") or [])

    def org_unit(self, unit_id: str) -> dict:
        for u in self.org_units_dict:
            if u["id"] == str(unit_id):
                return u
        raise ConfigError(f"組織 {unit_id} が組織辞書にありません")

    # ---- デッキ ----
    def deck_path(self, name: str) -> Path:
        override = (self.raw.get("deck_overrides") or {}).get(name)
        if override:
            return self.path(override)
        return CONFIG_DIR / "decks" / f"{name}.yaml"

    @property
    def decks(self) -> list[str]:
        return list(self.raw.get("decks", ["全庁", "部局別", "1pager", "手法解説"]))


def client_root(name: str, clients_dir: Path | None = None) -> Path:
    root = (clients_dir or CLIENTS_DIR) / name
    if not root.is_dir():
        raise ConfigError(f"自治体フォルダが見つかりません: {root}")
    return root


def load_client(name: str, clients_dir: Path | None = None) -> ClientConfig:
    root = client_root(name, clients_dir)
    client_yaml = root / "client.yaml"
    if not client_yaml.exists():
        raise ConfigError(f"{client_yaml} がありません。docs/自治体オンボーディング手順.md を参照")
    raw = load_yaml(client_yaml)
    questions = load_yaml(root / raw.get("questions", "questions.yaml"))
    defaults = load_yaml(CONFIG_DIR / "analysis_defaults.yaml")
    analysis = deep_merge(defaults, raw.get("analysis"))
    theme = deep_merge(load_yaml(CONFIG_DIR / "theme.yaml"), raw.get("theme"))
    action_library = deep_merge(load_yaml(CONFIG_DIR / "action_library.yaml"), raw.get("action_library"))
    comment_themes = deep_merge(load_yaml(CONFIG_DIR / "comment_themes.yaml"), raw.get("comment_themes"))

    previous: list[Wave] = []
    for spec in raw.get("data", {}).get("previous", []) or []:
        qpath = root / spec["questions"]
        ypath = root / spec["yoy_map"] if spec.get("yoy_map") else None
        previous.append(
            Wave(
                fiscal_year=str(spec["fiscal_year"]),
                responses=root / spec["responses"],
                questions=load_yaml(qpath),
                yoy_map=load_yaml(ypath) if ypath and ypath.exists() else None,
            )
        )

    org_dictionary = None
    if raw.get("org_dictionary"):
        org_path = root / raw["org_dictionary"]
        if org_path.exists():
            org_dictionary = load_yaml(org_path) or {}
            for u in org_dictionary.get("units") or []:   # ID は文字列で照合する（仕様 v2 §4）
                u["id"] = str(u["id"]).strip()
                if u.get("parent") is not None:
                    u["parent"] = str(u["parent"]).strip()

    return ClientConfig(
        name=name,
        root=root,
        raw=raw,
        questions=questions,
        analysis=analysis,
        theme=theme,
        action_library=action_library,
        comment_themes=comment_themes,
        previous=previous,
        org_dictionary=org_dictionary,
    )
