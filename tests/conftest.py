"""Hermetic fixtures — no network, no LLM. Builds a tiny git repo with a planted bug."""
from __future__ import annotations

import os
import subprocess

import pytest


def _git(cwd, *args, **env):
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        env={**os.environ, **env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _commit(repo, msg, author):
    _git(repo, "add", "-A")
    _git(
        repo, "commit", "-m", msg,
        GIT_AUTHOR_NAME=author, GIT_AUTHOR_EMAIL=f"{author.split()[0].lower()}@x.dev",
        GIT_COMMITTER_NAME=author, GIT_COMMITTER_EMAIL=f"{author.split()[0].lower()}@x.dev",
    )


@pytest.fixture
def buggy_repo(tmp_path):
    """A repo where the latest commit on pricing.py introduces the bug (by Dana Lee)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "commit.gpgsign", "false")

    (repo / "pricing.py").write_text(
        "def apply_discount(price, code):\n    return DISCOUNTS.get(code, 0.0)\n"
    )
    _commit(repo, "Add pricing module", "Alice Chen")

    (repo / "pricing.py").write_text(
        "def apply_discount(price, code):\n    return DISCOUNTS[code]\n"
    )
    _commit(repo, "Simplify discount lookup", "Dana Lee")

    return repo
