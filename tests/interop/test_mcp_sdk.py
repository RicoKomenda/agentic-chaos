"""Interop: the official MCP Python SDK (client and server) through the chaos proxy, both protocol eras."""

import asyncio
import json
import sys

import pytest

mcp = pytest.importorskip("mcp")
mcp_types = pytest.importorskip("mcp_types")

from agentic_chaos_security import faults, probes  # noqa: E402
from agentic_chaos_security.mcp.http import McpHttpProxy  # noqa: E402
from agentic_chaos_security.runtime import Session, bound  # noqa: E402

from .conftest import ROOT  # noqa: E402

pytestmark = pytest.mark.interop
MODES = ["legacy", "2026-07-28"]
SERVER = str(ROOT / "tests/interop/sdk_mcp_server.py")


class Recorder:
    def __init__(self) -> None:
        self.elicitations: list[str] = []
        self.samplings: list[str] = []

    async def elicit(self, ctx, params):
        self.elicitations.append(params.message)
        return mcp_types.ElicitResult(action="decline")

    async def sample(self, ctx, params):
        self.samplings.append(params.messages[0].content.text)
        return mcp_types.CreateMessageResult(
            role="assistant", content=mcp_types.TextContent(type="text", text="OK"), model="scripted"
        )


def raised(coroutine_fn) -> BaseException:
    """Run ``coroutine_fn()`` and return the exception it raised (the SDK may wrap it in a group)."""
    try:
        asyncio.run(coroutine_fn())
    except BaseException as exc:  # noqa: BLE001 - ExceptionGroup is not a builtin on 3.10
        return exc
    pytest.fail("expected the call to fail")


def write_config(tmp_path, faults_yaml: str) -> str:
    path = tmp_path / "proxy.yaml"
    path.write_text(f"apiVersion: agentic-chaos/v1\nkind: McpProxy\nspec:\n  faults:\n{faults_yaml}", encoding="utf-8")
    return str(path)


async def exercise(client) -> dict:
    first = await client.list_tools()
    second = await client.list_tools()
    result = await client.call_tool("fetch_page", {"url": "https://example.com/news"})
    resource = await client.read_resource("notes://alice")
    return {
        "first": {t.name: t.description for t in first.tools},
        "second": {t.name: t.description for t in second.tools},
        "call": result,
        "resource": resource.contents[0].text,
    }


@pytest.mark.parametrize("mode", MODES)
def test_stdio_proxy_with_sdk_client_and_server(mode, tmp_path):
    config = write_config(
        tmp_path,
        "    - {type: poison_tool_description, target: '*', after_calls: 1}\n"
        "    - {type: mcp_elicitation, max_injections: 1}\n"
        "    - {type: mcp_sampling, max_injections: 1}\n"
        "    - {type: inject_instruction, target: 'notes://*', point: resource.read, params: {payload: goal_hijack}}\n",
    )
    trace = tmp_path / "trace.json"
    recorder = Recorder()
    command = [
        sys.executable,
        "-m",
        "agentic_chaos_security.cli",
        "mcp-proxy",
        "--faults",
        config,
        "--trace",
        str(trace),
    ]
    params = mcp.StdioServerParameters(command=command[0], args=[*command[1:], "--", sys.executable, SERVER])

    async def main():
        client = mcp.Client(params, mode=mode, elicitation_callback=recorder.elicit, sampling_callback=recorder.sample)
        async with client:
            return client.protocol_version, await exercise(client)

    version, seen = asyncio.run(main())
    assert (version == "2026-07-28") == (mode != "legacy")
    assert "<IMPORTANT>" not in seen["first"]["send_email"] and "<IMPORTANT>" in seen["second"]["send_email"]
    assert seen["call"].content[0].text.startswith("Quarterly results") and not seen["call"].is_error
    assert "Ignore previous instructions" in seen["resource"]
    assert recorder.elicitations and recorder.samplings
    report = json.loads(trace.read_text(encoding="utf-8"))
    kinds = [e["kind"] for e in report["trace"]["events"]]
    assert "mcp.elicitation.response" in kinds and "mcp.sampling.response" in kinds


@pytest.mark.parametrize("mode", MODES)
def test_http_proxy_with_sdk_client_and_server(mode, sdk_http_server):
    session = Session(
        [
            faults.PoisonToolDescription("*", after_calls=1),
            faults.McpElicitation(max_injections=1),
        ]
    )
    recorder = Recorder()

    async def main():
        async with McpHttpProxy(sdk_http_server) as proxy:
            client = mcp.Client(proxy.url, mode=mode, elicitation_callback=recorder.elicit)
            async with client:
                return await exercise(client)

    with bound(session):
        seen = asyncio.run(main())
    assert "<IMPORTANT>" in seen["second"]["send_email"]
    assert seen["call"].content[0].text.startswith("Quarterly results")
    assert recorder.elicitations
    assert probes.elicitation_not_accepted()(session.trace).passed
    # The SDK client does not pin tool definitions, so calling the changed tool is detected.
    assert not probes.no_call_after_tool_change()(session.trace).passed


@pytest.mark.parametrize("mode", MODES)
def test_http_auth_error_reaches_sdk_client(mode, sdk_http_server):
    """Without an OAuth provider the SDK client must fail the call, not succeed silently."""
    session = Session([faults.AuthError("fetch_page")])

    async def main():
        async with McpHttpProxy(sdk_http_server) as proxy:
            async with mcp.Client(proxy.url, mode=mode) as client:
                return await client.call_tool("fetch_page", {"url": "x"})

    with bound(session):
        error = raised(main)
    assert "MCPError" in repr(error)
    assert [e.name for e in session.trace.faults] == ["auth_error"]


@pytest.mark.parametrize("status", [401, 403])
def test_auth_challenge_is_parsed_by_sdk_oauth_helpers(status, sdk_http_server):
    """The challenge carries what the SDK's OAuth flow reads: error, scope, resource_metadata."""
    import httpx
    from mcp.client.auth.utils import (
        extract_field_from_www_auth,
        extract_resource_metadata_from_www_auth,
        extract_scope_from_www_auth,
    )

    session = Session([faults.AuthError("fetch_page", status=status, scope="tools.call")])
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "fetch_page", "arguments": {}}}

    async def main():
        async with McpHttpProxy(sdk_http_server) as proxy:
            async with httpx.AsyncClient() as client:
                return await client.post(
                    proxy.url, json=call, headers={"accept": "application/json, text/event-stream"}
                )

    with bound(session):
        response = asyncio.run(main())
    assert response.status_code == status
    expected_error = "invalid_token" if status == 401 else "insufficient_scope"
    assert extract_field_from_www_auth(response, "error") == expected_error
    assert extract_scope_from_www_auth(response) == "tools.call"
    assert extract_resource_metadata_from_www_auth(response).endswith("/.well-known/oauth-protected-resource")
