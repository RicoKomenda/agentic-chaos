# Roadmap

This is a direction, not a commitment. Discussion happens in issues. The scenario research behind many of these
items, with priorities, is in [research/scenarios.md](research/scenarios.md#proposed-additions-to-agentic-chaos).

## Library

- [x] Core runtime: sessions, traces, seeded fault selection, sync and async instrumentation
- [x] Reliability and security faults, security invariant probes, baseline vs. chaos verdicts
- [x] YAML experiment format and CLI with CI-friendly exit codes
- [x] `httpx` transport for provider-level faults (OpenAI, Anthropic, LiteLLM, ...)
- [x] Inter-agent points (`agent.discover`, `agent.call`, `agent.message`): card spoofing, injection, delegation loops (ASI07)
- [x] Message replay / reordering (`replay`) and duplicate delivery (`duplicate`); streaming (SSE) in the A2A transport
- [x] Payment points and AP2 probes (intent, review, duplicate charges, extension downgrade)
- [ ] Human-approval control helpers: approval fatigue, misleading summaries (ASI09)
- [ ] Multi-turn experiments and long-session goal-drift probes (ASI10)
- [x] Pass-rate thresholds with confidence intervals; availability, token and cost probes; LLM-as-judge probes
- [ ] Fuzz mode: random fault combinations to explore, then promote findings to fixed experiments
- [ ] HTML report and trend comparison across runs

## 1.0 readiness

- [x] Interop with official SDKs (MCP Python + TypeScript reference server, A2A, OpenAI, Anthropic) and the DVMA case study
- [x] Statistics: pass-rate thresholds, confidence intervals, availability/cost/judge probes
- [x] Contract: `agentic-chaos/v1` format, validation, JSON Schema, public API, strict typing, versioning policy
- [x] Safety of the tool: redaction, kill switch, proxy limits and binding, non-blocking latency
- [x] Platform support: Windows and macOS CI, Python 3.14
- [ ] Framework adapters, pytest plugin, GitHub Action, JUnit/HTML/OpenTelemetry output
- [ ] Release engineering: PyPI trusted publishing, signed provenance, docs site
- [x] Remaining risk coverage: `reroute`, residency/model/control/approval probes, recipes for ASI05/ASI09/ASI10

## Integrations

- [x] **MCP chaos proxy** (stdio): poisoned descriptions, rug pull, tool shadowing, sampling and elicitation abuse,
      `list_changed` floods, timeouts, oversized results
- [x] MCP proxy: Streamable HTTP transport; `auth_error` (401/403 with `WWW-Authenticate`) across MCP, A2A and providers
- [x] A2A `httpx` transport (Agent Card discovery, `message/*`, `tasks/*`)
- [ ] Framework adapters: LangGraph / LangChain, OpenAI Agents SDK, Anthropic Agent SDK, Pydantic AI, CrewAI, Google ADK, LlamaIndex
- [ ] OpenTelemetry: evaluate probes over GenAI semantic-convention traces; emit chaos spans
- [ ] pytest plugin (`@pytest.mark.chaos`) and a GitHub Action
- [ ] Kubernetes bridge (LitmusChaos / Chaos Mesh) for infrastructure faults on inference servers, vector databases and gateways
- [ ] Ready-made targets: intentionally vulnerable agent labs for training and demos

## Community

- [ ] Catalog of experiments derived from public AI incidents
- [ ] Game-day guide: running security chaos exercises with blue and red teams
- [ ] Mapping to further frameworks (MITRE ATLAS, NIST AI RMF)
