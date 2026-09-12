from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fixtures.make_synthetic import build  # noqa: E402
from caqnal_report.config import load_client  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="session")
def cfg():
    build()
    return load_client("テスト市", clients_dir=FIXTURES)


@pytest.fixture(scope="session")
def results(cfg):
    from caqnal_report.analysis.pipeline import run_analysis

    return run_analysis(cfg)
