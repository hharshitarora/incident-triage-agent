"""Phase 2 — Git History: find recent commits touching the implicated files.

Deterministic (no LLM): shells out to git. This is the phase most likely to name
the actual culprit, so we keep it fully inspectable.
"""
from __future__ import annotations

import os
import re
import subprocess

from ..observability import span
from ..state import SuspectCommit, TriageState

_SEP = "\x1f"


_PATCH_LIMIT = 1500  # chars; keep prime-suspect diffs small enough for the prompt
_PRIME_SUSPECTS = 3  # how many top commits get their diff attached
_MAX_CANDIDATES = 10  # commits whose diff we fetch and score locally

# Words that appear in almost every traceback and discriminate nothing.
_NOISE = frozenset("""
error none self true false object attribute line file module traceback
main run call args kwargs return type value key index name str int
""".split())


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


def _error_tokens(parsed) -> set[str]:
    """Identifiers the error is actually about."""
    if parsed is None:
        return set()
    text = " ".join(filter(None, [parsed.error_type, parsed.message, parsed.signature]))
    tokens = {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)}
    for frame in (parsed.stack_frames or []):
        if frame.function:
            tokens.add(frame.function.lower())
    return tokens - _NOISE


def _raising_file(parsed) -> str:
    """The file in the deepest frame: where the exception actually came from."""
    frames = getattr(parsed, "stack_frames", None) or []
    return frames[-1].file.replace("\\", "/").split("/")[-1] if frames else ""


def _failing_source(parsed) -> str:
    frames = getattr(parsed, "stack_frames", None) or []
    for frame in reversed(frames):
        if frame.source:
            return " ".join(frame.source.split())
    return ""


def _relevance(patch: str, tokens: set[str]) -> float:
    """How much a commit's diff has to do with the reported error.

    Weighted so that *removing* a line mentioning the error's subject counts for
    more than adding one. Most regressions are a deleted guard, a dropped
    default, or a removed argument: the bug is in what is no longer there.

    A mention only in surrounding context is worth little, since a docstring
    commit touching the same function picks that up for free.
    """
    if not patch or not tokens:
        return 0.0
    removed, added = [], []
    for line in patch.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:].lower())
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:].lower())
    context = patch.lower()

    score = 0.0
    for token in tokens:
        in_removed = any(token in line for line in removed)
        in_added = any(token in line for line in added)
        if in_removed:
            # Changed or deleted. Either way the commit altered behaviour the
            # error is about, which is what a regression looks like.
            score += 3.0
        elif in_added:
            # Added only. Weighted low on purpose: the commit that first creates
            # a file mentions every identifier in it, so without this the
            # initial commit outranks the change that actually broke something.
            score += 0.5
        elif token in context:
            score += 0.25
    return round(score / len(tokens), 4)


def _boosted(base: float, patch: str, commit_files: list[str],
             raising_file: str, failing_source: str) -> float:
    """Weight a commit by how much it looks like a regression, not a creation.

    Four adjustments, all weights rather than filters, because the change that
    breaks a call site is often not in the file that raises.

    **Raising file.** The exception came from somewhere specific. Without this,
    a commit in a neighbouring module outscores the culprit purely by mentioning
    the same identifier: for `zero_division`, a report file whose diff removed a
    line containing `mean` beat the stats file that deleted the empty-list
    guard, because the guard's own lines never say `mean`.

    **File creation.** A diff carrying `new file mode` wrote the code; it did not
    change it. It also mentions every identifier in the file for free, so it wins
    any token-overlap contest it is allowed to enter. It is almost never the
    regression, and this was the single biggest source of wrong answers while
    tuning: `Add stats helpers` outranking `Inline the empty check`.

    **Deletions.** Regressions remove things: a guard, a default, an argument.
    The removed lines usually say nothing about the error, which is exactly why
    token overlap alone cannot find them, so having deletions at all is its own
    signal.

    **The failing line**, but only for commits that also delete something. The
    author who first wrote the line is not the author who broke it, and without
    that condition this bonus rewards the original commit.
    """
    score = base
    has_deletions = any(
        line.startswith("-") and not line.startswith("---")
        for line in patch.splitlines()
    )

    if raising_file and any(f.split("/")[-1] == raising_file for f in commit_files):
        score *= 1.75
    if "new file mode" in patch:
        score *= 0.25
    if has_deletions:
        score *= 1.4
    if failing_source and has_deletions:
        added = " ".join(
            " ".join(line[1:].split())
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        if failing_source in added:
            score += 1.5
    return round(score, 4)


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

        # `--is-inside-work-tree` succeeds for any subdirectory of a repo, so a
        # path that is merely *inside* one passes the check above and the agent
        # then investigates the wrong project's history without saying so. That
        # is not hypothetical: a half-built demo sandbox inside this repo did
        # exactly that, and the only symptom was an unexplained "unclear".
        toplevel = _git(repo, "rev-parse", "--show-toplevel").stdout.strip()
        if toplevel:
            want = os.path.realpath(repo)
            got = os.path.realpath(toplevel)
            if os.path.normcase(want) != os.path.normcase(got):
                warnings.append(
                    f"git_history: skipped, '{repo}' is not a repository root; "
                    f"it sits inside '{got}'"
                )
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

        # Rank by relevance to the error, not by recency.
        #
        # This used to sort by date and attach diffs to the two newest commits.
        # That is a trap the eval suite exposed: when a docstring tweak lands
        # after the real regression, the culprit's diff is never shown to the
        # report node at all, while two unrelated recent diffs are. The prompt
        # then asks the model to "cite the exact change", and the only changes
        # it can see are the wrong ones. It was not reasoning badly; it was
        # choosing from the wrong shortlist.
        #
        # Scoring stays deterministic and local: fetch each candidate's diff and
        # measure overlap with the identifiers the error names. Recency is only
        # a tiebreaker now.
        tokens = _error_tokens(parsed)
        raising_file = _raising_file(parsed)
        failing_source = _failing_source(parsed)
        candidates = sorted(commits.values(), key=lambda c: c.date, reverse=True)[:_MAX_CANDIDATES]

        scored = []
        for candidate in candidates:
            patch = _diff(repo, candidate.sha, files)
            base = _relevance(patch, tokens)
            score = _boosted(base, patch, candidate.files, raising_file, failing_source)
            scored.append((score, candidate.date, candidate, patch))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)

        suspects = []
        for rank, (score, _, candidate, patch) in enumerate(scored[:8]):
            candidate.why = f"relevance {score:.2f} to {sorted(tokens)[:5]}"
            # Only the top few carry their diff into the prompt, to bound size.
            candidate.patch = patch if rank < _PRIME_SUSPECTS else ""
            suspects.append(candidate)

        note = f"git_history: {len(suspects)} suspect commit(s) across {len(files)} file(s)"
        return {
            "suspect_commits": suspects,
            "trace": state.get("trace", []) + [note],
            "warnings": warnings,
        }
