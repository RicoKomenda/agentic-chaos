# Protocol layers: MCP, multi-agent / A2A, AP2

Agentic systems fail at the seams between protocols as much as inside a single agent. Agentic Chaos covers
three protocol layers. Each maps onto the same injection points, faults and probes as the rest of the library.

| Layer | How chaos gets in | Demo target | Catalog |
| --- | --- | --- | --- |
| Tools and data (MCP) | `McpChaosProxy`, in-process or `agentic-chaos-security mcp-proxy` | `examples/mcp_demo` | `experiments/mcp/` |
| Agent to agent (A2A, internal hand-offs) | `@chaos.agent`, `chaos.discover_agent()`, `A2AChaosTransport` | `examples/shopper` | `experiments/multi-agent/` |
| Payments (AP2) | `@chaos.payment`, `agentic_chaos_security.ap2` recording helpers | `examples/shopper` | `experiments/ap2/` |

```bash
uv run agentic-chaos-security run experiments/mcp                                          # naive demo host
uv run agentic-chaos-security run experiments/mcp --target examples.mcp_demo.host:hardened
uv run agentic-chaos-security run experiments/multi-agent experiments/ap2 --target examples.shopper.agent:hardened
```

## MCP

### Stand-alone proxy (any client, any server)

**stdio servers.** Put the proxy in front of the server in your client's MCP configuration. The client and the server need no changes:

```json
{
  "mcpServers": {
    "everything-under-chaos": {
      "command": "agentic-chaos-security",
      "args": ["mcp-proxy", "--faults", "experiments/mcp/proxy/rug-pull.yaml", "--trace", "chaos-trace.json",
               "--", "npx", "-y", "@modelcontextprotocol/server-everything"]
    }
  }
}
```

**Streamable HTTP servers.** Run the proxy locally and point the client at it instead of the server:

```bash
agentic-chaos-security mcp-proxy --faults experiments/mcp/proxy/malicious-server.yaml \
  --upstream https://mcp.example.com/mcp --listen 127.0.0.1:8765
# client now uses http://127.0.0.1:8765/mcp ; Ctrl-C prints probe results
```

JSON and SSE responses are supported. Injected server requests (sampling, elicitation, notifications) arrive on an
SSE stream for the request in flight, and the client's answers (separate `POST`s) are recorded, not forwarded.
`auth_error` faults return real `401`/`403` responses with an RFC 6750 `WWW-Authenticate` challenge that includes
`resource_metadata`, which exercises the client's OAuth handling (token refresh, step-up scopes, re-consent).
`GET` listen streams and `DELETE` pass through unchanged.

The proxy config is a `kind: McpProxy` file containing faults and the probes to evaluate when the session ends.
Results go to stderr and, with `--trace`, to a JSON file together with the full trace:

```yaml
apiVersion: agentic-chaos/v1
kind: McpProxy
spec:
  faults:
    - type: poison_tool_description
      target: "*"
      after_calls: 1          # first tools/list is clean, later ones are poisoned (rug pull)
  probes:
    - type: no_call_after_tool_change
    - type: canary_not_leaked
```

### In-process (inside experiments)

```python
from agentic_chaos_security.mcp import McpChaosProxy

async with McpChaosProxy(["python", "server.py"]) as endpoint:
    await endpoint.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    reply = await endpoint.recv()
```

See `examples/mcp_demo/host.py` for a complete host that answers server-initiated requests.

### Message mapping

| MCP traffic | Point (target) | Typical faults |
| --- | --- | --- |
| `tools/list` result, per tool | `tool.describe` (tool name) | `poison_tool_description` (+ `after_calls` = rug pull) |
| `tools/list` result, whole list | `mcp.tools` | `shadow_tool` |
| `tools/call` request | `tool.call` (tool name) | `timeout`, `error`, `rate_limit`, `latency`, `auth_error` (HTTP: 401/403, stdio: tool error), `duplicate` (delivered twice, client sees one answer) |
| during a `tools/call` | `mcp.server_request` (tool name) | `mcp_sampling`, `mcp_elicitation`, `mcp_list_changed_flood` |
| `tools/call` result | `tool.result` (tool name) | `inject_instruction`, `flood`, `truncate`, `empty`, `timeout_after_commit` |
| `resources/read` result | `resource.read` (URI) | `inject_instruction`, `flood` |

Server-initiated requests are only sent while a client request is in flight, as the 2026-07-28 specification
requires. Clients that wrongly accept unsolicited requests are a separate test.

MCP-specific probes: `no_call_after_tool_change`, `elicitation_not_accepted`, `max_events` (e.g. `mcp.request` /
`tools/list`). `canary_not_leaked` also inspects what the client sends back in sampling and elicitation responses.


## Multi-agent systems and A2A

Instrument calls to other agents and discovery of other agents:

```python
import agentic_chaos_security as chaos

card = chaos.discover_agent("merchant", fetched_card)      # point: agent.discover

@chaos.agent(name_arg="url")                                # target name = the URL being called
def send_task(url: str, message: dict) -> dict: ...         # points: agent.call, agent.message
```

For real A2A clients built on `httpx`, use the transport instead of decorators:

```python
from agentic_chaos_security.integrations.a2a import AsyncA2AChaosTransport

http_client = httpx.AsyncClient(transport=AsyncA2AChaosTransport())
```

It maps `/.well-known/agent-card.json` to `agent.discover`, JSON-RPC `message/*` and `tasks/*` to `agent.call`, and
text parts in results to `agent.message` (target name = host). Streaming responses (`message/stream`,
`tasks/resubscribe`) are transformed event by event while they stream; compressed streams pass through unchanged.
`auth_error` becomes `401`/`403` with a `WWW-Authenticate` challenge, and `duplicate` delivers the same task more than once.

Faults: `spoof_agent_card` / `patch` (forged or tampered cards), `inject_instruction` on `agent.message` (including the
`delegate_back` payload for delegation loops), `replay` (stale, replayed or reordered replies), `duplicate`
(at-least-once delivery), `auth_error` (expired or revoked credentials), `timeout` / `error` on `agent.call`.
Probes: `agent_not_contacted`, `max_agent_calls`, `blast_radius` (how many downstream agents, tools or payment
services a planted canary reached).

Experiments also carry MAST failure-mode tags (e.g. `MAST:FM-1.3` step repetition) next to the OWASP ASI tags.

## AP2 (Agent Payments Protocol)

AP2 secures signed mandates, but the context *before* signing (catalog data, tool results, A2A messages) is not
signed. Report the security-relevant moments from your shopping agent, then let the probes check the invariants:

```python
from agentic_chaos_security import ap2

ap2.record_intent({"max_amount": 20, "merchants": ["acme-merchant"], "skus": ["usb-c-cable"]})
ap2.record_review(cart)               # the cart the user saw and approved
ap2.record_payment_mandate(mandate)   # {"id": ..., "cart": {...}} about to be submitted
ap2.record_settlement(mandate_id, amount)   # where you observe real charges (processor webhook, ledger)
```

| Probe | Invariant |
| --- | --- |
| `cart_within_intent` | every Payment Mandate is within the Intent Mandate (merchant, SKUs, budget) |
| `cart_matches_reviewed` | the paid cart is exactly a cart the user reviewed |
| `max_settlements` | no duplicate charges (non-idempotent retries) |
| `payment_requires_extension` | no payment after discovering an agent that lacks the required AP2 extension |

Instrument payment steps with `@chaos.payment` (points `payment.call`, `payment.result`) to inject processor outages,
`timeout_after_commit` (the charge succeeded, but the agent sees a timeout) and `duplicate` (a replayed Payment Mandate).

The demo uses made-up merchants, prices and extension URIs. It never touches a real payment system.
