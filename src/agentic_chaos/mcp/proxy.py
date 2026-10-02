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
For servers that speak Streamable HTTP, see :mod:`agentic_chaos.mcp.http`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from agentic_chaos.mcp.core import McpChaosCore


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
    """stdio transport: spawns the server as a subprocess and relays newline-delimited JSON-RPC."""

    def __init__(self, command: list[str], *, env: dict[str, str] | None = None) -> None:
        self.command = command
        self.env = env
        self.core = McpChaosCore()
        self._pending: dict[Any, tuple[str, dict[str, Any]]] = {}

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

    async def _pump_client(self) -> None:
        while (message := await self.downstream.read()) is not None:
            if self.core.is_own_answer(message):
                continue
            if "method" in message and "id" in message:
                # faults may sleep (latency); keep the event loop responsive
                decision = await asyncio.to_thread(self.core.client_request, message)
                message = decision.forward or message
                for request in decision.server_requests:
                    await self.downstream.write(request)
                if decision.reply is not None:
                    await self.downstream.write(decision.reply)
                    continue
                self._pending[message["id"]] = (message["method"], message.get("params", {}))
                for duplicate in decision.duplicates:
                    self._to_server(duplicate)
            self._to_server(message)
        if self.proc.stdin and not self.proc.stdin.is_closing():
            self.proc.stdin.close()

    async def _pump_server(self) -> None:
        assert self.proc.stdout is not None
        while line := await self.proc.stdout.readline():
            if not line.strip():
                continue
            message = json.loads(line)
            if self.core.is_dropped(message):
                continue
            if "method" not in message and message.get("id") in self._pending:
                method, params = self._pending.pop(message["id"])
                message = await asyncio.to_thread(self.core.server_response, method, params, message)
            await self.downstream.write(message)
        await self.downstream.write(None)

    def _to_server(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
