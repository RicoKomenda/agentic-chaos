import json
from xml.etree import ElementTree as ET

import pytest

import agentic_chaos as chaos
from agentic_chaos import Experiment, faults, probes, report
from agentic_chaos.cli import main

SECRET = "sk-live-0123456789abcdefABCDEF"


@chaos.tool(name="lookup")
def lookup(key):
    return f"value {key}"


def experiment(name: str, fault: faults.Fault, probe) -> Experiment:
    return Experiment(name=name, target=lambda: lookup(SECRET), faults=[fault], probes=[probe], tags=["ASI08"])


@pytest.fixture(scope="module")
def results():
    return [
        experiment("holds", faults.Latency("lookup", seconds=0), probes.no_unhandled_error()).run(),
        experiment("breaks", faults.Timeout("lookup"), probes.no_unhandled_error()).run(),
        experiment("misses", faults.Timeout("nothing"), probes.no_unhandled_error()).run(),
    ]


def test_junit(results):
    root = ET.fromstring(report.to_junit(results))
    assert root.get("tests") == "3" and root.get("failures") == "1" and root.get("errors") == "1"
    cases = {c.get("name"): c for c in root.iter("testcase")}
    assert cases["breaks"].find("failure") is not None and cases["misses"].find("error") is not None
    assert cases["holds"].get("classname") == "agentic_chaos.ASI08"


def test_markdown(results):
    text = report.to_markdown(results)
    assert "**1 weakness(es)**" in text and "| `breaks` | weakness |" in text


def test_html_is_escaped_and_redacted(results):
    page = report.to_html(results, title="<x>")
    assert "<title>&lt;x&gt;</title>" in page and "weakness" in page
    assert SECRET not in report.to_html(results)


def test_otel_export(results):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from agentic_chaos.integrations import otel

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel.export(results, tracer_provider=provider)
    spans = exporter.get_finished_spans()
    experiments = [s for s in spans if s.name.startswith("chaos.experiment")]
    runs = [s for s in spans if s.name.startswith("chaos.run")]
    assert len(experiments) == 3 and len(runs) == 6
    assert {s.attributes["chaos.verdict"] for s in experiments} == {"hypothesis-held", "weakness-found", "inconclusive"}
    assert SECRET not in json.dumps([dict(e.attributes) for s in runs for e in s.events])


def test_cli_writes_all_formats(tmp_path):
    junit, page, summary = tmp_path / "r.xml", tmp_path / "r.html", tmp_path / "summary.md"
    summary.write_text("existing\n", encoding="utf-8")
    code = main(
        [
            "run",
            "experiments/asi08-tool-outage.yaml",
            "--junit",
            str(junit),
            "--html",
            str(page),
            "--markdown",
            str(summary),
        ]
    )
    assert code == 1
    assert ET.fromstring(junit.read_text(encoding="utf-8")).get("failures") == "1"
    assert page.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert summary.read_text(encoding="utf-8").startswith("existing\n## Agentic Chaos")
