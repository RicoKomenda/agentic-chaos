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

## Rules of thumb

- Use test credentials and sandboxed side effects. A successful injection should prove a point, not send an email.
- Announce experiments to on-call and security operations teams, or let them run them.
- Only run experiments against systems you own or are authorised to test.
- Record every experiment and its outcome; findings are security findings and should be triaged as such.
