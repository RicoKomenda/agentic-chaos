# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/),
and the project uses [Semantic Versioning](https://semver.org/) (see [docs/versioning.md](docs/versioning.md)).

## [Unreleased]

### Added

- Core: sessions, traces, seeded fault selection, sync and async instrumentation for tools, models, memory,
  security controls, agents and payments; `runtime.suspended()` for setup outside chaos.
- Faults: reliability (`latency`, `timeout`, `error`, `rate_limit`, `empty`, `truncate`, `corrupt_json`,
  `timeout_after_commit`), security (`inject_instruction`, `poison_tool_description`, `poison_memory`, `flood`,
  `patch`, `control_outage`, `force_verdict`, `auth_error`, `replay`, `duplicate`), agents (`spoof_agent_card`)
  and MCP (`shadow_tool`, `mcp_sampling`, `mcp_elicitation`, `mcp_list_changed_flood`).
- Probes for security invariants, resilience, availability, detection, consumption (`tokens_within`,
  `cost_within`), MCP, multi-agent and AP2; model-graded `judge` probes with `judges.OpenAICompatibleJudge`.
- Statistics: per-probe pass-rate thresholds with Wilson confidence intervals and `require_confidence`.
- Experiment file format `agentic-chaos/v1` with validation, readable errors and a generated JSON Schema
  (`schema/agentic-chaos.v1.schema.json`); CLI `run`, `validate`, `schema`, `faults`, `probes`, `mcp-proxy`.
- MCP chaos proxy for stdio and Streamable HTTP, for both handshake-era and 2026-07-28 clients.
- Transports for model providers and A2A (`httpx` and `httpx2`), including streaming, auth errors and
  duplicate delivery; A2A 0.3, 1.0 and HTTP+JSON.
- Demo targets (`mailbot`, `mcp_demo`, `shopper`), a catalog of 28 experiments and 2 MCP proxy configurations, an interop suite against the
  official MCP, A2A, OpenAI and Anthropic SDKs, and the DVMA case study.
- `reroute` fault (region failover, model fallback); probes `hosts_within`, `models_within`,
  `control_invoked`, `approved_before`; recipes for ASI05, ASI09 and ASI10.
- Platform support: Windows, macOS, Python 3.10-3.14.
- Public API defined by `__all__`, `py.typed`, strict type checking.
- Safety: reports and proxy traces are redacted by default (`--no-redact`, `--redact-pattern`); kill switch
  (`AGENTIC_CHAOS_DISABLED`, `AGENTIC_CHAOS_KILL_FILE`, `runtime.disable()`); HTTP proxy request limits,
  chunked request bodies and loopback-only binding (`--allow-remote`); latency faults no longer block the
  event loop in async code (`runtime.aintercept`).

### Deprecated

- `apiVersion: agentic-chaos/v1alpha1`: use `agentic-chaos/v1` (identical structure).
