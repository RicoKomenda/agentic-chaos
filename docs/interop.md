# Interoperability

Agentic Chaos is tested against official third-party SDKs, not just its own demo targets. The interop
suite lives in `tests/interop/` and runs in CI (job `interop`).

```bash
uv sync --group interop
uv run pytest -m interop
```

| Component | Versions tested | What is covered |
| --- | --- | --- |
| MCP Python SDK (`mcp`) client and server | 2.2 | stdio proxy and Streamable HTTP proxy; legacy handshake (2025-11-25) and 2026-07-28 protocol; rug pull, resource injection, elicitation, sampling, 401/403 challenges parsed by the SDK's OAuth helpers |
| MCP TypeScript reference server (`@modelcontextprotocol/server-everything`) | 2026.8.31 | stdio proxy, both protocol modes, tool-result injection (skipped without Node.js) |
| A2A Python SDK (`a2a-sdk`) client and server | 1.2 (protocol 1.0) | JSON-RPC and HTTP+JSON bindings, blocking and streaming; card spoofing, text injection, errors |
| OpenAI Python SDK | 3.24 (`httpx2`) | chat completions, streaming deltas, retries on 429, 401/403/timeout mapping, raw wire faults |
| Anthropic Python SDK | 1.11 (`httpx2`) | messages, retries on 429, 401/403/timeout mapping |
| LangChain / LangGraph | langchain-core 1.6, langgraph 1.2 | tool adapter, `ToolNode` in a compiled graph |
| OpenAI Agents SDK | 0.23 | `FunctionTool` adapter |
| Pydantic AI | 2.53 | tool adapter in an `Agent` driven by `TestModel` |

## Findings

Building the interop suite changed the library and surfaced behaviour worth knowing about:

1. **MCP 2026-07-28 replaced server-initiated requests during a call.** Servers now answer with an
   `InputRequiredResult` and the client retries the call with `inputResponses` (SEP-2322). The proxy
   detects the protocol version per request (`_meta`) and delivers injected sampling/elicitation in the
   style the client speaks. Without this, sampling and elicitation faults silently missed modern clients.
2. **The MCP SDK client does not pin tool definitions.** After a rug pull it calls the changed tool
   without re-approval, so `no_call_after_tool_change` fails against it. Hosts need their own pinning.
3. **A2A 1.0 renamed the JSON-RPC methods** (`SendMessage`, `SendStreamingMessage`, `GetTask`, ...)
   and added an HTTP+JSON binding (`/message:send`). The A2A transport now recognises 1.0, 0.3 and REST.
4. **Current OpenAI, Anthropic and MCP SDKs use `httpx2`**, not `httpx`. Transports are built per module:
   use `agentic_chaos.integrations.httpx2` for those SDKs.
5. **On an empty 200 response the OpenAI SDK raises a bare `json.JSONDecodeError`**, not `openai.APIError`.
   Applications that only catch `APIError` crash on a malformed provider or gateway response.
6. **Without an OAuth provider configured, the MCP SDK client surfaces a 401 as a generic error**
   (`MCPError -32603 "Server returned an error response"`); the status and challenge are not visible to
   the application. With an OAuth provider, the challenge fields the SDK reads (`error`, `scope`,
   `resource_metadata`) are present in the proxy's responses.

7. **LangGraph 1.2's `ToolNode` re-raises tool errors by default.** A single injected tool timeout ends the
   whole graph run unless `handle_tool_errors` is configured; earlier versions turned errors into tool messages.

## Version policy

The interop suite pins nothing: CI installs the latest releases, so a breaking change in an SDK shows up
as a failing interop job rather than as a silent gap. When an SDK changes its wire behaviour, add a test
for the new behaviour and keep the old one while the previous version is still in common use.
