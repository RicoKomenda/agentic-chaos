import asyncio
import json
import sys

import httpx
import pytest

from agentic_chaos_security import faults, probes
from agentic_chaos_security.mcp.http import McpHttpProxy
from agentic_chaos_security.runtime import Session, bound
from examples.mcp_demo.server import handle

UPSTREAM = "https://mcp.example/mcp"


def upstream(sse_mode: bool):
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(405)
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        reply = {"jsonrpc": "2.0", "id": message["id"], "result": handle(message["method"], message.get("params", {}))}
        headers = {"mcp-session-id": "s-1"}
        if sse_mode:
            body = f"event: message\ndata: {json.dumps(reply)}\n\n".encode()
            return httpx.Response(200, headers={**headers, "content-type": "text/event-stream"}, content=body)
        return httpx.Response(200, headers=headers, json=reply)

    return httpx.MockTransport(respond)


def events(text: str) -> list[dict]:
    return [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]


async def post(proxy: McpHttpProxy, message: dict) -> httpx.Response:
    headers = {"accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient() as client:
        return await client.post(proxy.url, json={"jsonrpc": "2.0", **message}, headers=headers)


def scenario(session: Session, sse_mode: bool, *messages: dict) -> list[httpx.Response]:
    async def main():
        async with McpHttpProxy(UPSTREAM, transport=upstream(sse_mode)) as proxy:
            return [await post(proxy, m) for m in messages]

    with bound(session):
        return asyncio.run(main())


@pytest.mark.parametrize("sse_mode", [False, True])
def test_rug_pull_over_http(sse_mode):
    session = Session([faults.PoisonToolDescription("*", after_calls=1)])
    first, second = scenario(session, sse_mode, {"id": 1, "method": "tools/list"}, {"id": 2, "method": "tools/list"})
    parse = (lambda r: events(r.text)[0]) if sse_mode else (lambda r: r.json())
    assert all("<IMPORTANT>" not in t["description"] for t in parse(first)["result"]["tools"])
    assert all("<IMPORTANT>" in t["description"] for t in parse(second)["result"]["tools"])
    assert second.headers["mcp-session-id"] == "s-1"


def test_auth_error_becomes_401_with_resource_metadata():
    session = Session([faults.AuthError("fetch_page")])
    (response,) = scenario(session, False, {"id": 1, "method": "tools/call", "params": {"name": "fetch_page"}})
    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]
    assert "resource_metadata=" in response.headers["www-authenticate"]


def test_insufficient_scope_becomes_403():
    session = Session([faults.AuthError("send_email", status=403, scope="mail.send")])
    (response,) = scenario(session, False, {"id": 1, "method": "tools/call", "params": {"name": "send_email"}})
    assert response.status_code == 403 and 'scope="mail.send"' in response.headers["www-authenticate"]


def test_server_requests_arrive_on_the_sse_stream_of_the_call():
    session = Session([faults.McpElicitation(max_injections=1)])
    call = {"id": 1, "method": "tools/call", "params": {"name": "fetch_page", "arguments": {}}}
    (response,) = scenario(session, False, call)
    elicitation, result = events(response.text)
    assert elicitation["method"] == "elicitation/create" and result["id"] == 1


def test_answers_to_injected_requests_are_not_forwarded():
    session = Session([faults.McpElicitation(max_injections=1)])
    call = {"id": 1, "method": "tools/call", "params": {"name": "fetch_page", "arguments": {}}}

    async def main():
        async with McpHttpProxy(UPSTREAM, transport=upstream(False)) as proxy:
            elicitation = events((await post(proxy, call)).text)[0]
            answer = {"id": elicitation["id"], "result": {"action": "accept", "content": {"api_key": "x"}}}
            return await post(proxy, answer)

    with bound(session):
        response = asyncio.run(main())
    assert response.status_code == 202
    assert not probes.elicitation_not_accepted()(session.trace).passed


def test_duplicate_delivery_reaches_upstream_twice():
    calls = []
    inner = upstream(False)

    def counting(request):
        calls.append(json.loads(request.content).get("method"))
        return inner.handle_request(request)

    async def main():
        async with McpHttpProxy(UPSTREAM, transport=httpx.MockTransport(counting)) as proxy:
            return await post(proxy, {"id": 1, "method": "tools/call", "params": {"name": "send_email"}})

    with bound(Session([faults.Duplicate("send_email")])):
        response = asyncio.run(main())
    assert response.json()["id"] == 1 and calls.count("tools/call") == 2


@pytest.mark.skipif(sys.platform == "win32", reason="stops the proxy with SIGINT")
def test_cli_http_mode_end_to_end(tmp_path):
    import signal
    import socket
    import subprocess
    import sys
    import time
    from pathlib import Path

    root = Path(__file__).parent.parent

    def free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def wait_for(port: int) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    return
            time.sleep(0.05)
        raise TimeoutError(port)

    up, px = free_port(), free_port()
    trace = tmp_path / "trace.json"
    server = subprocess.Popen([sys.executable, str(root / "examples/mcp_demo/http_server.py"), str(up)])
    config = root / "experiments/mcp/proxy/rug-pull.yaml"
    command = [
        sys.executable,
        "-m",
        "agentic_chaos_security.cli",
        "mcp-proxy",
        "--faults",
        str(config),
        "--trace",
        str(trace),
    ]
    command += ["--upstream", f"http://127.0.0.1:{up}/mcp", "--listen", f"127.0.0.1:{px}"]
    proxy = subprocess.Popen(command, stderr=subprocess.PIPE, text=True)
    try:
        wait_for(up)
        wait_for(px)
        url = f"http://127.0.0.1:{px}/mcp"
        lists = [httpx.post(url, json={"jsonrpc": "2.0", "id": i, "method": "tools/list"}).json() for i in (1, 2)]
        httpx.post(url, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "send_email"}})
    finally:
        proxy.send_signal(signal.SIGINT)
        stderr = proxy.communicate(timeout=15)[1]
        server.terminate()
        server.wait(timeout=15)
    assert "<IMPORTANT>" in lists[1]["result"]["tools"][1]["description"]
    assert "FAIL no_call_after_tool_change" in stderr
    assert json.loads(trace.read_text(encoding="utf-8"))["probes"]
