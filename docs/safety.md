# Running chaos safely

Chaos experiments deliberately make systems misbehave. Treat them like any other change with production impact.

## Progression

1. **Unit / CI**: stubbed tools and scripted or recorded models. No external side effects. This is where most experiments should live.
2. **Staging**: real models, sandboxed tools (test mailboxes, test payment accounts, read-only data).
3. **Production (opt-in, guarded)**: only with narrow `target` globs, low `probability`, `max_injections`,
   a kill switch, and monitoring in place. Never inject into tools with irreversible side effects.

## Built-in guardrails

- Instrumentation is inert unless an experiment session is active in the current execution context.
- Adversarial payloads are benign and canary-tagged; the default exfiltration address uses the reserved
  `.invalid` top-level domain, so it cannot be delivered.
- Runs are seeded and reproducible.

## Kill switch

All instrumentation (decorators, transports, both MCP proxies) becomes a pure pass-through, and records
nothing, when any of these is set:

| Switch | Use |
| --- | --- |
| `AGENTIC_CHAOS_DISABLED=1` | environment of the process (also makes `agentic-chaos run` refuse to start) |
| `AGENTIC_CHAOS_KILL_FILE=/path/to/file` | a running process stops injecting within a second of the file being created, e.g. a long-lived MCP proxy in front of a production server |
| `agentic_chaos.runtime.disable()` | from code, e.g. a feature flag or admin endpoint |

## Secrets in traces and reports

Traces contain prompts, tool arguments, HTTP headers and model output. JSON reports (`--report`) and proxy
trace files (`--trace`) are **redacted by default**: values of secret-named keys (`authorization`, `api_key`,
`x-api-key`, `password`, `cookie`, `mcp-session-id`, ...) and well-known credential formats (bearer tokens,
`sk-` keys, AWS keys, GitHub/Slack tokens, Google API keys, JWTs, PEM private keys) are replaced. Canary tokens
and token-usage counts are kept. Add your own formats with `--redact-pattern REGEX` (or
`redact.Redactor(extra_keys=..., extra_patterns=...)`); `--no-redact` disables redaction. Probes always see the
unredacted in-memory trace. Redaction is pattern-based and best-effort: treat reports as sensitive anyway.

## The MCP HTTP proxy

The proxy has no authentication of its own and forwards the client's credentials upstream. It listens on
loopback by default and refuses other addresses unless you pass `--allow-remote`. Incoming requests are
bounded: body size (10 MiB, `413`), header size and count (`431`), and time to send the request (30 s, `408`);
see `mcp.http.Limits`. Experiment files import and run the entrypoint they name: only run files you trust.

## Rules of thumb

- Use test credentials and sandboxed side effects. A successful injection should prove a point, not send an email.
- Announce experiments to on-call and security operations teams, or let them run them.
- Only run experiments against systems you own or are authorised to test.
- Record every experiment and its outcome; findings are security findings and should be triaged as such.
