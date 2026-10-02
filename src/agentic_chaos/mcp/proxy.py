"""MCP chaos proxy: sits between an MCP client and an MCP server (stdio transport) and injects faults.

    client  <-->  McpChaosProxy  <-->  server subprocess

No changes to the client or server are needed. JSON-RPC messages are mapped onto the generic
injection points, so every fault in the library works at the protocol level:

=========================  ===================================================
MCP message                Injection point (target name)
=========================  ===================================================
``tools/list`` result      ``tool.describe`` (tool name) per tool, then ``mcp.tools`` (``tools/list``)
``tools/call`` request     ``tool.call`` (tool name), then ``mcp.server_request`` (tool name)
``tools/call`` result      ``tool.result`` (tool name), applied to the text content
``resources/read`` result  ``resource.read`` (resource URI), applied to text contents
=========================  ===================================================

Faults on ``mcp.server_request`` make the proxy act like a malicious server *while a client request
is in flight*: it sends ``sampling/createMessage`` or ``elicitation/create`` requests or notification
floods to the client and records how the client answers.

Use it in-process (``async with McpChaosProxy(cmd) as endpoint``) inside an experiment, or as a
stand-alone stdio proxy in front of any MCP client: ``agentic-chaos mcp-proxy --faults f.yaml -- cmd``.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import sys
from typing import Any

from agentic_chaos.faults import ChaosError, ChaosRateLimit, ChaosTimeout, Override
from agentic_chaos.runtime import intercept, record

REQUEST_TIMEOUT = -32001  # code used by MCP SDKs for request timeouts


class Endpoint:
    """In-memory duplex connection between an in-process MCP client and the proxy."""

    def __init__(self) -> None:
        self._to_proxy: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._to_client: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    # client side
    async def send(self, message: dict[str, Any]) -> None:
        await self._to_proxy.put(message)

    async def recv(self) -> dict[str, Any] | None:
        return await self._to_client.get()

    async def close(self) -> None:
        await self._to_proxy.put(None)

    # proxy side
    async def read(self) -> dict[str, Any] | None:
        return await self._to_proxy.get()

    async def write(self, message: dict[str, Any] | None) -> None:
        await self._to_client.put(message)


class StdioEndpoint:
    """The proxy's own stdin/stdout, for use as a stand-alone stdio MCP server."""

    async def open(self) -> StdioEndpoint:
        loop = asyncio.get_running_loop()
        self._reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(self._reader), sys.stdin)
        return self

    async def read(self) -> dict[str, Any] | None:
        while True:
            line = await self._reader.readline()
            if not line:
                return None
            if line.strip():
                return json.loads(line)

    async def write(self, message: dict[str, Any] | None) -> None:
        if message is not None:
            sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
            sys.stdout.flush()


class McpChaosProxy:
    def __init__(self, command: list[str], *, env: dict[str, str] | None = None) -> None:
        self.command = command
        self.env = env
        self._pending: dict[Any, tuple[str, dict[str, Any]]] = {}
        self._own: dict[str, str] = {}
        self._ids = itertools.count(1)

    async def start(self, downstream: Endpoint | StdioEndpoint | None = None) -> Any:
        self.downstream = downstream or Endpoint()
        self.proc = await asyncio.create_subprocess_exec(
            *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, env=self.env
        )
        self._client_task = asyncio.create_task(self._pump_client())
        self._server_task = asyncio.create_task(self._pump_server())
        return self.downstream

    async def wait(self) -> None:
        """Run until the client disconnects, then shut the server down."""
        await self._client_task
        await self.aclose()

    async def aclose(self) -> None:
        if self.proc.stdin and not self.proc.stdin.is_closing():
            self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            self.proc.kill()
            await self.proc.wait()
        for task in (self._client_task, self._server_task):
            task.cancel()
        await asyncio.gather(self._client_task, self._server_task, return_exceptions=True)

    async def __aenter__(self) -> Endpoint:
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        if isinstance(self.downstream, Endpoint):
            await self.downstream.close()
        await self.aclose()

    # --- client -> server -------------------------------------------------------------------

    async def _pump_client(self) -> None:
        while (message := await self.downstream.read()) is not None:
            if "method" not in message and message.get("id") in self._own:
                self._record_client_answer(message)
                continue
            if "method" in message and "id" in message:
                record("mcp.request", message["method"], params=message.get("params", {}))
                if message["method"] == "tools/call" and not await self._before_tool_call(message):
                    continue
                self._pending[message["id"]] = (message["method"], message.get("params", {}))
            self._to_server(message)
        if self.proc.stdin and not self.proc.stdin.is_closing():
            self.proc.stdin.close()

    async def _before_tool_call(self, message: dict[str, Any]) -> bool:
        """Apply call-time faults. Returns False if the proxy answered instead of the server."""
        params = message.get("params", {})
        name = params.get("name", "")
        record("tool.call", name, kwargs=params.get("arguments", {}))
        try:
            intercept("tool.call", name, None, arguments=params.get("arguments", {}))
        except Override as forced:
            await self._reply(message, result=_text_result(str(forced.value)))
            return False
        except ChaosTimeout as exc:
            await self._reply(message, error={"code": REQUEST_TIMEOUT, "message": str(exc)})
            return False
        except ChaosRateLimit as exc:
            await self._reply(message, result=_text_result(f"Error: {exc}", is_error=True))
            return False
        except ChaosError as exc:
            await self._reply(message, result=_text_result(f"Error: {exc}", is_error=True))
            return False
        for request in intercept("mcp.server_request", name, []) or []:
            await self._server_initiated(request)
        return True

    async def _server_initiated(self, request: dict[str, Any]) -> None:
        if request["method"].startswith("notifications/"):
            record("mcp.notification", request["method"])
            await self.downstream.write({"jsonrpc": "2.0", **request})
            return
        request_id = f"agentic-chaos-{next(self._ids)}"
        self._own[request_id] = request["method"]
        record("mcp.server_request", request["method"], params=request.get("params", {}))
        await self.downstream.write({"jsonrpc": "2.0", "id": request_id, **request})

    def _record_client_answer(self, message: dict[str, Any]) -> None:
        method = self._own.pop(message["id"])
        if method == "sampling/createMessage":
            record("mcp.sampling.response", method, result=message.get("result"), error=message.get("error"))
        elif method == "elicitation/create":
            result = message.get("result") or {}
            record(
                "mcp.elicitation.response", method, action=result.get("action", "error"), content=result.get("content")
            )

    # --- server -> client -------------------------------------------------------------------

    async def _pump_server(self) -> None:
        assert self.proc.stdout is not None
        while line := await self.proc.stdout.readline():
            if not line.strip():
                continue
            message = json.loads(line)
            if "method" not in message and message.get("id") in self._pending:
                method, params = self._pending.pop(message["id"])
                try:
                    message = self._transform(method, params, message)
                except ChaosTimeout as exc:
                    message = {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": REQUEST_TIMEOUT, "message": str(exc)},
                    }
            await self.downstream.write(message)
        await self.downstream.write(None)

    def _transform(self, method: str, params: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
        result = message.get("result")
        if not isinstance(result, dict):
            return message
        if method == "tools/list":
            tools = [dict(t) for t in result.get("tools", [])]
            for tool in tools:
                tool["description"] = intercept("tool.describe", tool["name"], tool.get("description", ""))
            tools = intercept("mcp.tools", "tools/list", tools)
            for tool in tools:
                record("tool.describe", tool["name"], description=tool.get("description", ""))
            result = {**result, "tools": tools}
        elif method == "tools/call":
            name = params.get("name", "")
            content = result.get("content", [])
            text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
            changed = intercept("tool.result", name, text)
            if changed != text:
                others = [c for c in content if c.get("type") != "text"]
                content = [
                    {"type": "text", "text": changed if isinstance(changed, str) else json.dumps(changed)},
                    *others,
                ]
                result = {**result, "content": content}
            record("tool.call.result", name, result=changed)
        elif method == "resources/read":
            contents = []
            for item in result.get("contents", []):
                if "text" in item:
                    item = {**item, "text": intercept("resource.read", item.get("uri", ""), item["text"])}
                    record("resource.read", item.get("uri", ""), text=item["text"])
                contents.append(item)
            result = {**result, "contents": contents}
        return {**message, "result": result}

    # --- helpers ----------------------------------------------------------------------------

    def _to_server(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())

    async def _reply(self, request: dict[str, Any], **body: Any) -> None:
        await self.downstream.write({"jsonrpc": "2.0", "id": request["id"], **body})


def _text_result(text: str, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}
