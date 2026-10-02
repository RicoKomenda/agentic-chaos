"""Interop: tool adapters for LangChain/LangGraph, the OpenAI Agents SDK and Pydantic AI."""

import asyncio
import json

import pytest

from agentic_chaos import faults
from agentic_chaos.runtime import Session, bound

pytestmark = pytest.mark.interop
PAGE = "Quarterly results are up 4%."


def fetch_page(url: str) -> str:
    """Fetch a web page and return its text."""
    return PAGE


# --- LangChain / LangGraph --------------------------------------------------------------------


def run_tool_node(tools, call):
    """Run LangGraph's prebuilt ToolNode inside a minimal compiled graph."""
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    state = graph.compile().invoke({"messages": [AIMessage(content="", tool_calls=[call])]})
    return state["messages"][-1]


def test_langchain_tool_and_langgraph_tool_node():
    pytest.importorskip("langchain_core")
    pytest.importorskip("langgraph.prebuilt")
    from langchain_core.tools import tool

    from agentic_chaos.integrations.langchain import instrument_tools

    (lc_tool,) = instrument_tools([tool(fetch_page)])
    session = Session([faults.Truncate("fetch_page", keep=0.5)])
    with bound(session):
        assert lc_tool.invoke({"url": "https://example.com"}) == PAGE[: len(PAGE) // 2]
        call = {"name": "fetch_page", "args": {"url": "https://example.com"}, "id": "1", "type": "tool_call"}
        message = run_tool_node([lc_tool], call)
    assert message.content == PAGE[: len(PAGE) // 2]
    assert [e.data["kwargs"] for e in session.trace.of("tool.call", "fetch_page")][0] == {"url": "https://example.com"}


def test_langgraph_surfaces_tool_timeouts_to_the_model():
    pytest.importorskip("langchain_core")
    pytest.importorskip("langgraph.prebuilt")
    from langchain_core.tools import tool

    from agentic_chaos.integrations.langchain import instrument_tools

    call = {"name": "fetch_page", "args": {"url": "x"}, "id": "1", "type": "tool_call"}
    with bound(Session([faults.Timeout("fetch_page")])):
        try:
            message = run_tool_node(instrument_tools([tool(fetch_page)]), call)
        except TimeoutError:
            return  # LangGraph 1.2: ToolNode re-raises tool errors by default (see docs/interop.md)
    # older defaults turn the exception into an error message the model sees
    assert message.status == "error" and "timed out" in message.content


# --- OpenAI Agents SDK -------------------------------------------------------------------------


def test_openai_agents_function_tool():
    agents = pytest.importorskip("agents")
    from agents.tool_context import ToolContext

    from agentic_chaos.integrations.openai_agents import instrument_tools

    (instrumented,) = instrument_tools([agents.function_tool(fetch_page)])
    arguments = json.dumps({"url": "https://example.com"})
    context = ToolContext(context=None, tool_name="fetch_page", tool_call_id="1", tool_arguments=arguments)
    session = Session([faults.InjectInstruction("fetch_page", payload="goal_hijack")], seed=1)
    with bound(session):
        result = asyncio.run(instrumented.on_invoke_tool(context, arguments))
    assert str(result).startswith(PAGE) and "Ignore previous instructions" in str(result)
    assert session.trace.of("tool.call", "fetch_page")[0].data["kwargs"] == {"url": "https://example.com"}


# --- Pydantic AI --------------------------------------------------------------------------------


def test_pydantic_ai_agent_with_test_model():
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    from agentic_chaos.integrations.pydantic_ai import instrument_tool

    agent = pydantic_ai.Agent(TestModel(), tools=[instrument_tool(fetch_page)])
    session = Session([faults.Truncate("fetch_page", keep=0.5)])
    with bound(session):
        result = agent.run_sync("Summarise the page")
    assert PAGE[: len(PAGE) // 2] in str(result.output) and PAGE not in str(result.output)
    assert session.trace.of("tool.call", "fetch_page")
