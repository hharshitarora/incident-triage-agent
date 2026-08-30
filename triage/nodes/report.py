"""Phase 4 — Report: synthesize the evidence into a structured triage report."""
from __future__ import annotations

import textwrap

from ..llm import structured
from ..observability import span
from ..state import TriageReport, TriageState

_PROMPT = """You are triaging a production incident. Using ONLY the evidence below,
produce a triage report.

Rules:
- root_cause_hypothesis: your best explanation, grounded in the evidence. When a
  suspect commit includes a diff, cite the exact change that introduced the bug.
- confidence: calibrated 0-1. Low if the evidence is thin or contradictory.
- suspect_commit: the SHA of the single most likely culprit, or null if unclear.
- recommended_owner: usually the author of the suspect commit.
- severity: low | medium | high | critical.
- next_steps: concrete, specific actions (not generic advice).
Do not invent commits, issues, or files that are not listed.

PARSED ERROR:
{error}

SUSPECT COMMITS (from git history):
{commits}

RELATED ISSUES:
{issues}
"""


def report(state: TriageState) -> dict:
    with span("report"):
        parsed = state.get("parsed_error")
        commits = state.get("suspect_commits", [])
        issues = state.get("related_issues", [])

        def _fmt_commit(c) -> str:
            head = (
                f"- {c.sha} | {c.author} | {c.date} | {c.summary}"
                f" | files: {', '.join(c.files) or 'n/a'}"
            )
            if c.patch:
                head += f"\n  diff:\n{textwrap.indent(c.patch, '    ')}"
            return head

        commits_str = "\n".join(_fmt_commit(c) for c in commits) or "none"
        issues_str = (
            "\n".join(f"- #{i.number} [{i.state}] {i.title} ({i.url})" for i in issues)
            or "none"
        )

        prompt = _PROMPT.format(
            error=parsed.model_dump_json(indent=2) if parsed else "{}",
            commits=commits_str,
            issues=issues_str,
        )
        rep = structured(prompt, TriageReport)
        note = f"report: confidence={rep.confidence:.2f} · severity={rep.severity}"
        return {"report": rep, "trace": state.get("trace", []) + [note]}
