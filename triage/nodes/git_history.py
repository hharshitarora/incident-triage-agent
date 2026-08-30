"""Phase 2 — Git History: find recent commits touching the implicated files.

Deterministic (no LLM): shells out to git. This is the phase most likely to name
the actual culprit, so we keep it fully inspectable.
"""
from __future__ import annotations

import subprocess

from ..observability import span
from ..state import SuspectCommit, TriageState

_SEP = "\x1f"


_PATCH_LIMIT = 1500  # chars; keep prime-suspect diffs small enough for the prompt
_PRIME_SUSPECTS = 2  # how many top commits get their diff attached


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        timeout=20,
    )


def _diff(repo: str, sha: str, files: list[str]) -> str:
    """Return the (truncated) diff a commit introduced, scoped to the implicated files."""
    pathspec = [f for f in files if f != "."]
    r = _git(repo, "show", sha, "--no-color", "--format=", "--", *pathspec)
    if r.returncode != 0 or not r.stdout.strip():
        return ""
    patch = r.stdout.strip()
    return patch[:_PATCH_LIMIT] + ("\n... (truncated)" if len(patch) > _PATCH_LIMIT else "")


def git_history(state: TriageState) -> dict:
    with span("git_history"):
        repo = state["repo_path"]
        warnings = list(state.get("warnings", []))

        if _git(repo, "rev-parse", "--is-inside-work-tree").returncode != 0:
            warnings.append(f"git_history: '{repo}' is not a git repository — skipping")
            return {"suspect_commits": [], "warnings": warnings}

        parsed = state.get("parsed_error")
        files = list(parsed.implicated_files) if parsed and parsed.implicated_files else ["."]

        commits: dict[str, SuspectCommit] = {}
        for f in files:
            r = _git(
                repo, "log", "-n", "5",
                f"--format=%H{_SEP}%an{_SEP}%ad{_SEP}%s", "--date=short", "--", f,
            )
            if r.returncode != 0:
                continue
            for line in r.stdout.strip().splitlines():
                if not line:
                    continue
                parts = (line.split(_SEP) + ["", "", "", ""])[:4]
                sha, author, date, summary = parts
                c = commits.setdefault(
                    sha,
                    SuspectCommit(sha=sha[:10], author=author, date=date, summary=summary),
                )
                if f != "." and f not in c.files:
                    c.files.append(f)

        # Most recent first, then attach diffs to the prime suspects only.
        suspects = sorted(commits.values(), key=lambda c: c.date, reverse=True)[:8]
        for suspect in suspects[:_PRIME_SUSPECTS]:
            suspect.patch = _diff(repo, suspect.sha, files)

        note = f"git_history: {len(suspects)} suspect commit(s) across {len(files)} file(s)"
        return {
            "suspect_commits": suspects,
            "trace": state.get("trace", []) + [note],
            "warnings": warnings,
        }
