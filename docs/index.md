# Agentic Chaos

![Agentic Chaos logo](assets/logo.png){ width="240" }

**Security chaos engineering for AI agents and LLM applications.**

Agentic Chaos injects controlled, reproducible faults into agentic systems, both accidents (outages,
latency, malformed output) and adversaries (prompt injection, tool and memory poisoning, failing
security controls). It then checks that your security and resilience invariants still hold.

```bash
pip install agentic-chaos-security
agentic-chaos-security run experiments/ --target my_app.agent:handle
```

- **Start here:** [Principles](principles.md), then [Writing experiments](writing-experiments.md).
- **Protocols:** [MCP proxy, A2A transport and AP2 payments](protocols.md).
- **In your stack:** [framework adapters, pytest, GitHub Actions and reports](integrations.md).
- **Judging non-deterministic agents:** [Statistics](statistics.md).
- **What is covered:** the [fault catalog](fault-catalog.md), mapped to the OWASP Top 10 for Agentic Applications
  and for LLM Applications.
