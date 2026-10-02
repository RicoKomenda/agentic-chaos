"""Safety of the tool itself: redaction, kill switch, proxy request limits and binding."""

import asyncio
import json
import socket

import httpx
import pytest

import agentic_chaos as chaos
from agentic_chaos import Experiment, faults, probes, runtime
from agentic_chaos.cli import _is_loopback, main
from agentic_chaos.mcp.http import Limits, McpHttpProxy
from agentic_chaos.redact import Redactor, redact
from agentic_chaos.runtime import Session, bound

SECRET = "sk-live-0123456789abcdefABCDEF"


def test_redacts_secret_keys_and_patterns_but_not_canaries_or_usage():
    data = {
        "headers": {"Authorization": "Bearer abcdefghijklmnop", "x-api-key": "k1", "mcp-session-id": "s1"},
        "body": f"use {SECRET} and AKIAABCDEFGHIJKLMNOP then reply AC-CANARY-0000beef",
        "usage": {"input_tokens": 12, "output_tokens": 3, "max_tokens": 50},
        "password": "",
    }
    out = redact(data)
    assert out["headers"] == {"Authorization": "[REDACTED]", "x-api-key": "[REDACTED]", "mcp-session-id": "[REDACTED]"}
    assert SECRET not in out["body"] and "[REDACTED:api-key]" in out["body"]
    assert "[REDACTED:aws-access-key]" in out["body"] and "AC-CANARY-0000beef" in out["body"]
    assert out["usage"] == data["usage"] and out["password"] == ""


def test_custom_patterns_and_keys():
    redactor = Redactor(extra_keys=["customer_iban"], extra_patterns=[r"ACME-\d{6}"])
    assert redactor({"customer_iban": "DE00", "note": "order ACME-123456"}) == {
        "customer_iban": "[REDACTED]",
        "note": "order [REDACTED:custom-0]",
    }


def test_reports_are_redacted_by_default():
    @chaos.tool(name="call_api")
    def call_api(token):
        return "ok"

    result = Experiment(
        name="r",
        target=lambda: call_api(SECRET),
        faults=[faults.Latency("call_api", seconds=0)],
        probes=[probes.no_unhandled_error()],
    ).run()
    assert SECRET not in json.dumps(result.to_dict(include_traces=True))
    assert SECRET in json.dumps(result.to_dict(include_traces=True, redactor=False))


def test_cli_refuses_to_run_while_switched_off(monkeypatch, capsys):
    monkeypatch.setenv(runtime.DISABLE_ENV, "1")
    assert main(["run", "experiments/asi08-tool-outage.yaml"]) == 2
    assert "switched off" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("host", "loopback"),
    [("127.0.0.1", True), ("::1", True), ("[::1]", True), ("localhost", True), ("0.0.0.0", False), ("10.0.0.5", False)],
)
def test_loopback_detection(host, loopback):
    assert _is_loopback(host) is loopback


def test_cli_refuses_remote_bind_without_flag(tmp_path):
    config = tmp_path / "p.yaml"
    config.write_text("apiVersion: agentic-chaos/v1\nkind: McpProxy\nspec:\n  faults: []\n")
    with pytest.raises(SystemExit, match="refusing to listen"):
        main(["mcp-proxy", "--faults", str(config), "--upstream", "http://127.0.0.1:1/mcp", "--listen", "0.0.0.0:0"])


# --- HTTP proxy limits ------------------------------------------------------------------------


def upstream():
    def respond(request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": message.get("id"), "result": {"echo": len(request.content)}}
        )

    return httpx.MockTransport(respond)


async def raw_exchange(proxy: McpHttpProxy, payload: bytes, pause: float = 0.0) -> bytes:
    host, port = proxy.server.sockets[0].getsockname()[:2]
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(payload)
    await writer.drain()
    if pause:
        await asyncio.sleep(pause)
    data = await reader.read()
    writer.close()
    return data


def exchange(payload: bytes, limits: Limits, pause: float = 0.0) -> bytes:
    async def main_():
        async with McpHttpProxy("http://up.test/mcp", transport=upstream(), limits=limits) as proxy:
            return await raw_exchange(proxy, payload, pause)

    with bound(Session()):
        return asyncio.run(main_())


def request(body: bytes, extra_headers: str = "") -> bytes:
    head = f"POST /mcp HTTP/1.1\r\nhost: x\r\ncontent-type: application/json\r\ncontent-length: {len(body)}\r\n"
    return (head + extra_headers + "\r\n").encode() + body


BODY = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()


def test_body_limit():
    assert exchange(request(b"x" * 2000), Limits(max_body=1000)).startswith(b"HTTP/1.1 413")


def test_header_limits():
    many = "".join(f"x-h{i}: v\r\n" for i in range(20))
    assert exchange(request(BODY, many), Limits(max_headers=10)).startswith(b"HTTP/1.1 431")
    assert exchange(request(BODY, f"x-long: {'a' * 500}\r\n"), Limits(max_line=200)).startswith(b"HTTP/1.1 431")


def test_chunked_request_body():
    chunks = f"{len(BODY[:10]):x}\r\n".encode() + BODY[:10] + f"\r\n{len(BODY[10:]):x}\r\n".encode() + BODY[10:]
    payload = b"POST /mcp HTTP/1.1\r\nhost: x\r\ntransfer-encoding: chunked\r\n\r\n" + chunks + b"\r\n0\r\n\r\n"
    response = exchange(payload, Limits())
    assert response.startswith(b"HTTP/1.1 200") and b'"echo"' in response


def test_slow_request_times_out():
    assert exchange(b"POST /mcp HTTP/1.1\r\nhost: x\r\n", Limits(read_timeout=0.2), pause=0.5).startswith(
        b"HTTP/1.1 408"
    )


def test_proxy_binds_loopback_by_default():
    async def main_():
        async with McpHttpProxy("http://up.test/mcp", transport=upstream()) as proxy:
            return proxy.server.sockets[0].getsockname()[0]

    assert asyncio.run(main_()) == "127.0.0.1"
    assert socket.gethostbyname("localhost").startswith("127.")
