# Contributing to Agentic Chaos

Thanks for your interest! The project is early, so design discussions are as valuable as code.

## Ways to contribute

- **Experiments**: add a YAML file to `experiments/` with a clear hypothesis, tags mapping it to a risk
  (e.g. `ASI06`, `LLM01`), and an expectation in `tests/test_examples.py`.
- **Faults and probes**: new failure modes, ideally grounded in a real incident, paper or advisory. Link the source in the docstring.
- **Integrations**: adapters for agent frameworks, MCP, observability stacks and CI systems (see the [roadmap](docs/roadmap.md)).
- **Docs**: guides, game-day write-ups, mappings to further frameworks.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Guidelines

- Payloads must stay **benign and canary-based**. Do not contribute working exploits, real exfiltration
  endpoints or malware. A successful experiment proves a canary moved, nothing more.
- Faults must be deterministic for a given seed.
- Instrumentation must be a no-op outside an experiment session.
- Keep the core dependency-free apart from PyYAML; integrations go behind optional extras.

## Proposing larger changes

Open an issue first so we can agree on the approach. For changes to the experiment format
(`apiVersion`), describe the migration path.
