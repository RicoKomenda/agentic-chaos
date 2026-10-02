"""Inject faults into Agent2Agent (A2A) traffic at the HTTP layer.

    import httpx
    from agentic_chaos.integrations.a2a import A2AChaosTransport

    http_client = httpx.AsyncClient(transport=AsyncA2AChaosTransport())   # pass to your A2A client

Mapping (target name = remote agent host):

* ``GET /.well-known/agent-card.json`` (or legacy ``agent.json``) -> ``agent.discover`` on the card
* JSON-RPC ``message/*`` and ``tasks/*`` requests -> ``agent.call`` (timeouts, errors, rate limits)
* text parts in JSON-RPC results -> ``agent.message`` (injection, patching, truncation, ...)

Streaming responses (``text/event-stream``) are passed through unchanged for now.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

try:
    import httpx
except ImportError as exc:  # pragma: no cover
    raise ImportError("install the httpx extra: pip install 'agentic-chaos[httpx]'") from exc

from agentic_chaos.faults import ChaosError, ChaosRateLimit, ChaosTimeout
from agentic_chaos.runtime import intercept, record

CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")
INTERNAL_ERROR = -32603


def _rpc(request: httpx.Request) -> dict[str, Any] | None:
    if request.method != "POST":
        return None
    try:
        body = json.loads(request.content or b"{}")
    except ValueError:
        return None
    method = body.get("method", "") if isinstance(body, dict) else ""
    return body if method.startswith(("message/", "tasks/")) else None


def _before(request: httpx.Request) -> httpx.Response | None:
    rpc = _rpc(request)
    if rpc is None:
        return None
    host = request.url.host
    record("agent.call", host, method=rpc["method"], params=rpc.get("params", {}))
    try:
        intercept("agent.call", host, None, method=rpc["method"])
    except ChaosTimeout as exc:
        raise httpx.ReadTimeout(str(exc), request=request) from exc
    except ChaosRateLimit as exc:
        return httpx.Response(429, json={"error": str(exc)}, request=request)
    except ChaosError as exc:
        error = {"code": INTERNAL_ERROR, "message": str(exc)}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": rpc.get("id"), "error": error}, request=request)
    return None


def _after(request: httpx.Request, response: httpx.Response, body: bytes) -> httpx.Response:
    host = request.url.host
    if "text/event-stream" in response.headers.get("content-type", ""):
        return _rebuild(request, response, body)
    if request.method == "GET" and request.url.path.endswith(CARD_PATHS):
        card = json.loads(body)
        card = intercept("agent.discover", host, card)
        record("agent.discover", host, card=card)
        return _rebuild(request, response, json.dumps(card).encode())
    if _rpc(request) is None:
        return _rebuild(request, response, body)
    data = json.loads(body)
    if isinstance(data, dict) and "result" in data:
        try:
            data["result"] = _map_text(data["result"], lambda text: intercept("agent.message", host, text))
        except ChaosTimeout as exc:
            raise httpx.ReadTimeout(str(exc), request=request) from exc
        record("agent.call.result", host, result=data["result"])
    return _rebuild(request, response, json.dumps(data).encode())


def _map_text(value: Any, fn: Callable[[str], Any]) -> Any:
    """Apply ``fn`` to every text part (``{"kind"|"type": "text", "text": ...}`` or ``{"text": ...}``)."""
    if isinstance(value, list):
        return [_map_text(v, fn) for v in value]
    if isinstance(value, dict):
        if isinstance(value.get("text"), str) and value.get("kind", value.get("type", "text")) == "text":
            return {**value, "text": fn(value["text"])}
        return {k: _map_text(v, fn) for k, v in value.items()}
    return value


def _rebuild(request: httpx.Request, response: httpx.Response, body: bytes) -> httpx.Response:
    headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-encoding")}
    return httpx.Response(response.status_code, headers=headers, content=body, request=request)


class A2AChaosTransport(httpx.BaseTransport):
    def __init__(self, wrapped: httpx.BaseTransport | None = None) -> None:
        self.wrapped = wrapped or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if (short_circuit := _before(request)) is not None:
            return short_circuit
        response = self.wrapped.handle_request(request)
        return _after(request, response, response.read())

    def close(self) -> None:
        self.wrapped.close()


class AsyncA2AChaosTransport(httpx.AsyncBaseTransport):
    def __init__(self, wrapped: httpx.AsyncBaseTransport | None = None) -> None:
        self.wrapped = wrapped or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if (short_circuit := _before(request)) is not None:
            return short_circuit
        response = await self.wrapped.handle_async_request(request)
        return _after(request, response, await response.aread())

    async def aclose(self) -> None:
        await self.wrapped.aclose()
