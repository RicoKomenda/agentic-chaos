import json

import httpx
import pytest

from agentic_chaos_security import faults, probes
from agentic_chaos_security.integrations.a2a import A2AChaosTransport
from agentic_chaos_security.runtime import Session, bound

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


def streaming_agent(request: httpx.Request) -> httpx.Response:
    events = [
        {"kind": "status-update", "status": {"state": "working"}},
        {"kind": "artifact-update", "artifact": {"parts": [{"kind": "text", "text": "Cart total 9.99."}]}},
    ]
    body = "".join(f"data: {json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': e})}\n\n" for e in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def stream(c: httpx.Client) -> list[dict]:
    body = {"jsonrpc": "2.0", "id": 1, "method": "message/stream", "params": {"message": {"parts": []}}}
    with c.stream("POST", "https://merchant.example/a2a", json=body) as response:
        text = "".join(response.iter_text())
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_streamed_events_are_transformed():
    c = httpx.Client(transport=A2AChaosTransport(httpx.MockTransport(streaming_agent)))
    session = Session([faults.InjectInstruction("merchant.example", payload="goal_hijack")], seed=1)
    with bound(session):
        events = stream(c)
    artifact_text = events[1]["result"]["artifact"]["parts"][0]["text"]
    assert artifact_text.startswith("Cart total 9.99.\nIgnore previous instructions")
    assert events[0]["result"]["status"]["state"] == "working"
    assert len(session.trace.of("agent.call.result")) == 2


def test_stream_passthrough_without_session():
    c = httpx.Client(transport=A2AChaosTransport(httpx.MockTransport(streaming_agent)))
    assert stream(c)[1]["result"]["artifact"]["parts"][0]["text"] == "Cart total 9.99."


def test_auth_error_is_401_with_challenge():
    with bound(Session([faults.AuthError("merchant.example")])):
        response = send(client())
    assert response.status_code == 401 and "invalid_token" in response.headers["www-authenticate"]


def test_duplicate_delivery():
    seen = []

    def counting(request):
        seen.append(request.method)
        return remote_agent(request)

    c = httpx.Client(transport=A2AChaosTransport(httpx.MockTransport(counting)))
    with bound(Session([faults.Duplicate("merchant.example", times=2)])):
        send(c)
    assert seen.count("POST") == 3


def test_async_stream_transformed():
    import asyncio

    from agentic_chaos_security.integrations.a2a import AsyncA2AChaosTransport

    async def main():
        transport = AsyncA2AChaosTransport(httpx.MockTransport(streaming_agent))
        async with httpx.AsyncClient(transport=transport) as c:
            body = {"jsonrpc": "2.0", "id": 1, "method": "message/stream", "params": {}}
            async with c.stream("POST", "https://merchant.example/a2a", json=body) as response:
                return "".join([chunk async for chunk in response.aiter_text()])

    with bound(Session([faults.Truncate("merchant.example", keep=0.5)])):
        text = asyncio.run(main())
    assert '"Cart tot"' in text and "Cart total" not in text
