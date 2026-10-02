"""The experiment catalogs must separate the naive demo targets from the hardened ones."""

from pathlib import Path

import pytest

from agentic_chaos import Verdict, loader

CATALOG = Path(__file__).parent.parent / "experiments"
W, H = Verdict.WEAKNESS, Verdict.HELD

# catalog directory -> (demo module, {experiment file stem: expected verdict for the naive variant})
SUITES = {
    ".": (
        "examples.mailbot.agent",
        {
            "asi01-indirect-prompt-injection": W,
            "asi01-goal-hijack": W,
            "asi04-tool-description-poisoning": W,
            "asi06-memory-poisoning": W,
            "control-guardrail-outage": W,
            "control-defense-in-depth": W,
            "asi08-tool-outage": W,
            "llm-provider-rate-limit": W,
            "asi08-slow-dependencies": H,
        },
    ),
    "mcp": (
        "examples.mcp_demo.host",
        {
            "rug-pull": W,
            "tool-shadowing": W,
            "sampling-injection": W,
            "elicitation-phishing": W,
            "list-changed-flood": W,
            "server-timeout": W,
        },
    ),
    "multi-agent": (
        "examples.shopper.agent",
        {"agent-card-spoofing": W, "delegation-loop": W, "remote-agent-outage": W},
    ),
    "ap2": (
        "examples.shopper.agent",
        {
            "context-poisoning": W,
            "cart-mutation-after-review": W,
            "duplicate-charge": W,
            "extension-downgrade": W,
            "processor-outage": H,
        },
    ),
}
CASES = [(suite, name, expected) for suite, (_, names) in SUITES.items() for name, expected in names.items()]


def run(suite: str, name: str, variant: str) -> Verdict:
    module = SUITES[suite][0]
    return loader.load(CATALOG / suite / f"{name}.yaml", entrypoint=f"{module}:{variant}").run().verdict


@pytest.mark.parametrize("suite", sorted(SUITES))
def test_catalog_is_covered(suite):
    assert {p.stem for p in (CATALOG / suite).glob("*.yaml")} == set(SUITES[suite][1])


@pytest.mark.parametrize(("suite", "name", "expected"), CASES)
def test_naive_variant(suite, name, expected):
    assert run(suite, name, "naive") is expected


@pytest.mark.parametrize(("suite", "name"), [(s, n) for s, n, _ in CASES])
def test_hardened_variant_holds(suite, name):
    assert run(suite, name, "hardened") is Verdict.HELD


def test_guardrail_that_fails_open_is_caught():
    assert run(".", "asi01-indirect-prompt-injection", "guarded_fail_open") is Verdict.HELD
    assert run(".", "control-guardrail-outage", "guarded_fail_open") is Verdict.WEAKNESS


def test_directory_expansion_skips_proxy_configs():
    files = loader.expand([CATALOG])
    assert files and all("proxy" not in f.parts for f in files)
