# Landscape and related work

## Chaos engineering

- [Principles of Chaos Engineering](https://principlesofchaos.org/): steady state, hypothesis, real-world events, production, automation, blast radius.
- Infrastructure tools: Netflix Chaos Monkey / Simian Army, [LitmusChaos](https://litmuschaos.io/), [Chaos Mesh](https://chaos-mesh.org/), Gremlin, Chaos Toolkit, Harness Resilience Testing.
- [Overview of principles, examples and tools](https://www.loadview-testing.com/blog/chaos-engineering-principles-examples-tools/) (LoadView).
- [Integrating chaos engineering with AI/ML for failure prediction](https://www.harness.io/blog/integrating-chaos-engineering-with-ai-ml-proactive-failure-prediction) (Harness): using chaos telemetry to train failure predictors.

## Security chaos engineering

- Kelly Shortridge and Aaron Rinehart, *Security Chaos Engineering* (O'Reilly): verifying that security controls work by injecting the conditions in which they might fail.
- ChaoSlingr: an early open-source security chaos tool for cloud misconfigurations.

## Chaos for AI agents

- [deepankarm/agent-chaos](https://github.com/deepankarm/agent-chaos) (Python, Apache-2.0): scenario/variant model, LLM, tool and user-input faults, DeepEval / Pydantic Evals assertions, fuzzing.
- [reaatech/agent-chaos](https://github.com/reaatech/agent-chaos) (TypeScript, MIT): middleware fault injection between agents and tools, YAML scenarios.
- [Rightbrain: Agentic Chaos](https://rightbrain.ai/resources/agentic-chaos/): deterministic, seeded fault injection as a feature of an agent runtime.
- Chaos for agent frameworks is also an active research topic: AgentChaos (programmatic HTTP-layer fault injection into multi-agent systems), *Assessing and Enhancing the Robustness of LLM-based Multi-Agent Systems Through Chaos Engineering* (arXiv 2505.03096), ReliabilityBench.

## Agent security research and testing

- OWASP Top 10 for Agentic Applications and OWASP Top 10 for LLM Applications: risk taxonomies used in the [fault catalog](fault-catalog.md).
- Tool poisoning benchmarks such as MCPTox (arXiv 2508.14925).
- AI red-teaming tools: garak, PyRIT, promptfoo, Giskard, DeepTeam.

## Where Agentic Chaos fits

Existing agent chaos tools mostly focus on **reliability** (does the agent survive failing dependencies?).
Red-teaming tools focus on the **model** (can it be tricked?). Agentic Chaos focuses on the **security
properties of the whole system under turbulence**:

- security controls are first-class injection targets (`control_outage`, `force_verdict`), so fail-open behaviour and missing defence in depth become testable
- adversarial and accidental faults combine in a single experiment
- results are judged by security invariants (`fails_closed`, `canary_not_leaked`) and detection (`alert_raised`)
- experiments are mapped to established risk taxonomies

Agentic Chaos aims to complement these tools, not replace them. Integrations with them are welcome.
