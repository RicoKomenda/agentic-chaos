# Mailbot demo agent

A deterministic toy agent with a scripted "model" that follows instructions in its context, like many real
models do. It needs no API key, so the whole experiment catalog runs offline.

| Variant | Guardrail | On guardrail failure | Action authorization | Error handling |
| --- | --- | --- | --- | --- |
| `naive` | none | - | none | none |
| `guarded_fail_open` | yes, first 2,000 characters only | continues (fail open) | none | retries |
| `hardened` | yes, full context | refuses (fail closed) | allow-listed recipients | retries, graceful messages |

```bash
uv run agentic-chaos run experiments/*.yaml --target examples.mailbot.agent:naive
uv run agentic-chaos run experiments/*.yaml --target examples.mailbot.agent:guarded_fail_open
uv run agentic-chaos run experiments/*.yaml --target examples.mailbot.agent:hardened
```

The interesting case is `guarded_fail_open`: it passes the plain prompt-injection experiment, so it looks
secure, and fails only when the guardrail is disrupted at the same time, or when the payload is hidden beyond its
inspection window (`asi01-context-flood`).
