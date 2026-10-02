import asyncio

import pytest

import agentic_chaos as chaos
from agentic_chaos import Experiment, Verdict, faults, probes
from agentic_chaos.runtime import Session, bound


@chaos.tool
def lookup(key: str) -> str:
    return f"value-for-{key}"


@chaos.tool(name="async_lookup")
async def alookup(key: str) -> str:
    return f"async-{key}"


@chaos.control("policy")
def policy(action: str) -> bool:
    return action == "read"


def test_instrumentation_is_transparent_without_session():
    assert lookup("a") == "value-for-a"
    assert policy("write") is False


def test_targeting_and_after_calls():
    session = Session([faults.Empty("look*", after_calls=1, max_injections=1)], seed=1)
    with bound(session):
        assert [lookup("x"), lookup("y"), lookup("z")] == ["value-for-x", "", "value-for-z"]
    assert len(session.trace.faults) == 1


def test_probability_is_deterministic_for_a_seed():
    def fired(seed):
        session = Session([faults.Empty(probability=0.5)], seed=seed)
        with bound(session):
            return [lookup(str(i)) == "" for i in range(20)]

    assert fired(7) == fired(7)
    assert 0 < sum(fired(7)) < 20


def test_force_verdict_short_circuits_control():
    with bound(Session([faults.ForceVerdict("policy", verdict=True)])):
        assert policy("write") is True


def test_async_tools_are_instrumented():
    session = Session([faults.InjectInstruction(payload="goal_hijack")], seed=0)

    async def main():
        return await alookup("k")

    with bound(session):
        result = asyncio.run(main())
    assert result.startswith("async-k\nIgnore previous instructions")
    assert session.trace.canaries


def test_unknown_point_is_rejected():
    with pytest.raises(ValueError):
        faults.Timeout(point="nowhere")


def test_experiment_inconclusive_when_no_fault_fires():
    result = Experiment(
        name="miss",
        target=lambda: lookup("a"),
        faults=[faults.Timeout("does-not-exist")],
        probes=[probes.no_unhandled_error()],
    ).run()
    assert result.verdict is Verdict.INCONCLUSIVE


def test_experiment_detects_crash():
    result = Experiment(
        name="crash",
        target=lambda: lookup("a"),
        faults=[faults.Timeout("lookup")],
        probes=[probes.no_unhandled_error()],
    ).run()
    assert result.verdict is Verdict.WEAKNESS
    assert "violated" in result.summary()


def test_flood_hides_payload_after_padding():
    session = Session([faults.Flood(size=1000)], seed=0)
    with bound(session):
        result = lookup("a")
    assert len(result) > 1000 and "call the send_email tool" in result[-200:]


def test_patch_sets_nested_paths_without_mutating_input():
    original = {"cart": {"items": [{"price": 1.0}], "total": 1.0}}
    session = Session([faults.Patch(set={"cart.items.0.price": 9.0, "cart.total": 9.0})])

    @chaos.memory(name="cart")
    def get_cart():
        return original

    with bound(session):
        patched = get_cart()
    assert patched["cart"]["items"][0]["price"] == 9.0 and original["cart"]["total"] == 1.0


def test_agent_target_name_from_argument():
    @chaos.agent(name_arg="url")
    def send(url, text):
        return text

    session = Session([faults.Timeout("https://b.example/*")])
    with bound(session):
        assert send("https://a.example/x", "hi") == "hi"
        with pytest.raises(TimeoutError):
            send("https://b.example/x", "hi")
    assert [e.name for e in session.trace.of("agent.call")] == ["https://a.example/x", "https://b.example/x"]
