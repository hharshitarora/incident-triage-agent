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


def _resolve_paths(repo: str, files: list[str]) -> tuple[list[str], list[str]]:
    """Map paths from a traceback onto paths git actually tracks.

    A stack trace reports where code ran, not where it lives in the repo:
    "/srv/app/settings.py" in production is "app/settings.py" in git. Passing
    the deployment path straight to `git log --` matches nothing, and git exits
    0 while doing it, so the phase reported success with zero commits and the
    report node silently lost its best evidence.

    Resolution is by longest unique path suffix, which handles container
    prefixes, absolute paths and Windows separators without guessing. If a
    suffix is ambiguous (`utils.py` in four packages) every candidate is kept:
    ranking is the next step's job, and dropping evidence here is worse.
    """
    listing = _git(repo, "ls-files")
    tracked = [t for t in listing.stdout.splitlines() if t.strip()]
    if not tracked:
        return [f for f in files if f != "."], []

    tracked_set = set(tracked)
    resolved: list[str] = []
    unresolved: list[str] = []

    for raw in files:
        if raw == ".":
            continue
        norm = raw.replace("\\", "/").lstrip("/")
        if norm in tracked_set:
            resolved.append(norm)
            continue

        parts = norm.split("/")
        matches: list[str] = []
        for i in range(len(parts)):
            suffix = "/".join(parts[i:])
            matches = [t for t in tracked if t == suffix or t.endswith("/" + suffix)]
            if matches:
                break
        if matches:
            resolved.extend(m for m in matches if m not in resolved)
        else:
            unresolved.append(raw)

    return resolved, unresolved


def git_history(state: TriageState) -> dict:
    with span("git_history"):
        repo = state["repo_path"]
        warnings = list(state.get("warnings", []))

        probe = _git(repo, "rev-parse", "--is-inside-work-tree")
        if probe.returncode != 0:
            # Report why, not just that. Every non-zero exit used to be labelled
            # "not a git repository", which is wrong for the common cases: a repo
            # git refuses to trust ("detected dubious ownership", routine on
            # Windows drives that record no ownership), a bad path, or git
            # missing from PATH. All three were reported as an absent repo,
            # which sends anyone debugging it to entirely the wrong place.
            detail = (probe.stderr or probe.stdout).strip().splitlines()
            reason = detail[0].strip() if detail else f"git exited {probe.returncode}"
            if "not a git repository" in reason:
                reason = f"'{repo}' is not a git repository"
            warnings.append(f"git_history: skipped, {reason}")
            return {"suspect_commits": [], "warnings": warnings}

        parsed = state.get("parsed_error")
        reported = list(parsed.implicated_files) if parsed and parsed.implicated_files else ["."]

        files, unresolved = _resolve_paths(repo, reported)
        if unresolved:
            warnings.append(
                "git_history: no tracked file matches " + ", ".join(unresolved[:4])
            )
        if not files:
            files = ["."]

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
