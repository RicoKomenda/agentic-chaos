"""Inject faults into any SDK that accepts a custom ``httpx`` client (LiteLLM, older OpenAI/Anthropic SDKs, ...).

    import httpx
    from agentic_chaos.integrations.httpx import ChaosTransport

    client = SomeSDK(http_client=httpx.Client(transport=ChaosTransport()))

SDKs built on ``httpx2`` (current OpenAI and Anthropic SDKs, MCP SDK 2.x) use the same transports from
:mod:`agentic_chaos.integrations.httpx2`.

Requests pass through the ``llm.call`` point; the target name is the request host (e.g.
``api.anthropic.com``). Raised chaos errors are translated into what a provider failure looks like on
the wire: 429, 401/403 with a challenge, 500, or a read timeout. ``duplicate`` re-sends the request.

Responses pass through ``llm.response``. With ``mode="text"`` (the default) faults act on the model's
*text*: every string under a ``content`` or ``text`` key of a JSON body, or of each event of a streamed
(SSE) response - chat completions, Responses API, Anthropic messages and their streaming deltas.
That makes ``inject_instruction`` look like a manipulated model or gateway, and ``truncate`` like a
cut-off answer. With ``mode="raw"`` faults act on the undecoded body, to break the wire format itself
(``corrupt_json``, ``empty``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from types import ModuleType
from typing import TYPE_CHECKING, Any

from agentic_chaos import _sse as sse
from agentic_chaos.faults import ChaosAuthError, ChaosError, ChaosRateLimit, ChaosTimeout, Repeat
from agentic_chaos.runtime import intercept, record

__all__ = [
    "AsyncChaosTransport",
    "ChaosTransport",
    "build",
    "map_text",
]

TEXT_KEYS = ("content", "text")


def map_text(value: Any, fn: Callable[[str], Any], keys: tuple[str, ...] = TEXT_KEYS) -> Any:
    """Apply ``fn`` to every string stored under one of ``keys``, anywhere in a JSON value."""
    if isinstance(value, list):
        return [map_text(v, fn, keys) for v in value]
    if isinstance(value, dict):
        return {k: fn(v) if k in keys and isinstance(v, str) else map_text(v, fn, keys) for k, v in value.items()}
    return value


if TYPE_CHECKING:
    import httpx


def build(http: ModuleType) -> tuple[type, type]:
    """Build the provider transports for an httpx-compatible module (``httpx`` or ``httpx2``)."""

    def _before(request: httpx.Request) -> tuple[httpx.Response | None, int]:
        host = request.url.host
        record("llm.call", host, method=request.method, path=request.url.path)
        try:
            intercept("llm.call", host, None)
        except Repeat as repeat:
            return None, repeat.times
        except ChaosTimeout as exc:
            raise http.ReadTimeout(str(exc), request=request) from exc
        except ChaosAuthError as exc:
            headers = {"www-authenticate": exc.www_authenticate()}
            error = {"error": {"type": "authentication_error", "message": str(exc)}}
            return http.Response(exc.status, headers=headers, json=error, request=request), 0
        except ChaosRateLimit as exc:
            headers = {"retry-after": str(exc.retry_after)} if exc.retry_after is not None else {}
            error = {"error": {"type": "rate_limit_error", "message": str(exc)}}
            return http.Response(429, headers=headers, json=error, request=request), 0
        except ChaosError as exc:
            error = {"error": {"type": "api_error", "message": str(exc)}}
            return http.Response(500, json=error, request=request), 0
        return None, 0

    def _headers(response: httpx.Response) -> dict[str, str]:
        drop = ("content-length", "content-encoding", "transfer-encoding")
        return {k: v for k, v in response.headers.items() if k.lower() not in drop}

    def _text_fn(host: str, request: httpx.Request) -> Callable[[str], Any]:
        def fn(text: str) -> Any:
            try:
                return intercept("llm.response", host, text)
            except ChaosTimeout as exc:
                raise http.ReadTimeout(str(exc), request=request) from exc

        return fn

    def _after(request: httpx.Request, response: httpx.Response, body: bytes, mode: str) -> httpx.Response:
        host = request.url.host
        if mode == "raw" or response.status_code >= 400:
            text = body.decode("utf-8", errors="replace")
            mutated = intercept("llm.response", host, text) if mode == "raw" else text
            if mutated == text:
                return http.Response(response.status_code, headers=_headers(response), content=body, request=request)
            content = (mutated or "").encode()
            return http.Response(response.status_code, headers=_headers(response), content=content, request=request)
        try:
            data = json.loads(body)
        except ValueError:
            return http.Response(response.status_code, headers=_headers(response), content=body, request=request)
        data = map_text(data, _text_fn(host, request))
        record("llm.response", host, body=data)
        content = json.dumps(data).encode()
        return http.Response(response.status_code, headers=_headers(response), content=content, request=request)

    def _is_stream(response: httpx.Response, mode: str) -> bool:
        content_type = response.headers.get("content-type", "")
        return mode == "text" and "text/event-stream" in content_type and "content-encoding" not in response.headers

    def _event_fn(host: str, request: httpx.Request) -> Callable[[sse.Event], bytes]:
        fn = _text_fn(host, request)
        return lambda event: sse.transform_json(event, lambda data: map_text(data, fn)).encode()

    class _SSEStream(http.SyncByteStream):  # type: ignore[misc,name-defined]
        def __init__(self, inner: Any, apply: Callable[[sse.Event], bytes]) -> None:
            self.inner, self.apply = inner, apply

        def __iter__(self) -> Iterator[bytes]:
            parser = sse.Parser()
            for chunk in self.inner:
                yield from (self.apply(e) for e in parser.feed(chunk))
            yield from (self.apply(e) for e in parser.flush())

        def close(self) -> None:
            self.inner.close()

    class _AsyncSSEStream(http.AsyncByteStream):  # type: ignore[misc,name-defined]
        def __init__(self, inner: Any, apply: Callable[[sse.Event], bytes]) -> None:
            self.inner, self.apply = inner, apply

        async def __aiter__(self) -> AsyncIterator[bytes]:
            parser = sse.Parser()
            async for chunk in self.inner:
                for event in parser.feed(chunk):
                    yield self.apply(event)
            for event in parser.flush():
                yield self.apply(event)

        async def aclose(self) -> None:
            await self.inner.aclose()

    class ChaosTransport(http.BaseTransport):  # type: ignore[misc,name-defined]
        def __init__(self, wrapped: httpx.BaseTransport | None = None, *, mode: str = "text") -> None:
            if mode not in ("text", "raw"):
                raise ValueError("mode must be 'text' or 'raw'")
            self.wrapped = wrapped or http.HTTPTransport()
            self.mode = mode

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            short_circuit, extra = _before(request)
            if short_circuit is not None:
                return short_circuit
            for _ in range(extra):
                self.wrapped.handle_request(request).read()
            response = self.wrapped.handle_request(request)
            if _is_stream(response, self.mode):
                stream = _SSEStream(response.stream, _event_fn(request.url.host, request))
                return http.Response(response.status_code, headers=_headers(response), stream=stream, request=request)
            return _after(request, response, response.read(), self.mode)

        def close(self) -> None:
            self.wrapped.close()

    class AsyncChaosTransport(http.AsyncBaseTransport):  # type: ignore[misc,name-defined]
        def __init__(self, wrapped: httpx.AsyncBaseTransport | None = None, *, mode: str = "text") -> None:
            if mode not in ("text", "raw"):
                raise ValueError("mode must be 'text' or 'raw'")
            self.wrapped = wrapped or http.AsyncHTTPTransport()
            self.mode = mode

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            short_circuit, extra = _before(request)
            if short_circuit is not None:
                return short_circuit
            for _ in range(extra):
                await (await self.wrapped.handle_async_request(request)).aread()
            response = await self.wrapped.handle_async_request(request)
            if _is_stream(response, self.mode):
                stream = _AsyncSSEStream(response.stream, _event_fn(request.url.host, request))
                return http.Response(response.status_code, headers=_headers(response), stream=stream, request=request)
            return _after(request, response, await response.aread(), self.mode)

        async def aclose(self) -> None:
            await self.wrapped.aclose()

    return ChaosTransport, AsyncChaosTransport


try:
    import httpx as _httpx
except ImportError:  # pragma: no cover - httpx2-only environments use agentic_chaos.integrations.httpx2
    pass
else:
    ChaosTransport, AsyncChaosTransport = build(_httpx)
