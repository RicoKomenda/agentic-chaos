"""pytest integration (registered automatically when agentic-chaos is installed).

Nothing changes unless you use it:

* the ``chaos`` fixture runs experiments inside ordinary tests::

      def test_guardrail_fails_closed(chaos):
          chaos.assert_held("experiments/control-guardrail-outage.yaml", target="my_app.agent:handle")

* experiment files become test items when listed in the ``chaos_experiments`` ini option::

      [tool.pytest.ini_options]
      chaos_experiments = ["experiments/*.yaml", "experiments/mcp/*.yaml"]

  Weaknesses fail; inconclusive results fail too unless ``--chaos-inconclusive=skip``.

Options: ``--chaos-runs N`` overrides ``runs`` everywhere, ``--chaos-target`` overrides the entrypoint of
collected files.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agentic_chaos import loader
from agentic_chaos.experiment import Experiment, ExperimentResult, Verdict

__all__ = ["ChaosFixture", "ChaosHypothesisFailed"]


class ChaosHypothesisFailed(AssertionError):
    """An experiment did not hold; the message is the experiment summary."""


class ChaosFixture:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.results: list[ExperimentResult] = []

    def run(
        self, experiment: Experiment | str | Path, *, target: str | None = None, runs: int | None = None
    ) -> ExperimentResult:
        runs = runs or self.config.getoption("chaos_runs")
        if isinstance(experiment, Experiment):
            if runs:
                experiment.runs = runs
            result = experiment.run()
        else:
            result = loader.load(experiment, entrypoint=target, runs=runs).run()
        self.results.append(result)
        return result

    def assert_held(self, experiment: Experiment | str | Path, **kwargs: Any) -> ExperimentResult:
        result = self.run(experiment, **kwargs)
        if result.verdict is not Verdict.HELD:
            raise ChaosHypothesisFailed("\n" + result.summary())
        return result


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("agentic-chaos")
    group.addoption("--chaos-runs", type=int, default=None, help="override runs for every experiment")
    group.addoption("--chaos-target", default=None, help="override the entrypoint of collected experiment files")
    group.addoption(
        "--chaos-inconclusive",
        choices=("fail", "skip"),
        default="fail",
        help="how collected experiments with an inconclusive verdict are reported",
    )
    parser.addini("chaos_experiments", type="args", default=[], help="glob(s) of experiment files to collect as tests")


@pytest.fixture
def chaos(request: pytest.FixtureRequest) -> ChaosFixture:
    return ChaosFixture(request.config)


def pytest_collect_file(parent: pytest.Collector, file_path: Path) -> pytest.Collector | None:
    patterns: list[str] = parent.config.getini("chaos_experiments")
    if not patterns or file_path.suffix not in (".yaml", ".yml"):
        return None
    relative = file_path.relative_to(parent.config.rootpath).as_posix()
    if any(fnmatch.fnmatch(relative, p) for p in patterns):
        return ExperimentFile.from_parent(parent, path=file_path)
    return None


class ExperimentFile(pytest.File):
    def collect(self) -> Iterator[pytest.Item]:
        doc = loader.read(self.path)
        if isinstance(doc, dict) and doc.get("kind") == "Experiment":
            name = (doc.get("metadata") or {}).get("name") or self.path.stem
            yield ExperimentItem.from_parent(self, name=str(name))


class ExperimentItem(pytest.Item):
    def runtest(self) -> None:
        config = self.config
        result = loader.load(
            self.path, entrypoint=config.getoption("chaos_target"), runs=config.getoption("chaos_runs")
        ).run()
        self.user_properties.append(("verdict", result.verdict.value))
        if result.verdict is Verdict.INCONCLUSIVE and config.getoption("chaos_inconclusive") == "skip":
            pytest.skip(f"inconclusive: {result.reason}")
        if result.verdict is not Verdict.HELD:
            raise ChaosHypothesisFailed("\n" + result.summary())

    def repr_failure(self, excinfo: pytest.ExceptionInfo[BaseException], style: Any = None) -> str:
        if isinstance(excinfo.value, (ChaosHypothesisFailed, loader.ValidationError)):
            return str(excinfo.value)
        return str(super().repr_failure(excinfo))

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, 0, f"chaos experiment: {self.name}"
