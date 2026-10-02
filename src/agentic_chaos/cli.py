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
    run.add_argument("files", nargs="+", type=Path)
    run.add_argument("--target", help="override spec.target.entrypoint (module:callable)")
    run.add_argument("--runs", type=int, help="override spec.runs")
    run.add_argument("--report", type=Path, help="write a JSON report to this path")
    run.add_argument("--traces", action="store_true", help="include full traces in the JSON report")

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

    results = []
    for path in args.files:
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


if __name__ == "__main__":
    sys.exit(main())
