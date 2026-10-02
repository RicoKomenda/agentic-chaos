"""Interop: the official TypeScript reference server (server-everything) behind the stdio proxy."""

import asyncio
import json
import shutil
import sys

import pytest

mcp = pytest.importorskip("mcp")

pytestmark = [
    pytest.mark.interop,
    pytest.mark.skipif(shutil.which("npx") is None, reason="needs Node.js / npx"),
]
PACKAGE = "@modelcontextprotocol/server-everything"


@pytest.mark.parametrize("mode", ["legacy", "auto"])
def test_typescript_reference_server_through_proxy(mode, tmp_path):
    config = tmp_path / "proxy.yaml"
    config.write_text(
        "apiVersion: agentic-chaos/v1alpha1\nkind: McpProxy\nspec:\n  faults:\n"
        "    - {type: poison_tool_description, target: '*', after_calls: 1}\n"
        "    - {type: inject_instruction, target: echo, point: tool.result, params: {payload: goal_hijack}}\n"
    )
    trace = tmp_path / "trace.json"
    args = ["-m", "agentic_chaos.cli", "mcp-proxy", "--faults", str(config), "--trace", str(trace)]
    params = mcp.StdioServerParameters(command=sys.executable, args=[*args, "--", "npx", "-y", PACKAGE])

    async def main():
        async with mcp.Client(params, mode=mode, read_timeout_seconds=120) as client:
            first = await client.list_tools()
            second = await client.list_tools()
            echoed = await client.call_tool("echo", {"message": "hello"})
            return first, second, echoed

    first, second, echoed = asyncio.run(main())
    names = {t.name for t in first.tools}
    assert "echo" in names
    assert all("<IMPORTANT>" not in (t.description or "") for t in first.tools)
    assert all("<IMPORTANT>" in (t.description or "") for t in second.tools)
    assert "hello" in echoed.content[0].text and "Ignore previous instructions" in echoed.content[0].text
    events = json.loads(trace.read_text())["trace"]["events"]
    assert any(e["kind"] == "fault" and e["name"] == "inject_instruction" for e in events)
