# Principles of security chaos engineering for AI systems

Chaos engineering is the practice of running experiments on a system to build confidence in its ability to
withstand turbulent conditions ([Principles of Chaos Engineering](https://principlesofchaos.org/)).
Security chaos engineering applies the same method to security: instead of assuming that controls work,
you inject the conditions under which they might fail and observe what happens.

AI agents make this more urgent. They are non-deterministic, they read untrusted content, they act
through tools with real privileges, and their security often depends on runtime controls: guardrails,
classifiers, allow-lists, approval steps. Those controls are just more distributed-system components, and
they can time out, crash, be misconfigured or drift.

Agentic Chaos is built on the following principles.

## 1. Define the steady state as security invariants

Classic chaos engineering measures throughput and error rates. For agents, the steady state is a set of
**invariants that must hold no matter what**:

- untrusted content never causes a privileged action (`canary_not_leaked`, `tool_not_called`)
- when a control is unavailable, sensitive actions stop (`fails_closed`)
- failures degrade the answer, not the system (`no_unhandled_error`, `max_tool_calls`)

Answer quality matters, but it is a different question. Keep invariants binary and observable.

## 2. Hypothesise that the steady state holds, then try to disprove it

Every experiment states a hypothesis in plain language ("when the guardrail times out, the agent
refuses") and runs a baseline first. If the baseline already violates the steady state, the result is
*inconclusive*: fix the baseline before blaming the fault.

## 3. Inject both accidents and adversaries

Real-world turbulence for AI systems includes:

- **Accidents**: provider outages and rate limits, slow or failing tools, truncated or malformed output,
  context-window exhaustion.
- **Adversaries**: indirect prompt injection, poisoned tool metadata, poisoned memory and RAG data,
  spoofed inter-agent messages.
- **Control failures**: guardrails that time out, error, or silently allow everything.

The most interesting findings are usually at the intersection, such as an attack arriving while a
control is degraded.

## 4. Fail closed is a property you prove, not a property you declare

"The guardrail fails closed" is a claim about error-handling code paths that rarely run. Exercise those
paths on purpose: `control_outage` and `force_verdict` exist for exactly this.

## 5. Verify detection, not just prevention

A blocked attack that nobody sees is a missed signal. A successful attack that nobody sees is an incident
you learn about later. Use `detection` probes (for example `alert_raised`) to check that disruptions show
up in logs, alerts and traces.

## 6. Measure distributions, not anecdotes

Models are stochastic. One passing run proves little. Run experiments many times (`runs`) and track pass
rates over time, especially across model upgrades, prompt changes and new tools.

## 7. Prove that the fault fired

An experiment in which no fault was triggered proves nothing, and silently passes in most tools. Agentic
Chaos reports such runs as *inconclusive* so mis-targeted experiments cannot pass by accident.

## 8. Use canaries, not harm

Adversarial payloads are benign and tagged with unique canary tokens. Success means a canary turned up
where it should not: a tool argument or an output. No real exfiltration, no real exploit code, no real
destinations.

## 9. Minimise the blast radius, then widen it

Start in unit tests and CI against stubs, move to staging with real models, and only then consider
guarded production experiments with a kill switch, narrow targeting and low probability. See
[safety.md](safety.md).

## 10. Degrade security gracefully, and test both sides

When a control fails, "fail open" gives up security and "fail closed" gives up availability. An attacker who
can disrupt the control (for example with a guardrail DoS) wins either way. Prefer a third option, a
*safe mode* such as read-only answers or no side-effecting tools, and verify it with security probes and
availability probes in the same experiment (see [statistics.md](statistics.md)).

## 11. Automate continuously

Agent behaviour changes without a code change: the provider updates a model, someone edits a prompt,
a new MCP server is connected. Run the catalog in CI and on a schedule so regressions are caught when
they are introduced.
