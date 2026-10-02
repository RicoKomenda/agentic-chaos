# Shopper demo: multi-agent purchase with AP2-style mandates

A shopping agent discovers a merchant agent (Agent Card), exchanges messages with it, gets the user's review,
and submits a Payment Mandate to a processor. All parties are simulated in-process but instrumented like remote
services (`agent.discover`, `agent.call`/`agent.message`, `payment.call`/`payment.result`). Nothing real is charged.

| Behaviour | `naive` | `hardened` |
| --- | --- | --- |
| Merchant card | used as discovered | trusted URL and required AP2 extension verified |
| Merchant instructions | followed, up to 20 follow-ups | followed, but follow-ups capped at 2 |
| Intent Mandate | not enforced | enforced before signing |
| Final cart | paid as returned | must equal the reviewed cart |
| Payment retries | new mandate id per retry | same mandate id (idempotent) |
| Merchant unreachable or rejects credentials | falls back to an unvetted marketplace | stops with a clear message |
| Processor | no replay protection | rejects replayed Payment Mandates |

```bash
uv run agentic-chaos run experiments/multi-agent experiments/ap2 --target examples.shopper.agent:naive
uv run agentic-chaos run experiments/multi-agent experiments/ap2 --target examples.shopper.agent:hardened
```
