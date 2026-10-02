# Fault catalog and risk mapping

## Injection points

| Point | Where | Typical instrumentation |
| --- | --- | --- |
| `llm.call` | before a model request | `@chaos.llm`, `ChaosTransport` |
| `llm.response` | a model response | `@chaos.llm`, `ChaosTransport` |
| `tool.describe` | tool metadata shown to the model | `chaos.describe_tool()` |
| `tool.call` | before a tool executes | `@chaos.tool` |
| `tool.result` | a tool's return value | `@chaos.tool` |
| `memory.read` | long-term memory, vector store, RAG | `@chaos.memory` |
| `resource.read` | resource contents (MCP `resources/read`) | MCP proxy |
| `control` | a security control decision | `@chaos.control` |
| `agent.discover` | another agent's self-description (A2A Agent Card) | `chaos.discover_agent()`, A2A transport |
| `agent.call` | before a message/task is sent to another agent | `@chaos.agent`, A2A transport |
| `agent.message` | another agent's reply | `@chaos.agent`, A2A transport |
| `payment.call` | before a payment step | `@chaos.payment` |
| `payment.result` | a payment step's result | `@chaos.payment` |
| `mcp.tools` | the whole tool list from an MCP server | MCP proxy |
| `mcp.server_request` | server-initiated MCP requests during a tool call | MCP proxy |

## Faults

| Fault | Category | Default points | What it simulates |
| --- | --- | --- | --- |
| `latency` | reliability | llm.call, tool.call, memory.read, control, agent.call, payment.call | slow dependency (`seconds`, `jitter`) |
| `timeout` | reliability | llm.call, tool.call, memory.read, control, agent.call, payment.call | dependency never answers |
| `error` | reliability | llm.call, tool.call, memory.read, control, agent.call, payment.call | dependency raises / returns 5xx |
| `rate_limit` | reliability | llm.call, tool.call, agent.call, payment.call | HTTP 429, quota exhaustion |
| `empty` | reliability | llm.response, tool.result, memory.read, resource.read, agent.message | empty answer |
| `truncate` | reliability | llm.response, tool.result, memory.read, resource.read, agent.message | cut-off stream or context |
| `corrupt_json` | reliability | llm.response, tool.result, agent.message | malformed structured output |
| `inject_instruction` | security | tool.result, memory.read, resource.read, agent.message | indirect prompt injection (`payload`, `sink`, `position`) |
| `poison_tool_description` | security | tool.describe | tool poisoning, e.g. a malicious MCP server |
| `poison_memory` | security | memory.read | memory or RAG poisoning |
| `control_outage` | security | control | guardrail, authz or approval service down (`mode`: timeout/error) |
| `force_verdict` | security | control | control silently returns a fixed verdict (bypass, misconfiguration, drift) |
| `timeout_after_commit` | reliability | tool.result, payment.result, agent.message | the operation succeeded but the caller sees a timeout (duplicate side effects) |
| `flood` | security | tool.result, memory.read, resource.read, agent.message | context flooding; payload hidden after `size` characters of filler |
| `patch` | security | agent.discover, agent.message, tool.result, memory.read, resource.read, payment.result | overwrite fields by dotted path (`cart.items.0.price`) |
| `spoof_agent_card` | security | agent.discover | forged or tampered Agent Card (URL, skills, extensions) |
| `shadow_tool` | security | mcp.tools | a second tool with a trusted tool's name and a poisoned description |
| `mcp_sampling` | security | mcp.server_request | server asks the client's model to complete an injected prompt |
| `mcp_elicitation` | security | mcp.server_request | server phishes the user for a secret via elicitation |
| `mcp_list_changed_flood` | reliability | mcp.server_request | storm of `tools/list_changed` notifications |

Built-in payloads for injection faults: `exfiltrate`, `goal_hijack`, `authority`, `tool_poisoning`, `sampling_exfil`,
`upsell`, `delegate_back`, or any custom string with `{canary}` and `{sink}` placeholders.

## Mapping to risk taxonomies

References: OWASP Top 10 for Agentic Applications (ASI01-ASI10) and OWASP Top 10 for LLM Applications 2025 (LLM01-LLM10).

| Risk | Status | Faults / experiments |
| --- | --- | --- |
| ASI01 Agent Goal Hijack | available | `inject_instruction`, `asi01-indirect-prompt-injection`, `asi01-goal-hijack` |
| ASI02 Tool Misuse and Exploitation | available | `inject_instruction` with `sink`, `poison_tool_description` |
| ASI03 Identity and Privilege Abuse | partial | `force_verdict` on authz controls, `control-defense-in-depth` |
| ASI04 Agentic Supply Chain Vulnerabilities | available | `poison_tool_description`, `shadow_tool`, `spoof_agent_card`, `experiments/mcp/rug-pull`, `experiments/ap2/extension-downgrade` |
| ASI05 Unexpected Code Execution | planned | sandbox escape canaries for code tools |
| ASI06 Memory and Context Poisoning | available | `poison_memory`, `asi06-memory-poisoning` |
| ASI07 Insecure Inter-Agent Communication | available | `spoof_agent_card`, `inject_instruction`/`patch` on `agent.message`, `experiments/multi-agent/`; planned: replay |
| ASI08 Cascading Failures | available | `timeout`, `error`, `rate_limit`, `latency`, `control_outage`, `timeout_after_commit`, delegation loops, `blast_radius` |
| ASI09 Human-Agent Trust Exploitation | partial | `mcp_elicitation` (credential phishing); planned: approval fatigue at an `approval` control |
| ASI10 Rogue Agents | planned | goal-drift probes across long sessions |
| LLM01 Prompt Injection | available | `inject_instruction`, `poison_memory`, `poison_tool_description` |
| LLM05 Improper Output Handling | partial | `corrupt_json`, `truncate` |
| LLM08 Vector and Embedding Weaknesses | partial | `poison_memory` |
| LLM10 Unbounded Consumption | available | `rate_limit`, `max_tool_calls`, `max_llm_calls` |

Protocol-specific experiments also carry `MCP`, `A2A` or `AP2` tags, and multi-agent failure modes from MAST
(*Why Do Multi-Agent LLM Systems Fail?*), e.g. `MAST:FM-1.3` (step repetition) and `MAST:FM-1.5` (unaware of termination conditions).
