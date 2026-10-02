"""Case study: DVMA's real agent loop under chaos (skipped unless a DVMA checkout and deps are present)."""

from pathlib import Path

import pytest

pytest.importorskip("sqlmodel")
pytest.importorskip("qdrant_client")
pytest.importorskip("openai")

from agentic_chaos_security import Verdict, loader  # noqa: E402
from case_studies.dvma import harness  # noqa: E402

pytestmark = pytest.mark.skipif(not harness.available(), reason="DVMA checkout not found (set DVMA_PATH)")
EXPERIMENTS = Path(__file__).resolve().parents[2] / "case_studies/dvma/experiments"
EXPECTED = {
    "kb-outage": Verdict.HELD,
    "kb-policy-poisoning": Verdict.WEAKNESS,
    "provider-rate-limit": Verdict.WEAKNESS,
    "provider-timeout": Verdict.WEAKNESS,
}


@pytest.mark.parametrize(("name", "verdict"), sorted(EXPECTED.items()))
def test_dvma_case_study(name, verdict):
    assert loader.load(EXPERIMENTS / f"{name}.yaml").run().verdict is verdict
