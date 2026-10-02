# Public API

Everything listed in a module's `__all__` is public and covered by the [versioning policy](versioning.md).
Anything else, including modules whose name starts with an underscore, is internal and may change without
notice. `tests/public_api.json` is a snapshot of the public surface; changing it is a deliberate act.

| Module | What it is for |
| --- | --- |
| `agentic_chaos_security` | the everyday API: `Experiment`, `Verdict`, `ExperimentResult`, `ProbeStats`, instrumentation decorators (`tool`, `llm`, `memory`, `control`, `agent`, `payment`), `describe_tool`, `discover_agent`, `output`, `intercept`, `suspended`, and the `faults`, `probes`, `ap2` modules |
| `agentic_chaos_security.faults` | fault classes, the `FAULTS` registry and `build`; errors raised by faults (`ChaosError`, `ChaosTimeout`, `ChaosRateLimit`, `ChaosAuthError`); `Override` and `Repeat` for integration authors |
| `agentic_chaos_security.probes` | probe factories, `PROBES` registry, `build`, `expect`, `custom`, `Probe`, `ProbeResult` |
| `agentic_chaos_security.ap2` | AP2 recording helpers and payment probes |
| `agentic_chaos_security.experiment` | `Experiment`, `ExperimentResult`, `RunResult`, `ProbeStats`, `Verdict` |
| `agentic_chaos_security.loader` | `load`, `from_dict`, `read`, `expand`, `load_proxy_config`, `resolve`, `ValidationError` |
| `agentic_chaos_security.schema` | `validate`, `json_schema`, `API_VERSION`, `DEPRECATED_VERSIONS`, signature introspection |
| `agentic_chaos_security.runtime` | sessions and traces for custom integrations: `Session`, `Trace`, `Event`, `bound`, `current`, `intercept`, `record`, `suspended`, `POINTS` |
| `agentic_chaos_security.inject` | the instrumentation decorators (re-exported at top level) |
| `agentic_chaos_security.payloads` | built-in canary payloads, `render`, `new_canary` |
| `agentic_chaos_security.stats` | `wilson_interval`, `runs_needed` |
| `agentic_chaos_security.judges` | `OpenAICompatibleJudge`, `parse_verdict` |
| `agentic_chaos_security.mcp` | `McpChaosProxy` (stdio), `Endpoint`, `StdioEndpoint`; `mcp.http.McpHttpProxy`; `mcp.core` for custom transports |
| `agentic_chaos_security.integrations.httpx` / `httpx2` / `a2a` | provider and A2A transports, `build(module)` for other httpx-compatible clients |
| `agentic_chaos_security.cli` | `main(argv)` |

## Extension points

- **Custom faults**: subclass `faults.Fault` and set `kind`, `points` and `apply()`. Subclasses register
  themselves in `FAULTS`, so experiment files and the validator know them as soon as the module is imported.
- **Custom probes**: add a factory to `probes.PROBES` (see `tests/custom_probe_target.py`). Experiment files may
  use it if the target's module imports the module that registers it.
- **Custom instrumentation**: call `runtime.intercept(point, name, value)` and `runtime.record(kind, name, ...)`.
  Translate the errors listed under `faults` into what your transport would really see.

## Stable non-Python surfaces

These are part of the contract as well: the experiment file format (`agentic-chaos/v1`,
`schema/agentic-chaos.v1.schema.json`), injection point names, trace event kinds that probes read, CLI
commands, flags and exit codes (`0` held, `1` weakness, `2` inconclusive or invalid input), and the fields of
JSON reports.
