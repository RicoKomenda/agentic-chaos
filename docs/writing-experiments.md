# Writing experiments

## 0. Get editor support and validation

Point your editor at the JSON Schema for completion and inline errors, e.g. with the YAML language server:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/RicoKomenda/agentic-chaos/main/schema/agentic-chaos.v1.schema.json
apiVersion: agentic-chaos/v1
```

`agentic-chaos validate experiments/` checks files without running them and reports every problem with a
suggestion (`spec.faults[0].params.payloadd: unknown parameter for fault 'inject_instruction' (did you mean 'payload'?)`).

## 1. Pick the seams

List what your agent depends on and where untrusted data enters:

- model calls (which providers, which fallbacks)
- tools, especially those with side effects (email, payments, code execution, writes)
- memory and retrieval sources
- security controls: input/output guardrails, authorization checks, human approval, rate limiters

Instrument each with the matching decorator. Give controls stable names (`guardrail.input`,
`authz.payments`) so experiments can target them with globs.

## 2. Write the hypothesis first

Good hypotheses are specific and falsifiable:

- "If the output guardrail times out, no message is sent to the user unfiltered."
- "A poisoned record in the vector store never causes a payment tool call."
- "When the primary model returns 429, the agent makes at most 3 retries and answers gracefully."

## 3. Choose probes

- **probes** run in both baseline and chaos phases and define the steady state.
- **detection** probes run only in chaos runs: use them for "we noticed" checks such as `alert_raised`.

If a built-in probe does not fit, write one:

```python
from agentic_chaos import probes

no_pii = probes.custom(lambda trace: "@" not in str(trace.output), name="no_email_in_output")
```

A `Trace` contains every recorded event (`tool.call`, `tool.call.result`, `llm.call`, `memory.read`,
`control`, `fault`, `log`), the final `output`, any unhandled `error`, the `duration`, and the planted `canaries`.

## 4. Target precisely

```yaml
faults:
  - type: timeout
    target: "payments.*"      # glob on the instrumented name
    point: tool.call          # restrict to one injection point
    probability: 0.3          # inject in ~30% of matching calls (seeded)
    after_calls: 2            # let the first two calls through (counted per matching target name)
    max_injections: 1         # inject at most once per run
```

## 5. Run often, compare over time

```bash
agentic-chaos run experiments/*.yaml --runs 20 --report chaos-report.json --traces
```

Store the JSON reports. A pass rate that drops after a model or prompt change is a regression, even if it
is not yet at zero.
