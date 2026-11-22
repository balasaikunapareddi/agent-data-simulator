"""Command-line interface.

    python -m agent_data_simulator demo            # build + evaluate, one command
    python -m agent_data_simulator build           # build an estate only
    python -m agent_data_simulator eval            # evaluate against a built estate
    python -m agent_data_simulator realism         # realism report for a built estate
    python -m agent_data_simulator ask "..."       # ask the agent one question

Mock mode is the default (no API key). Add --live to use Gemini
(requires GEMINI_API_KEY in the environment).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import run_agent
from .estate import build_estate, load_estate
from .evaluate import format_report, run_evaluation
from .llm import make_llm
from .realism import format_realism, realism_report

DEFAULT_ESTATE = Path("estate")


def _print_build_summary(built: dict) -> None:
    summary = built["summary"]
    print(f"Estate built at {built['dir']} (seed {summary['seed']})")
    for table, count in summary["row_counts"].items():
        print(f"  {table}: {count} rows")
    print("Messiness injected:")
    for flaw in summary["messiness_log"]:
        print(f"  - {flaw['flaw_type']} on {flaw['table']}: {flaw['affected_rows']} rows affected")
    bench = built["benchmark"]
    print(f"Benchmark: {len(bench['questions'])} questions kept, {len(bench['dropped'])} dropped")
    for dropped in bench["dropped"]:
        print(f"  dropped {dropped['qid']}: {dropped['reason']}")


def cmd_build(args) -> int:
    llm_client = make_llm(live=True) if args.live else None
    built = build_estate(Path(args.out), seed=args.seed, llm_client=llm_client)
    _print_build_summary(built)
    return 0


def cmd_demo(args) -> int:
    built = build_estate(Path(args.out), seed=args.seed)
    _print_build_summary(built)
    tools, benchmark = load_estate(Path(args.out))

    print()
    print(format_realism(realism_report(Path(args.out) / "estate.db")))
    print()

    if args.live:
        report = run_evaluation(benchmark, tools, make_llm(live=True), label="live (Gemini)")
        print(format_report(report))
    else:
        careful = run_evaluation(
            benchmark, tools, make_llm(variant="careful"), label="mock agent (careful)"
        )
        print(format_report(careful))
        print()
        naive = run_evaluation(
            benchmark, tools, make_llm(variant="naive"), label="mock agent (naive)"
        )
        print(format_report(naive))
        print()
        print(
            "The gap between the two mock agents is the point of the wind tunnel: "
            "same tools, same estate — the naive agent trusts the first formal "
            "document and writes the obvious SQL, and the benchmark catches it."
        )
    return 0


def cmd_eval(args) -> int:
    tools, benchmark = load_estate(Path(args.estate))
    llm = make_llm(live=args.live, variant=args.variant)
    label = "live (Gemini)" if args.live else f"mock agent ({args.variant})"
    report = run_evaluation(benchmark, tools, llm, label=label)
    print(format_report(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nJSON report written to {args.json}")
    return 0


def cmd_realism(args) -> int:
    report = realism_report(Path(args.estate) / "estate.db")
    print(format_realism(report))
    return 0


def cmd_ask(args) -> int:
    tools, _ = load_estate(Path(args.estate))
    llm = make_llm(live=args.live, variant=args.variant)
    result = run_agent(args.question, tools, llm)
    for step in result["trace"]:
        print(f"  step: {json.dumps(step, default=str)}")
    if result["abstained"]:
        print(f"ABSTAINED: {result['reason']}")
    else:
        print(f"ANSWER: {result['answer']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-data-simulator", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="build a synthetic estate")
    p_build.add_argument("--out", default=str(DEFAULT_ESTATE))
    p_build.add_argument("--seed", type=int, default=42)
    p_build.add_argument("--live", action="store_true", help="render documents with Gemini")
    p_build.set_defaults(func=cmd_build)

    p_demo = sub.add_parser("demo", help="build an estate and run the full evaluation")
    p_demo.add_argument("--out", default=str(DEFAULT_ESTATE))
    p_demo.add_argument("--seed", type=int, default=42)
    p_demo.add_argument("--live", action="store_true", help="evaluate the live Gemini agent")
    p_demo.set_defaults(func=cmd_demo)

    p_eval = sub.add_parser("eval", help="evaluate the agent against a built estate")
    p_eval.add_argument("--estate", default=str(DEFAULT_ESTATE))
    p_eval.add_argument("--live", action="store_true")
    p_eval.add_argument("--variant", choices=["careful", "naive"], default="careful")
    p_eval.add_argument("--json", default=None, help="also write the JSON report here")
    p_eval.set_defaults(func=cmd_eval)

    p_real = sub.add_parser("realism", help="print the realism report for a built estate")
    p_real.add_argument("--estate", default=str(DEFAULT_ESTATE))
    p_real.set_defaults(func=cmd_realism)

    p_ask = sub.add_parser("ask", help="ask the agent a single question")
    p_ask.add_argument("question")
    p_ask.add_argument("--estate", default=str(DEFAULT_ESTATE))
    p_ask.add_argument("--live", action="store_true")
    p_ask.add_argument("--variant", choices=["careful", "naive"], default="careful")
    p_ask.set_defaults(func=cmd_ask)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
