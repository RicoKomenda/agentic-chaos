"""Instrumentation: mark the places in an AI application where chaos may be injected.

All wrappers are transparent outside an experiment run, so they can stay in production code.

    @chaos.tool
    def fetch_page(url: str) -> str: ...

    @chaos.control("guardrail.input")
    def scan(text: str) -> bool: ...
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar, overload

from agentic_chaos.faults import Override
from agentic_chaos.runtime import intercept, record

F = TypeVar("F", bound=Callable[..., Any])


def _instrument(kind: str, before: str | None, after: str | None) -> Callable[..., Any]:
    """Build a decorator that wraps sync or async callables with ``before``/``after`` injection points."""

    @overload
    def decorator(fn: F) -> F: ...
    @overload
    def decorator(fn: None = None, *, name: str | None = None) -> Callable[[F], F]: ...
    @overload
    def decorator(fn: str) -> Callable[[F], F]: ...

    def decorator(fn: Any = None, *, name: str | None = None) -> Any:
        if isinstance(fn, str):
            return decorator(name=fn)
        if fn is None:
            return lambda f: decorator(f, name=name)

        label = name or fn.__name__

        def _before(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
            record(kind, label, args=list(args), kwargs=kwargs)
            if before:
                intercept(before, label, None, args=args, kwargs=kwargs)

        def _after(result: Any) -> Any:
            if after:
                result = intercept(after, label, result)
            record(f"{kind}.result", label, result=result)
            return result

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    _before(args, kwargs)
                except Override as forced:
                    return _after(forced.value)
                return _after(await fn(*args, **kwargs))

            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                _before(args, kwargs)
            except Override as forced:
                return _after(forced.value)
            return _after(fn(*args, **kwargs))

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


def describe_tool(name: str, description: str) -> str:
    """Pass tool metadata through the ``tool.describe`` point before it is shown to a model."""
    description = intercept("tool.describe", name, description)
    record("tool.describe", name, description=description)
    return description


def output(value: Any) -> Any:
    """Optionally mark an intermediate output (e.g. a streamed message) for probes to inspect."""
    record("output", "partial", value=value)
    return value
