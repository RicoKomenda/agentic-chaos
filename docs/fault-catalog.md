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
| `control` | a security control decision | `@chaos.control` |

## Faults

| Fault | Category | Default points | What it simulates |
| --- | --- | --- | --- |
| `latency` | reliability | llm.call, tool.call, memory.read, control | slow dependency (`seconds`, `jitter`) |
| `timeout` | reliability | llm.call, tool.call, memory.read, control | dependency never answers |
| `error` | reliability | llm.call, tool.call, memory.read, control | dependency raises / returns 5xx |
| `rate_limit` | reliability | llm.call, tool.call | HTTP 429, quota exhaustion |
| `empty` | reliability | llm.response, tool.result, memory.read | empty answer |
| `truncate` | reliability | llm.response, tool.result, memory.read | cut-off stream or context |
| `corrupt_json` | reliability | llm.response, tool.result | malformed structured output |
| `inject_instruction` | security | tool.result, memory.read | indirect prompt injection (`payload`, `sink`, `position`) |
| `poison_tool_description` | security | tool.describe | tool poisoning, e.g. a malicious MCP server |
| `poison_memory` | security | memory.read | memory or RAG poisoning |
| `control_outage` | security | control | guardrail, authz or approval service down (`mode`: timeout/error) |
| `force_verdict` | security | control | control silently returns a fixed verdict (bypass, misconfiguration, drift) |

Built-in payloads for injection faults: `exfiltrate`, `goal_hijack`, `authority`, `tool_poisoning`, or any custom
string with `{canary}` and `{sink}` placeholders.

## Mapping to risk taxonomies

References: OWASP Top 10 for Agentic Applications (ASI01-ASI10) and OWASP Top 10 for LLM Applications 2025 (LLM01-LLM10).

| Risk | Status | Faults / experiments |
| --- | --- | --- |
| ASI01 Agent Goal Hijack | available | `inject_instruction`, `asi01-indirect-prompt-injection`, `asi01-goal-hijack` |
| ASI02 Tool Misuse and Exploitation | available | `inject_instruction` with `sink`, `poison_tool_description` |
| ASI03 Identity and Privilege Abuse | partial | `force_verdict` on authz controls, `control-defense-in-depth` |
| ASI04 Agentic Supply Chain Vulnerabilities | partial | `poison_tool_description`; planned: MCP proxy with rug-pull |
| ASI05 Unexpected Code Execution | planned | sandbox escape canaries for code tools |
| ASI06 Memory and Context Poisoning | available | `poison_memory`, `asi06-memory-poisoning` |
| ASI07 Insecure Inter-Agent Communication | planned | message spoofing / replay at an `agent.message` point |
| ASI08 Cascading Failures | available | `timeout`, `error`, `rate_limit`, `latency`, `control_outage` |
| ASI09 Human-Agent Trust Exploitation | planned | approval fatigue and misleading summaries at an `approval` control |
| ASI10 Rogue Agents | planned | goal-drift probes across long sessions |
| LLM01 Prompt Injection | available | `inject_instruction`, `poison_memory`, `poison_tool_description` |
| LLM05 Improper Output Handling | partial | `corrupt_json`, `truncate` |
| LLM08 Vector and Embedding Weaknesses | partial | `poison_memory` |
| LLM10 Unbounded Consumption | available | `rate_limit`, `max_tool_calls`, `max_llm_calls` |
