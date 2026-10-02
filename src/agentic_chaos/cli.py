"""Command line interface: ``agentic-chaos run experiments/*.yaml``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentic_chaos import __version__, faults, loader, probes
from agentic_chaos.experiment import Verdict


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

    proxy = sub.add_parser(
        "mcp-proxy",
        help="run a fault-injecting MCP proxy (stdio) in front of an MCP server",
        description="Example: agentic-chaos mcp-proxy --faults rug-pull.yaml -- npx -y <mcp-server-package>",
    )
    proxy.add_argument("--faults", type=Path, required=True, help="kind: McpProxy file with faults and probes")
    proxy.add_argument("--trace", type=Path, help="write the recorded trace and probe results as JSON on exit")
    proxy.add_argument("server", nargs=argparse.REMAINDER, help="-- followed by the MCP server command")

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
        return _mcp_proxy(args)

    results = []
    for path in loader.expand(args.files):
        experiment = loader.load(path, entrypoint=args.target, runs=args.runs)
        result = experiment.run()
        results.append(result)
        print(result.summary(), end="\n\n")

    if args.report:
        args.report.write_text(json.dumps([r.to_dict(args.traces) for r in results], indent=2))
        print(f"report written to {args.report}")

    weaknesses = sum(r.verdict is Verdict.WEAKNESS for r in results)
    inconclusive = sum(r.verdict is Verdict.INCONCLUSIVE for r in results)
    print(f"{len(results)} experiment(s): {weaknesses} weakness(es), {inconclusive} inconclusive")
    return 1 if weaknesses else (2 if inconclusive else 0)


def _mcp_proxy(args: argparse.Namespace) -> int:
    import asyncio

    from agentic_chaos.mcp import McpChaosProxy, StdioEndpoint
    from agentic_chaos.runtime import Session, bound

    command = args.server[1:] if args.server[:1] == ["--"] else args.server
    if not command:
        print("error: missing MCP server command after --", file=sys.stderr)
        return 2
    fault_list, probe_list, seed = loader.load_proxy_config(args.faults)
    session = Session(fault_list, seed=seed)

    async def serve() -> None:
        with bound(session):
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
        report = {"probes": [r.to_dict() for r in results], "trace": session.trace.to_dict()}
        args.trace.write_text(json.dumps(report, indent=2))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
