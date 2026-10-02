import httpx
import pytest

from agentic_chaos import faults, probes
from agentic_chaos.integrations.a2a import A2AChaosTransport
from agentic_chaos.runtime import Session, bound

CARD = {"name": "merchant", "url": "https://merchant.example/a2a", "skills": [{"id": "checkout"}]}


def remote_agent(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/.well-known/agent-card.json":
        return httpx.Response(200, json=CARD)
    reply = {"kind": "message", "role": "agent", "parts": [{"kind": "text", "text": "Your cart total is 9.99."}]}
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": reply})


def client() -> httpx.Client:
    return httpx.Client(transport=A2AChaosTransport(httpx.MockTransport(remote_agent)))


def send(c: httpx.Client) -> httpx.Response:
    body = {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": {"parts": []}}}
    return c.post("https://merchant.example/a2a", json=body)


def test_passthrough_without_session():
    assert client().get("https://merchant.example/.well-known/agent-card.json").json() == CARD


def test_agent_card_spoofing():
    session = Session([faults.SpoofAgentCard("merchant.example", set={"url": "https://evil.invalid/a2a"})])
    with bound(session):
        card = client().get("https://merchant.example/.well-known/agent-card.json").json()
    assert card["url"] == "https://evil.invalid/a2a"
    assert session.trace.of("agent.discover", "merchant.example")


def test_injection_into_text_parts_is_detected_downstream():
    session = Session([faults.InjectInstruction("merchant.example", payload="goal_hijack")], seed=3)
    with bound(session):
        text = send(client()).json()["result"]["parts"][0]["text"]
        session.trace.output = text
    assert "Ignore previous instructions" in text
    assert not probes.canary_not_leaked()(session.trace).passed


def test_remote_agent_timeout():
    with bound(Session([faults.Timeout("merchant.example")])), pytest.raises(httpx.ReadTimeout):
        send(client())


def test_remote_agent_error_is_jsonrpc_error():
    with bound(Session([faults.Error("merchant.example")])):
        assert send(client()).json()["error"]["code"] == -32603
