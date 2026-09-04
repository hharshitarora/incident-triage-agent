"""Regression tests for two bugs found by running the eval harness against this agent.

Both were silent. The agent produced a confident, well-written report in each
case; it had simply lost its best evidence on the way there.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from triage.nodes.git_history import _resolve_paths, git_history
from triage.state import ParsedError


def _git(cwd, *args, **env):
    subprocess.run(
        ["git", "-C", str(cwd), *args], check=True,
        env={**os.environ, **env},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def nested_repo(tmp_path):
    """A repo whose files live under a package, like most real ones."""
    repo = tmp_path / "svc"
    (repo / "app").mkdir(parents=True)
    (repo / "lib" / "util").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _git(repo, "config", "commit.gpgsign", "false")

    (repo / "app" / "settings.py").write_text('def get_timeout(cfg):\n    return cfg["timeout"]\n')
    (repo / "lib" / "util" / "helpers.py").write_text("def helper():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "Initial",
         GIT_AUTHOR_NAME="Dana Lee", GIT_AUTHOR_EMAIL="dana@x.dev",
         GIT_COMMITTER_NAME="Dana Lee", GIT_COMMITTER_EMAIL="dana@x.dev")
    return repo


# --- bug 1: traceback paths never matched repo paths ------------------------

def test_deployment_path_resolves_to_the_repo_path(nested_repo):
    """A stack trace says where code ran, not where it lives.

    "/srv/app/settings.py" in production is "app/settings.py" in git. Passing
    the deployment path to `git log --` matches nothing and still exits 0, so
    the phase reported success with zero commits.
    """
    resolved, unresolved = _resolve_paths(str(nested_repo), ["/srv/app/settings.py"])
    assert resolved == ["app/settings.py"]
    assert not unresolved


def test_deeply_nested_suffix_resolves(nested_repo):
    resolved, _ = _resolve_paths(str(nested_repo), ["/opt/deploy/current/lib/util/helpers.py"])
    assert resolved == ["lib/util/helpers.py"]


def test_bare_filename_resolves(nested_repo):
    resolved, _ = _resolve_paths(str(nested_repo), ["settings.py"])
    assert resolved == ["app/settings.py"]


def test_windows_separators_resolve(nested_repo):
    resolved, _ = _resolve_paths(str(nested_repo), ["C:\\srv\\app\\settings.py"])
    assert resolved == ["app/settings.py"]


def test_already_correct_path_is_left_alone(nested_repo):
    resolved, unresolved = _resolve_paths(str(nested_repo), ["app/settings.py"])
    assert resolved == ["app/settings.py"] and not unresolved


def test_unknown_file_is_reported_not_silently_dropped(nested_repo):
    resolved, unresolved = _resolve_paths(str(nested_repo), ["/srv/app/bootstrap.py"])
    assert resolved == []
    assert unresolved == ["/srv/app/bootstrap.py"]


def test_ambiguous_suffix_keeps_every_candidate(tmp_path):
    """`utils.py` in four packages is ambiguous. Ranking is a later step's job.

    Dropping the evidence here would be worse than carrying extra candidates.
    """
    repo = tmp_path / "amb"
    for pkg in ("a", "b"):
        (repo / pkg).mkdir(parents=True)
        (repo / pkg / "utils.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "Initial",
         GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@x.dev",
         GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@x.dev")

    resolved, _ = _resolve_paths(str(repo), ["/srv/utils.py"])
    assert sorted(resolved) == ["a/utils.py", "b/utils.py"]


def test_git_history_finds_commits_from_a_deployment_path(nested_repo):
    """End to end: the whole reason the fix matters."""
    out = git_history({
        "repo_path": str(nested_repo),
        "parsed_error": ParsedError(
            error_type="KeyError", message="'timeout'", signature="KeyError timeout",
            implicated_files=["/srv/app/settings.py"],
        ),
    })
    assert out["suspect_commits"], "deployment path found no commits"
    assert out["suspect_commits"][0].author == "Dana Lee"


# --- bug 2: every git failure was reported as "not a git repository" --------

def test_a_real_missing_repo_still_says_so(tmp_path):
    out = git_history({"repo_path": str(tmp_path)})
    assert out["suspect_commits"] == []
    assert any("not a git" in w for w in out["warnings"])


def test_other_git_failures_report_the_actual_reason(tmp_path):
    """A path that does not exist is not the same as a directory without a repo.

    Both used to produce "is not a git repository", which sends whoever is
    debugging it looking for the wrong problem entirely.
    """
    missing = tmp_path / "does" / "not" / "exist"
    out = git_history({"repo_path": str(missing)})
    assert out["suspect_commits"] == []
    warning = " ".join(out["warnings"]).lower()
    assert "git_history: skipped" in warning
    assert "not a git repository" not in warning
