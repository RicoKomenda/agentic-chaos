import httpx

from agentic_chaos import faults
from agentic_chaos.integrations.httpx import ChaosTransport
from agentic_chaos.runtime import Session, bound


def client() -> httpx.Client:
    upstream = httpx.MockTransport(lambda request: httpx.Response(200, json={"content": "hello"}))
    return httpx.Client(transport=ChaosTransport(upstream))


def test_passthrough_without_session():
    assert client().get("https://api.example.com/v1/messages").json() == {"content": "hello"}


def test_rate_limit_becomes_http_429():
    session = Session([faults.RateLimit("api.example.com", retry_after=3)])
    with bound(session):
        response = client().post("https://api.example.com/v1/messages")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "3"
    assert session.trace.of("llm.call", "api.example.com")


def test_response_corruption():
    with bound(Session([faults.Truncate(keep=0.5)])):
        body = client().get("https://api.example.com/v1/messages").text
    assert body == '{"content":"hello"}'[: len('{"content":"hello"}') // 2]
