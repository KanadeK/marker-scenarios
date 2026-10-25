import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .core import (
    InputError,
    capture_environment,
    evaluate,
    load_scenarios,
    parse_project,
    read_text,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Test declared PEP 508 dependency activation")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser(
        "capture", help="capture this interpreter's complete marker environment"
    )
    capture.add_argument("--name", default="current")
    check = commands.add_parser("check", help="test a static pyproject against explicit scenarios")
    check.add_argument("project", type=Path)
    check.add_argument("--scenarios", type=Path, required=True)
    check.add_argument("--format", choices=("text", "json"), default="text")
    check.add_argument("--fail-uncovered", action="store_true")
    for sub in (capture, check):
        sub.add_argument("--output", type=Path, help="create a new file; never overwrite")
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            result = capture_environment(args.name)
            code = 0
        else:
            result = evaluate(
                parse_project(read_text(args.project)), load_scenarios(args.scenarios)
            )
            code = int(bool(result["failures"] or (args.fail_uncovered and result["uncovered"])))
        if args.command == "capture" or args.format == "json":
            output = json.dumps(result, indent=2, ensure_ascii=True) + "\n"
        else:
            summary = result["summary"]
            lines = [
                f"{summary['scenarios']} scenarios; {summary['failures']} failures; "
                f"{summary['uncovered']} declarations uncovered"
            ]
            for row in result["scenarios"]:
                lines.append(
                    row["name"] + ": " + json.dumps(row["active_names"], ensure_ascii=True)
                )
            for problem in result["failures"]:
                lines.append(json.dumps(problem, ensure_ascii=True, sort_keys=True))
            if result["uncovered"]:
                lines.append("uncovered: " + json.dumps(result["uncovered"], ensure_ascii=True))
            output = "\n".join(lines) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(output)
        else:
            sys.stdout.write(output)
        return code
    except (InputError, OSError) as exc:
        message = str(exc) if isinstance(exc, InputError) else type(exc).__name__
        print(f"marker-scenarios: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
