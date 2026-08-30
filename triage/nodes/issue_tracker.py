"""Phase 3 — Issue Tracker: find related/duplicate GitHub issues.

Gracefully degrades: if no repo is configured or the API fails, it records a
warning and returns nothing rather than breaking the pipeline.
"""
from __future__ import annotations

import requests

from ..config import load_config
from ..observability import span
from ..state import RelatedIssue, TriageState


def issue_tracker(state: TriageState) -> dict:
    with span("issue_tracker"):
        repo = state.get("github_repo")
        warnings = list(state.get("warnings", []))

        if not repo:
            warnings.append("issue_tracker: no github_repo provided — skipping")
            return {"related_issues": [], "warnings": warnings}

        parsed = state.get("parsed_error")
        query = parsed.signature if parsed else ""

        headers = {"Accept": "application/vnd.github+json"}
        token = load_config().github_token
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            resp = requests.get(
                "https://api.github.com/search/issues",
                params={"q": f"repo:{repo} {query} in:title,body", "per_page": 5},
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])[:5]
        except Exception as exc:  # noqa: BLE001 — degrade, don't crash the pipeline
            warnings.append(f"issue_tracker: lookup failed ({exc})")
            return {"related_issues": [], "warnings": warnings}

        issues = [
            RelatedIssue(
                number=i["number"], title=i["title"], url=i["html_url"], state=i["state"]
            )
            for i in items
        ]
        note = f"issue_tracker: {len(issues)} related issue(s)"
        return {
            "related_issues": issues,
            "trace": state.get("trace", []) + [note],
            "warnings": warnings,
        }
