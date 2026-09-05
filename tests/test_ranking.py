"""Ranking suspects by relevance rather than recency.

The bug these cover: `git_history` used to sort by date and attach diffs to the
two newest commits. When any trivial change landed after the real regression,
the culprit's diff was never shown to the report node, while two unrelated
recent diffs were. The prompt then asks the model to cite the exact change, and
the only changes it can see are the wrong ones.

Ranking first is nice. Reaching the diffed shortlist at all is what matters.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from triage.nodes.git_history import _boosted, _error_tokens, _relevance, git_history
from triage.state import ParsedError, StackFrame

PRIME_SUSPECTS = 3


def _git(cwd, *args, **env):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   env={**os.environ, **env},
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _commit(repo, msg, author):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg,
         GIT_AUTHOR_NAME=author, GIT_AUTHOR_EMAIL="a@x.dev",
         GIT_COMMITTER_NAME=author, GIT_COMMITTER_EMAIL="a@x.dev")


@pytest.fixture
def buried_culprit(tmp_path):
    """A repo where the culprit is old and two trivial commits land after it."""
    repo = tmp_path / "svc"
    (repo / "app").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _git(repo, "config", "commit.gpgsign", "false")

    settings = repo / "app" / "settings.py"
    worker = repo / "app" / "worker.py"

    settings.write_text('def get_timeout(cfg):\n    return cfg.get("timeout", 30)\n')
    _commit(repo, "Add settings loader", "Priya Raman")

    worker.write_text('def start():\n    return get_timeout({})\n')
    _commit(repo, "Add worker bootstrap", "Marcus Ihde")

    settings.write_text('def get_timeout(cfg):\n    return cfg["timeout"]\n')
    _commit(repo, "Simplify settings access", "Dana Lee")   # <-- culprit
    culprit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()

    settings.write_text('def get_timeout(cfg):\n    return cfg["timeout"]\n\n# note\n')
    _commit(repo, "Document settings defaults", "Dana Lee")

    worker.write_text('def start():\n    return get_timeout({"retries": 8})\n')
    _commit(repo, "Bump retries for the worker pool", "Marcus Ihde")

    return repo, culprit


def _state(repo):
    return {
        "repo_path": str(repo),
        "parsed_error": ParsedError(
            error_type="KeyError", message="'timeout'", signature="KeyError timeout",
            stack_frames=[
                StackFrame(file="app/worker.py", function="start", source="return get_timeout({})"),
                StackFrame(file="app/settings.py", function="get_timeout",
                           source='return cfg["timeout"]'),
            ],
            implicated_files=["/srv/app/settings.py", "/srv/app/worker.py"],
        ),
    }


def test_culprit_diff_reaches_the_prompt(buried_culprit):
    """The regression itself. Two commits land after the culprit; its diff must
    still be one of the ones the report node actually sees."""
    repo, culprit = buried_culprit
    suspects = git_history(_state(repo))["suspect_commits"]
    diffed = [c for c in suspects[:PRIME_SUSPECTS] if c.patch]
    assert any(culprit.startswith(c.sha) for c in diffed), (
        "culprit carries no diff; the model cannot cite a change it cannot see"
    )


def test_culprit_outranks_the_trivial_commits_that_followed(buried_culprit):
    repo, culprit = buried_culprit
    suspects = git_history(_state(repo))["suspect_commits"]
    assert culprit.startswith(suspects[0].sha), (
        f"ranked {suspects[0].summary!r} above the actual regression"
    )


def test_ranking_is_explained(buried_culprit):
    """The highest-signal step stays inspectable, so `why` is not decoration."""
    repo, _ = buried_culprit
    for suspect in git_history(_state(repo))["suspect_commits"]:
        assert "relevance" in suspect.why


# --- the scoring rules, unit level -----------------------------------------

_TOKENS = {"timeout", "get_timeout"}


def test_deleting_a_line_scores_above_adding_one():
    """Regressions remove things: a guard, a default, an argument."""
    removed = "--- a/x.py\n+++ b/x.py\n-    return cfg.get('timeout', 30)\n+    return cfg['timeout']\n"
    added = "--- a/x.py\n+++ b/x.py\n+    # timeout is documented elsewhere\n"
    assert _relevance(removed, _TOKENS) > _relevance(added, _TOKENS)


def test_a_file_creation_is_heavily_discounted():
    """A creation mentions every identifier in the file for free.

    Without this discount `Add stats helpers` outranks `Inline the empty check`,
    which was the single biggest source of wrong answers while tuning.
    """
    creation = "new file mode 100644\n--- /dev/null\n+++ b/x.py\n+def get_timeout(cfg):\n+    return cfg['timeout']\n"
    base = _relevance(creation, _TOKENS)
    assert _boosted(base, creation, ["x.py"], "", "") < base


def test_touching_the_raising_file_is_weighted_up():
    patch = "--- a/x.py\n+++ b/x.py\n-    guard()\n"
    near = _boosted(1.0, patch, ["app/settings.py"], "settings.py", "")
    far = _boosted(1.0, patch, ["app/worker.py"], "settings.py", "")
    assert near > far


def test_the_failing_line_only_counts_for_commits_that_deleted_something():
    """Otherwise the bonus rewards whoever first wrote the line, not who broke it."""
    creation = "+++ b/x.py\n+    return cfg['timeout']\n"
    modification = "--- a/x.py\n+++ b/x.py\n-    return cfg.get('timeout', 30)\n+    return cfg['timeout']\n"
    src = "return cfg['timeout']"
    assert _boosted(1.0, creation, [], "", src) == pytest.approx(1.0)
    assert _boosted(1.0, modification, [], "", src) > 1.0


def test_error_tokens_drop_generic_traceback_noise():
    parsed = ParsedError(error_type="KeyError", message="'timeout'",
                         signature="KeyError timeout",
                         stack_frames=[StackFrame(file="a.py", function="get_timeout")])
    tokens = _error_tokens(parsed)
    assert "get_timeout" in tokens and "timeout" in tokens
    assert not {"error", "file", "line", "return"} & tokens


def test_no_tokens_means_no_score():
    assert _relevance("-  anything\n", set()) == 0.0
    assert _relevance("", {"timeout"}) == 0.0


# --- pointing at a subdirectory of a repo -----------------------------------

def test_a_subdirectory_of_a_repo_is_refused_not_silently_investigated(buried_culprit):
    """`--is-inside-work-tree` succeeds for any subdirectory.

    Without an explicit root check the agent investigates the parent project's
    history and reports on the wrong repository entirely, with no warning. A
    half-built demo sandbox inside this very repo did exactly that.
    """
    repo, _ = buried_culprit
    out = git_history({"repo_path": str(repo / "app"), "parsed_error": None})
    assert out["suspect_commits"] == []
    assert any("not a repository root" in w for w in out["warnings"])
