# MCP demo: server, host and chaos proxy

- `server.py`: a dependency-free MCP server (stdio) with `fetch_page`, `send_email` and a `notes://alice` resource.
- `http_server.py`: the same server over Streamable HTTP (`python examples/mcp_demo/http_server.py 8000`), for trying
  `agentic-chaos mcp-proxy --upstream http://127.0.0.1:8000/mcp`.
- `host.py`: a tiny MCP host with a scripted "model". It connects to the server through
  `agentic_chaos.mcp.McpChaosProxy`, so every experiment injects faults on the wire.

| Behaviour | `naive` | `hardened` |
| --- | --- | --- |
| Tool definitions | trusted as listed, last duplicate wins | pinned at approval; changed or duplicate tools disabled |
| Sampling requests | auto-approved | rejected (needs user approval) |
| Elicitation | auto-filled | declined |
| `list_changed` | re-list on every notification | re-list at most once |
| Tool errors | crash the run | graceful message |

```bash
uv run agentic-chaos run experiments/mcp --target examples.mcp_demo.host:naive
uv run agentic-chaos run experiments/mcp --target examples.mcp_demo.host:hardened
```
