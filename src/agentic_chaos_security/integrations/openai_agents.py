"""OpenAI Agents SDK adapter: put function tools under chaos.

    from agentic_chaos_security.integrations.openai_agents import instrument_tools
    agent = Agent(name="assistant", tools=instrument_tools([search, send_email]))

Each call passes ``tool.call`` (with the parsed arguments) and ``tool.result`` (target = the tool's name).
For model calls, build the model's ``AsyncOpenAI`` client with an ``httpx2.AsyncClient`` on
``integrations.httpx2.AsyncChaosTransport``.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from typing import Any

try:
    from agents import FunctionTool
except ImportError as exc:  # pragma: no cover
    raise ImportError("openai-agents is not installed") from exc

from agentic_chaos_security.inject import tool as instrument

__all__ = ["instrument_tool", "instrument_tools"]


def instrument_tool(tool: FunctionTool) -> FunctionTool:
    original = tool.on_invoke_tool

    async def on_invoke_tool(ctx: Any, input_json: str) -> Any:
        try:
            arguments = json.loads(input_json) if input_json else {}
        except ValueError:
            arguments = {"_raw": input_json}

        async def call(**kwargs: Any) -> Any:
            return await original(ctx, input_json)

        return await instrument(call, name=tool.name)(**arguments)

    return dataclasses.replace(tool, on_invoke_tool=on_invoke_tool)


def instrument_tools(tools: Sequence[Any]) -> list[Any]:
    """Instrument every ``FunctionTool``; other tool types (hosted tools) are returned unchanged."""
    return [instrument_tool(t) if isinstance(t, FunctionTool) else t for t in tools]
