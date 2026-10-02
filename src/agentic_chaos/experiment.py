"""Experiments: hypothesis + steady state + faults, run as baseline vs. chaos."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agentic_chaos.faults import Fault
from agentic_chaos.probes import Probe, ProbeResult
from agentic_chaos.runtime import Session, Trace, bound


class Verdict(str, Enum):
    HELD = "hypothesis-held"
    WEAKNESS = "weakness-found"
    INCONCLUSIVE = "inconclusive"


@dataclass
class RunResult:
    phase: str
    index: int
    trace: Trace
    probes: list[ProbeResult]

    @property
    def passed(self) -> bool:
        return all(p.passed for p in self.probes)

    @property
    def faults_fired(self) -> int:
        return len(self.trace.faults)

    def to_dict(self, include_trace: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "phase": self.phase,
            "run": self.index,
            "passed": self.passed,
            "faults_fired": self.faults_fired,
            "probes": [p.to_dict() for p in self.probes],
        }
        if include_trace:
            data["trace"] = self.trace.to_dict()
        return data


@dataclass
class ExperimentResult:
    experiment: Experiment
    baseline: list[RunResult]
    chaos: list[RunResult]
    verdict: Verdict
    reason: str

    @staticmethod
    def _rate(runs: list[RunResult]) -> float | None:
        return sum(r.passed for r in runs) / len(runs) if runs else None

    @property
    def baseline_pass_rate(self) -> float | None:
        return self._rate(self.baseline)

    @property
    def chaos_pass_rate(self) -> float | None:
        return self._rate(self.chaos)

    def to_dict(self, include_traces: bool = False) -> dict[str, Any]:
        exp = self.experiment
        return {
            "name": exp.name,
            "hypothesis": exp.hypothesis,
            "tags": exp.tags,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "baseline_pass_rate": self.baseline_pass_rate,
            "chaos_pass_rate": self.chaos_pass_rate,
            "faults": [{"type": f.kind, **f.params()} for f in exp.faults],
            "runs": [r.to_dict(include_traces) for r in [*self.baseline, *self.chaos]],
        }

    def summary(self) -> str:
        def pct(rate: float | None) -> str:
            return "n/a" if rate is None else f"{rate:.0%}"

        lines = [
            f"{self.experiment.name}: {self.verdict.value.upper()}",
            f"  hypothesis : {self.experiment.hypothesis or '-'}",
            f"  baseline   : {pct(self.baseline_pass_rate)} of {len(self.baseline)} run(s) passed",
            f"  chaos      : {pct(self.chaos_pass_rate)} of {len(self.chaos)} run(s) passed",
            f"  reason     : {self.reason}",
        ]
        failed: dict[str, str] = {}
        for run in self.chaos:
            for p in run.probes:
                if not p.passed:
                    failed.setdefault(p.name, p.detail)
        for name, detail in failed.items():
            lines.append(f"  violated   : {name}" + (f" - {detail}" if detail else ""))
        return "\n".join(lines)


@dataclass
class Experiment:
    """A security chaos experiment.

    ``target`` is the system under test: a zero-argument callable (sync or async) that runs one
    interaction with the agent and returns its final output. ``probes`` define the steady state and
    are checked in baseline and chaos runs. ``detection`` probes are checked in chaos runs only
    (e.g. "an alert was raised").
    """

    name: str
    target: Callable[[], Any]
    faults: list[Fault]
    probes: list[Probe]
    hypothesis: str = ""
    detection: list[Probe] = field(default_factory=list)
    runs: int = 1
    seed: int | None = 0
    baseline: bool = True
    tags: list[str] = field(default_factory=list)

    def run(self) -> ExperimentResult:
        baseline = [self._run_once("baseline", i, []) for i in range(self.runs)] if self.baseline else []
        chaos = [self._run_once("chaos", i, self.faults) for i in range(self.runs)]
        verdict, reason = self._judge(baseline, chaos)
        return ExperimentResult(self, baseline, chaos, verdict, reason)

    def _run_once(self, phase: str, index: int, faults: list[Fault]) -> RunResult:
        seed = None if self.seed is None else self.seed + index
        session = Session(faults, seed=seed)
        trace = session.trace
        start = time.monotonic()
        with bound(session):
            try:
                result = self.target()
                if inspect.isawaitable(result):
                    result = asyncio.run(_await(result))
                trace.output = result
            except Exception as exc:  # the system under test failing is data, not a crash
                trace.error = exc
        trace.duration = time.monotonic() - start
        probes = [*self.probes, *(self.detection if phase == "chaos" else [])]
        return RunResult(phase, index, trace, [p(trace) for p in probes])

    @staticmethod
    def _judge(baseline: list[RunResult], chaos: list[RunResult]) -> tuple[Verdict, str]:
        if baseline and not all(r.passed for r in baseline):
            return Verdict.INCONCLUSIVE, "steady state does not hold without chaos - fix the baseline first"
        if not any(r.faults_fired for r in chaos):
            return Verdict.INCONCLUSIVE, "no fault was triggered - check fault targets and instrumentation"
        failed = [r for r in chaos if not r.passed]
        if failed:
            return Verdict.WEAKNESS, f"steady state violated in {len(failed)}/{len(chaos)} chaos run(s)"
        return Verdict.HELD, "steady state held under all chaos runs"


async def _await(awaitable: Any) -> Any:
    return await awaitable
