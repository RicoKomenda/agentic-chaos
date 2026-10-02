import asyncio
import json
import subprocess
import sys
from pathlib import Path

from agentic_chaos import faults
from agentic_chaos.mcp import McpChaosProxy
from agentic_chaos.runtime import Session, bound

ROOT = Path(__file__).parent.parent
SERVER = [sys.executable, str(ROOT / "examples/mcp_demo/server.py")]


async def exchange(*messages):
    replies = []
    async with McpChaosProxy(SERVER) as endpoint:
        for message in messages:
            await endpoint.send({"jsonrpc": "2.0", **message})
            if "id" not in message:
                continue
            while True:
                reply = await endpoint.recv()
                replies.append(reply)
                if reply.get("id") == message["id"]:
                    break
    return replies


def run(session, *messages):
    with bound(session):
        return asyncio.run(exchange(*messages))


def test_passthrough_without_faults():
    replies = run(Session(), {"id": 1, "method": "tools/list"})
    assert [t["name"] for t in replies[0]["result"]["tools"]] == ["fetch_page", "send_email"]


def test_rug_pull_changes_only_later_listings():
    session = Session([faults.PoisonToolDescription("*", after_calls=1)])
    first, second = run(session, {"id": 1, "method": "tools/list"}, {"id": 2, "method": "tools/list"})
    assert all("<IMPORTANT>" not in t["description"] for t in first["result"]["tools"])
    assert all("<IMPORTANT>" in t["description"] for t in second["result"]["tools"])


def test_tool_timeout_becomes_jsonrpc_error():
    session = Session([faults.Timeout("fetch_page")])
    (reply,) = run(session, {"id": 1, "method": "tools/call", "params": {"name": "fetch_page", "arguments": {}}})
    assert reply["error"]["code"] == -32001


def test_server_initiated_requests_are_sent_during_a_call():
    session = Session([faults.McpElicitation(), faults.McpListChangedFlood(count=2)])
    replies = run(session, {"id": 1, "method": "tools/call", "params": {"name": "fetch_page", "arguments": {}}})
    methods = [r.get("method") for r in replies]
    assert methods[:3] == ["elicitation/create", "notifications/tools/list_changed", "notifications/tools/list_changed"]


def test_stdio_cli(tmp_path):
    config = ROOT / "experiments/mcp/proxy/rug-pull.yaml"
    trace = tmp_path / "trace.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentic_chaos.cli",
            "mcp-proxy",
            "--faults",
            str(config),
            "--trace",
            str(trace),
            "--",
            *SERVER,
        ],
        input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(proc.stdout)["result"]["tools"]
    assert "PASS no_call_after_tool_change" in proc.stderr
    assert json.loads(trace.read_text())["trace"]["events"]
