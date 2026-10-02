"""Chaos session runtime: holds active faults, records a trace, and resolves injections.

A :class:`Session` is bound to the current execution context (``contextvars``) while an
experiment run is in progress. Instrumented code calls :func:`intercept` at well-known
injection points; when no session is active, interception is a no-op so instrumented
code can ship to production unchanged.
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import fnmatch
import logging
import os
import random
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

__all__ = [
    "DISABLE_ENV",
    "KILL_FILE_ENV",
    "Event",
    "InjectionContext",
    "POINTS",
    "Session",
    "Trace",
    "aintercept",
    "bound",
    "current",
    "disable",
    "enable",
    "intercept",
    "is_enabled",
    "iter_strings",
    "record",
    "suspended",
]

if TYPE_CHECKING:
    from agentic_chaos_security.faults import Fault

#: Injection points understood by the runtime. Faults declare which of these they act on.
POINTS = (
    "llm.call",  # before a model request is sent
    "llm.response",  # the model's response
    "tool.describe",  # tool metadata (name/description/schema) shown to the model
    "tool.call",  # before a tool executes
    "tool.result",  # a tool's return value
    "memory.read",  # data returned from long-term memory / RAG
    "resource.read",  # resource contents (e.g. MCP resources/read)
    "control",  # a security control decision (guardrail, authz, approval, ...)
    "agent.discover",  # another agent's self-description (e.g. an A2A Agent Card)
    "agent.call",  # before a message/task is sent to another agent
    "agent.message",  # another agent's reply
    "payment.call",  # before a payment step (charge, mandate submission) executes
    "payment.result",  # the result of a payment step
    "mcp.tools",  # the full tool list returned by an MCP server
    "mcp.server_request",  # server-initiated MCP requests/notifications (sampling, elicitation, ...)
)


@dataclass
class Event:
    kind: str
    name: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "data": _jsonable(self.data)}


@dataclass
class Trace:
    """Everything observed during one run of the system under test."""

    events: list[Event] = field(default_factory=list)
    output: Any = None
    error: BaseException | None = None
    duration: float = 0.0
    canaries: set[str] = field(default_factory=set)

    def record(self, kind: str, name: str, **data: Any) -> Event:
        event = Event(kind, name, data)
        self.events.append(event)
        return event

    def of(self, kind: str, name: str = "*") -> list[Event]:
        return [e for e in self.events if e.kind == kind and fnmatch.fnmatchcase(e.name, name)]

    def tool_calls(self, name: str = "*") -> list[Event]:
        return self.of("tool.call", name)

    @property
    def faults(self) -> list[Event]:
        return self.of("fault")

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": _jsonable(self.output),
            "error": repr(self.error) if self.error else None,
            "duration": round(self.duration, 4),
            "events": [e.to_dict() for e in self.events],
        }


class Session:
    def __init__(self, faults: list[Fault] | None = None, seed: int | None = None) -> None:
        self.faults = list(faults or [])
        self.rng = random.Random(seed)
        self.trace = Trace()
        self._seen: Counter[tuple[int, str]] = Counter()  # per fault and target name
        self._fired: Counter[int] = Counter()
        self._history: dict[tuple[str, str], list[Any]] = {}

    def intercept(
        self, point: str, name: str, value: Any, *, _delays: list[float] | None = None, **context: Any
    ) -> Any:
        history = self._history.setdefault((point, name), [])
        previous = list(history)
        if value is not None:
            history.append(copy.deepcopy(value))
        for index, fault in enumerate(self.faults):
            if not fault.matches(point, name):
                continue
            self._seen[index, name] += 1
            if self._seen[index, name] <= fault.after_calls:
                continue
            if fault.max_injections is not None and self._fired[index] >= fault.max_injections:
                continue
            if self.rng.random() >= fault.probability:
                continue
            self._fired[index] += 1
            self.trace.record("fault", fault.kind, point=point, target=name, params=fault.params())
            value = fault.apply(value, InjectionContext(point, name, self, context, previous, _delays))
        return value


@dataclass
class InjectionContext:
    point: str
    name: str
    session: Session
    extra: dict[str, Any]
    #: Earlier (unfaulted) values seen at this point and target, oldest first - used by replay faults.
    previous: list[Any] = field(default_factory=list)
    #: Set inside async code: delays are collected here and awaited instead of slept (see :func:`aintercept`).
    deferred_delays: list[float] | None = None

    def sleep(self, seconds: float) -> None:
        """Delay the instrumented call without blocking an event loop when called from async code."""
        if self.deferred_delays is not None:
            self.deferred_delays.append(seconds)
        else:
            time.sleep(seconds)

    @property
    def rng(self) -> random.Random:
        return self.session.rng

    def register_canary(self, token: str) -> None:
        self.session.trace.canaries.add(token)


_current: contextvars.ContextVar[Session | None] = contextvars.ContextVar(
    "agentic_chaos_security_session", default=None
)


#: Kill switch: set this environment variable to 1/true/yes to make all instrumentation inert.
DISABLE_ENV = "AGENTIC_CHAOS_DISABLED"
#: Kill switch for running processes: if this environment variable names a file and the file exists,
#: all instrumentation is inert (checked at most once per second).
KILL_FILE_ENV = "AGENTIC_CHAOS_KILL_FILE"
_disabled_by_code = False
_kill_file_cache: tuple[float, bool] = (0.0, False)


def disable() -> None:
    """Make all instrumentation inert in this process (programmatic kill switch)."""
    global _disabled_by_code
    _disabled_by_code = True


def enable() -> None:
    global _disabled_by_code
    _disabled_by_code = False


def is_enabled() -> bool:
    """False if chaos is switched off by :func:`disable`, ``AGENTIC_CHAOS_DISABLED`` or the kill file."""
    global _kill_file_cache
    if _disabled_by_code or os.environ.get(DISABLE_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    kill_file = os.environ.get(KILL_FILE_ENV)
    if not kill_file:
        return True
    checked_at, killed = _kill_file_cache
    now = time.monotonic()
    if now - checked_at > 1.0:
        killed = os.path.exists(kill_file)
        _kill_file_cache = (now, killed)
    return not killed


def current() -> Session | None:
    """The active session, or None outside experiments and while the kill switch is on."""
    session = _current.get()
    if session is None or not is_enabled():
        return None
    return session


class suspended:
    """Context manager that pauses chaos in the current context, e.g. for test setup or seeding."""

    def __enter__(self) -> None:
        self._token = _current.set(None)

    def __exit__(self, *exc: object) -> None:
        _current.reset(self._token)


def intercept(point: str, name: str, value: Any = None, **context: Any) -> Any:
    """Pass ``value`` through any active faults for ``point``/``name``. No-op outside a session."""
    session = current()
    if session is None:
        return value
    return session.intercept(point, name, value, **context)


async def aintercept(point: str, name: str, value: Any = None, **context: Any) -> Any:
    """Like :func:`intercept`, for async code: latency faults are awaited instead of blocking the loop."""
    session = current()
    if session is None:
        return value
    delays: list[float] = []
    try:
        value = session.intercept(point, name, value, _delays=delays, **context)
    finally:
        if delays:
            await asyncio.sleep(sum(delays))
    return value


def record(kind: str, name: str, **data: Any) -> None:
    session = current()
    if session is not None:
        session.trace.record(kind, name, **data)


class _LogCapture(logging.Handler):
    def __init__(self, trace: Trace) -> None:
        super().__init__(level=logging.WARNING)
        self.trace = trace

    def emit(self, record: logging.LogRecord) -> None:
        self.trace.record("log", record.name, level=record.levelname, message=record.getMessage())


class bound:
    """Context manager that activates a session and captures WARNING+ log records into its trace."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def __enter__(self) -> Session:
        self._token = _current.set(self.session)
        self._handler = _LogCapture(self.session.trace)
        logging.getLogger().addHandler(self._handler)
        return self.session

    def __exit__(self, *exc: object) -> None:
        logging.getLogger().removeHandler(self._handler)
        _current.reset(self._token)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return repr(value)


def iter_strings(value: Any) -> Iterator[str]:
    """Yield every string nested inside ``value`` (used by leak-detection probes)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from iter_strings(k)
            yield from iter_strings(v)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            yield from iter_strings(v)
    elif value is not None and not isinstance(value, (int, float, bool)):
        yield str(value)
