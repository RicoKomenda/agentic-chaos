"""Instrumentation: mark the places in an AI application where chaos may be injected.

All wrappers are transparent outside an experiment run, so they can stay in production code.

    @chaos.tool
    def fetch_page(url: str) -> str: ...

    @chaos.control("guardrail.input")
    def scan(text: str) -> bool: ...

    @chaos.agent(name_arg="url")          # target name taken from the call's ``url`` argument
    def send_task(url: str, message: dict) -> dict: ...
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from agentic_chaos.faults import Override, Repeat
from agentic_chaos.runtime import intercept, record

F = TypeVar("F", bound=Callable[..., Any])


def _instrument(kind: str, before: str | None, after: str | None) -> Callable[..., Any]:
    """Build a decorator that wraps sync or async callables with ``before``/``after`` injection points."""

    def decorator(fn: Any = None, *, name: str | None = None, name_arg: str | None = None) -> Any:
        if isinstance(fn, str):
            return decorator(name=fn, name_arg=name_arg)
        if fn is None:
            return lambda f: decorator(f, name=name, name_arg=name_arg)

        signature = inspect.signature(fn) if name_arg else None

        def _label(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
            if signature is not None and name_arg is not None:
                bound = signature.bind_partial(*args, **kwargs)
                if name_arg in bound.arguments:
                    return str(bound.arguments[name_arg])
            return name or fn.__name__

        def _before(label: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> int:
            """Record the call and apply call-time faults. Returns how many extra deliveries to make."""
            record(kind, label, args=list(args), kwargs=kwargs)
            if before:
                try:
                    intercept(before, label, None, args=args, kwargs=kwargs)
                except Repeat as repeat:
                    return repeat.times
            return 0

        def _duplicate(label: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
            record(kind, label, args=list(args), kwargs=kwargs, duplicate=True)

        def _after(label: str, result: Any) -> Any:
            if after:
                result = intercept(after, label, result)
            record(f"{kind}.result", label, result=result)
            return result

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                label = _label(args, kwargs)
                try:
                    extra = _before(label, args, kwargs)
                except Override as forced:
                    return _after(label, forced.value)
                result = await fn(*args, **kwargs)
                for _ in range(extra):
                    _duplicate(label, args, kwargs)
                    result = await fn(*args, **kwargs)
                return _after(label, result)

            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            label = _label(args, kwargs)
            try:
                extra = _before(label, args, kwargs)
            except Override as forced:
                return _after(label, forced.value)
            result = fn(*args, **kwargs)
            for _ in range(extra):
                _duplicate(label, args, kwargs)
                result = fn(*args, **kwargs)
            return _after(label, result)

        return wrapper

    return decorator


#: A tool the agent can call. Points: ``tool.call`` (before) and ``tool.result`` (after).
tool = _instrument("tool.call", "tool.call", "tool.result")
#: A model invocation. Points: ``llm.call`` and ``llm.response``.
llm = _instrument("llm.call", "llm.call", "llm.response")
#: A read from long-term memory, a vector store or another RAG source. Point: ``memory.read``.
memory = _instrument("memory.read", None, "memory.read")
#: A security control decision (guardrail, authorization, human approval). Point: ``control``.
#: Faults can make it time out, error, or return a forced verdict without running.
control = _instrument("control", "control", None)
#: A message or task sent to another agent (A2A, internal multi-agent hand-offs).
#: Points: ``agent.call`` (before) and ``agent.message`` (the other agent's reply).
agent = _instrument("agent.call", "agent.call", "agent.message")
#: A payment step (charge, mandate submission). Points: ``payment.call`` and ``payment.result``.
payment = _instrument("payment.call", "payment.call", "payment.result")


def describe_tool(name: str, description: str) -> str:
    """Pass tool metadata through the ``tool.describe`` point before it is shown to a model."""
    description = intercept("tool.describe", name, description)
    record("tool.describe", name, description=description)
    return description


def discover_agent(name: str, card: dict[str, Any]) -> dict[str, Any]:
    """Pass another agent's self-description (e.g. an A2A Agent Card) through ``agent.discover``."""
    card = intercept("agent.discover", name, card)
    record("agent.discover", name, card=card)
    return card


def output(value: Any) -> Any:
    """Optionally mark an intermediate output (e.g. a streamed message) for probes to inspect."""
    record("output", "partial", value=value)
    return value
