"""caqnal CLI

  caqnal profile  --client 八戸市
  caqnal init     --client 八戸市 --questions data/R8_設問.xlsx --sheet R8年度設問 --responses data/R8_回答.csv \
                  [--previous-fy R7 --previous-sheet R7年度設問 --previous-responses data/R7_回答.csv]
  caqnal validate --client 八戸市
  caqnal analyze  --client 八戸市
  caqnal render   --client 八戸市 [--deck 全庁|部局別|1pager|excel|all] [--pdf]
  caqnal all      --client 八戸市 [--pdf]
  caqnal verify   --client 八戸市
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from pathlib import Path

from . import config as C


def _client(args) -> C.ClientConfig:
    return C.load_client(args.client)


# ---------------------------------------------------------------- profile
def cmd_profile(args) -> None:
    from .ingest.profile import profile_dir

    root = C.client_root(args.client)
    written = profile_dir(root / "data", root / "profile")
    for p in written:
        print(f"wrote {p.relative_to(C.PROJECT_ROOT)}")


# ---------------------------------------------------------------- init
def _read_csv_columns(path: Path, encoding: str) -> list[str]:
    import pandas as pd

    return [str(c) for c in pd.read_csv(path, encoding=encoding, nrows=0).columns]


def cmd_init(args) -> None:
    from .ingest.question_master import build_questions, parse_master
    from .ingest.yoy import build_yoy_map

    root = C.client_root(args.client)
    header = "# 設問マスタ（caqnal init で生成した下書き）。role / reverse / condition を確認して確定する。\n"
    adapter, entries = parse_master(root / args.questions, args.sheet, args.adapter)
    cols = _read_csv_columns(root / args.responses, args.encoding) if args.responses else None
    current = build_questions(entries, adapter, cols, args.scale)
    C.dump_yaml(current, root / "questions.yaml", header + f"# adapter: {adapter}\n")
    print(f"wrote questions.yaml ({len(current)} 問, adapter={adapter})")

    if args.previous_sheet or args.previous_questions:
        pq = root / (args.previous_questions or args.questions)
        padapter, pentries = parse_master(pq, args.previous_sheet, args.adapter)
        pcols = _read_csv_columns(root / args.previous_responses, args.encoding) if args.previous_responses else None
        prev = build_questions(pentries, padapter, pcols, args.scale)
        fy = args.previous_fy or "prev"
        C.dump_yaml(prev, root / f"questions_{fy}.yaml", header + f"# adapter: {padapter}\n")
        ymap = build_yoy_map(current, prev)
        C.dump_yaml(ymap, root / "yoy_map.yaml",
                    "# 経年対応表（caqnal init で生成した下書き）。status=要確認 を 参考比較 / 比較不可 に確定する。\n")
        n_ok = sum(1 for e in ymap["map"] if e["status"] == "比較可")
        n_chk = sum(1 for e in ymap["map"] if e["status"] == "要確認")
        print(f"wrote questions_{fy}.yaml ({len(prev)} 問), yoy_map.yaml (比較可 {n_ok} / 要確認 {n_chk} / "
              f"未対応前年設問 {len(ymap['unmatched_previous'])})")


# ---------------------------------------------------------------- validate / analyze / render
def cmd_validate(args) -> None:
    from .ingest.validate import validate

    cfg = _client(args)
    report = validate(cfg)
    print(report.render())
    if report.has_errors:
        sys.exit(1)


def _analyze(cfg: C.ClientConfig, out_dir: Path) -> None:
    from .analysis.pipeline import run_analysis, write_intermediate

    results = run_analysis(cfg)
    write_intermediate(results, out_dir, cfg)


def _render(cfg: C.ClientConfig, inter_dir: Path, out_dir: Path, decks: list[str], pdf: bool) -> list[Path]:
    from .analysis.pipeline import load_intermediate
    from .render.deck import render_all

    results = load_intermediate(inter_dir)
    return render_all(cfg, results, out_dir, decks, pdf=pdf)


def cmd_analyze(args) -> None:
    cfg = _client(args)
    _analyze(cfg, cfg.out_dir / "intermediate")
    print(f"wrote {cfg.out_dir / 'intermediate'}")


def cmd_render(args) -> None:
    cfg = _client(args)
    decks = cfg.decks + ["excel"] if args.deck == "all" else [args.deck]
    for p in _render(cfg, cfg.out_dir / "intermediate", cfg.out_dir, decks, args.pdf):
        print(f"wrote {p}")


def cmd_all(args) -> None:
    from .ingest.validate import validate

    cfg = _client(args)
    report = validate(cfg)
    print(report.render())
    if report.has_errors:
        sys.exit(1)
    _analyze(cfg, cfg.out_dir / "intermediate")
    for p in _render(cfg, cfg.out_dir / "intermediate", cfg.out_dir, cfg.decks + ["excel"], args.pdf):
        print(f"wrote {p}")


def _hash_tree(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() != ".pdf":
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def cmd_verify(args) -> None:
    cfg = _client(args)
    hashes = []
    for _ in range(2):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            _analyze(cfg, out / "intermediate")
            _render(cfg, out / "intermediate", out, cfg.decks + ["excel"], pdf=False)
            hashes.append(_hash_tree(out))
    a, b = hashes
    diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    if diff:
        print("再現性 NG: 以下のファイルが一致しません")
        for k in diff:
            print("  ", k)
        sys.exit(1)
    print(f"再現性 OK: {len(a)} ファイルが 2 回の実行で一致")


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="caqnal", description="CAQNAL 自治体エンゲージメント調査 レポート生成")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_client(p):
        p.add_argument("--client", required=True, help="local-governments/ 配下のフォルダ名（例: 八戸市）")

    p = sub.add_parser("profile", help="data/ の受領ファイルを profile/*.md に要約")
    add_client(p)
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("init", help="設問マスタ xlsx から questions.yaml / yoy_map.yaml の下書きを生成")
    add_client(p)
    p.add_argument("--questions", required=True, help="設問マスタ xlsx（自治体フォルダ基準）")
    p.add_argument("--sheet", default=None)
    p.add_argument("--responses", default=None, help="当年度 回答 CSV（ヘッダー突合に使う）")
    p.add_argument("--adapter", default=None, choices=["hachinohe", "saga", "standard"])
    p.add_argument("--scale", default="agree5")
    p.add_argument("--encoding", default="utf-8-sig")
    p.add_argument("--previous-fy", default=None)
    p.add_argument("--previous-questions", default=None)
    p.add_argument("--previous-sheet", default=None)
    p.add_argument("--previous-responses", default=None)
    p.set_defaults(func=cmd_init)

    for name, fn, hlp in [("validate", cmd_validate, "入力チェック（仕様書1-3）"),
                          ("analyze", cmd_analyze, "解析 → output/intermediate/"),
                          ("verify", cmd_verify, "2回実行して再現性を確認")]:
        p = sub.add_parser(name, help=hlp)
        add_client(p)
        p.set_defaults(func=fn)

    p = sub.add_parser("render", help="中間データ → PPTX/Excel/PDF")
    add_client(p)
    p.add_argument("--deck", default="all", help="全庁 | 部局別 | 1pager | excel | all")
    p.add_argument("--pdf", action="store_true")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("all", help="validate → analyze → render")
    add_client(p)
    p.add_argument("--pdf", action="store_true")
    p.set_defaults(func=cmd_all)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except C.ConfigError as e:
        print(f"設定エラー: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
