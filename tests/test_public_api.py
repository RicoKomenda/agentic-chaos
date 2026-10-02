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
    "agentic_chaos.runtime",
    "agentic_chaos.schema",
    "agentic_chaos.stats",
]


def current() -> dict[str, list[str]]:
    return {name: sorted(importlib.import_module(name).__all__) for name in MODULES}


def test_every_exported_name_exists():
    for name in MODULES:
        module = importlib.import_module(name)
        assert [n for n in module.__all__ if not hasattr(module, n)] == [], name


def test_public_api_matches_snapshot():
    assert current() == json.loads(SNAPSHOT.read_text())


if __name__ == "__main__":
    SNAPSHOT.write_text(json.dumps(current(), indent=2) + "\n")
