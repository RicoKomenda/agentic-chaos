"""Report formats for experiment results: JUnit XML, Markdown and a self-contained HTML page.

All formats are built from :meth:`ExperimentResult.to_dict`, so they are redacted the same way as JSON
reports (see :mod:`agentic_chaos.redact`).
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import Any
from xml.etree import ElementTree as ET

from agentic_chaos.experiment import ExperimentResult, Verdict
from agentic_chaos.redact import Redactor

__all__ = ["to_html", "to_junit", "to_markdown"]

_MARK = {Verdict.HELD.value: "held", Verdict.WEAKNESS.value: "weakness", Verdict.INCONCLUSIVE.value: "inconclusive"}


def _reports(results: Sequence[ExperimentResult], redactor: Redactor | bool) -> list[dict[str, Any]]:
    return [r.to_dict(redactor=redactor) for r in results]


def _duration(result: ExperimentResult) -> float:
    return sum(run.trace.duration for run in [*result.baseline, *result.chaos])


def _probe_lines(report: dict[str, Any]) -> list[str]:
    lines = []
    for p in report["probes"]:
        if p["phase"] != "chaos":
            continue
        mark = "ok" if p["meets_threshold"] else "FAIL"
        lines.append(
            f"{mark} {p['name']}: {p['passes']}/{p['runs']} ({p['rate']:.0%}, CI {p['ci_low']:.0%}-{p['ci_high']:.0%})"
            f" needs >= {p['threshold']:.0%}"
        )
    return lines


def to_junit(
    results: Sequence[ExperimentResult], *, redactor: Redactor | bool = True, suite: str = "agentic-chaos"
) -> str:
    """JUnit XML: one test case per experiment. Weaknesses are failures, inconclusive results are errors."""
    reports = _reports(results, redactor)
    root = ET.Element("testsuites", name=suite)
    testsuite = ET.SubElement(root, "testsuite", name=suite)
    counts = {"tests": 0, "failures": 0, "errors": 0}
    total = 0.0
    for result, report in zip(results, reports, strict=True):
        duration = _duration(result)
        total += duration
        counts["tests"] += 1
        case = ET.SubElement(
            testsuite,
            "testcase",
            classname=".".join(["agentic_chaos", *report["tags"][:1]]) if report["tags"] else "agentic_chaos",
            name=report["name"],
            time=f"{duration:.3f}",
        )
        details = "\n".join([f"hypothesis: {report['hypothesis']}", *_probe_lines(report)])
        if report["verdict"] == Verdict.WEAKNESS.value:
            counts["failures"] += 1
            ET.SubElement(case, "failure", message=report["reason"], type="weakness-found").text = details
        elif report["verdict"] == Verdict.INCONCLUSIVE.value:
            counts["errors"] += 1
            ET.SubElement(case, "error", message=report["reason"], type="inconclusive").text = details
        else:
            ET.SubElement(case, "system-out").text = details
    for element in (root, testsuite):
        element.set("tests", str(counts["tests"]))
        element.set("failures", str(counts["failures"]))
        element.set("errors", str(counts["errors"]))
        element.set("time", f"{total:.3f}")
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def to_markdown(results: Sequence[ExperimentResult], *, redactor: Redactor | bool = True) -> str:
    """A Markdown summary table (e.g. for ``$GITHUB_STEP_SUMMARY``)."""
    reports = _reports(results, redactor)
    weaknesses = sum(r["verdict"] == Verdict.WEAKNESS.value for r in reports)
    inconclusive = sum(r["verdict"] == Verdict.INCONCLUSIVE.value for r in reports)
    lines = [
        "## Agentic Chaos",
        "",
        f"{len(reports)} experiment(s): **{weaknesses} weakness(es)**, {inconclusive} inconclusive",
        "",
        "| Experiment | Verdict | Reason |",
        "| --- | --- | --- |",
    ]
    for report in reports:
        reason = report["reason"].replace("|", "\\|")
        lines.append(f"| `{report['name']}` | {_MARK[report['verdict']]} | {reason} |")
    return "\n".join(lines) + "\n"


_CSS = """
:root { --fg: #1f2328; --muted: #59636e; --bg: #ffffff; --line: #d1d9e0;
  --ok: #1a7f37; --bad: #cf222e; --warn: #9a6700; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e6edf3; --muted: #9198a1; --bg: #0d1117; --line: #3d444d;
    --ok: #3fb950; --bad: #f85149; --warn: #d29922; }
}
body { font: 15px/1.5 system-ui, sans-serif; color: var(--fg); background: var(--bg);
  margin: 0 auto; padding: 24px 16px; max-width: 1100px; }
h1 { font-size: 24px; } h2 { font-size: 18px; margin: 28px 0 4px; }
.muted { color: var(--muted); } table { border-collapse: collapse; width: 100%; margin: 8px 0; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
.badge { font-weight: 600; } .held { color: var(--ok); } .weakness { color: var(--bad); }
.inconclusive { color: var(--warn); } code { font-size: 13px; } section { border-top: 1px solid var(--line); }
"""


def to_html(
    results: Sequence[ExperimentResult], *, redactor: Redactor | bool = True, title: str = "Agentic Chaos report"
) -> str:
    """A self-contained HTML report (no external resources), light and dark mode."""
    reports = _reports(results, redactor)
    e = html.escape
    rows = "".join(
        f"<tr><td><a href='#exp-{i}'><code>{e(r['name'])}</code></a></td>"
        f"<td class='badge {_MARK[r['verdict']]}'>{_MARK[r['verdict']]}</td><td>{e(r['reason'])}</td></tr>"
        for i, r in enumerate(reports)
    )
    sections = []
    for i, r in enumerate(reports):
        probes = "".join(
            f"<tr><td><code>{e(p['name'])}</code></td><td>{e(p['phase'])}</td><td>{p['passes']}/{p['runs']}</td>"
            f"<td>{p['rate']:.0%}</td><td>{p['ci_low']:.0%}-{p['ci_high']:.0%}</td><td>{p['threshold']:.0%}</td>"
            + ("<td class='held'>ok</td></tr>" if p["meets_threshold"] else "<td class='weakness'>fail</td></tr>")
            for p in r["probes"]
        )
        faults = "".join(f"<li><code>{e(str(f))}</code></li>" for f in r["faults"])
        tags = " ".join(f"<code>{e(t)}</code>" for t in r["tags"])
        sections.append(
            f"<section id='exp-{i}'><h2>{e(r['name'])} "
            f"<span class='badge {_MARK[r['verdict']]}'>{_MARK[r['verdict']]}</span></h2>"
            f"<p class='muted'>{tags}</p><p><strong>Hypothesis:</strong> {e(r['hypothesis'] or '-')}</p>"
            f"<p><strong>Reason:</strong> {e(r['reason'])}</p><h3>Probes</h3><table><tr><th>Probe</th><th>Phase</th>"
            f"<th>Passed</th><th>Rate</th><th>95% CI</th><th>Needs</th><th></th></tr>{probes}</table>"
            f"<h3>Faults</h3><ul>{faults}</ul></section>"
        )
    weaknesses = sum(r["verdict"] == Verdict.WEAKNESS.value for r in reports)
    return (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'><title>{e(title)}</title>"
        f"<style>{_CSS}</style></head><body><h1>{e(title)}</h1>"
        f"<p class='muted'>{len(reports)} experiment(s), {weaknesses} weakness(es)</p>"
        f"<table><tr><th>Experiment</th><th>Verdict</th><th>Reason</th></tr>{rows}</table>"
        f"{''.join(sections)}</body></html>\n"
    )
