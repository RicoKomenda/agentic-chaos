"""Agentic Chaos - security chaos engineering for AI agents and LLM applications."""

from importlib import metadata as _metadata

from agentic_chaos_security import ap2, faults, probes
from agentic_chaos_security.experiment import Experiment, ExperimentResult, ProbeStats, Verdict
from agentic_chaos_security.inject import (
    agent,
    control,
    describe_tool,
    discover_agent,
    llm,
    memory,
    output,
    payment,
    tool,
)
from agentic_chaos_security.runtime import intercept, suspended

try:
    __version__ = _metadata.version("agentic-chaos-security")
except _metadata.PackageNotFoundError:  # pragma: no cover - running from a source tree without installing
    __version__ = "0.0.0+unknown"

__all__ = [
    "Experiment",
    "ExperimentResult",
    "ProbeStats",
    "Verdict",
    "ap2",
    "agent",
    "control",
    "describe_tool",
    "discover_agent",
    "faults",
    "intercept",
    "llm",
    "memory",
    "output",
    "payment",
    "probes",
    "suspended",
    "tool",
]
