"""Inject faults into any SDK that accepts a custom ``httpx`` client (OpenAI, Anthropic, LiteLLM, ...).

    import httpx, anthropic
    from agentic_chaos.integrations.httpx import ChaosTransport

    client = anthropic.Anthropic(http_client=httpx.Client(transport=ChaosTransport()))

Requests pass through the ``llm.call`` point and responses through ``llm.response``; the target
name is the request host (e.g. ``api.anthropic.com``). Raised chaos errors are translated into
what a real provider outage looks like on the wire: 429, 500, or a read timeout.
"""

from __future__ import annotations

try:
    import httpx
except ImportError as exc:  # pragma: no cover
    raise ImportError("install the httpx extra: pip install 'agentic-chaos[httpx]'") from exc

from agentic_chaos.faults import ChaosError, ChaosRateLimit, ChaosTimeout
from agentic_chaos.runtime import intercept, record


def _before(request: httpx.Request) -> httpx.Response | None:
    host = request.url.host
    record("llm.call", host, method=request.method, path=request.url.path)
    try:
        intercept("llm.call", host, None)
    except ChaosTimeout as exc:
        raise httpx.ReadTimeout(str(exc), request=request) from exc
    except ChaosRateLimit as exc:
        headers = {"retry-after": str(exc.retry_after)} if exc.retry_after is not None else {}
        return httpx.Response(429, headers=headers, json={"error": {"message": str(exc)}}, request=request)
    except ChaosError as exc:
        return httpx.Response(500, json={"error": {"message": str(exc)}}, request=request)
    return None


def _after(request: httpx.Request, response: httpx.Response, body: bytes) -> httpx.Response:
    text = body.decode("utf-8", errors="replace")
    mutated = intercept("llm.response", request.url.host, text)
    if mutated == text:
        return httpx.Response(response.status_code, headers=response.headers, content=body, request=request)
    headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-encoding")}
    return httpx.Response(response.status_code, headers=headers, content=(mutated or "").encode(), request=request)


class ChaosTransport(httpx.BaseTransport):
    def __init__(self, wrapped: httpx.BaseTransport | None = None) -> None:
        self.wrapped = wrapped or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if (short_circuit := _before(request)) is not None:
            return short_circuit
        response = self.wrapped.handle_request(request)
        return _after(request, response, response.read())

    def close(self) -> None:
        self.wrapped.close()


class AsyncChaosTransport(httpx.AsyncBaseTransport):
    def __init__(self, wrapped: httpx.AsyncBaseTransport | None = None) -> None:
        self.wrapped = wrapped or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if (short_circuit := _before(request)) is not None:
            return short_circuit
        response = await self.wrapped.handle_async_request(request)
        return _after(request, response, await response.aread())

    async def aclose(self) -> None:
        await self.wrapped.aclose()
