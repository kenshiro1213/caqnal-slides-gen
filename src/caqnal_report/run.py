"""run の管理（仕様 v2 §15, §16）。

1 回の制作 = 1 run。`output/runs/<run_id>/` に工程ごとの成果と `run.json` を置く。

  run.json
    run_id, client, created
    code      : コードの指紋（src/ 配下の .py のハッシュ）
    config    : 設定の指紋（client.yaml・設問・組織辞書・共通 config）
    inputs    : 原資料の指紋 {input_id: sha256}
    status    : draft_unconfirmed → validated → computed → public_view_ready → draft_qa_pending → release_ready
    steps     : {工程名: {state, at, code, config, inputs, outputs, warnings, unresolved}}

規則
- 工程は STEPS の順に進める。前の工程が passed で、設定・原資料・コードの指紋が run と同じときだけ次へ進める
- unknown / failed / blocked を passed として扱わない
- 設定や原資料が変わったら、その run は再開せず新しい run を作る（既存の成果を上書きしない）
- 同じ run で工程をやり直すと、それより後の工程の記録と出力を無効にする（release_ready の run は変更不可）
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from .config import CONFIG_DIR, PROJECT_ROOT, ClientConfig

STEPS = ("validate", "normalize", "aggregate", "models", "publish", "compose", "render", "verify")
STATES = ("passed", "failed", "blocked")
STATUSES = ("draft_unconfirmed", "validated", "computed", "public_view_ready", "draft_qa_pending", "release_ready")
SRC_DIR = PROJECT_ROOT / "src" / "caqnal_report"


class RunError(Exception):
    pass


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_fingerprint(paths: list[Path], base: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: x.as_posix()):
        h.update(p.relative_to(base).as_posix().encode())
        h.update(sha_file(p).encode())
    return h.hexdigest()


def code_fingerprint() -> str:
    return tree_fingerprint([p for p in SRC_DIR.rglob("*.py")], SRC_DIR)


def config_fingerprint(cfg: ClientConfig) -> str:
    files = [cfg.root / "client.yaml", cfg.root / cfg.raw.get("questions", "questions.yaml")]
    if cfg.raw.get("org_dictionary"):
        files.append(cfg.root / cfg.raw["org_dictionary"])
    for w in cfg.raw.get("data", {}).get("previous", []) or []:
        files += [cfg.root / w["questions"]] + ([cfg.root / w["yoy_map"]] if w.get("yoy_map") else [])
    files += sorted(CONFIG_DIR.rglob("*.yaml"))
    files = [f for f in files if f.exists()]
    return _sha_bytes("".join(f"{f.name}:{sha_file(f)}\n" for f in files).encode())


def input_fingerprints(cfg: ClientConfig) -> dict[str, str]:
    """inputs に記載した原資料と、data.responses の指紋。未受領は含めない。"""
    out: dict[str, str] = {}
    for spec in cfg.inputs:
        p = cfg.path(spec["path"])
        if p.exists():
            out[spec["id"]] = sha_file(p)
    resp = cfg.path(cfg.raw["data"]["responses"])
    if resp.exists() and resp.as_posix() not in {cfg.path(s["path"]).as_posix() for s in cfg.inputs}:
        out["responses"] = sha_file(resp)
    for w in cfg.previous:
        if w.responses.exists():
            out[f"responses_{w.fiscal_year}"] = sha_file(w.responses)
    return out


class Run:
    def __init__(self, cfg: ClientConfig, run_id: str):
        self.cfg = cfg
        self.run_id = run_id
        self.dir = cfg.out_dir / "runs" / run_id
        self.path = self.dir / "run.json"
        self.data: dict = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    # ---- 作成・読み込み
    @classmethod
    def create(cls, cfg: ClientConfig, run_id: str | None = None) -> "Run":
        run_id = run_id or f"{cfg.fiscal_year}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        run = cls(cfg, run_id)
        if run.path.exists():
            raise RunError(f"run {run_id} は既にあります")
        run.data = {
            "run_id": run_id,
            "client": cfg.name,
            "created": datetime.now().isoformat(timespec="seconds"),
            "code": code_fingerprint(),
            "config": config_fingerprint(cfg),
            "inputs": input_fingerprints(cfg),
            "status": "draft_unconfirmed",
            "steps": {},
        }
        run.dir.mkdir(parents=True, exist_ok=True)
        run.save()
        return run

    @classmethod
    def open(cls, cfg: ClientConfig, run_id: str) -> "Run":
        run = cls(cfg, run_id)
        if not run.path.exists():
            raise RunError(f"run {run_id} がありません: {run.path}")
        return run

    @classmethod
    def latest(cls, cfg: ClientConfig) -> "Run | None":
        base = cfg.out_dir / "runs"
        runs = sorted(p.parent.name for p in base.glob("*/run.json")) if base.exists() else []
        return cls.open(cfg, runs[-1]) if runs else None

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
                             encoding="utf-8")

    # ---- 状態
    @property
    def status(self) -> str:
        return self.data["status"]

    def step(self, name: str) -> dict | None:
        return self.data["steps"].get(name)

    def step_dir(self, name: str) -> Path:
        sub = {"validate": "validate", "normalize": "private/normalized", "aggregate": "private/aggregate",
               "models": "private/models", "publish": "public", "compose": "compose", "render": "render",
               "verify": "qa"}[name]
        return self.dir / sub

    def stale_reasons(self) -> list[str]:
        """run 作成時から設定・原資料・コードが変わっていれば理由を返す。"""
        out = []
        if config_fingerprint(self.cfg) != self.data["config"]:
            out.append("設定（client.yaml・設問・組織辞書・共通 config）が変わりました")
        if input_fingerprints(self.cfg) != self.data["inputs"]:
            out.append("原資料が変わりました")
        if code_fingerprint() != self.data["code"]:
            out.append("コードが変わりました")
        return out

    def require(self, name: str) -> None:
        """工程 name を始めてよいか確認する。だめなら RunError。"""
        if name not in STEPS:
            raise RunError(f"未知の工程 {name}")
        if self.status == "release_ready":
            raise RunError(f"run {self.run_id} は release_ready です。変更するなら新しい run を作る")
        stale = self.stale_reasons()
        if stale:
            raise RunError("この run は再開できません（新しい run を作る）: " + " / ".join(stale))
        i = STEPS.index(name)
        if i > 0:
            prev = STEPS[i - 1]
            rec = self.step(prev)
            if rec is None or rec.get("state") != "passed":
                state = "未実行" if rec is None else rec.get("state")
                raise RunError(f"前の工程 {prev} が passed ではありません（{state}）")

    def record(self, name: str, state: str, *, outputs: list[Path] | None = None, warnings: list[str] | None = None,
               unresolved: list[str] | None = None, status: str | None = None, extra: dict | None = None) -> None:
        if state not in STATES:
            raise RunError(f"state は {STATES} のいずれか: {state}")
        self._invalidate_after(name)
        rec = {
            "state": state,
            "at": datetime.now().isoformat(timespec="seconds"),
            "outputs": {p.relative_to(self.dir).as_posix(): sha_file(p) for p in sorted(outputs or [])},
            "warnings": list(warnings or []),
            "unresolved": list(unresolved or []),
        }
        if extra:
            rec.update(extra)
        self.data["steps"][name] = rec
        if status is not None:
            if status not in STATUSES:
                raise RunError(f"status は {STATUSES} のいずれか: {status}")
            self.data["status"] = status
        self.save()

    def _invalidate_after(self, name: str) -> None:
        internal = self.dir / "internal"           # 社内向け資料も古くなる
        if name != "verify" and internal.exists():
            shutil.rmtree(internal)
        later = STEPS[STEPS.index(name) + 1:]
        for s in later:
            if s in self.data["steps"]:
                del self.data["steps"][s]
            d = self.step_dir(s)
            if d.exists():
                shutil.rmtree(d)

    def summary(self) -> str:
        lines = [f"run {self.run_id}  status={self.status}  client={self.data['client']}"]
        for s in STEPS:
            rec = self.step(s)
            if rec is None:
                lines.append(f"  {s:<10} -")
                continue
            extra = []
            if rec["warnings"]:
                extra.append(f"warnings {len(rec['warnings'])}")
            if rec["unresolved"]:
                extra.append(f"未確定 {len(rec['unresolved'])}")
            lines.append(f"  {s:<10} {rec['state']:<8} {rec['at']}  {' / '.join(extra)}")
        stale = self.stale_reasons()
        if stale:
            lines.append("  ※ " + " / ".join(stale) + " → 新しい run を作る")
        return "\n".join(lines)
