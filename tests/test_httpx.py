import httpx

from agentic_chaos_security import faults
from agentic_chaos_security.integrations.httpx import ChaosTransport
from agentic_chaos_security.runtime import Session, bound


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


def test_text_mode_acts_on_model_text():
    with bound(Session([faults.Truncate(keep=0.5)])):
        assert client().get("https://api.example.com/v1/messages").json() == {"content": "he"}


def test_raw_mode_acts_on_the_wire_body():
    upstream = httpx.MockTransport(lambda request: httpx.Response(200, json={"content": "hello"}))
    raw = httpx.Client(transport=ChaosTransport(upstream, mode="raw"))
    with bound(Session([faults.Truncate(keep=0.5)])):
        body = raw.get("https://api.example.com/v1/messages").text
    assert body == '{"content":"hello"}'[: len('{"content":"hello"}') // 2]
