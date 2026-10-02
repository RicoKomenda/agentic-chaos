"""Inject faults into Agent2Agent (A2A) traffic at the HTTP layer.

    import httpx
    from agentic_chaos.integrations.a2a import A2AChaosTransport

    http_client = httpx.AsyncClient(transport=AsyncA2AChaosTransport())   # pass to your A2A client

Mapping (target name = remote agent host):

* ``GET /.well-known/agent-card.json`` (or legacy ``agent.json``) -> ``agent.discover`` on the card
* A2A calls -> ``agent.call`` (timeouts, errors, rate limits, auth errors, duplicates):
  JSON-RPC 1.0 (``SendMessage``, ``SendStreamingMessage``, ``GetTask``, ...), JSON-RPC 0.3 (``message/*``,
  ``tasks/*``), and the HTTP+JSON binding (``/message:send``, ``/message:stream``, ``/tasks/...``)
* text parts in results -> ``agent.message`` (injection, patching, truncation, ...); both 1.0 parts
  (``{"text": ...}``) and 0.3 parts (``{"kind": "text", "text": ...}``) are recognised

Streaming responses (``SendStreamingMessage``, ``message/stream``, subscriptions) are transformed event by event while
they stream; compressed (``content-encoding``) streams pass through unchanged. ``auth_error`` faults
become ``401``/``403`` responses with a ``WWW-Authenticate`` header, and ``duplicate`` delivers the same
request to the remote agent more than once.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from agentic_chaos import sse
from agentic_chaos.faults import ChaosAuthError, ChaosError, ChaosRateLimit, ChaosTimeout, Repeat
from agentic_chaos.runtime import intercept, record

CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")
INTERNAL_ERROR = -32603


#: A2A 1.0 JSON-RPC methods (PascalCase, from the protocol's service definition).
V1_METHODS = frozenset(
    {
        "SendMessage",
        "SendStreamingMessage",
        "GetTask",
        "ListTasks",
        "CancelTask",
        "SubscribeToTask",
        "CreateTaskPushNotificationConfig",
        "GetTaskPushNotificationConfig",
        "ListTaskPushNotificationConfigs",
        "DeleteTaskPushNotificationConfig",
        "GetExtendedAgentCard",
    }
)
_REST_PATH = re.compile(r"/(message:(send|stream)|tasks(/[^/]+)?(:cancel|:subscribe)?|extendedAgentCard)$")


@dataclass
class _Call:
    binding: str  # "jsonrpc" or "rest"
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    rpc_id: Any = None

    def payload(self, data: Any) -> Any:
        """The part of a response body that carries the A2A result."""
        if self.binding == "jsonrpc":
            return data.get("result") if isinstance(data, dict) else None
        return data

    def with_payload(self, data: Any, payload: Any) -> Any:
        return {**data, "result": payload} if self.binding == "jsonrpc" else payload


def build(httpx: ModuleType) -> tuple[type, type]:
    """Build the A2A transports for an httpx-compatible module (``httpx`` or ``httpx2``)."""

    def _call(request: httpx.Request) -> _Call | None:
        """Recognise an A2A call (any supported binding and protocol version)."""
        if request.method == "POST":
            try:
                body = json.loads(request.content or b"{}")
            except ValueError:
                body = None
            if isinstance(body, dict) and "jsonrpc" in body:
                method = str(body.get("method", ""))
                if method in V1_METHODS or method.startswith(("message/", "tasks/", "agent/")):
                    return _Call("jsonrpc", method, body.get("params") or {}, body.get("id"))
                return None
        if request.method in ("POST", "GET") and _REST_PATH.search(request.url.path):
            try:
                params = json.loads(request.content) if request.content else {}
            except ValueError:
                params = {}
            return _Call("rest", f"{request.method} {request.url.path}", params if isinstance(params, dict) else {})
        return None

    def _before(request: httpx.Request) -> tuple[httpx.Response | None, int]:
        """Apply call-time faults. Returns (response to short-circuit with, extra deliveries)."""
        call = _call(request)
        if call is None:
            return None, 0
        host = request.url.host
        record("agent.call", host, method=call.method, params=call.params)
        try:
            intercept("agent.call", host, None, method=call.method)
        except Repeat as repeat:
            for _ in range(repeat.times):
                record("agent.call", host, method=call.method, params=call.params, duplicate=True)
            return None, repeat.times
        except ChaosTimeout as exc:
            raise httpx.ReadTimeout(str(exc), request=request) from exc
        except ChaosAuthError as exc:
            headers = {"www-authenticate": exc.www_authenticate()}
            body = {"error": exc.error, "error_description": exc.description}
            return httpx.Response(exc.status, headers=headers, json=body, request=request), 0
        except ChaosRateLimit as exc:
            return httpx.Response(429, json={"error": str(exc)}, request=request), 0
        except ChaosError as exc:
            if call.binding == "rest":
                return httpx.Response(500, json={"error": {"code": 500, "message": str(exc)}}, request=request), 0
            error = {"code": INTERNAL_ERROR, "message": str(exc)}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": call.rpc_id, "error": error}, request=request), 0
        return None, 0

    def _is_stream(request: httpx.Request, response: httpx.Response) -> bool:
        return (
            "text/event-stream" in response.headers.get("content-type", "")
            and "content-encoding" not in response.headers
            and _call(request) is not None
        )

    def _transform_payload(host: str, request: httpx.Request) -> Callable[[sse.Event], bytes]:
        call = _call(request)
        assert call is not None

        def apply(event: sse.Event) -> bytes:
            def fn(data: Any) -> Any:
                payload = call.payload(data)
                if payload is None:
                    return data
                result = _map_text(payload, lambda text: intercept("agent.message", host, text))
                record("agent.call.result", host, result=result, streamed=True)
                return call.with_payload(data, result)

            try:
                return sse.transform_json(event, fn).encode()
            except ChaosTimeout as exc:
                raise httpx.ReadTimeout(str(exc), request=request) from exc

        return apply

    class _SSEStream(httpx.SyncByteStream):
        def __init__(self, inner: httpx.SyncByteStream, apply: Callable[[sse.Event], bytes]) -> None:
            self.inner = inner
            self.apply = apply

        def __iter__(self) -> Iterator[bytes]:
            parser = sse.Parser()
            for chunk in self.inner:
                for event in parser.feed(chunk):
                    yield self.apply(event)
            for event in parser.flush():
                yield self.apply(event)

        def close(self) -> None:
            self.inner.close()

    class _AsyncSSEStream(httpx.AsyncByteStream):
        def __init__(self, inner: httpx.AsyncByteStream, apply: Callable[[sse.Event], bytes]) -> None:
            self.inner = inner
            self.apply = apply

        async def __aiter__(self) -> AsyncIterator[bytes]:
            parser = sse.Parser()
            async for chunk in self.inner:
                for event in parser.feed(chunk):
                    yield self.apply(event)
            for event in parser.flush():
                yield self.apply(event)

        async def aclose(self) -> None:
            await self.inner.aclose()

    def _streamed(request: httpx.Request, response: httpx.Response, stream: Any) -> httpx.Response:
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return httpx.Response(response.status_code, headers=headers, stream=stream, request=request)

    def _after(request: httpx.Request, response: httpx.Response, body: bytes) -> httpx.Response:
        host = request.url.host
        if "text/event-stream" in response.headers.get("content-type", ""):
            return _rebuild(request, response, body)  # compressed or non-RPC streams
        if request.method == "GET" and request.url.path.endswith(CARD_PATHS):
            card = json.loads(body)
            card = intercept("agent.discover", host, card)
            record("agent.discover", host, card=card)
            return _rebuild(request, response, json.dumps(card).encode())
        call = _call(request)
        if call is None or response.status_code >= 400:
            return _rebuild(request, response, body)
        try:
            data = json.loads(body)
        except ValueError:
            return _rebuild(request, response, body)
        payload = call.payload(data)
        if payload is not None:
            try:
                payload = _map_text(payload, lambda text: intercept("agent.message", host, text))
            except ChaosTimeout as exc:
                raise httpx.ReadTimeout(str(exc), request=request) from exc
            record("agent.call.result", host, result=payload)
            data = call.with_payload(data, payload)
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
            short_circuit, extra = _before(request)
            if short_circuit is not None:
                return short_circuit
            for _ in range(extra):
                self.wrapped.handle_request(request).read()
            response = self.wrapped.handle_request(request)
            if _is_stream(request, response):
                apply = _transform_payload(request.url.host, request)
                return _streamed(request, response, _SSEStream(response.stream, apply))  # type: ignore[arg-type]
            return _after(request, response, response.read())

        def close(self) -> None:
            self.wrapped.close()

    class AsyncA2AChaosTransport(httpx.AsyncBaseTransport):
        def __init__(self, wrapped: httpx.AsyncBaseTransport | None = None) -> None:
            self.wrapped = wrapped or httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            short_circuit, extra = _before(request)
            if short_circuit is not None:
                return short_circuit
            for _ in range(extra):
                await (await self.wrapped.handle_async_request(request)).aread()
            response = await self.wrapped.handle_async_request(request)
            if _is_stream(request, response):
                apply = _transform_payload(request.url.host, request)
                return _streamed(request, response, _AsyncSSEStream(response.stream, apply))  # type: ignore[arg-type]
            return _after(request, response, await response.aread())

        async def aclose(self) -> None:
            await self.wrapped.aclose()

    return A2AChaosTransport, AsyncA2AChaosTransport


try:
    import httpx as _httpx
except ImportError:  # pragma: no cover - httpx2-only environments use agentic_chaos.integrations.httpx2
    pass
else:
    A2AChaosTransport, AsyncA2AChaosTransport = build(_httpx)
