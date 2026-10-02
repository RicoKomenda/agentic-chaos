"""Experiments: hypothesis + steady state + faults, run as baseline vs. chaos.

Agents are non-deterministic, so every probe is judged by its *pass rate* across runs against a
threshold (``min_pass_rate``, default 1.0 = must always hold). Each rate is reported with a Wilson
confidence interval. With ``require_confidence``, a rate whose interval still straddles the
threshold makes the experiment inconclusive instead of deciding on a point estimate.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agentic_chaos_security.faults import Fault
from agentic_chaos_security.probes import Probe, ProbeResult
from agentic_chaos_security.redact import Redactor, redact
from agentic_chaos_security.runtime import Session, Trace, bound
from agentic_chaos_security.stats import runs_needed, wilson_interval

__all__ = [
    "Experiment",
    "ExperimentResult",
    "ProbeStats",
    "RunResult",
    "Verdict",
]


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
class ProbeStats:
    """How often one probe held in one phase."""

    name: str
    phase: str
    passes: int
    runs: int
    threshold: float
    confidence: float
    detail: str = ""

    @property
    def rate(self) -> float:
        return self.passes / self.runs if self.runs else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.passes, self.runs, self.confidence)

    @property
    def meets_threshold(self) -> bool:
        return self.rate >= self.threshold

    @property
    def confident(self) -> bool:
        """True if the confidence interval lies entirely on one side of the threshold."""
        if self.threshold >= 1.0:
            return True  # any failure decides; all passes is the strongest possible evidence
        low, high = self.interval
        return low >= self.threshold or high < self.threshold

    def to_dict(self) -> dict[str, Any]:
        low, high = self.interval
        return {
            "name": self.name,
            "phase": self.phase,
            "passes": self.passes,
            "runs": self.runs,
            "rate": self.rate,
            "ci_low": round(low, 4),
            "ci_high": round(high, 4),
            "threshold": self.threshold,
            "meets_threshold": self.meets_threshold,
        }

    def describe(self) -> str:
        low, high = self.interval
        mark = "ok  " if self.meets_threshold else "FAIL"
        line = f"{mark} {self.name}: {self.passes}/{self.runs} ({self.rate:.0%}"
        if self.runs > 1:
            line += f", {self.confidence:.0%} CI {low:.0%}-{high:.0%}"
        line += f") needs >= {self.threshold:.0%}"
        return line + (f" - {self.detail}" if self.detail and not self.meets_threshold else "")


@dataclass
class ExperimentResult:
    experiment: Experiment
    baseline: list[RunResult]
    chaos: list[RunResult]
    verdict: Verdict
    reason: str
    probe_stats: list[ProbeStats] = field(default_factory=list)

    @staticmethod
    def _rate(runs: list[RunResult]) -> float | None:
        return sum(r.passed for r in runs) / len(runs) if runs else None

    @property
    def baseline_pass_rate(self) -> float | None:
        return self._rate(self.baseline)

    @property
    def chaos_pass_rate(self) -> float | None:
        return self._rate(self.chaos)

    def to_dict(self, include_traces: bool = False, *, redactor: Redactor | None | bool = True) -> dict[str, Any]:
        """A JSON-ready report. Secrets are redacted unless ``redactor=False``.

        See :mod:`agentic_chaos_security.redact`.
        """
        report = self._report(include_traces)
        if redactor is False:
            return report
        redacted: dict[str, Any] = redact(report, None if redactor is True else redactor)
        return redacted

    def _report(self, include_traces: bool) -> dict[str, Any]:
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
            "probes": [p.to_dict() for p in self.probe_stats],
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
        chaos_stats = [p for p in self.probe_stats if p.phase == "chaos"]
        if len(self.chaos) > 1 or any(p.threshold < 1 for p in chaos_stats):
            lines += [f"  probe      : {p.describe()}" for p in chaos_stats]
        else:
            lines += [
                f"  violated   : {p.name}" + (f" - {p.detail}" if p.detail else "")
                for p in chaos_stats
                if not p.meets_threshold
            ]
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
    #: Default ``min_pass_rate`` for probes that do not set their own (see :func:`probes.expect`).
    pass_rate: float = 1.0
    confidence: float = 0.95
    #: Make the verdict inconclusive while a probe's confidence interval straddles its threshold.
    require_confidence: bool = False

    def run(self) -> ExperimentResult:
        baseline = [self._run_once("baseline", i, []) for i in range(self.runs)] if self.baseline else []
        chaos = [self._run_once("chaos", i, self.faults) for i in range(self.runs)]
        stats = self._stats("baseline", baseline) + self._stats("chaos", chaos)
        verdict, reason = self._judge(baseline, chaos, stats)
        return ExperimentResult(self, baseline, chaos, verdict, reason, stats)

    def _threshold(self, probe: Probe) -> float:
        return float(getattr(probe, "min_pass_rate", self.pass_rate))

    def _stats(self, phase: str, runs: list[RunResult]) -> list[ProbeStats]:
        if not runs:
            return []
        probes = [*self.probes, *(self.detection if phase == "chaos" else [])]
        stats = []
        for index, probe in enumerate(probes):
            results = [run.probes[index] for run in runs]
            failed = next((r for r in results if not r.passed), None)
            stats.append(
                ProbeStats(
                    name=results[0].name,
                    phase=phase,
                    passes=sum(r.passed for r in results),
                    runs=len(results),
                    threshold=self._threshold(probe),
                    confidence=self.confidence,
                    detail=failed.detail if failed else "",
                )
            )
        return stats

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

    def _judge(self, baseline: list[RunResult], chaos: list[RunResult], stats: list[ProbeStats]) -> tuple[Verdict, str]:
        below = [s for s in stats if s.phase == "baseline" and not s.meets_threshold]
        if below:
            names = ", ".join(s.name for s in below)
            return Verdict.INCONCLUSIVE, f"steady state does not hold without chaos ({names}) - fix the baseline first"
        if not any(r.faults_fired for r in chaos):
            return Verdict.INCONCLUSIVE, "no fault was triggered - check fault targets and instrumentation"
        chaos_stats = [s for s in stats if s.phase == "chaos"]
        if self.require_confidence:
            undecided = [s for s in chaos_stats if not s.confident]
            if undecided:
                hint = "; ".join(
                    f"{s.name} needs about {runs_needed(s.threshold, s.confidence)} runs" for s in undecided
                )
                return Verdict.INCONCLUSIVE, f"not enough runs to decide at {self.confidence:.0%} confidence ({hint})"
        failing = [s for s in chaos_stats if not s.meets_threshold]
        if failing:
            if all(s.threshold >= 1 for s in failing):
                failed_runs = sum(not r.passed for r in chaos)
                return Verdict.WEAKNESS, f"steady state violated in {failed_runs}/{len(chaos)} chaos run(s)"
            names = ", ".join(f"{s.name} {s.rate:.0%} < {s.threshold:.0%}" for s in failing)
            return Verdict.WEAKNESS, f"pass rate below threshold: {names}"
        return Verdict.HELD, "steady state held at the required pass rates" if any(
            s.threshold < 1 for s in chaos_stats
        ) else "steady state held under all chaos runs"


async def _await(awaitable: Any) -> Any:
    return await awaitable
