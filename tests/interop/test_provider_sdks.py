"""Interop: the official OpenAI and Anthropic Python SDKs through the provider chaos transport.

Upstreams are mocked (no API keys); the SDKs do all parsing, retries and error mapping themselves.
"""

import json

import pytest

openai = pytest.importorskip("openai")
anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")

from agentic_chaos_security import faults, probes  # noqa: E402
from agentic_chaos_security.integrations.httpx2 import ChaosTransport  # noqa: E402
from agentic_chaos_security.runtime import Session, bound  # noqa: E402

pytestmark = pytest.mark.interop
ANSWER = "Quarterly results are up 4%."


def provider(request):
    body = json.loads(request.content or b"{}")
    if request.url.path.endswith("/chat/completions"):
        if body.get("stream"):
            chunks = [
                {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "m",
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": word}, "finish_reason": None}]}
                for word in ("Quarterly ", "results ", "are ", "up.")
            ]  # fmt: skip
            sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())
        return httpx2.Response(
            200,
            json={
                "id": "c1", "object": "chat.completion", "created": 0, "model": "m",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
            },
        )  # fmt: skip
    if request.url.path.endswith("/messages"):
        return httpx2.Response(
            200,
            json={
                "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
                "content": [{"type": "text", "text": ANSWER}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 5, "output_tokens": 7},
            },
        )  # fmt: skip
    return httpx2.Response(404)


def openai_client(mode="text", retries=2):
    transport = ChaosTransport(httpx2.MockTransport(provider), mode=mode)
    return openai.OpenAI(
        api_key="test", base_url="https://api.openai.test/v1", max_retries=retries,
        http_client=httpx2.Client(transport=transport),
    )  # fmt: skip


def anthropic_client(retries=2):
    transport = ChaosTransport(httpx2.MockTransport(provider))
    return anthropic.Anthropic(
        api_key="test", base_url="https://api.anthropic.test", max_retries=retries,
        http_client=httpx2.Client(transport=transport),
    )  # fmt: skip


def chat(client, **kw):
    return client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}], **kw)


def message(client):
    return client.messages.create(model="m", max_tokens=50, messages=[{"role": "user", "content": "hi"}])


def test_passthrough():
    with bound(Session()):
        assert chat(openai_client()).choices[0].message.content == ANSWER
        assert message(anthropic_client()).content[0].text == ANSWER


def test_injection_into_model_output_parses_in_both_sdks():
    session = Session([faults.InjectInstruction(point="llm.response", payload="goal_hijack")], seed=4)
    with bound(session):
        text = chat(openai_client()).choices[0].message.content
        claude_text = message(anthropic_client()).content[0].text
        session.trace.output = [text, claude_text]
    assert text.startswith(ANSWER) and "Ignore previous instructions" in text
    assert "Ignore previous instructions" in claude_text
    assert not probes.canary_not_leaked()(session.trace).passed


def test_injection_into_streamed_deltas():
    session = Session([faults.InjectInstruction(point="llm.response", payload="goal_hijack", max_injections=1)])
    with bound(session):
        stream = chat(openai_client(), stream=True)
        text = "".join(chunk.choices[0].delta.content or "" for chunk in stream)
    assert text.startswith("Quarterly ") and "Ignore previous instructions" in text


def test_rate_limits_are_retried_by_the_sdks_then_raised():
    session = Session([faults.RateLimit(retry_after=0)])
    with bound(session):
        with pytest.raises(openai.RateLimitError):
            chat(openai_client(retries=2))
        with pytest.raises(anthropic.RateLimitError):
            message(anthropic_client(retries=1))
    calls = [e.name for e in session.trace.of("llm.call")]
    assert calls.count("api.openai.test") == 3 and calls.count("api.anthropic.test") == 2
    assert not probes.max_llm_calls(2)(session.trace).passed  # retry amplification is observable


@pytest.mark.parametrize(
    ("status", "openai_error", "anthropic_error"),
    [(401, "AuthenticationError", "AuthenticationError"), (403, "PermissionDeniedError", "PermissionDeniedError")],
)
def test_auth_errors_map_to_sdk_exceptions(status, openai_error, anthropic_error):
    with bound(Session([faults.AuthError(status=status)])):
        with pytest.raises(getattr(openai, openai_error)):
            chat(openai_client())
        with pytest.raises(getattr(anthropic, anthropic_error)):
            message(anthropic_client())


def test_timeouts_map_to_sdk_timeout_errors():
    with bound(Session([faults.Timeout()])):
        with pytest.raises(openai.APITimeoutError):
            chat(openai_client(retries=0))
        with pytest.raises(anthropic.APITimeoutError):
            message(anthropic_client(retries=0))


def test_raw_mode_breaks_the_wire_format():
    # Finding: on an empty 200 body the OpenAI SDK raises a bare JSONDecodeError, not openai.APIError,
    # so applications that only catch APIError crash on a malformed provider/gateway response.
    with bound(Session([faults.Empty(point="llm.response")])), pytest.raises(json.JSONDecodeError):
        chat(openai_client(mode="raw", retries=0))
