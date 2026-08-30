from triage.nodes.git_history import git_history
from triage.state import ParsedError


def test_finds_culprit_and_attaches_diff(buggy_repo):
    state = {
        "repo_path": str(buggy_repo),
        "parsed_error": ParsedError(
            error_type="KeyError", message="'SUMMER25'",
            signature="KeyError DISCOUNTS[code]", implicated_files=["pricing.py"],
        ),
    }
    out = git_history(state)
    suspects = out["suspect_commits"]

    assert suspects, "expected at least one suspect commit"
    top = suspects[0]  # most recent first
    assert top.summary == "Simplify discount lookup"
    assert top.author == "Dana Lee"
    # prime suspect carries the diff that introduced the bug
    assert "DISCOUNTS[code]" in top.patch


def test_non_git_path_degrades_gracefully(tmp_path):
    out = git_history({"repo_path": str(tmp_path)})
    assert out["suspect_commits"] == []
    assert any("not a git" in w for w in out["warnings"])
