# Roadmap

This is a direction, not a commitment. Discussion happens in issues. The scenario research behind many of these
items, with priorities, is in [research/scenarios.md](research/scenarios.md#proposed-additions-to-agentic-chaos).

## Library

- [x] Core runtime: sessions, traces, seeded fault selection, sync and async instrumentation
- [x] Reliability and security faults, security invariant probes, baseline vs. chaos verdicts
- [x] YAML experiment format and CLI with CI-friendly exit codes
- [x] `httpx` transport for provider-level faults (OpenAI, Anthropic, LiteLLM, ...)
- [ ] Inter-agent messaging point (`agent.message`): spoofing, replay, delay (ASI07)
- [ ] Human-approval control helpers: approval fatigue, misleading summaries (ASI09)
- [ ] Multi-turn experiments and long-session goal-drift probes (ASI10)
- [ ] LLM-as-judge probes (pluggable evaluators)
- [ ] Fuzz mode: random fault combinations to explore, then promote findings to fixed experiments
- [ ] HTML report and trend comparison across runs

## Integrations

- [ ] **MCP chaos proxy**: a stdio/HTTP proxy between an agent and any MCP server: poisoned descriptions, rug-pull
      (tool definitions change after approval), slow or dropped responses, oversized results
- [ ] Framework adapters: LangGraph / LangChain, OpenAI Agents SDK, Anthropic Agent SDK, Pydantic AI, CrewAI, Google ADK, LlamaIndex
- [ ] OpenTelemetry: evaluate probes over GenAI semantic-convention traces; emit chaos spans
- [ ] pytest plugin (`@pytest.mark.chaos`) and a GitHub Action
- [ ] Kubernetes bridge (LitmusChaos / Chaos Mesh) for infrastructure faults on inference servers, vector databases and gateways
- [ ] Ready-made targets: intentionally vulnerable agent labs for training and demos

## Community

- [ ] Catalog of experiments derived from public AI incidents
- [ ] Game-day guide: running security chaos exercises with blue and red teams
- [ ] Mapping to further frameworks (MITRE ATLAS, NIST AI RMF)
