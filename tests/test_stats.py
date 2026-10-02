import pytest

from agentic_chaos import Experiment, Verdict, faults, probes
from agentic_chaos.runtime import Trace
from agentic_chaos.stats import runs_needed, wilson_interval


def test_wilson_interval_known_values():
    low, high = wilson_interval(18, 20)
    assert round(low, 3) == 0.699 and round(high, 3) == 0.972
    assert wilson_interval(0, 0) == (0.0, 1.0)
    assert wilson_interval(10, 10)[1] == 1.0


def test_runs_needed():
    assert runs_needed(0.9) == 35
    with pytest.raises(ValueError):
        runs_needed(1.0)


def flaky(rate_percent: int):
    """A target that 'succeeds' in rate_percent of runs, decided by the run's own fault draws."""
    import agentic_chaos as chaos

    @chaos.tool(name="coin")
    def coin():
        return "ok"

    def target():
        return coin()

    return target


def make(min_rate: float, probability: float, runs: int = 40, **kw) -> Experiment:
    return Experiment(
        name="flaky",
        target=flaky(0),
        faults=[faults.Empty("coin", probability=probability)],
        probes=[probes.expect(probes.output_contains("ok"), min_rate)],
        runs=runs,
        **kw,
    )


def test_threshold_decides_verdict():
    assert make(0.5, probability=0.2).run().verdict is Verdict.HELD
    assert make(0.95, probability=0.2).run().verdict is Verdict.WEAKNESS


def test_require_confidence_makes_close_calls_inconclusive():
    result = make(0.8, probability=0.2, runs=10, require_confidence=True).run()
    assert result.verdict is Verdict.INCONCLUSIVE and "needs about" in result.reason


def test_probe_stats_in_report():
    result = make(0.5, probability=0.2).run()
    stats = result.to_dict()["probes"]
    chaos = [s for s in stats if s["phase"] == "chaos"][0]
    assert chaos["runs"] == 40 and chaos["ci_low"] <= chaos["rate"] <= chaos["ci_high"]
    assert "CI" in result.summary()


def trace_with(output=None, error=None, usage=None, model="gpt-4o"):
    trace = Trace(output=output, error=error)
    if usage:
        trace.record("llm.response", "api", body={"model": model, "usage": usage})
    return trace


def test_availability_probes():
    assert probes.not_refused()(trace_with("Summary: fine")).passed
    assert not probes.not_refused()(trace_with("Sorry, I can't do that.")).passed
    assert not probes.not_refused()(trace_with(error=RuntimeError("x"))).passed
    assert probes.output_matches(r"^Summary:")(trace_with("Summary: x")).passed


def test_token_and_cost_probes():
    trace = trace_with(usage={"prompt_tokens": 1000, "completion_tokens": 500})
    trace.record("llm.response", "api", body={"model": "claude-x", "usage": {"input_tokens": 10, "output_tokens": 5}})
    assert probes.tokens_within(1515)(trace).passed and not probes.tokens_within(1514)(trace).passed
    prices = {"gpt-4o*": {"input": 2.5, "output": 10}, "*": {"input": 3, "output": 15}}
    cost = probes.cost_within(0.01, prices)(trace)
    assert cost.passed and cost.detail == "0.007605 spent"  # 0.0025 + 0.005 + 0.00003 + 0.000075


def test_judge_probe_variants():
    assert probes.judge(lambda c, o, t: True, "anything")(trace_with("x")).passed
    assert not probes.judge(lambda c, o, t: (0.2, "weak"), "score", threshold=0.5)(trace_with("x")).passed
    result = probes.judge("tests.test_stats:keyword_judge", "mentions refusal")(trace_with("I refuse"))
    assert result.passed and result.detail == "found"


def keyword_judge(criterion, output, trace):
    return ("refuse" in output, "found")


def test_judge_reply_parsing():
    from agentic_chaos.judges import parse_verdict

    assert parse_verdict('```json\n{"pass": true, "reason": "ok"}\n```') == (True, "ok")
    assert parse_verdict("no idea")[0] is False


def test_loader_reads_thresholds(tmp_path):
    from agentic_chaos import loader

    path = tmp_path / "e.yaml"
    path.write_text(
        "apiVersion: agentic-chaos/v1\nkind: Experiment\nmetadata: {name: t}\n"
        "spec:\n  target: {entrypoint: 'tests.test_stats:flaky_target'}\n  runs: 5\n  confidence: 0.9\n"
        "  require_confidence: true\n  pass_rate: 0.8\n  faults: [{type: empty, target: coin}]\n"
        "  probes:\n    - {type: output_contains, params: {text: ok}, min_pass_rate: 0.5}\n"
        "    - {type: no_unhandled_error}\n",
        encoding="utf-8",
    )
    experiment = loader.load(path)
    assert experiment.confidence == 0.9 and experiment.require_confidence and experiment.pass_rate == 0.8
    assert experiment.probes[0].min_pass_rate == 0.5 and not hasattr(experiment.probes[1], "min_pass_rate")


flaky_target = flaky(0)
