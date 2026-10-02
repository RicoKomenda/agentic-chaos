"""LangChain / LangGraph adapter: put LangChain tools under chaos without changing their code.

    from agentic_chaos.integrations.langchain import instrument_tools
    tools = instrument_tools([search, send_email])        # then ToolNode(tools), create_agent(..., tools)

Each call passes ``tool.call`` and its result ``tool.result`` (target = the tool's name). For model calls,
give the chat model a client built on a chaos transport (e.g. ``ChatOpenAI(http_client=...)`` with
``integrations.httpx2.ChaosTransport``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

try:
    from langchain_core.tools import BaseTool, StructuredTool
except ImportError as exc:  # pragma: no cover
    raise ImportError("langchain-core is not installed") from exc

from agentic_chaos.inject import tool as instrument

__all__ = ["instrument_tool", "instrument_tools"]


def instrument_tool(tool: BaseTool) -> BaseTool:
    """Return a copy of ``tool`` whose sync and async implementations are instrumented."""
    if isinstance(tool, StructuredTool) or (getattr(tool, "func", None) or getattr(tool, "coroutine", None)):
        update: dict[str, Any] = {}
        if getattr(tool, "func", None) is not None:
            update["func"] = instrument(tool.func, name=tool.name)  # type: ignore[attr-defined]
        if getattr(tool, "coroutine", None) is not None:
            update["coroutine"] = instrument(tool.coroutine, name=tool.name)  # type: ignore[attr-defined]
        return tool.model_copy(update=update)

    # any other BaseTool: route calls through an instrumented wrapper around its public interface
    def call(**kwargs: Any) -> Any:
        return tool.invoke(kwargs)

    async def acall(**kwargs: Any) -> Any:
        return await tool.ainvoke(kwargs)

    return StructuredTool(
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema if tool.args_schema is not None else tool.get_input_schema(),
        func=instrument(call, name=tool.name),
        coroutine=instrument(acall, name=tool.name),
    )


def instrument_tools(tools: Sequence[BaseTool]) -> list[BaseTool]:
    return [instrument_tool(t) for t in tools]
