"""Command line interface: ``agentic-chaos run experiments/*.yaml``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agentic_chaos import __version__, faults, loader, probes, report, runtime, schema
from agentic_chaos.experiment import Verdict
from agentic_chaos.redact import Redactor, redact

__all__ = [
    "main",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentic-chaos", description="Security chaos engineering for AI agents.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one or more experiment files")
    run.add_argument("files", nargs="+", type=Path, help="experiment files or directories")
    run.add_argument("--target", help="override spec.target.entrypoint (module:callable)")
    run.add_argument("--runs", type=int, help="override spec.runs")
    run.add_argument("--report", type=Path, help="write a JSON report to this path")
    run.add_argument("--traces", action="store_true", help="include full traces in the JSON report")
    run.add_argument("--no-redact", action="store_true", help="do not redact secrets in the report (unsafe)")
    run.add_argument("--redact-pattern", action="append", default=[], help="extra regex to redact (repeatable)")
    run.add_argument("--junit", type=Path, help="write a JUnit XML report (one test case per experiment)")
    run.add_argument("--html", type=Path, help="write a self-contained HTML report")
    run.add_argument("--markdown", type=Path, help="write a Markdown summary (e.g. $GITHUB_STEP_SUMMARY)")
    run.add_argument("--otel", action="store_true", help="export results as OpenTelemetry spans (needs the otel extra)")

    proxy = sub.add_parser(
        "mcp-proxy",
        help="run a fault-injecting MCP proxy in front of an MCP server (stdio or Streamable HTTP)",
        description=(
            "stdio:  agentic-chaos mcp-proxy --faults f.yaml -- npx -y <mcp-server-package>\n"
            "HTTP:   agentic-chaos mcp-proxy --faults f.yaml --upstream https://host/mcp --listen 127.0.0.1:8765"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    proxy.add_argument("--faults", type=Path, required=True, help="kind: McpProxy file with faults and probes")
    proxy.add_argument("--trace", type=Path, help="write the recorded trace and probe results as JSON on exit")
    proxy.add_argument("--no-redact", action="store_true", help="do not redact secrets in the trace file (unsafe)")
    proxy.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow --listen on a non-loopback address (the proxy has no authentication of its own)",
    )
    proxy.add_argument("--upstream", help="Streamable HTTP endpoint of the MCP server (HTTP mode)")
    proxy.add_argument("--listen", default="127.0.0.1:8765", help="address for HTTP mode (default 127.0.0.1:8765)")
    proxy.add_argument("server", nargs=argparse.REMAINDER, help="-- followed by the MCP server command (stdio mode)")

    check = sub.add_parser("validate", help="check experiment and proxy files without running them")
    check.add_argument("files", nargs="+", type=Path, help="files or directories")
    check.add_argument("--target", help="validate as if --target were passed to run")

    schema_cmd = sub.add_parser("schema", help="print the JSON Schema for experiment files")
    schema_cmd.add_argument("--output", type=Path, help="write to a file instead of stdout")

    sub.add_parser("faults", help="list available faults")
    sub.add_parser("probes", help="list available probes")

    args = parser.parse_args(argv)

    if args.command == "faults":
        for kind, cls in sorted(faults.FAULTS.items()):
            print(f"{kind:<26} {cls.category:<12} {','.join(cls.points):<40} {' '.join(cls.maps_to)}")
        return 0
    if args.command == "probes":
        for kind, fn in sorted(probes.PROBES.items()):
            print(f"{kind:<22} {(fn.__doc__ or '').strip().splitlines()[0] if fn.__doc__ else ''}")
        return 0

    if args.command == "mcp-proxy":
        try:
            return _mcp_proxy(args)
        except loader.ValidationError as exc:
            return _report_invalid(exc)
    if args.command == "schema":
        text = json.dumps(schema.json_schema(), indent=2) + "\n"
        if args.output:
            args.output.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0
    if args.command == "validate":
        return _validate(args)

    if not runtime.is_enabled():
        print(f"chaos is switched off ({runtime.DISABLE_ENV} or {runtime.KILL_FILE_ENV}); not running", file=sys.stderr)
        return 2

    results = []
    for path in loader.expand(args.files):
        try:
            experiment = loader.load(path, entrypoint=args.target, runs=args.runs)
        except loader.ValidationError as exc:
            return _report_invalid(exc)
        result = experiment.run()
        results.append(result)
        print(result.summary(), end="\n\n")

    redactor: Redactor | bool = False if args.no_redact else Redactor(extra_patterns=args.redact_pattern)
    outputs = {args.junit: report.to_junit, args.html: report.to_html, args.markdown: report.to_markdown}
    for path, render in outputs.items():
        if path:
            mode = "a" if args.markdown and path == args.markdown else "w"  # step summaries are appended to
            with open(path, mode, encoding="utf-8") as handle:
                handle.write(render(results, redactor=redactor))
            print(f"report written to {path}")
    if args.otel:
        from agentic_chaos.integrations import otel

        otel.export(results, redactor=redactor)
    if args.report:
        args.report.write_text(
            json.dumps([r.to_dict(args.traces, redactor=redactor) for r in results], indent=2), encoding="utf-8"
        )
        print(f"report written to {args.report}")

    weaknesses = sum(r.verdict is Verdict.WEAKNESS for r in results)
    inconclusive = sum(r.verdict is Verdict.INCONCLUSIVE for r in results)
    print(f"{len(results)} experiment(s): {weaknesses} weakness(es), {inconclusive} inconclusive")
    return 1 if weaknesses else (2 if inconclusive else 0)


def _is_loopback(host: str) -> bool:
    import ipaddress

    if host in ("", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _report_invalid(exc: Exception) -> int:
    print(f"invalid experiment file:\n{exc}", file=sys.stderr)
    return 2


def _validate(args: argparse.Namespace) -> int:
    paths = [p for f in args.files for p in (sorted(f.rglob("*.yaml")) if f.is_dir() else [f])]
    invalid = 0
    for path in paths:
        try:
            doc = loader.read(path)
            if isinstance(doc, dict) and doc.get("kind") == "McpProxy":
                loader.load_proxy_config(path)
            else:
                loader.load(path, entrypoint=args.target)
            print(f"ok       {path}")
        except loader.ValidationError as exc:
            invalid += 1
            print(f"invalid  {exc}")
    print(f"{len(paths)} file(s), {invalid} invalid")
    return 1 if invalid else 0


def _mcp_proxy(args: argparse.Namespace) -> int:
    import asyncio
    import signal

    from agentic_chaos.mcp import McpChaosProxy, StdioEndpoint
    from agentic_chaos.runtime import Session, bound

    command = args.server[1:] if args.server[:1] == ["--"] else args.server
    if not command and not args.upstream:
        print("error: give --upstream URL (HTTP) or -- followed by the server command (stdio)", file=sys.stderr)
        return 2
    fault_list, probe_list, seed = loader.load_proxy_config(args.faults)
    session = Session(fault_list, seed=seed)

    async def serve() -> None:
        with bound(session):
            if args.upstream:
                from agentic_chaos.mcp.http import McpHttpProxy

                host, _, port = args.listen.rpartition(":")
                if not _is_loopback(host) and not args.allow_remote:
                    raise SystemExit(
                        f"refusing to listen on {host}: the proxy forwards credentials and has no authentication; "
                        "use --allow-remote if this is intended"
                    )
                proxy_http = McpHttpProxy(args.upstream)
                server = await proxy_http.serve(host or "127.0.0.1", int(port))
                print(f"[agentic-chaos] MCP chaos proxy listening on {proxy_http.url}", file=sys.stderr)
                stop = asyncio.Event()
                for name in ("SIGINT", "SIGTERM"):
                    try:  # POSIX: stop cleanly so probes run and the trace is written
                        asyncio.get_running_loop().add_signal_handler(getattr(signal, name), stop.set)
                    except (NotImplementedError, AttributeError, RuntimeError):
                        pass  # Windows: Ctrl-C raises KeyboardInterrupt instead
                async with server:
                    await stop.wait()
            else:
                proxy = McpChaosProxy(command)
                await proxy.start(await StdioEndpoint().open())
                await proxy.wait()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    results = [p(session.trace) for p in probe_list]
    for r in results:  # stdout belongs to the MCP protocol; report on stderr
        print(f"[agentic-chaos] {'PASS' if r.passed else 'FAIL'} {r.name} {r.detail}".rstrip(), file=sys.stderr)
    if args.trace:
        report: Any = {"probes": [r.to_dict() for r in results], "trace": session.trace.to_dict()}
        if not args.no_redact:
            report = redact(report)
        args.trace.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
