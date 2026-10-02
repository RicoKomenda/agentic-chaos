"""The public API is a contract (docs/versioning.md). Changing it must be a deliberate, reviewed edit of
tests/public_api.json - regenerate with: uv run python tests/test_public_api.py
"""

import importlib
import json
from pathlib import Path

SNAPSHOT = Path(__file__).with_name("public_api.json")
MODULES = [
    "agentic_chaos_security",
    "agentic_chaos_security.ap2",
    "agentic_chaos_security.cli",
    "agentic_chaos_security.experiment",
    "agentic_chaos_security.faults",
    "agentic_chaos_security.inject",
    "agentic_chaos_security.integrations.a2a",
    "agentic_chaos_security.integrations.httpx",
    "agentic_chaos_security.integrations.httpx2",
    "agentic_chaos_security.judges",
    "agentic_chaos_security.loader",
    "agentic_chaos_security.mcp",
    "agentic_chaos_security.mcp.core",
    "agentic_chaos_security.mcp.http",
    "agentic_chaos_security.mcp.proxy",
    "agentic_chaos_security.payloads",
    "agentic_chaos_security.probes",
    "agentic_chaos_security.redact",
    "agentic_chaos_security.report",
    "agentic_chaos_security.pytest_plugin",
    "agentic_chaos_security.integrations.otel",
    "agentic_chaos_security.integrations.langchain",
    "agentic_chaos_security.integrations.openai_agents",
    "agentic_chaos_security.integrations.pydantic_ai",
    "agentic_chaos_security.runtime",
    "agentic_chaos_security.schema",
    "agentic_chaos_security.stats",
]


#: Adapters for optional third-party packages; checked only where those packages are installed.
OPTIONAL = {
    "agentic_chaos_security.integrations.langchain",
    "agentic_chaos_security.integrations.openai_agents",
    "agentic_chaos_security.integrations.pydantic_ai",
    "agentic_chaos_security.integrations.otel",
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
