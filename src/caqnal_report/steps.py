"""v2 工程の実行（run に記録しながら 1 工程ずつ進める）。"""
from __future__ import annotations

from .config import ClientConfig
from .run import Run, RunError


def open_or_create(cfg: ClientConfig, run_id: str | None = None, new: bool = False) -> Run:
    """run を開く。指定なしなら最新の run。無い・再開できない（設定や原資料が変わった）ときは新しく作る。"""
    if new:
        return Run.create(cfg, run_id)
    if run_id:
        return Run.open(cfg, run_id)
    run = Run.latest(cfg)
    if run is None or run.stale_reasons() or run.status == "release_ready":
        return Run.create(cfg)
    return run


def step_validate(run: Run) -> bool:
    from .ingest.case_check import check_case

    run.require("validate")
    rep = check_case(run.cfg)
    print(rep.render())
    state = "blocked" if rep.status == "blocked" else "passed"
    run.record("validate", state, warnings=rep.warnings, unresolved=rep.unresolved,
               status="draft_unconfirmed" if rep.status != "validated" else "validated",
               extra={"errors": rep.errors, "case_status": rep.status})
    return state == "passed"


def step_normalize(run: Run) -> bool:
    from .ingest.normalize import NormalizeError, normalize, write_normalized

    if run.step("validate") is None:
        if not step_validate(run):
            return False
    run.require("normalize")
    try:
        res = normalize(run.cfg)
    except NormalizeError as e:
        print(f"[normalize] blocked: {e}")
        run.record("normalize", "blocked", extra={"errors": [str(e)]})
        return False
    paths = write_normalized(res, run.step_dir("normalize"))
    run.record("normalize", "passed", outputs=paths, warnings=res.warnings, unresolved=res.unresolved,
               extra={"stages": res.log.get("stages")})
    print(f"[normalize] passed → {run.step_dir('normalize')}")
    for k, v in (res.log.get("stages") or {}).items():
        if v:
            print(f"  {k}: {v}")
    for w in res.warnings:
        print(f"  WARN   {w}")
    return True


__all__ = ["RunError", "open_or_create", "step_normalize", "step_validate"]
