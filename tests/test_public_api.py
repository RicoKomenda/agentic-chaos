"""The public API is a contract (docs/versioning.md). Changing it must be a deliberate, reviewed edit of
tests/public_api.json - regenerate with: uv run python tests/test_public_api.py
"""

import importlib
import json
from pathlib import Path

SNAPSHOT = Path(__file__).with_name("public_api.json")
MODULES = [
    "agentic_chaos",
    "agentic_chaos.ap2",
    "agentic_chaos.cli",
    "agentic_chaos.experiment",
    "agentic_chaos.faults",
    "agentic_chaos.inject",
    "agentic_chaos.integrations.a2a",
    "agentic_chaos.integrations.httpx",
    "agentic_chaos.integrations.httpx2",
    "agentic_chaos.judges",
    "agentic_chaos.loader",
    "agentic_chaos.mcp",
    "agentic_chaos.mcp.core",
    "agentic_chaos.mcp.http",
    "agentic_chaos.mcp.proxy",
    "agentic_chaos.payloads",
    "agentic_chaos.probes",
    "agentic_chaos.redact",
    "agentic_chaos.report",
    "agentic_chaos.pytest_plugin",
    "agentic_chaos.integrations.otel",
    "agentic_chaos.integrations.langchain",
    "agentic_chaos.integrations.openai_agents",
    "agentic_chaos.integrations.pydantic_ai",
    "agentic_chaos.runtime",
    "agentic_chaos.schema",
    "agentic_chaos.stats",
]


#: Adapters for optional third-party packages; checked only where those packages are installed.
OPTIONAL = {
    "agentic_chaos.integrations.langchain",
    "agentic_chaos.integrations.openai_agents",
    "agentic_chaos.integrations.pydantic_ai",
    "agentic_chaos.integrations.otel",
}


def importable() -> dict[str, object]:
    modules = {}
    for name in MODULES:
        try:
            modules[name] = importlib.import_module(name)
        except ImportError:
            assert name in OPTIONAL, f"{name} failed to import"
    return modules


def current() -> dict[str, list[str]]:
    return {name: sorted(module.__all__) for name, module in importable().items()}


def test_every_exported_name_exists():
    for name, module in importable().items():
        assert [n for n in module.__all__ if not hasattr(module, n)] == [], name


def test_public_api_matches_snapshot():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert set(snapshot) == set(MODULES)
    found = current()
    assert found == {name: snapshot[name] for name in found}


if __name__ == "__main__":
    assert set(current()) == set(MODULES), "install the interop group to regenerate the full snapshot"
    SNAPSHOT.write_text(json.dumps(current(), indent=2) + "\n", encoding="utf-8")
