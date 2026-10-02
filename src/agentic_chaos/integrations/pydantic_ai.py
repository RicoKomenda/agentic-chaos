"""Pydantic AI adapter: put tools under chaos.

    from agentic_chaos.integrations.pydantic_ai import instrument_tool
    agent = Agent(model, tools=[instrument_tool(Tool(search)), instrument_tool(send_email)])

Plain functions can also be decorated directly with ``@chaos.tool`` below ``@agent.tool_plain``: the
wrapper keeps the signature and type hints Pydantic AI reads. For model calls, pass a provider whose HTTP
client uses a chaos transport.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

try:
    from pydantic_ai import Tool
except ImportError as exc:  # pragma: no cover
    raise ImportError("pydantic-ai is not installed") from exc

from agentic_chaos.inject import tool as instrument

__all__ = ["instrument_tool"]


def instrument_tool(tool: Tool[Any] | Callable[..., Any]) -> Tool[Any]:
    """Return an instrumented ``Tool`` (accepts a ``Tool`` or a plain function)."""
    if not isinstance(tool, Tool):
        tool = Tool(tool)
    wrapped = instrument(tool.function, name=tool.name)
    # rebuild rather than replace: Tool precomputes a schema that holds the original function
    return Tool(
        wrapped,
        name=tool.name,
        description=tool.description,
        takes_ctx=tool.takes_ctx,
        max_retries=tool.max_retries,
    )
