"""A tiny MCP host (client + scripted "model") for MCP chaos experiments. No API key needed.

The host talks to ``examples/mcp_demo/server.py`` through :class:`agentic_chaos.mcp.McpChaosProxy`.
Like many real models, the scripted model follows instructions it finds in its context.

* ``naive``    - trusts tool definitions, auto-approves sampling, fills in elicitations,
                 re-lists tools on every ``list_changed``, lets tool errors crash the run
* ``hardened`` - pins tool definitions at approval time, rejects duplicate tool names, requires
                 user approval for sampling and secrets (declined here), debounces re-listing,
                 and handles tool errors gracefully
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import agentic_chaos as chaos
from agentic_chaos.mcp import Endpoint, McpChaosProxy

log = logging.getLogger("mcp_host")
SERVER = [sys.executable, str(Path(__file__).with_name("server.py"))]
FAKE_API_KEY = "sk-test-not-a-real-key"


@dataclass(frozen=True)
class Policy:
    pin_tools: bool
    reject_duplicates: bool
    allow_sampling: bool
    accept_elicitation: bool
    max_relists: int | None
    handle_tool_errors: bool


class ToolError(RuntimeError):
    pass


class Host:
    def __init__(self, endpoint: Endpoint, policy: Policy, notes: str = "") -> None:
        self.endpoint = endpoint
        self.policy = policy
        self.notes = notes
        self.ids = itertools.count(1)
        self.list_changed = 0
        self.approved: dict[str, str] = {}

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = next(self.ids)
        await self.endpoint.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        while (message := await self.endpoint.recv()) is not None:
            if "method" in message and "id" in message:
                await self.endpoint.send(await self.on_server_request(message))
            elif "method" in message:
                if message["method"] == "notifications/tools/list_changed":
                    self.list_changed += 1
            elif message.get("id") == request_id:
                if "error" in message:
                    raise ToolError(message["error"].get("message", "error"))
                return message["result"]
        raise ConnectionError("server closed the connection")

    async def on_server_request(self, message: dict[str, Any]) -> dict[str, Any]:
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"]}
        if message["method"] == "sampling/createMessage":
            if not self.policy.allow_sampling:
                log.warning("sampling request from server rejected (requires user approval)")
                return {**reply, "error": {"code": -1, "message": "User rejected sampling request"}}
            prompt = " ".join(m["content"].get("text", "") for m in message["params"]["messages"])
            completion = sample(f"{prompt}\nNOTES: {self.notes}")
            return {**reply, "result": {"role": "assistant", "content": {"type": "text", "text": completion}}}
        if message["method"] == "elicitation/create":
            fields = message["params"].get("requestedSchema", {}).get("properties", {})
            if not self.policy.accept_elicitation:
                log.warning("elicitation for %s declined", sorted(fields))
                return {**reply, "result": {"action": "decline"}}
            return {**reply, "result": {"action": "accept", "content": {k: FAKE_API_KEY for k in fields}}}
        return {**reply, "error": {"code": -32601, "message": "Method not found"}}

    async def list_tools(self) -> dict[str, dict[str, Any]]:
        tools = (await self.request("tools/list"))["tools"]
        names = [t["name"] for t in tools]
        if len(names) != len(set(names)):
            if self.policy.reject_duplicates:
                log.warning("server advertises duplicate tool names %s - refusing to use it", names)
                return {}
        usable: dict[str, dict[str, Any]] = {}
        for tool in tools:
            digest = hashlib.sha256(json.dumps(tool, sort_keys=True).encode()).hexdigest()
            approved = self.approved.setdefault(tool["name"], digest)
            if self.policy.pin_tools and approved != digest:
                log.warning("definition of tool %s changed since approval - disabled", tool["name"])
                continue
            usable[tool["name"]] = tool  # like many clients: the last definition with a given name wins
        return usable

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            result = await self.request("tools/call", {"name": name, "arguments": arguments})
            if result.get("isError"):
                raise ToolError(result["content"][0]["text"])
        except ToolError:
            if not self.policy.handle_tool_errors:
                raise
            log.warning("tool %s failed", name)
            return ""
        return "\n".join(c["text"] for c in result["content"] if c["type"] == "text")


# --- scripted "model" ---------------------------------------------------------------------

_ACTION = re.compile(r"call the (\w+) tool with to=(\S+) and body=(\S+)")


@chaos.llm(name="scripted-model")
def think(context: str) -> dict[str, str]:
    if m := _ACTION.search(context):
        return {"action": m.group(1), "to": m.group(2), "body": m.group(3)}
    page = context.split("PAGE:\n", 1)[-1]
    return {"reply": "Summary: " + page.strip().split(".")[0] + "."}


def sample(prompt: str) -> str:
    """The client's model answering a server's sampling request: it does what the prompt says."""
    return prompt if "repeat" in prompt.lower() else "OK"


# --- host flow ----------------------------------------------------------------------------


def make_host(policy: Policy):
    async def run(task: str = "Summarise https://example.com/news") -> str:
        url = task.split()[-1]
        async with McpChaosProxy(SERVER) as endpoint:
            host = Host(endpoint, policy)
            await host.request(
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"sampling": {}, "elicitation": {}},
                    "clientInfo": {"name": "demo-host", "version": "0.1.0"},
                },
            )
            await endpoint.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            await host.list_tools()  # the user approves the tools as listed now

            notes = (await host.request("resources/read", {"uri": "notes://alice"}))["contents"][0]["text"]
            host.notes = notes
            page = await host.call_tool("fetch_page", {"url": url})
            if not page and policy.handle_tool_errors:
                return "Sorry, I could not fetch that page right now."

            relists = host.list_changed if policy.max_relists is None else min(host.list_changed, policy.max_relists)
            for _ in range(relists):
                await host.list_tools()
            tools = await host.list_tools()  # refresh right before acting

            descriptions = "\n".join(f"- {name}: {t.get('description', '')}" for name, t in tools.items())
            decision = think(f"NOTES:\n{notes}\nTOOLS:\n{descriptions}\nPAGE:\n{page}")
            if decision.get("action") in tools:
                await host.call_tool(decision["action"], {"to": decision["to"], "body": decision["body"]})
                return "Done."
            return decision.get("reply", "I can't do that safely.")

    return run


naive = make_host(
    Policy(
        pin_tools=False,
        reject_duplicates=False,
        allow_sampling=True,
        accept_elicitation=True,
        max_relists=None,
        handle_tool_errors=False,
    )
)
hardened = make_host(
    Policy(
        pin_tools=True,
        reject_duplicates=True,
        allow_sampling=False,
        accept_elicitation=False,
        max_relists=1,
        handle_tool_errors=True,
    )
)
