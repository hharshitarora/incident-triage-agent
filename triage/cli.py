"""CLI entrypoint: point it at a repo + a log, get a root-cause triage report."""
from __future__ import annotations

import argparse
import sys

from .graph import build_graph
from .observability import setup_tracing
from .state import TriageState


def _read_log(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _render(result: TriageState) -> None:
    rep = result.get("report")
    print("\n" + "=" * 68)
    print("  INCIDENT TRIAGE REPORT")
    print("=" * 68)
    if not rep:
        print("  No report produced.")
    else:
        print(f"  Summary      : {rep.summary}")
        print(f"  Root cause   : {rep.root_cause_hypothesis}")
        print(f"  Confidence   : {rep.confidence:.0%}")
        print(f"  Severity     : {rep.severity}")
        print(f"  Suspect      : {rep.suspect_commit or 'unclear'}")
        print(f"  Owner        : {rep.recommended_owner or 'unassigned'}")
        if rep.related_issue_numbers:
            print(f"  Related      : {', '.join('#' + str(n) for n in rep.related_issue_numbers)}")
        if rep.next_steps:
            print("  Next steps   :")
            for step in rep.next_steps:
                print(f"    - {step}")

    trace = result.get("trace", [])
    if trace:
        print("-" * 68)
        print("  phases:")
        for t in trace:
            print(f"    · {t}")
    warnings = result.get("warnings", [])
    if warnings:
        print("  warnings:")
        for w in warnings:
            print(f"    ! {w}")
    print("=" * 68 + "\n")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="triage",
        description="Incident-triage agent: logs -> git history -> issues -> root-cause report",
    )
    p.add_argument("--repo", required=True, help="path to the git repo to investigate")
    p.add_argument("--log", required=True, help="path to the error log ('-' for stdin)")
    p.add_argument("--github-repo", help="owner/name for related-issue lookup")
    p.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = p.parse_args(argv)

    # Windows consoles default to cp1252; force UTF-8 so separators render.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    setup_tracing()
    app = build_graph()
    result: TriageState = app.invoke(
        {
            "repo_path": args.repo,
            "log_text": _read_log(args.log),
            "github_repo": args.github_repo,
            "trace": [],
            "warnings": [],
        }
    )

    if args.json:
        rep = result.get("report")
        print(rep.model_dump_json(indent=2) if rep else "{}")
    else:
        _render(result)


if __name__ == "__main__":
    main()
