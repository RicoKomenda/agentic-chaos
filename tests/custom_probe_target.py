"""A target module that registers its own probe, as extensions do."""

from agentic_chaos.probes import PROBES, ProbeResult


def always_fine():
    return lambda trace: ProbeResult("always_fine", True)


PROBES["always_fine"] = always_fine


def run() -> str:
    return "ok"
