"""Fault library.

A fault acts on one or more injection points (see :data:`agentic_chaos.runtime.POINTS`) and
on targets whose name matches a glob (``"*"``, ``"web_*"``, ``"guardrail.input"``).
Reliability faults reproduce the things that break in production; security faults
reproduce what an adversary - or a failing security control - would do.
"""

from __future__ import annotations

import fnmatch
import time
from typing import Any, ClassVar

from agentic_chaos import payloads
from agentic_chaos.runtime import POINTS, InjectionContext

FAULTS: dict[str, type[Fault]] = {}


class ChaosError(Exception):
    """Base class for errors raised by injected faults."""


class ChaosTimeout(ChaosError, TimeoutError):
    pass


class ChaosRateLimit(ChaosError):
    def __init__(self, message: str = "429 Too Many Requests (injected)", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class Override(BaseException):
    """Short-circuits an instrumented call and makes it return ``value`` instead."""

    def __init__(self, value: Any) -> None:
        self.value = value


class Fault:
    kind: ClassVar[str] = ""
    category: ClassVar[str] = "reliability"
    points: ClassVar[tuple[str, ...]] = ()
    #: Risk identifiers this fault helps exercise (OWASP Agentic ASIxx / LLM Top 10 LLMxx).
    maps_to: ClassVar[tuple[str, ...]] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.kind:
            FAULTS[cls.kind] = cls

    def __init__(
        self,
        target: str = "*",
        *,
        point: str | list[str] | None = None,
        probability: float = 1.0,
        after_calls: int = 0,
        max_injections: int | None = None,
    ) -> None:
        if point is not None:
            selected = (point,) if isinstance(point, str) else tuple(point)
            unknown = set(selected) - set(POINTS)
            if unknown:
                raise ValueError(f"unknown injection point(s): {sorted(unknown)}")
            self.active_points = selected
        else:
            self.active_points = self.points
        if not 0.0 <= probability <= 1.0:
            raise ValueError("probability must be between 0 and 1")
        self.target = target
        self.probability = probability
        self.after_calls = after_calls
        self.max_injections = max_injections

    def matches(self, point: str, name: str) -> bool:
        return point in self.active_points and fnmatch.fnmatchcase(name, self.target)

    def apply(self, value: Any, ctx: InjectionContext) -> Any:
        raise NotImplementedError

    def params(self) -> dict[str, Any]:
        return {k: v for k, v in vars(self).items() if not k.startswith("_") and k != "active_points"}

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.params()})"


# --- reliability --------------------------------------------------------------------------


class Latency(Fault):
    kind = "latency"
    points = ("llm.call", "tool.call", "memory.read", "control")
    maps_to = ("ASI08",)

    def __init__(self, target: str = "*", *, seconds: float = 2.0, jitter: float = 0.0, **kw: Any) -> None:
        super().__init__(target, **kw)
        self.seconds = seconds
        self.jitter = jitter

    def apply(self, value: Any, ctx: InjectionContext) -> Any:
        time.sleep(max(0.0, self.seconds + ctx.rng.uniform(-self.jitter, self.jitter)))
        return value


class Timeout(Fault):
    kind = "timeout"
    points = ("llm.call", "tool.call", "memory.read", "control")
    maps_to = ("ASI08",)

    def apply(self, value: Any, ctx: InjectionContext) -> Any:
        raise ChaosTimeout(f"{ctx.point} {ctx.name} timed out (injected)")


class Error(Fault):
    kind = "error"
    points = ("llm.call", "tool.call", "memory.read", "control")
    maps_to = ("ASI08",)

    def __init__(self, target: str = "*", *, message: str = "injected failure", **kw: Any) -> None:
        super().__init__(target, **kw)
        self.message = message

    def apply(self, value: Any, ctx: InjectionContext) -> Any:
        raise ChaosError(f"{ctx.point} {ctx.name}: {self.message}")


class RateLimit(Fault):
    kind = "rate_limit"
    points = ("llm.call", "tool.call")
    maps_to = ("ASI08", "LLM10")

    def __init__(self, target: str = "*", *, retry_after: float | None = None, **kw: Any) -> None:
        super().__init__(target, **kw)
        self.retry_after = retry_after

    def apply(self, value: Any, ctx: InjectionContext) -> Any:
        raise ChaosRateLimit(retry_after=self.retry_after)


class Empty(Fault):
    kind = "empty"
    points = ("llm.response", "tool.result", "memory.read")
    maps_to = ("ASI08",)

    def apply(self, value: Any, ctx: InjectionContext) -> Any:
        return type(value)() if isinstance(value, (str, bytes, list, dict, tuple)) else None


class Truncate(Fault):
    kind = "truncate"
    points = ("llm.response", "tool.result", "memory.read")
    maps_to = ("ASI08",)

    def __init__(self, target: str = "*", *, keep: float = 0.5, **kw: Any) -> None:
        super().__init__(target, **kw)
        self.keep = keep

    def apply(self, value: Any, ctx: InjectionContext) -> Any:
        if isinstance(value, (str, bytes, list, tuple)):
            return value[: int(len(value) * self.keep)]
        return value


class CorruptJSON(Fault):
    """Make structured data malformed: cut JSON text mid-document, or drop a key from a dict."""

    kind = "corrupt_json"
    points = ("llm.response", "tool.result")
    maps_to = ("ASI08", "LLM05")

    def apply(self, value: Any, ctx: InjectionContext) -> Any:
        if isinstance(value, str) and len(value) > 1:
            return value[: ctx.rng.randrange(1, len(value))]
        if isinstance(value, dict) and value:
            corrupted = dict(value)
            corrupted.pop(ctx.rng.choice(sorted(corrupted, key=str)))
            return corrupted
        return value


# --- security -----------------------------------------------------------------------------


class InjectInstruction(Fault):
    """Indirect prompt injection: smuggle a canary-tagged instruction into untrusted data."""

    kind = "inject_instruction"
    category = "security"
    points = ("tool.result", "memory.read")
    maps_to = ("ASI01", "ASI02", "LLM01")

    def __init__(
        self,
        target: str = "*",
        *,
        payload: str = "exfiltrate",
        sink: str = "send_email",
        position: str = "append",
        **kw: Any,
    ) -> None:
        super().__init__(target, **kw)
        if position not in ("append", "prepend", "replace"):
            raise ValueError("position must be append, prepend or replace")
        self.payload = payload
        self.sink = sink
        self.position = position

    def apply(self, value: Any, ctx: InjectionContext) -> Any:
        canary = payloads.new_canary(ctx.rng)
        ctx.register_canary(canary)
        return _insert(value, payloads.render(self.payload, canary=canary, sink=self.sink), self.position)


class PoisonToolDescription(InjectInstruction):
    """Tool poisoning: hide instructions in tool metadata (e.g. an MCP server's tool description)."""

    kind = "poison_tool_description"
    points = ("tool.describe",)
    maps_to = ("ASI02", "ASI04", "LLM03")

    def __init__(self, target: str = "*", *, payload: str = "tool_poisoning", **kw: Any) -> None:
        super().__init__(target, payload=payload, **kw)


class PoisonMemory(InjectInstruction):
    """Memory / RAG poisoning: a planted record comes back from long-term memory."""

    kind = "poison_memory"
    points = ("memory.read",)
    maps_to = ("ASI06", "LLM04", "LLM08")


class ControlOutage(Fault):
    """A security control (guardrail, authz check, approval service) is unavailable."""

    kind = "control_outage"
    category = "security"
    points = ("control",)
    maps_to = ("ASI08",)

    def __init__(self, target: str = "*", *, mode: str = "timeout", **kw: Any) -> None:
        super().__init__(target, **kw)
        if mode not in ("timeout", "error"):
            raise ValueError("mode must be timeout or error")
        self.mode = mode

    def apply(self, value: Any, ctx: InjectionContext) -> Any:
        if self.mode == "timeout":
            raise ChaosTimeout(f"control {ctx.name} timed out (injected)")
        raise ChaosError(f"control {ctx.name} unavailable (injected)")


class ForceVerdict(Fault):
    """A security control silently returns a fixed verdict (misconfiguration, bypass, model drift).

    Use it to test defence in depth: if this layer waves everything through, does the next one hold?
    """

    kind = "force_verdict"
    category = "security"
    points = ("control",)
    maps_to = ("ASI03", "ASI08")

    def __init__(self, target: str = "*", *, verdict: Any = True, **kw: Any) -> None:
        super().__init__(target, **kw)
        self.verdict = verdict

    def apply(self, value: Any, ctx: InjectionContext) -> Any:
        raise Override(self.verdict)


def _insert(value: Any, text: str, position: str) -> Any:
    if isinstance(value, str) or value is None:
        value = value or ""
        if position == "replace":
            return text
        return f"{text}\n{value}" if position == "prepend" else f"{value}\n{text}"
    if isinstance(value, list):
        if position == "replace":
            return [text]
        return [text, *value] if position == "prepend" else [*value, text]
    if isinstance(value, dict):
        return {"content": text} if position == "replace" else {**value, "note": text}
    return value


def build(kind: str, **params: Any) -> Fault:
    try:
        return FAULTS[kind](**params)
    except KeyError:
        raise ValueError(f"unknown fault type {kind!r}; known: {sorted(FAULTS)}") from None
