"""The provider and A2A chaos transports for SDKs built on ``httpx2`` (current OpenAI and Anthropic SDKs).

import httpx2, openai
from agentic_chaos_security.integrations.httpx2 import ChaosTransport

client = openai.OpenAI(http_client=httpx2.Client(transport=ChaosTransport()))
"""

from __future__ import annotations

try:
    import httpx2
except ImportError as exc:  # pragma: no cover
    raise ImportError("httpx2 is not installed (it ships with current OpenAI and Anthropic SDKs)") from exc

from agentic_chaos_security.integrations import a2a as _a2a
from agentic_chaos_security.integrations import httpx as _httpx

ChaosTransport, AsyncChaosTransport = _httpx.build(httpx2)
A2AChaosTransport, AsyncA2AChaosTransport = _a2a.build(httpx2)

__all__ = ["A2AChaosTransport", "AsyncA2AChaosTransport", "AsyncChaosTransport", "ChaosTransport"]
