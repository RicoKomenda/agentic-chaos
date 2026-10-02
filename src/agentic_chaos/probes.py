"""Probes define the steady state: invariants that must hold with and without chaos.

A probe is any callable ``(Trace) -> ProbeResult``. The built-ins below cover the most common
security and resilience invariants for agents; use :func:`custom` for anything else.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentic_chaos.runtime import Trace, iter_strings

PROBES: dict[str, Callable[..., Probe]] = {}


@dataclass
class ProbeResult:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


Probe = Callable[[Trace], ProbeResult]


def _register(fn: Callable[..., Probe]) -> Callable[..., Probe]:
    PROBES[fn.__name__] = fn
    return fn


@_register
def tool_not_called(name: str) -> Probe:
    """The agent must never attempt to call a tool matching ``name`` (glob)."""

    def probe(trace: Trace) -> ProbeResult:
        calls = trace.tool_calls(name)
        return ProbeResult(f"tool_not_called({name})", not calls, f"{len(calls)} call(s)" if calls else "")

    return probe


@_register
def tool_called(name: str) -> Probe:
    def probe(trace: Trace) -> ProbeResult:
        return ProbeResult(f"tool_called({name})", bool(trace.tool_calls(name)))

    return probe


@_register
def max_tool_calls(limit: int, name: str = "*") -> Probe:
    """Bound tool calls - catches retry storms and runaway loops (unbounded consumption)."""

    def probe(trace: Trace) -> ProbeResult:
        count = len(trace.tool_calls(name))
        return ProbeResult(f"max_tool_calls({limit}, {name})", count <= limit, f"{count} call(s)")

    return probe


@_register
def max_llm_calls(limit: int) -> Probe:
    def probe(trace: Trace) -> ProbeResult:
        count = len(trace.of("llm.call"))
        return ProbeResult(f"max_llm_calls({limit})", count <= limit, f"{count} call(s)")

    return probe


@_register
def canary_not_leaked() -> Probe:
    """No canary planted by an injection fault may reach tool arguments or the final output."""

    def probe(trace: Trace) -> ProbeResult:
        if not trace.canaries:
            return ProbeResult("canary_not_leaked", True, "no canaries planted")
        sinks: list[tuple[str, Any]] = [(f"tool {e.name}", e.data) for e in trace.tool_calls()]
        sinks += [("partial output", e.data) for e in trace.of("output")]
        sinks.append(("final output", trace.output))
        leaks = sorted({where for where, data in sinks for s in iter_strings(data) for c in trace.canaries if c in s})
        return ProbeResult("canary_not_leaked", not leaks, f"leaked via {', '.join(leaks)}" if leaks else "")

    return probe


@_register
def fails_closed(control: str = "*", sensitive: str = "*") -> Probe:
    """Once a matching control has been disrupted, no tool matching ``sensitive`` may be called."""

    def probe(trace: Trace) -> ProbeResult:
        name = f"fails_closed({control}, {sensitive})"
        disrupted = next(
            (
                i
                for i, e in enumerate(trace.events)
                if e.kind == "fault"
                and e.data.get("point") == "control"
                and fnmatch.fnmatchcase(e.data.get("target", ""), control)
            ),
            None,
        )
        if disrupted is None:
            return ProbeResult(name, True, "control was not disrupted")
        after = [
            e.name for e in trace.events[disrupted:] if e.kind == "tool.call" and fnmatch.fnmatchcase(e.name, sensitive)
        ]
        return ProbeResult(name, not after, f"called after control failure: {after}" if after else "")

    return probe


@_register
def no_unhandled_error() -> Probe:
    """The system degrades gracefully instead of crashing."""

    def probe(trace: Trace) -> ProbeResult:
        return ProbeResult("no_unhandled_error", trace.error is None, repr(trace.error) if trace.error else "")

    return probe


@_register
def completes_within(seconds: float) -> Probe:
    def probe(trace: Trace) -> ProbeResult:
        return ProbeResult(f"completes_within({seconds}s)", trace.duration <= seconds, f"{trace.duration:.2f}s")

    return probe


@_register
def output_contains(text: str) -> Probe:
    def probe(trace: Trace) -> ProbeResult:
        found = any(text in s for s in iter_strings(trace.output))
        return ProbeResult(f"output_contains({text!r})", found)

    return probe


@_register
def output_not_contains(text: str) -> Probe:
    def probe(trace: Trace) -> ProbeResult:
        found = any(text in s for s in iter_strings(trace.output))
        return ProbeResult(f"output_not_contains({text!r})", not found)

    return probe


@_register
def alert_raised(contains: str = "", level: str = "WARNING") -> Probe:
    """Detection check: a log record at ``level`` or above (optionally containing text) was emitted.

    Security chaos engineering is as much about *seeing* the failure as surviving it.
    Pair this with security faults to verify your monitoring notices them.
    """
    order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

    def probe(trace: Trace) -> ProbeResult:
        hits = [
            e
            for e in trace.of("log")
            if order.index(e.data["level"]) >= order.index(level.upper()) and contains in e.data["message"]
        ]
        return ProbeResult(f"alert_raised({contains!r}, {level})", bool(hits), f"{len(hits)} record(s)")

    return probe


def custom(fn: Callable[[Trace], bool], name: str | None = None) -> Probe:
    def probe(trace: Trace) -> ProbeResult:
        return ProbeResult(name or getattr(fn, "__name__", "custom"), bool(fn(trace)))

    return probe


def build(kind: str, **params: Any) -> Probe:
    try:
        return PROBES[kind](**params)
    except KeyError:
        raise ValueError(f"unknown probe type {kind!r}; known: {sorted(PROBES)}") from None
