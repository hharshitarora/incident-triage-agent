from triage.nodes.issue_tracker import issue_tracker


def test_issue_tracker_skips_without_repo():
    # No github_repo -> no network call, graceful warning.
    out = issue_tracker({"warnings": []})
    assert out["related_issues"] == []
    assert any("no github_repo" in w for w in out["warnings"])
