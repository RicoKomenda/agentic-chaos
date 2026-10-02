# Research notes: security chaos scenarios for AI systems

*State of research: October 2026. Sources are linked inline. Some come from vendor blogs or aggregators; verify
primary sources before quoting numbers in talks or papers.*

This document collects failure and attack scenarios across the AI application stack. For each one it notes why
it matters for security and how Agentic Chaos can (or could) turn it into an experiment.

Legend: **[now]** expressible with the current library · **[new]** needs a new fault, point or probe (see
[the gap list](#proposed-additions-to-agentic-chaos)).

## Three cross-cutting insights

**1. Every resilience mechanism is also a security decision.** Retries, timeouts, fallbacks, failover regions,
caches and circuit breakers all create a *degraded path*. That path is rarely exercised, rarely reviewed, and
often has weaker controls than the happy path: a different model, a different region, a gateway that is
bypassed, a guardrail that is skipped. Chaos engineering is the only practical way to exercise these paths
on purpose.

**2. Guardrails are now an attack target, not just a defence.** Research in 2026 shows attackers can inflate
guardrail latency by up to 148×, or make guardrails block nearly all legitimate traffic. This creates a
dilemma: fail open and the attacker bypasses the control; fail closed and the attacker denies service.
Experiments must therefore check **security and availability together**, not just one of them.

**3. Attackers can *induce* the degraded path.** Quota exhaustion forces a model fallback. Context flooding
pushes system prompts or content past a guardrail's inspection window. Guardrail DoS triggers timeouts. A
fault that you test as an "accident" is often reachable by an adversary.

A design principle follows from these, and the project should promote it: **graceful security degradation.**
Systems need more choices than "fail open" or "fail closed". When a control is degraded, the agent should drop
to a *safe mode*, for example read-only tools only, no external side effects, or human review for everything,
and it should raise an alert.

---

## A. Model provider layer (OpenAI, Anthropic, Bedrock, Vertex, Azure OpenAI)

### A1. Full or correlated provider outage
On 3 September 2026, OpenAI, Anthropic and xAI had overlapping outages
([Bloomberg](https://www.bloomberg.com/news/articles/2026-09-03/openai-anthropic-spacexai-hit-by-service-outages-for-ai-models)).
"Multi-provider fallback" assumes failures are independent, and that assumption can fail.
- **Security angle:** what does the agent do with no model? Queue, refuse, or route to a "last resort" (local or unvetted) model?
- **Experiment [now]:** `error` / `rate_limit` on `llm.call` for `*` (all hosts) via `ChaosTransport`; probes `no_unhandled_error`, `canary_not_leaked`, `max_llm_calls`.

### A2. Throttling and capacity errors
Bedrock returns `ThrottlingException` (429) per account and model, and agents/knowledge bases add
`DependencyFailedException` / `BadGatewayException`
([AWS re:Post](https://repost.aws/knowledge-center/bedrock-throttling-error)). Vertex AI returns
`429 RESOURCE_EXHAUSTED` on shared capacity, reportedly even well below quota
([Google docs](https://cloud.google.com/vertex-ai/generative-ai/docs/provisioned-throughput/error-code-429),
[developer forum](https://discuss.google.dev/t/we-are-getting-a-lot-of-429-errors-calling-the-vertex-api-paid-priority-tier-is-not-respected/373220)).
- **Security angle:** retry storms (cost, ASI08/LLM10), and the agent loop's retries stacking on top of SDK retries.
- **Experiment [now]:** `rate_limit` with `probability: 0.5`; probes `max_llm_calls`, `completes_within`. **[new]** `cost_within` probe.

### A3. Fallback to a weaker or unvetted model (safety downgrade)
Gateways and SDKs fall back to another model on 429 or timeout. The fallback model may have weaker
alignment, different tool-calling behaviour, or **different guardrail bindings**. LiteLLM had to fix a bug so
that the *requested* model's guardrails keep running on rate-limit fallback
([BerriAI/litellm#41783](https://github.com/BerriAI/litellm/pull/41783)). Weaker models are easier to steer
([weak-to-strong jailbreaking](https://arxiv.org/pdf/2401.17256)), and cascaded LLM systems can fail as a
cascade under attack ([arXiv 2605.17288](https://arxiv.org/pdf/2605.17288)).
- **Attacker-induced:** exhaust the quota for the primary model, then attack the fallback.
- **Experiment [new]:** `force_fallback` fault (route `llm.call` to a configured fallback model or host) and run
  the **same** security catalog on that path. Probe idea: `controls_invoked(same_as_baseline)`, which checks that every control seen in the baseline also ran on the fallback path.

### A4. Cross-region failover and data residency
Bedrock cross-region inference routes to other regions under load
([AWS ML blog mirror](https://hyperedge.tech/2026/02/11/mastering-amazon-bedrock-throttling-and-service-availability-a-comprehensive-guide/),
[data-residency design notes](https://hidekazu-konishi.com/entry/amazon_bedrock_cross_region_inference_and_data_residency.html)).
Failover can silently move processing out of the intended jurisdiction
([analysis](https://tianpan.co/blog/2026/06/02/the-inference-region-your-data-residency-policy-forgot-to-pin)).
- **Security angle:** a compliance breach caused by an availability event, usually invisible in traces.
- **Experiment [new]:** `region_failover` fault plus a `region_in([...])` probe based on request host or response metadata.

### A5. Silent model updates and deprecations
Provider-side updates change behaviour without a version change: formatting, tool-call ordering, and safety
behaviour ([arXiv 2604.27789](https://arxiv.org/html/2604.27789v1)).
- **Security angle:** a prompt-injection resistance you measured last month may not exist today.
- **Experiment [now]:** run the catalog on a schedule with `runs: 20` and track pass rates. **[new]** `model_swap` fault to rehearse an upgrade before the provider forces one.

### A6. Provider-side safety layer changes
An Azure SDK upgrade silently stopped surfacing Prompt Shields jailbreak errors
([Azure/azure-sdk-for-java#42094](https://github.com/Azure/azure-sdk-for-java/issues/42094)). Content-filter
responses also break streaming clients ([vercel/ai#4220](https://github.com/vercel/ai/issues/4220)).
- **Experiment [now]:** `force_verdict` on a `control` wrapping the provider filter result (as if the filter is silently off); `corrupt_json` / `empty` on `llm.response`.

### A7. Malformed, truncated and cut-off responses
Malformed `tool_calls` and truncated content are the hardest faults to diagnose
([AgentChaos](https://pith.science/paper/2608.06790)).
- **Experiment [now]:** `corrupt_json`, `truncate`, `empty` on `llm.response`. **[new]** `stream_cut` for SSE streams.

---

## B. AI gateway layer (LiteLLM, Portkey, Kong AI Gateway, ...)

### B1. Gateway outage leads to a bypass
When the gateway is down, teams (or code) call the provider directly, which skips guardrails, budgets, logging and key management.
- **Experiment [now]:** `error` on `llm.call` for the gateway host; probe `tool_not_called` / custom probe that no request reaches a provider host directly. **[new]** `hosts_only([...])` probe.

### B2. Gateway compromise (assume breach)
- LiteLLM 1.82.7 / 1.82.8 were published to PyPI with a credential-stealing `.pth` payload on 24 March 2026
  ([LiteLLM security update](https://docs.litellm.ai/blog/security-update-march-2026),
  [Datadog Security Labs](https://securitylabs.datadoghq.com/articles/litellm-compromised-pypi-teampcp-supply-chain-campaign/)).
- CVE-2026-49468: auth bypass via Host-header route confusion in LiteLLM proxy < 1.84.0
  ([GitLab advisory](https://advisories.gitlab.com/pypi/litellm/CVE-2026-49468/)).
- CVE-2026-84377: authenticated SSRF that sends the proxy's provider credentials to an attacker-chosen host
  ([GitLab advisory](https://advisories.gitlab.com/pypi/litellm/CVE-2026-84377/)).
- **Security angle:** the gateway concentrates every provider key and sees every prompt. Chaos question: *if the
  gateway returns attacker-controlled responses, what can they make the agent do?*
- **Experiment [now]:** `inject_instruction` on `llm.response` (adversarial model output) with `canary_not_leaked` and `fails_closed` probes.

### B3. Guardrail failure configuration in the gateway
LiteLLM guardrails can be set to `unreachable_fallback: fail_open` or `fail_on_error: false`
([docs](https://docs.litellm.ai/docs/proxy/guardrails/vigil_guard)). These settings are easy to flip for an incident and then forget.
- **Experiment [now]:** `control_outage` on the gateway's guardrail (via `ChaosTransport` on the guardrail host) plus an injection; probe `canary_not_leaked`.

### B4. Semantic cache poisoning (cross-tenant)
Shared semantic caches can be poisoned so another tenant receives attacker-chosen answers
([NDSS 2026](https://www.ndss-symposium.org/wp-content/uploads/2026-f200-paper.pdf),
[CacheAttack, arXiv 2601.23088](https://arxiv.org/html/2601.23088v2)).
- **Experiment [new]:** `cache.read` injection point and `poison_cache` fault; `canary_not_leaked` across two simulated tenants.

### B5. Prompt-cache timing side channels
Shared prompt caches leaked whether other users sent a given prefix, and providers were found sharing caches
globally ([Auditing Prompt Caching, arXiv 2502.07776](https://arxiv.org/abs/2502.07776),
[CacheProbe](https://arxiv.org/html/2605.30613)).
- **Chaos angle:** limited. This is better served by an audit tool, so it stays out of scope for faults and is documented for awareness.

---

## C. Guardrails and security controls

### C1. Guardrail outage, throttling and latency
Bedrock `ApplyGuardrail` has its own quotas, which are separate from model invocation. The guardrails reference
in AWS's agent toolkit gives 25 text units per second as the default
([aws/agent-toolkit-for-aws](https://github.com/aws/agent-toolkit-for-aws/blob/main/plugins/aws-core/skills/amazon-bedrock/references/guardrails.md),
[re:Post](https://repost.aws/articles/ARF-hZoZ_TRxOlxlxm2rKKjw/how-i-troubleshoot-amazon-bedrock-guardrails-throttling-throttlingexception-too-many-requests)).
Fail-open exception handlers exist in real code: one project's injection guardrail returned
`is_injection=False` on any LLM error
([example issue](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/192)).
- **Experiment [now]:** `control-guardrail-outage` (in the catalog); add `rate_limit` on `control` and `latency` on `control` with `completes_within`.

### C2. Denial of service *against* the guardrail
"From Shield to Target" ([arXiv 2606.14517](https://arxiv.org/html/2606.14517)) injects content that mimics
the guardrail's own analysis schema. This traps LLM-based guardrails in long reasoning: 13–63× token
amplification that transfers across 8 model families, and up to 148× latency in a LangGraph + NeMo Guardrails
deployment. Token budgets only move the problem: fail-open let checkout actions through unreviewed, while
fail-closed blocked 34 of 55 legitimate desktop actions. The related OverThink attack embeds decoy problems that inflate reasoning
tokens up to 46× ([arXiv 2502.02542](https://arxiv.org/abs/2502.02542)).
- **Experiment [now/new]:** `latency` on `control` (seconds proportional to amplification) combined with
  `inject_instruction`. Assert **both** `canary_not_leaked` / `fails_closed` **and** an availability probe.
  **[new]** `success_rate_at_least` (availability) and `tokens_within` probes. This is the flagship
  experiment for the "graceful security degradation" principle.

### C3. Weaponised false positives (availability attacks through safety)
- About 30-character adversarial strings made Llama Guard 3 block over 97% of user requests ([arXiv 2410.02916](https://arxiv.org/html/2410.02916)).
- A single blocker document in a RAG corpus can make the system refuse targeted queries
  ([Jamming RAG, arXiv 2406.05870](https://arxiv.org/html/2406.05870); [MutedRAG, arXiv 2504.21680](https://arxiv.org/html/2504.21680)).
- **Experiment [now]:** `force_verdict` with `verdict: false` on the guardrail (blocks everything), or `poison_memory`
  with a refusal-triggering payload; probe: legitimate tasks still complete, or degrade with a clear message, and an alert fires.

### C4. Inspection-window mismatch
Guardrails often inspect fewer tokens than the model reads. Payloads split across a long prompt pass the
guardrail and are reassembled by the model ([Prompt Overflow, arXiv 2605.23196](https://arxiv.org/html/2605.23196v1)).
- **Experiment [new]:** `context_flood` fault (pad tool results or memory with filler, then put the payload at the end); probe `canary_not_leaked`.

### C5. Streaming output and chunk boundaries
Output guardrails applied to streamed chunks can miss values split across chunk boundaries
([BerriAI/litellm#41936](https://github.com/BerriAI/litellm/pull/41936); research on
[streaming moderation](https://arxiv.org/html/2608.10279)).
- **Experiment [new]:** `rechunk` fault on streamed `llm.response` (split at adversarial boundaries), with canary / PII probes on the client-visible stream.

### C6. Human approval as a control
Approval timeouts that auto-approve, approvals requested out of hours, and approval flooding ("fatigue") are
documented attack patterns ([ATR-2026-00118](https://agentthreatrule.org/en/rules/ATR-2026-00118)).
- **Experiment [now/new]:** wrap the approval step with `@chaos.control("approval.*")`; `control_outage` (approver unavailable) must not mean "approved". **[new]** an `approval` helper with timeout semantics, plus a `flood` fault that issues many low-risk approvals before the risky one.

### C7. Misconfigured or drifting controls
- **Experiment [now]:** `force_verdict` (`control-defense-in-depth` in the catalog).

---

## D. Agent runtime and orchestration

### D1. Retries cause duplicate side effects
A timeout after the remote side committed, followed by a retry, produces a double charge or a duplicate email.
Agent stacks retry at three levels: model, tool wrapper and orchestrator
([write-up](https://dev.to/gabrielanhaia/idempotent-tool-calls-the-retry-safety-net-agents-forget-5cg8)).
- **Experiment [new]:** `timeout_after_commit` fault on `tool.result` (the tool runs, then the caller sees a timeout) and an `idempotent(tool)` probe (at most one *effective* execution per logical action).

### D2. Unbounded consumption and denial of wallet
Sponge examples, output amplification, reasoning amplification (OverThink), and tool-chain token exhaustion
([Clawdrain, arXiv 2603.00902](https://arxiv.org/pdf/2603.00902)).
- **Experiment [now/new]:** `inject_instruction` with a loop-inducing payload; probes `max_tool_calls`, `max_llm_calls`. **[new]** `tokens_within` / `cost_within`.

### D3. Context-window overflow
Long sessions or flooded inputs can silently truncate the system prompt and tool rules
([odysseus#5193](https://github.com/odysseus-dev/odysseus/issues/5193)).
- **Experiment [new]:** `context_flood`, plus a probe that the system instructions are still honoured (canary-based instruction test).

### D4. Multi-agent cascades
*Agents of Chaos* ([arXiv 2602.20021](https://arxiv.org/abs/2602.20021)) ran six agents with email, shell,
Discord and memory for two weeks against 20 researchers and documented security failures that came from
integration, not from the models. The OWASP agentic list covers this as ASI08.
- **Experiment [new]:** an `agent.message` point with `inject_instruction`, `spoof_sender`, `delay` and `drop` faults; probe: blast radius (number of agents that acted on the canary).

---

## E. Tools, MCP and data sources

### E1. MCP rug pull
Tool definitions change after the user approved them, and most clients do not re-verify
([ETDI, arXiv 2506.01333](https://arxiv.org/pdf/2506.01333), [MCP-38 taxonomy](https://arxiv.org/pdf/2603.18063)).
- **Experiment [now]:** `poison_tool_description` with `after_calls: 1` simulates the switch. **[new]** an MCP chaos proxy that does this on the wire for any client.

### E2. MCP server or OAuth failure
Expired tokens, revoked consent, and servers that disappear mid-session. *Hypothesis to test:* fallbacks to broader credentials or alternative servers.
- **Experiment [new]:** `auth_error` fault (401/403) on `tool.call`; probe that no tool with broader scope is called instead.

### E3. Retrieval outage and fallback paths
The recommended fallback for a vector DB outage is keyword search
([RAG infrastructure guide](https://introl.com/blog/rag-infrastructure-production-retrieval-augmented-generation-guide)).
*Hypothesis to test:* document-level access control that is implemented as a vector-store metadata filter is
missing on the fallback path. Mixing embeddings from different models silently corrupts retrieval.
- **Experiment [now/new]:** `error` on `memory.read` for the vector store; custom probe that no document outside the caller's ACL appears in context. **[new]** `embedding` point.

---

## F. Self-hosted inference

Single requests can crash vLLM servers: CVE-2026-34756 (huge `n`, OOM) and CVE-2026-34755 (thousands of
base64 frames) ([SentinelOne](https://www.sentinelone.com/vulnerability-database/cve-2026-34756/),
[HOL](https://hol.org/guard/security/cves/CVE-2026-34755-vllm-affected-by-denial-of-service-via-unbounded)).
- **Chaos angle:** infrastructure faults such as pod kills, GPU OOM and node loss are best injected with LitmusChaos or Chaos Mesh. Agentic Chaos should **bridge** to them and assert agent-level security invariants during the outage.

---

## G. Observability and detection

Tracing can silently cover only part of the traffic. In one self-hosted Langfuse setup only about 7% of agents were traced, because the
plugin fails open when unconfigured ([write-up](https://dev.to/c1-anderson/self-hosted-langfuse-tracing-7-of-my-ai-agents-and-clickhouse-logging-itself-3e80)).
Hosted observability has outages too ([status history](https://statusgator.com/services/langfuse)).
- **Security angle:** if trace export fails during an attack, the incident cannot be reconstructed.
- **Experiment [new]:** `telemetry_drop` fault (on a `trace.export` point); probe that security events are buffered or reach a secondary sink, and that `alert_raised` still fires.

---

---

## H. Agent failure-mode taxonomies

Chaos experiments need a vocabulary for *how agents fail*, not only for how they are attacked.

- **MAST** ([Why Do Multi-Agent LLM Systems Fail?, arXiv 2503.13657](https://arxiv.org/abs/2503.13657), NeurIPS 2025):
  14 failure modes in 3 categories, derived from 1,642 annotated traces.
  - *System design* (44.2%): disobeying the task or role spec, step repetition, loss of history, not knowing when to stop.
  - *Inter-agent misalignment* (32.3%): conversation reset, not asking for clarification, ignoring other agents' input, reasoning that does not match the action.
  - *Task verification* (21.3%): stopping early, skipping or weak verification.
- **Microsoft, Taxonomy of Failure Modes in Agentic AI Systems**
  ([v1, April 2025](https://www.microsoft.com/en-us/security/blog/2025/04/24/new-whitepaper-outlines-the-taxonomy-of-failure-modes-in-ai-agents/);
  [v2, June 2026](https://www.microsoft.com/en-us/security/blog/2026/06/04/updating-taxonomy-failure-modes-agentic-ai-systems-year-red-teaming-taught-us/)).
  It sorts failures as novel vs. existing and safety vs. security. Security failures include agent compromise, knowledge-base poisoning,
  cross-domain prompt injection, HITL bypass, incorrect permissions, resource exhaustion, insufficient isolation and loss of
  provenance. v2 adds seven modes, including MCP/plugin abuse, computer-use visual attacks, supply-chain compromise via tool
  descriptions, and capability disclosure as an attack pivot.
- **Agents of Chaos** ([arXiv 2602.20021](https://arxiv.org/abs/2602.20021)): a live two-week study of six agents that documents failures emerging from integration.

**Gap in Agentic Chaos:** today's faults hit *dependencies* (tools, models, memory, controls). The behavioural modes in MAST
(step repetition, missing termination, skipped verification) can be *observed* with probes, but there is no fault that
*induces* them yet, for example by perturbing a peer agent's message. That needs the `agent.message` point (section I).

---

## I. Agent protocols: MCP, A2A, AP2 and the commerce stack

The 2026 stack is layered. MCP connects agents to tools and data, A2A connects agents to agents, and AP2 / UCP / ACP / x402 carry
commerce and payments. MCP, A2A, AP2 and UCP are under Linux Foundation governance
([overview](https://stellagent.ai/insights/mcp-vs-a2a-vs-ap2-protocol-comparison)). Each layer adds protocol-level failure
modes that framework-level instrumentation does not see. Comparative threat models:
[MCP, A2A, Agora, ANP (arXiv 2602.11327)](https://arxiv.org/pdf/2602.11327),
[governance gaps in MCP/A2A/ACP (arXiv 2606.31498)](https://arxiv.org/pdf/2606.31498).

### I1. MCP

Current coverage is generic only: `poison_tool_description` through `describe_tool()`. MCP-specific surfaces:

| Scenario | Fault idea | Probe idea |
| --- | --- | --- |
| Rug pull: definitions change after approval ([ETDI](https://arxiv.org/pdf/2506.01333)) | proxy rewrites `tools/list` after N calls | tool called only with an approved definition hash |
| Tool name collision / shadowing across servers ([AP2 analysis, PoC 2](https://arxiv.org/html/2608.23858v1)) | second server registers a same-named tool | call routed to the expected server |
| Sampling abuse: server makes the client's LLM do work, injects instructions ([Unit 42](https://unit42.paloaltonetworks.com/model-context-protocol-attack-vectors/)) | proxy issues `sampling/createMessage` with payload | `canary_not_leaked`, `tokens_within` |
| Elicitation spoofing: server asks the user for secrets. Spec 2026-07-28 allows server requests only during an active client request ([summary](https://blog.mcpservers.org/posts/mcp-spec-2026-07-28)) | unsolicited or credential-seeking elicitation | client rejects it; no secret is passed |
| Resource / prompt poisoning | `inject_instruction` on `resources/read` | `canary_not_leaked` |
| Server outage, slow or oversized results | latency, error, huge payload | `no_unhandled_error`, `tokens_within` |
| OAuth token expiry / revoked consent; confused deputy | 401/403, audience mismatch | no fallback to broader credentials |
| `list_changed` notification storms | notification flood | bounded re-listing, no re-approval bypass |

**Approach:** an **MCP chaos proxy** (stdio and Streamable HTTP) that sits between any client and server. It needs no code
changes in the agent and works for Claude Code, IDEs, LangGraph and others. It maps to the existing `tool.*` points.

### I2. A2A (Agent2Agent)

Threats from [CSA's MAESTRO threat model](https://cloudsecurityalliance.org/blog/2025/04/30/threat-modeling-google-s-a2a-protocol-with-the-maestro-framework),
[Semgrep's guide](https://semgrep.dev/blog/2025/a-security-engineers-guide-to-the-a2a-protocol/),
[arXiv 2504.16902](https://arxiv.org/html/2504.16902v1) and [A2ABreak, arXiv 2609.10871](https://arxiv.org/pdf/2609.10871):

| Scenario | Fault idea | Probe idea |
| --- | --- | --- |
| Agent Card spoofing / typosquatting / tampering | discovery returns a forged or modified card (skills, URL, auth) | `no_task_to_untrusted_agent` (signed-card / allow-list check held) |
| Capability over-claim | card advertises skills the agent should not have | delegation respects the client's policy, not the card's claims |
| Task injection, replay, state confusion | duplicate or replayed `tasks/send`, out-of-order state transitions | idempotent task handling; no action on replayed tasks |
| Malicious task artifacts / messages | `inject_instruction` in artifacts returned by a remote agent | `canary_not_leaked` (blast radius across agents) |
| Streaming interruption or injection (SSE) | cut, delay, inject events | consistent task state, no partial-result actions |
| Push-notification spoofing | forged webhook callback | notification authenticity is verified before acting |
| Recursive delegation / delegation loops (DoS) | remote agent re-delegates back | `max_delegation_depth`, `tokens_within` |
| Remote agent outage / slow agent | latency, error on remote agent | graceful degradation, no fallback to an unvetted agent |

**Approach:** an `agent.message` and `agent.discover` injection point, plus an A2A client/server middleware adapter.
MAST-style misalignment (conversation reset, ignored input) can be *induced* here by dropping or reordering messages.

### I3. AP2 (Agent Payments Protocol)

AP2 secures signed **mandates** (Intent, Cart, Payment) but, as the AP2 documentation acknowledges, it assumes prompt injection cannot be fully
prevented ([AP2 security considerations](https://ap2-protocol.org/ap2/security_and_privacy_considerations/)).
[*Beyond the Mandate* (arXiv 2608.23858)](https://arxiv.org/html/2608.23858v1) finds 48 threats in five families: semantic
manipulation, authority spoofing, supply-chain and trust-root subversion, state-binding failures, and accountability failures. 8 of them are high severity.
The core result: *a valid signature over a mandate does not prove user intent when the pre-signing context
(catalog data, tool results, A2A messages) was manipulated.* Formal analysis:
[arXiv 2609.00060](https://arxiv.org/pdf/2609.00060). Guidance: [CSA](https://cloudsecurityalliance.org/blog/2025/10/06/secure-use-of-the-agent-payments-protocol-ap2-a-framework-for-trustworthy-ai-driven-transactions).

| Scenario | Fault idea | Probe idea |
| --- | --- | --- |
| Context poisoning before signing (price, merchant, quantity) | `inject_instruction` / value mutation on catalog and tool results | `cart_matches_intent`: signed cart lies within the Intent Mandate's constraints |
| Cart mutation after user review | mutate cart between display and signing | signed cart hash equals the displayed cart hash |
| Mandate replay / stale mandate reuse | resend an old Payment Mandate | rejected; at most one settlement per mandate |
| Protocol / extension downgrade | advertise an older AP2 extension | downgrade refused |
| Unsigned side-channel data (e.g. `risk_data`) poisoned | mutate unsigned fields | decisions do not depend on unsigned fields |
| Credentials provider or processor outage | timeout, error | no retry that double-charges; no fallback to a weaker payment path |
| Human-not-present flow with an over-broad Intent Mandate | agent buys at the edge of the mandate under injection | spend stays below a canary threshold |

**Approach:** a `payment` control point and a reference shopping-agent target (deterministic, test values only, in the
spirit of the mailbot demo). Payment canaries are amounts and merchant IDs, never real instruments.

### I4. Other protocols to track

- **UCP** (Universal Commerce Protocol, Google, 2026) and **ACP** (Agentic Commerce Protocol, OpenAI/Stripe): checkout flows share AP2's pre-signing-context problem. Note that "ACP" also names IBM's Agent Communication Protocol, which has [pivoted to discovery](https://stellagent.ai/insights/mcp-vs-a2a-vs-ap2-protocol-comparison).
- **x402** (HTTP 402 payments): per-request payments make denial of wallet a literal payment flow. Every retry storm becomes a series of real micro-payments.
- **ANP** (Agent Network Protocol), **Agora**: decentralised identity and discovery, with the same discovery-trust questions as A2A Agent Cards.
- **AG-UI** and similar agent-to-frontend protocols: event-stream integrity between agent and user interface (spoofed approval prompts).

---

## Proposed additions to Agentic Chaos

Priorities: **P1** has a high security payoff and fits the current design. **P2** is valuable but needs more design. **P3** is later.

| Priority | Addition | Kind | Scenarios |
| --- | --- | --- | --- |
| P1 | `success_rate_at_least`, `tokens_within`, `cost_within` | probes | C2, C3, D2, A2 |
| P1 | `force_fallback` (route to fallback model/host) + `controls_invoked` probe | fault + probe | A3, B1 |
| P1 | ~~`context_flood` (pad then payload)~~ (done as `flood`, `asi01-context-flood`) | fault | C4, D3 |
| P1 | ~~`timeout_after_commit` + idempotency probe~~ (done; `max_settlements` for payments) | fault + probe | D1 |
| P1 | guardrail-DoS experiment pair (security **and** availability) | catalog | C2 |
| P1 | approval-control catalog experiments (outage ≠ approve) | catalog | C6 |
| P2 | `region_failover` + `region_in` probe | fault + probe | A4 |
| P2 | ~~`auth_error` (401/403)~~ (done) | fault | E2 |
| P2 | `stream_cut`, `rechunk` for SSE | faults | A7, C5 |
| P2 | `agent.message` point: inject, spoof, delay, drop + blast-radius probe | point | D4 |
| P2 | MCP chaos proxy (rug pull, poisoning, auth errors on the wire) | integration | E1, E2 |
| P2 | `model_swap` | fault | A5 |
| P1 | ~~MCP chaos proxy (stdio + Streamable HTTP): rug pull, shadowing, sampling/elicitation abuse, auth errors~~ (done) | integration | E1, E2, I1 |
| P2 | ~~`agent.message` / `agent.discover` points + A2A adapter (card spoofing, delegation loops, replay, streaming)~~ (done) | points + integration | D4, H, I2 |
| P2 | ~~`payment` points + AP2 reference shopping target + intent/review/settlement probes~~ (done) | point + target + probe | I3 |
| P2 | MAST / Microsoft taxonomy tags on faults and experiments (MAST tags started) | catalog | H |
| P3 | `cache.read` point + `poison_cache` | point + fault | B4 |
| P3 | `trace.export` point + `telemetry_drop` | point + fault | G |
| P3 | LitmusChaos / Chaos Mesh bridge | integration | F |

Several scenarios already work today and only need catalog entries: false-positive DoS (`force_verdict: false`),
rug pull (`poison_tool_description` with `after_calls`), compromised gateway (`inject_instruction` on
`llm.response`), and guardrail latency (`latency` on `control`).
