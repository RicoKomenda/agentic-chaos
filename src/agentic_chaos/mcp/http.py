"""MCP chaos proxy for the Streamable HTTP transport.

    client --HTTP--> McpHttpProxy (listens locally) --HTTP--> upstream MCP endpoint

Point the client at the proxy URL instead of the server. ``POST`` requests are mapped onto injection
points exactly like the stdio proxy (see :mod:`agentic_chaos.mcp.proxy`). JSON and SSE (``text/event-stream``)
responses are both supported. Server-initiated requests injected by faults (sampling, elicitation,
notification floods) are delivered on an SSE stream for the request in flight, as the specification
requires; the client's answers arrive as separate ``POST`` requests and are recorded, not forwarded.

``auth_error`` faults become real ``401``/``403`` responses with a ``WWW-Authenticate`` header, so the
client's OAuth handling (token refresh, step-up scopes, re-consent) is exercised.

``GET`` (server listen streams) and ``DELETE`` (session end) are passed through.

The proxy has no authentication of its own and forwards the client's credentials upstream, so bind it to
loopback (the default). Requests are bounded: body size (413), header size and count (431), and time to
send the request (408). Chunked request bodies are supported.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

try:
    import httpx
except ImportError as exc:  # pragma: no cover
    raise ImportError("install the httpx extra: pip install 'agentic-chaos[httpx]'") from exc

from agentic_chaos import _sse as sse
from agentic_chaos.mcp.core import McpChaosCore

__all__ = [
    "Limits",
    "McpHttpProxy",
]

log = logging.getLogger("agentic_chaos.mcp.http")

_HOP_BY_HOP = {"host", "content-length", "connection", "transfer-encoding", "keep-alive", "accept-encoding"}
_RESPONSE_HEADERS = {"content-type", "mcp-session-id", "mcp-protocol-version", "www-authenticate", "retry-after"}
_REASONS = {
    200: "OK",
    202: "Accepted",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    408: "Request Timeout",
    413: "Content Too Large",
    431: "Request Header Fields Too Large",
    502: "Bad Gateway",
}


class _HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Limits:
    """Bounds for incoming requests."""

    max_body: int = 10 * 1024 * 1024
    max_line: int = 16 * 1024
    max_headers: int = 100
    read_timeout: float = 30.0


class McpHttpProxy:
    def __init__(
        self,
        upstream: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 120.0,
        limits: Limits | None = None,
    ) -> None:
        self.upstream = upstream
        self.limits = limits or Limits()
        self.core = McpChaosCore()
        self.client = httpx.AsyncClient(transport=transport, timeout=timeout)
        parts = urlsplit(upstream)
        self.resource_metadata = f"{parts.scheme}://{parts.netloc}/.well-known/oauth-protected-resource"

    async def serve(self, host: str = "127.0.0.1", port: int = 0) -> asyncio.Server:
        self.server = await asyncio.start_server(self._handle, host, port)
        return self.server

    @property
    def url(self) -> str:
        host, port = self.server.sockets[0].getsockname()[:2]
        return f"http://{host}:{port}{urlsplit(self.upstream).path or '/'}"

    async def aclose(self) -> None:
        self.server.close()
        await self.server.wait_closed()
        await self.client.aclose()

    async def __aenter__(self) -> McpHttpProxy:
        await self.serve()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- request handling -------------------------------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                request = await asyncio.wait_for(_read_request(reader, self.limits), self.limits.read_timeout)
            except asyncio.TimeoutError:
                raise _HttpError(408, "request not received in time") from None
            if request is not None:
                await self._dispatch(*request, writer)
        except _HttpError as exc:
            await _send(
                writer, exc.status, {"content-type": "application/json"}, json.dumps({"error": str(exc)}).encode()
            )
        except Exception as exc:  # never let one broken exchange take the proxy down
            log.exception("proxy error")
            await _send(writer, 502, {"content-type": "application/json"}, json.dumps({"error": str(exc)}).encode())
        finally:
            writer.close()

    async def _dispatch(self, method: str, headers: dict[str, str], body: bytes, writer: asyncio.StreamWriter) -> None:
        forward = {k: v for k, v in headers.items() if k not in _HOP_BY_HOP}
        message = _json(body) if method == "POST" else None
        if not isinstance(message, dict):
            await self._passthrough(method, forward, body, writer)
            return
        if self.core.is_own_answer(message):
            await _send(writer, 202, {}, b"")
            return
        if "method" not in message or "id" not in message:
            await self._passthrough(method, forward, body, writer)
            return

        decision = await asyncio.to_thread(self.core.client_request, message)
        if decision.forward is not None:
            message = decision.forward
            body = json.dumps(message).encode()
        if decision.auth is not None:
            exc = decision.auth
            await _send(
                writer,
                exc.status,
                {"content-type": "application/json", "www-authenticate": exc.www_authenticate(self.resource_metadata)},
                json.dumps({"error": exc.error, "error_description": exc.description}).encode(),
            )
            return
        if decision.reply is not None and not decision.server_requests:
            await _send(writer, 200, {"content-type": "application/json"}, json.dumps(decision.reply).encode())
            return
        for duplicate in decision.duplicates:
            await self.client.post(self.upstream, headers=forward, content=json.dumps(duplicate).encode())

        streaming = bool(decision.server_requests)
        if streaming:
            await _head(writer, 200, {"content-type": "text/event-stream", "cache-control": "no-cache"})
            for request in decision.server_requests:
                writer.write(sse.encode_json(request))
            await writer.drain()

        params = message.get("params", {})

        async def transform(payload: Any) -> Any:
            if isinstance(payload, dict) and "method" not in payload and payload.get("id") == message["id"]:
                return await asyncio.to_thread(self.core.server_response, message["method"], params, payload)
            return payload

        async with self.client.stream("POST", self.upstream, headers=forward, content=body) as upstream:
            content_type = upstream.headers.get("content-type", "")
            passthrough_headers = {k: v for k, v in upstream.headers.items() if k.lower() in _RESPONSE_HEADERS}
            if upstream.status_code != 200 and not streaming:
                await _send(writer, upstream.status_code, passthrough_headers, await upstream.aread())
                return
            if "text/event-stream" in content_type:
                if not streaming:
                    await _head(writer, upstream.status_code, passthrough_headers)
                parser = sse.Parser()
                async for chunk in upstream.aiter_bytes():
                    for event in parser.feed(chunk):
                        writer.write(await _transform_event(event, transform))
                    await writer.drain()
                for event in parser.flush():
                    writer.write(await _transform_event(event, transform))
            else:
                payload = await transform(_json(await upstream.aread()))
                if streaming:
                    writer.write(sse.encode_json(payload))
                else:
                    await _send(writer, upstream.status_code, passthrough_headers, json.dumps(payload).encode())
            await writer.drain()

    async def _passthrough(
        self, method: str, headers: dict[str, str], body: bytes, writer: asyncio.StreamWriter
    ) -> None:
        async with self.client.stream(method, self.upstream, headers=headers, content=body or None) as upstream:
            response_headers = {k: v for k, v in upstream.headers.items() if k.lower() in _RESPONSE_HEADERS}
            await _head(writer, upstream.status_code, response_headers)
            async for chunk in upstream.aiter_bytes():
                writer.write(chunk)
                await writer.drain()


async def _transform_event(event: sse.Event, transform: Any) -> bytes:
    try:
        payload = event.json()
    except ValueError:
        return event.encode()
    return sse.Event(json.dumps(await transform(payload)), event.event, event.id, event.extra).encode()


# --- minimal HTTP/1.1 plumbing (one exchange per connection) ---------------------------------


async def _readline(reader: asyncio.StreamReader, limits: Limits) -> bytes:
    try:
        line = await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError as exc:
        return exc.partial
    except asyncio.LimitOverrunError:
        raise _HttpError(431, "header line too long") from None
    if len(line) > limits.max_line:
        raise _HttpError(431, "header line too long")
    return line


async def _read_request(reader: asyncio.StreamReader, limits: Limits) -> tuple[str, dict[str, str], bytes] | None:
    request_line = await _readline(reader, limits)
    if not request_line.strip():
        return None
    method = request_line.decode("latin-1").split(" ", 1)[0].upper()
    headers: dict[str, str] = {}
    while (line := await _readline(reader, limits)) not in (b"\r\n", b"\n", b""):
        if len(headers) >= limits.max_headers:
            raise _HttpError(431, "too many header fields")
        key, _, value = line.decode("latin-1").partition(":")
        headers[key.strip().lower()] = value.strip()
    if "chunked" in headers.get("transfer-encoding", "").lower():
        return method, headers, await _read_chunked(reader, limits)
    try:
        length = int(headers.get("content-length", "0") or 0)
    except ValueError:
        raise _HttpError(400, "invalid content-length") from None
    if length < 0:
        raise _HttpError(400, "invalid content-length")
    if length > limits.max_body:
        raise _HttpError(413, f"body larger than {limits.max_body} bytes")
    body = await reader.readexactly(length) if length else b""
    return method, headers, body


async def _read_chunked(reader: asyncio.StreamReader, limits: Limits) -> bytes:
    body = bytearray()
    while True:
        size_line = (await _readline(reader, limits)).split(b";", 1)[0].strip()
        try:
            size = int(size_line, 16)
        except ValueError:
            raise _HttpError(400, "invalid chunk size") from None
        if size == 0:
            while (await _readline(reader, limits)) not in (b"\r\n", b"\n", b""):
                pass  # trailers
            return bytes(body)
        if len(body) + size > limits.max_body:
            raise _HttpError(413, f"body larger than {limits.max_body} bytes")
        body += await reader.readexactly(size)
        await _readline(reader, limits)  # CRLF after the chunk


async def _head(writer: asyncio.StreamWriter, status: int, headers: dict[str, str]) -> None:
    lines = [f"HTTP/1.1 {status} {_REASONS.get(status, 'Status')}"]
    lines += [f"{k}: {v}" for k, v in headers.items() if k.lower() not in ("content-length", "transfer-encoding")]
    lines.append("connection: close")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
    await writer.drain()


async def _send(writer: asyncio.StreamWriter, status: int, headers: dict[str, str], body: bytes) -> None:
    lines = [f"HTTP/1.1 {status} {_REASONS.get(status, 'Status')}"]
    lines += [f"{k}: {v}" for k, v in headers.items() if k.lower() not in ("content-length", "transfer-encoding")]
    lines += [f"content-length: {len(body)}", "connection: close"]
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
    await writer.drain()


def _json(body: bytes) -> Any:
    try:
        return json.loads(body) if body else None
    except ValueError:
        return None
