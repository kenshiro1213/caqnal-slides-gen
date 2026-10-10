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


def step_aggregate(run: Run) -> bool:
    from .analysis.describe import aggregate, write_aggregated
    from .ingest.normalize import read_normalized

    run.require("aggregate")
    try:
        res = aggregate(run.cfg, read_normalized(run.step_dir("normalize")))
    except ValueError as e:
        print(f"[aggregate] failed: {e}")
        run.record("aggregate", "failed", extra={"errors": [str(e)]})
        return False
    paths = write_aggregated(res, run.step_dir("aggregate"))
    run.record("aggregate", "passed", outputs=paths, warnings=res.warnings, unresolved=res.unresolved,
               extra={"reconcile": res.log.get("reconcile")})
    print(f"[aggregate] passed → {run.step_dir('aggregate')}  集計対象 {res.log['analysed_n']} 名")
    for w in res.warnings + [f"未確定 {u}" for u in res.unresolved]:
        print(f"  {w}")
    return True


def step_models(run: Run) -> bool:
    import pandas as pd

    from .analysis.models import fit_models, write_models
    from .ingest.normalize import read_normalized

    run.require("models")
    norm = read_normalized(run.step_dir("normalize"))
    rs = pd.read_csv(run.step_dir("aggregate") / "respondent_scores.csv", dtype={"respondent_id": str})
    try:
        res = fit_models(run.cfg, rs, norm["respondents"], norm["org"])
    except ValueError as e:
        print(f"[models] blocked: {e}")
        run.record("models", "blocked", extra={"errors": [str(e)]})
        return False
    paths = write_models(res, run.step_dir("models"))
    run.record("models", "passed", outputs=paths, warnings=res.warnings, unresolved=res.unresolved,
               status="computed")
    fit = res.tables["model_fit"]
    print(f"[models] passed → {run.step_dir('models')}")
    for _, r in fit.iterrows():
        cv = "" if pd.isna(r["cv_r2"]) else f" 検証R² {r['cv_r2']:.3f} / MAE {r['cv_mae']:.1f}（平均のみ {r['base_mae']:.1f}）"
        print(f"  {r['objective_id']:<14} {r['model']:<9} {r['candidate']}{cv}  {r['reasons'] or ''}")
    for w in res.warnings + [f"未確定 {u}" for u in res.unresolved]:
        print(f"  {w}")
    return True


def _read_dir(d) -> dict:
    import pandas as pd

    out = {}
    for p in sorted(d.glob("*.csv")):
        try:
            out[p.stem] = pd.read_csv(p, dtype={"scope_id": str, "unit_id": str, "respondent_id": str})
        except pd.errors.EmptyDataError:
            out[p.stem] = pd.DataFrame()
    return out


def step_publish(run: Run) -> bool:
    from .analysis.publish import PublishError, publish, write_published
    from .ingest.normalize import read_normalized

    run.require("publish")
    org = read_normalized(run.step_dir("normalize"))["org"]
    try:
        res = publish(run.cfg, _read_dir(run.step_dir("aggregate")), _read_dir(run.step_dir("models")), org)
    except PublishError as e:
        print(f"[publish] blocked: {e}")
        run.record("publish", "blocked", extra={"errors": [str(e)]})
        return False
    paths = write_published(res, run.step_dir("publish"))
    run.record("publish", "passed", outputs=paths, warnings=res.warnings, status="public_view_ready",
               extra={"readers": list(res.readers), "suppressed": len(res.suppression)})
    sup = res.suppression
    print(f"[publish] passed → {run.step_dir('publish')}  読者 {list(res.readers)}")
    print(f"  非表示: 組織・区分 {int(sup['unit_type'].isna().sum())} 件 / セル {int(sup['unit_type'].notna().sum())} 件"
          f"（うち差し引き推測の防止 {int(sup['reason'].str.startswith('差し引き').sum())} 件）")
    for w in res.warnings:
        print(f"  {w}")
    return True


def step_compose(run: Run) -> bool:
    from .analysis.compose import compose, write_compose

    run.require("compose")
    unresolved = [u for s in ("validate", "normalize", "aggregate", "models") for u in (run.step(s) or {}).get("unresolved", [])]
    res = compose(run.cfg, run.step_dir("publish"), unresolved)
    paths = write_compose(res, run.step_dir("compose"))
    missing = [] if (run.cfg.root / "interpretation.yaml").exists() else \
        ["interpretation.yaml が無いため、示唆・方針・施策の頁は未記入の枠になる"]
    run.record("compose", "passed", outputs=paths, unresolved=missing)
    print(f"[compose] passed → {run.step_dir('compose')}" + (f"  ※ {missing[0]}" if missing else ""))
    return True


def step_render(run: Run) -> bool:
    from .render.v2.deck import render_reader

    run.require("render")
    paths = []
    for reader in run.cfg.readers or [{"id": "全庁", "kind": "all_staff"}]:
        paths += render_reader(run.cfg, run.step_dir("publish"), run.step_dir("compose"), reader,
                               run.step_dir("render"))
    run.record("render", "passed", outputs=paths, status="draft_qa_pending")
    print(f"[render] passed → {run.step_dir('render')}")
    for p in paths:
        print(f"  {p.name}")
    return True


STEP_FUNCS = {"validate": step_validate, "normalize": step_normalize, "aggregate": step_aggregate,
              "models": step_models, "publish": step_publish, "compose": step_compose, "render": step_render}


def run_until(run: Run, last: str) -> bool:
    """未実行・未完了の工程を last まで順に実行する。"""
    from .run import STEPS

    for name in STEPS[: STEPS.index(last) + 1]:
        rec = run.step(name)
        if rec is not None and rec.get("state") == "passed":
            continue
        if not STEP_FUNCS[name](run):
            return False
    return True


__all__ = ["run_until", "RunError", "open_or_create", "step_normalize", "step_validate"]
