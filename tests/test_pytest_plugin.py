import textwrap

pytest_plugins = ["pytester"]

TARGET = """
import agentic_chaos as chaos

@chaos.tool(name="lookup")
def lookup():
    return "ok"

def robust():
    try:
        return lookup()
    except TimeoutError:
        return "degraded"

def fragile():
    return lookup()
"""

EXPERIMENT = """
apiVersion: agentic-chaos/v1
kind: Experiment
metadata: {{name: {name}}}
spec:
  target: {{entrypoint: "chaos_target:{target}"}}
  faults: [{{type: timeout, target: {fault_target}}}]
  probes: [{{type: no_unhandled_error}}]
"""


def setup(pytester, ini: str = "") -> None:
    pytester.makepyfile(chaos_target=TARGET)
    pytester.makefile(".yaml", **{
        "exp_robust": EXPERIMENT.format(name="robust", target="robust", fault_target="lookup"),
        "exp_fragile": EXPERIMENT.format(name="fragile", target="fragile", fault_target="lookup"),
        "exp_miss": EXPERIMENT.format(name="miss", target="robust", fault_target="nothing"),
    })  # fmt: skip
    pytester.makeini(textwrap.dedent(f"[pytest]\npythonpath = .\n{ini}"))


def test_plugin_is_inert_without_configuration(pytester):
    setup(pytester)
    pytester.runpytest().assert_outcomes()


def test_collects_experiment_files(pytester):
    setup(pytester, "chaos_experiments = exp_*.yaml")
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1, failed=2)
    result.stdout.fnmatch_lines(["*fragile: WEAKNESS-FOUND*", "*miss: INCONCLUSIVE*"])


def test_inconclusive_can_be_skipped(pytester):
    setup(pytester, "chaos_experiments = exp_*.yaml")
    pytester.runpytest("--chaos-inconclusive=skip").assert_outcomes(passed=1, failed=1, skipped=1)


def test_fixture(pytester):
    setup(pytester)
    pytester.makepyfile(
        test_uses_fixture="""
        import pytest
        from agentic_chaos.pytest_plugin import ChaosHypothesisFailed

        def test_robust(chaos):
            chaos.assert_held("exp_robust.yaml")

        def test_fragile(chaos):
            with pytest.raises(ChaosHypothesisFailed, match="WEAKNESS-FOUND"):
                chaos.assert_held("exp_fragile.yaml")

        def test_override_target_and_runs(chaos):
            result = chaos.run("exp_fragile.yaml", target="chaos_target:robust", runs=3)
            assert result.verdict.value == "hypothesis-held" and len(result.chaos) == 3
        """
    )
    pytester.runpytest().assert_outcomes(passed=3)
