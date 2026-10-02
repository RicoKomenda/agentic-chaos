"""The experiment catalog must separate the naive, fail-open and hardened mailbot variants."""

from pathlib import Path

import pytest

from agentic_chaos import Verdict, loader

CATALOG = Path(__file__).parent.parent / "experiments"

EXPECTED_NAIVE = {
    "asi01-indirect-prompt-injection": Verdict.WEAKNESS,
    "asi01-goal-hijack": Verdict.WEAKNESS,
    "asi04-tool-description-poisoning": Verdict.WEAKNESS,
    "asi06-memory-poisoning": Verdict.WEAKNESS,
    "control-guardrail-outage": Verdict.WEAKNESS,
    "control-defense-in-depth": Verdict.WEAKNESS,
    "asi08-tool-outage": Verdict.WEAKNESS,
    "llm-provider-rate-limit": Verdict.WEAKNESS,
    "asi08-slow-dependencies": Verdict.HELD,
}


def run(name: str, target: str) -> Verdict:
    return loader.load(CATALOG / f"{name}.yaml", entrypoint=f"examples.mailbot.agent:{target}").run().verdict


def test_catalog_is_covered():
    assert {p.stem for p in CATALOG.glob("*.yaml")} == set(EXPECTED_NAIVE)


@pytest.mark.parametrize("name", sorted(EXPECTED_NAIVE))
def test_naive_agent(name):
    assert run(name, "naive") is EXPECTED_NAIVE[name]


@pytest.mark.parametrize("name", sorted(EXPECTED_NAIVE))
def test_hardened_agent_holds(name):
    assert run(name, "hardened") is Verdict.HELD


def test_guardrail_that_fails_open_is_caught():
    assert run("asi01-indirect-prompt-injection", "guarded_fail_open") is Verdict.HELD
    assert run("control-guardrail-outage", "guarded_fail_open") is Verdict.WEAKNESS
